# 缺口、重複、亂序與非法 OHLC

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-16

## 學習目標

完成本章後，你能：

1. 用固定時間網格辨識 K 線缺口、重複與原始列亂序；
2. 逐列檢查有限值、正價格、非負成交量及 OHLC 包絡關係；
3. 說明 `quant-data status` 與 `KlineFeed` 各自保護哪一道邊界；
4. 分開記錄「偵測」「拒絕」與「依明示規則修復」，不把排序或補值藏在讀取流程；
5. 保存輸入 checksum、預期 finding、實際退出碼與 continue／no-go 決定。

## 問題情境：三根看起來合理的 K 線也可能不能研究

延續第 12 章的 BTCUSDT 一小時研究。你收到 `00:00`、`01:00`、`02:00 UTC`
三個時點的 K 線，價格也都在 100 左右。資料能被 Parquet 讀取，不代表它能成為
研究證據：`01:00` 可能根本不存在、可能出現兩次、可能排在 `02:00` 後面，或
`high` 甚至低於 `open` 與 `close`。

這四種問題會改變訊號、指標狀態與成交路徑。若讀取程式順手 forward-fill、去重、
排序或夾住異常價格，錯誤會從「可見 finding」變成「看似平滑的績效」。本章的目標
不是修好一份市場資料，而是先建立會拒絕不合格輸入的結構品質閘門。

## 執行前預測

先寫下你的答案與理由：

1. 一小時網格中只有 `00:00` 與 `02:00`，能否用 `00:00` 的 close 補成 `01:00`？
2. 同一 `open_time` 出現兩列時，能否在不知道來源優先序的情況下保留最後一列？
3. 三個時點都存在但順序是 `00:00 → 02:00 → 01:00`，排序後能否宣稱原始輸入乾淨？
4. `open=101`、`high=100`、`low=99`、`close=102` 是否為合法 K 線？
5. `quant-data status` 退出 0，是否足以證明原始 Parquet 列序嚴格遞增？

## 核心概念：先定義可檢查 oracle

本章固定 interval 為一小時，研究窗口採半開區間。對任意相鄰網格點，預期
`open_time` 差值是 `3_600_000` 毫秒。四類 finding 的 oracle 是：

| Finding | 本章固定輸入 | 可檢查 oracle | 預設決定 |
|---|---|---|---|
| 缺口 | `00:00`、`02:00`，缺 `01:00` | `gaps` 指出 `01:00`，`missing=1` | fail closed，不自動補值 |
| 重複 | `01:00` 出現兩次 | `duplicates=[1704070800000]` | fail closed，不任選一列 |
| 亂序 | `00:00 → 02:00 → 01:00` | 原始列序不嚴格遞增 | datasource 拒絕，不靜默排序 |
| 非法 OHLC | `open=101`、`high=100`、`low=99`、`close=102` | `ohlc_violations=[1704070800000]` | fail closed，不夾值 |

合法 OHLC 必須同時滿足：

- open、high、low、close 與 volume 全為有限數值；
- 四個價格都大於 0，volume 大於或等於 0；
- `low <= min(open, close)`；
- `max(open, close) <= high`。

這裡的合法只表示單列內部關係成立，不表示價格是真實市場成交，也不表示整個
研究窗口已完整。

## 偵測、拒絕與修復是三個不同動作

| 動作 | 產物 | 可以改原始資料嗎 | 本章規則 |
|---|---|---:|---|
| 偵測 | finding、時間戳、命令、退出碼 | 否 | 保留原輸入與 checksum |
| 拒絕 | `no-go`／fail-closed 決定 | 否 | 不讓有 finding 的資料進研究 |
| 修復 | 新資料版本、規則、來源與差異紀錄 | 可以，但不能覆蓋舊證據 | 本章不執行；先查明來源再另版重建 |

缺口可能來自來源真的沒有交易、下載失敗或分區遺漏；重複可能是同檔重送，也可能
是兩個來源碰撞。沒有 provenance 前，forward-fill、keep-first、keep-last 都只是猜測。
即使之後依明示規則修復，也要產生新 dataset 版本、重跑完整窗口，不能把舊研究
結果改寫成「當時就使用修正版」。

## 系統對照：兩道閘門保護不同結構

`v0.3.0` 的正式只讀入口 `quant-data status` 會讀取 manifest 與 Parquet，重算
coverage、缺口、重複及非法 OHLC。有 finding 時退出 2，沒有 finding 時退出 0。
但輸出的 `total_rows` 與逐檔 `sha256` 直接來自 manifest；本版 status 不會用實檔
重算列數，也不會把實檔 bytes 與 manifest checksum 比對。這兩欄只能識別 manifest
所宣告的版本，不能單獨證明目前磁碟內容仍與宣告一致。

但 K 線 status 在合併分區後會先依 `open_time` 排序，再計算集合式品質。因此它能
找缺口、重複與非法 OHLC，不能證明檔案的原始列序本來就嚴格遞增。JSON 中的
`order_violations` 對本版 K 線會保持空陣列；不能看到空陣列就自行補成「列序通過」。

真正消費原始 table 的已發布 `KlineFeed` 會逐列要求 `open_time` 嚴格遞增，遇到
重複或倒退立即丟出 `DataIntegrityError`。因此本章採兩道閘門：

1. `quant-data status` 必須退出 0，保護 coverage、缺口、重複與 OHLC finding；
2. `KlineFeed` 必須能依原始列序完整迭代，保護 datasource 消費順序。

任何一道失敗都停止。這不是在書稿複製第二套校驗器；固定 oracle 只建立輸入並
呼叫兩個已發布入口。下方另以 `sha256sum` 鎖定隨書保存的固定 JSON 輸入；那不是
status 的實檔 checksum 驗證，也不支持暫存 Parquet 已由產品入口核對 checksum。

## 動手驗證一：固定版本、lockfile 與契約測試

從書籍 repository 根目錄保存路徑，再切到 setup 建立的配套 worktree：

```bash
export BOOK_DIR="$(pwd)"
export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv sync --locked --dev
uv run pytest \
  tests/unit/test_data_validate.py \
  tests/unit/test_data_status.py \
  tests/unit/test_cli_data.py \
  tests/unit/test_datasource_feeds.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，Git status 應沒有
輸出。執行前 oracle 是：資料 validator、status／CLI 退出碼及 datasource 列序拒絕
測試全數通過。本章實測為：

```text
53 passed in 1.61s
```

## 動手驗證二：核對固定輸入與 checksum

本章固定資料是
[ch13-bar-structure-cases.json](../assets/ch13-bar-structure-cases.json)，執行 helper 是
[ch13-bar-structure-oracle.py](../assets/ch13-bar-structure-oracle.py)。兩者都隨書保存；
JSON 是教學值，不是市場下載。

```bash
(cd "$BOOK_DIR" && sha256sum manuscript/assets/ch13-bar-structure-cases.json)
```

預期且實測 checksum：

```text
0a03e8e8c989e280c3db0f7c55893d063801634bb8509e8f3a285f445ada41da  manuscript/assets/ch13-bar-structure-cases.json
```

若 checksum 不同就停止，不要沿用下方輸出。固定輸入逐項列出完整 UTC 毫秒時間戳與
OHLCV；helper 只把各案例寫到自己的暫存根目錄，再呼叫正式 `quant-data status`，
不會在原始列上排序、補值或去重。

## 動手驗證三：通過案例與四個 fail-closed 邊界

仍在配套 `v0.3.0` worktree 執行：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch13-bar-structure-oracle.py"
```

執行前 oracle 是：乾淨案例 status 退出 0，且同一份固定三列表格經 `KlineFeed`
完整產生 6 個 open／close 事件；缺口、重複與非法 OHLC 各自 status 退出 2；亂序
案例不能以 status 的退出 0 當成通過，必須由 `KlineFeed` 拒絕。
預期且實測輸出：

```text
clean=status-0,feed-events-6,PASS
gap=status-2,missing-1,FAIL-CLOSED
duplicate=status-2,ts-1704070800000,FAIL-CLOSED
invalid-ohlc=status-2,ts-1704070800000,FAIL-CLOSED
out-of-order=status-0,INSUFFICIENT
out-of-order-feed=FAIL-CLOSED
fixture-sha256=0a03e8e8c989e280c3db0f7c55893d063801634bb8509e8f3a285f445ada41da
chapter-13-structure-oracle=PASS
```

`out-of-order=status-0` 是刻意保存的能力邊界，不是綠燈。若流程只跑 status 就進研究，
等於容許 canonical sort 掩蓋來源列序；本章要求第二道 feed 閘門，因此總決定仍是
fail closed。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| 乾淨案例 status 退出 0，feed 完整迭代 | 固定三列通過本章兩道結構閘門 | 可繼續下一個資料 gate |
| `gaps` 非空 | 固定窗口缺少預期網格 | `no-go`；查來源，不 forward-fill |
| `duplicates` 非空 | 至少一個時點不唯一 | `no-go`；查 provenance，不任選一列 |
| `ohlc_violations` 非空 | 至少一列違反數值或包絡契約 | `no-go`；重取或依明示規則另版修復 |
| status 退出 0，但 `KlineFeed` 拒絕 | 集合式檢查乾淨，原始列序不可信 | `no-go`；不能先排序再冒充原始通過 |
| helper checksum 不符 | 本次輸入不是本章固定案例 | 停止；先定位版本或檔案差異 |

結構通過仍不代表資料已經 readiness。第 14 章還要核對歷史當下的可交易標的與規則，
第 15 章才把版本、manifest 與所有 gate 彙整成 readiness 報告。

## 常見陷阱

- 只看 `total_rows`，沒有檢查預期時間網格。
- 在 DataFrame 載入時先排序，讓原始亂序證據消失。
- 對重複列直接 `drop_duplicates(keep="last")`，卻沒有來源優先序與版本紀錄。
- 以 close forward-fill 缺口，讓策略在沒有市場證據的區間產生訊號或成交。
- 把 `high`／`low` 對調，或把異常值 clamp 回合法範圍，再宣稱原始資料通過。
- 看到 K 線 `order_violations=[]` 就宣稱原始列序已驗；本版 status 不提供這項證據。
- 把固定教學 JSON、暫存 Parquet 或 `53 passed` 冒充外部市場資料品質。
- 修復後覆蓋舊 dataset 與績效，沒有保存新版本和完整重跑差異。

## 對系統的回饋

每個 finding 至少保存：dataset／檔案身分、checksum、UTC 窗口、interval、原始列位置、
finding 類型、正式入口與退出碼、是否拒絕、候選來源原因，以及若要修復所需的新版本
規則。若 `status` 與 datasource 對同一品質名稱的語義不同，應把能力邊界寫進資料
契約或回報配套系統；不要在策略層悄悄補一個排序。

## 小結與練習

複製固定 JSON 到自己的實驗目錄，每次只改一件事：刪一列、複製一列、交換兩列、
再讓 `low > min(open, close)`。在執行前寫出預期入口、finding、退出碼與決定，執行後
比較 observed。最後設計一張 repair proposal，但不要修改原檔：列出來源依據、新
dataset 版本、checksum、完整重跑範圍及新舊結果不可互相覆蓋的理由。

你的專業成果是一張可審查的 bar structural-quality card：它能證明哪個固定輸入通過，
哪個 finding 使研究停止，以及修復為何必須成為另一個資料版本。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：`uv lock --check`；上述四組 unit tests；固定 JSON 的 `sha256sum`；`uv run python "$BOOK_DIR/manuscript/assets/ch13-bar-structure-oracle.py"`。
- 通過結果：配套 worktree 乾淨；`53 passed`；固定 JSON checksum 相符；乾淨三列 status 退出 0 且由 `KlineFeed` 完整產生 6 個事件；缺口、重複、非法 OHLC 各自退出 2；亂序的 status 綠燈被標為不足，原始列由 `KlineFeed` fail closed。
- 待處理差異：固定 JSON 與暫存 Parquet 不是外部市場資料；`v0.3.0` status 的列數與 SHA 來自 manifest，未驗證實檔 checksum；K 線 status 會 canonical sort，不能單獨證明原始列序；本章不執行自動修復、PIT universe、dataset manifest 或 readiness 報告。
