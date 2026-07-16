# 歷史當下的市場：PIT universe、生命週期與規則快照

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-16

## 學習目標

完成本章後，你能：

1. 依研究時點查詢 point-in-time universe，不以今天仍存在的標的回填歷史；
2. 用上架、下架、狀態與證據起點解釋一個標的在歷史當下是否可選；
3. 看懂 `survivorship_warning`，在生命週期證據未覆蓋研究時點時停止；
4. 把資料時點與規則快照的 `snapshot_ts` 配對，不用未來規則解釋舊資料；
5. 保存 PIT universe、生命週期來源、規則版本與 go／no-go 決定。

## 問題情境：今天的市場不是 2021 年的市場

延續前幾章的資料契約，但把研究時點固定為 `2021-06-15 00:00:00 UTC`。固定教學
案例有三個虛構標的：

- `BTCUSDT` 在研究前已上架，研究後仍存在；
- `OLDUSDT` 在研究時仍可交易，於 2022 年才下架；
- `NEWUSDT` 到 2023 年才上架。

若直接拿 `2024-01-01` 的存活清單回測 2021 年，會得到 `BTCUSDT,NEWUSDT`：已下架的
`OLDUSDT` 消失，當時尚不存在的 `NEWUSDT` 卻闖入。這不是單純少一列 metadata，而是
選樣規則偷看未來，形成倖存者偏差（survivorship bias）。

同一份 2021 資料也不能套用 2024 規則。最小下單量、tick size、交易狀態等都可能屬於
不同時代；即使 Python 能載入規則檔，時間不相容仍必須 no-go。

## 執行前預測

先寫下答案與理由：

1. 2021 年回測能否只從今天仍存在的標的開始選？
2. `OLDUSDT` 目前已下架，是否表示它不能出現在下架前的歷史 universe？
3. `NEWUSDT` 目前可交易，是否表示它能出現在上架前的 universe？
4. 一個標的的 `onboard_ts` 是 2019 年，但第一份完整市場快照到 2026 年才採集；查詢
   2021 年時，能否宣稱宇宙證據完整？
5. 2024 規則檔的 schema 與 symbol 都正確，能否用來解釋 2021 資料？

## 核心概念一：universe 也必須是 point-in-time 資料

Point-in-time（PIT）不是「資料帶日期」而已，而是每個決定只能使用當時已成立且有
證據支持的集合。`v0.3.0` 的存活判據是：

\\[
\mathrm{onboard\_ts} \le \mathrm{as\_of}
\quad\land\quad
(\mathrm{delist\_ts}=\mathrm{None}
\quad\lor\quad
\mathrm{delist\_ts} > \mathrm{as\_of})
\\]

因此，目前狀態為 `DELISTED` 的標的，在 `delist_ts` 之前仍可出現在歷史 universe；
反過來，目前為 `TRADING` 的標的，在 `onboard_ts` 之前不能出現。`SETTLING` 在本版的
PIT 查詢中仍算存活，這是以小時尺度近似過渡窗口；需要更細決定時，還要讀取
`settle_ts`、`delist_announce_ts` 與狀態，不能只看集合成員資格。

## 核心概念二：有日期不等於證據鏈已覆蓋

`point_in_time_universe` 另外回傳：

- `earliest_evidence_ts`：該市場所有生命週期列的最早 `first_seen_ts`；
- `survivorship_warning`：表為空，或 `as_of < earliest_evidence_ts` 時為 `True`。

交易所目前清單可能告訴你一個倖存標的在 2019 年上架，卻不能證明 2021 年的完整市場
還有哪些後來已下架標的。因此 `onboard_ts` 不會偷偷把證據鏈起點往前移。只有來源與
涵蓋期間可稽核的人工回填，才能以 `source=manual` 和明確 `first_seen_ts` 延伸證據鏈。

本章把 `survivorship_warning=True` 視為 fail closed。產品型別只誠實回報警示，不會
替研究者決定風險容忍度；把警示升級為 no-go 是本章資料契約的明示政策。

## 核心概念三：規則快照也是因果輸入

規則檔中的 `snapshot_ts` 是版本化引用，不是裝飾欄位。本章研究契約固定選用
`1622505600000`（`2021-06-01 00:00:00 UTC`）的教學規則快照，並要求：

1. PIT universe 中每個標的都有可載入、身分相符的規則；
2. 所有規則檔的 `snapshot_ts` 等於契約指定版本；
3. 規則快照不得晚於研究 `as_of`；
4. 任一缺檔、schema／symbol 不符、版本混用或未來快照都停止。

`snapshot_ts <= as_of` 只排除明顯未來資訊，不能自行證明那是完整的歷史規則。選定哪個
快照屬於哪個規則時代，仍要由 dataset contract、原始 exchangeInfo 存檔與 checksum
支持；不能把「最近但較早」當成自動正確。

## 系統對照：採集、查詢與消費是三個邊界

| 邊界 | `v0.3.0` 正式能力 | 本章如何使用 | 不代表什麼 |
|---|---|---|---|
| 生命週期採集 | `quant-data snapshot`／`SnapshotCollector` 保存原始 exchangeInfo、登記 checksum、更新生命週期與最新規則 | 由已發布測試核對採集到 PIT／rules 消費路徑 | 不會自動回填啟用前已下架標的 |
| PIT 查詢 | `LifecycleStore` 與 `point_in_time_universe` | 匯入固定離線生命週期證據，查 2021 與 2024 集合 | 警示不會自動替研究流程停止 |
| 規則消費 | `RulesRepository.load_symbol_rules` | 載入兩組固定規則檔，讀取身分與 `snapshot_ts` | 本版沒有依研究時點自動選歷史正規化規則的入口 |

採集器會保留 gzip 原始快照與 snapshot 登記，但正規化的 symbol rule 路徑是最新快照
語義，後續採集會原子重寫。若研究需要舊規則，必須另外保存可追溯的歷史規則產物，
或從相符原始快照以受驗證流程重建；不能從最新檔倒推歷史。

本章固定 JSON 是教學資料，標的名稱與數值不主張來自 Binance。helper 只把固定生命
週期列交給正式 store／PIT API，把固定規則 JSON 交給正式 repository，再執行章內
因果 gate；它不下載市場資料，也不另寫一套交易所生命週期判定。

## 動手驗證一：固定版本、lockfile 與已發布契約

從書籍 repository 根目錄保存路徑，再切到 setup 建立的配套 worktree：

```bash
export BOOK_DIR="$(pwd)"
export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv sync --locked --dev
uv run python --version
uv run pytest \
  tests/unit/test_data_universe.py \
  tests/unit/test_data_lifecycle.py \
  tests/integration/test_it2_pipeline_models.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，Git status 應沒有
輸出。執行前 oracle 是：生命週期狀態變更、PIT 存活判據、證據鏈警示、規則正規化
消費與 IT-2 串接全數通過。本章實測為：

```text
Python 3.12.3
52 passed in 0.78s
```

## 動手驗證二：鎖定固定輸入

固定案例是
[ch14-pit-rules-cases.json](../assets/ch14-pit-rules-cases.json)，執行 helper 是
[ch14-pit-rules-oracle.py](../assets/ch14-pit-rules-oracle.py)。先核對輸入身分：

```bash
(cd "$BOOK_DIR" && sha256sum manuscript/assets/ch14-pit-rules-cases.json)
```

預期且實測：

```text
271c20be5d4f4455dfbf6ab8b783d28d3aec17eef8ae4edd1ad4848f1b270401  manuscript/assets/ch14-pit-rules-cases.json
```

checksum 不同就停止。這個 checksum 只鎖定隨書教學輸入，不是交易所下載、dataset
manifest 或外部真實性證明。

## 動手驗證三：PIT 通過案例與三個 fail-closed 邊界

仍在配套 `v0.3.0` worktree 執行：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch14-pit-rules-oracle.py"
```

執行前 oracle 是：2021 universe 為 `BTCUSDT,OLDUSDT` 且證據鏈涵蓋；2024 universe
為 `BTCUSDT,NEWUSDT`，不能回填 2021；僅有 2026 首見快照的 2021 查詢必須警示；
2021 契約規則通過，2024 未來規則必須拒絕。預期且實測輸出：

```text
pit-2021=BTCUSDT,OLDUSDT,warning-false,PASS
current-2024=BTCUSDT,NEWUSDT
current-survivors-for-2021=MISMATCH,FAIL-CLOSED
pre-evidence-universe=warning-true,FAIL-CLOSED
rules-2021=snapshot-1622505600000,PASS
future-rules-for-2021=snapshot-1704067200000,FAIL-CLOSED
fixture-sha256=271c20be5d4f4455dfbf6ab8b783d28d3aec17eef8ae4edd1ad4848f1b270401
chapter-14-pit-rules-oracle=PASS
```

最終 `PASS` 表示 helper 正確辨識通過與拒絕案例，不表示三個 fail-closed 輸入可進入
研究。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| PIT 集合符合歷史生命週期，warning 為 false，規則版本相符 | 固定教學輸入通過本章 PIT／規則時代 gate | 可進入下一個資料 gate |
| 目前 universe 與歷史 PIT 集合不同 | 使用今天清單會移除已下架標的並加入尚未上架標的 | no-go；改用歷史生命週期證據 |
| `survivorship_warning=True` | 證據鏈沒有覆蓋研究時點 | no-go；補可稽核歷史資料或縮短窗口 |
| 規則缺檔、身分不符或 snapshot 混用 | 不能為所有候選重建一致的規則時代 | no-go；修復歷史規則產物 |
| `snapshot_ts > as_of` | 規則來自研究決定之後 | fail closed；不得以未來規則解釋舊資料 |
| fixture checksum 不符 | 本次輸入不是章內固定案例 | 停止；先定位版本或檔案差異 |

通過這一關仍不等於 dataset readiness。第 15 章才會把來源、coverage、結構 findings、
checksum、PIT 判定與規則時代彙整成可交接的 manifest／readiness 報告。

## 常見陷阱

- 先從今天的熱門或存活標的挑選，再回頭下載歷史資料。
- 看到 `status=DELISTED` 就從所有歷史時點刪除該標的。
- 只檢查 `onboard_ts`，沒有檢查 `delist_ts` 與證據鏈起點。
- 把當前 exchangeInfo 中的早年 `onboardDate` 當成當年完整 universe 的證據。
- 忽略 `survivorship_warning`，仍把結果標成完整 PIT 研究。
- 只要規則 schema 可載入就接受，沒有比較 `snapshot_ts` 與研究契約。
- 把最新正規化規則檔誤認為自動保留了所有歷史版本。
- 把固定虛構標的、manual row 或 `52 passed` 冒充外部市場歷史證據。

## 對系統的回饋

每次 PIT 決定至少保存：market、research `as_of`、symbol 集合、每列 onboard／settle／
delist 時點、`source`、`first_seen_ts`、`earliest_evidence_ts`、warning、規則檔身分與
`snapshot_ts`、原始快照／fixture checksum，以及 continue／no-go 理由。若歷史規則
只能從原始快照重建，重建工具、schema 與輸出 checksum 也要成為新的驗證紀錄，不能
覆蓋最新規則檔後聲稱一直如此。

## 小結與練習

複製固定 JSON 到自己的實驗目錄，每次只改一項：把 `OLDUSDT` 的下架時間移到研究
日前、把 `NEWUSDT` 的上架時間移到研究日前、把 `first_seen_ts` 移到研究日後、再把
一個規則 `snapshot_ts` 改成 2024。執行前先寫出預期集合、warning、規則 gate 與
go／no-go，執行後比較 observed；不要把修改後檔案冒充本章 checksum。

你的專業成果是一張 PIT universe／rules-era card：它能回答「歷史當下有哪些標的、
證據從何時開始、使用哪個規則時代、什麼不一致使研究停止」。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：`uv lock --check`；`uv sync --locked --dev`；上述 lifecycle／universe／IT-2 測試；固定 JSON 的 `sha256sum`；`uv run python "$BOOK_DIR/manuscript/assets/ch14-pit-rules-oracle.py"`。
- 通過結果：配套 tag 與 HEAD 相符且 worktree 乾淨；`52 passed`；固定 JSON checksum 相符；2021 PIT universe 含當時仍存活、後來下架的 `OLDUSDT`，排除尚未上架的 `NEWUSDT`；目前清單回填、證據不足與未來規則三種案例均 fail closed。
- 待處理差異：固定 JSON 與虛構標的是離線教學證據，不是 Binance 歷史資料；本版 PIT warning 由消費者決定是否停止；`SETTLING` 仍計入存活；正規化規則路徑只有最新快照語義，本章不宣稱已有歷史規則自動選擇器、dataset manifest 或 readiness 報告。
