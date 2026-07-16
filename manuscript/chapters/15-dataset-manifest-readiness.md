# 校驗和、版本與資料 readiness 報告

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-16

## 學習目標

完成本章後，你能：

1. 用 dataset manifest 固定資料來源、模式、期間、schema、規則版本、檔案與生成條件；
2. 區分「產品品質檢查為綠燈」與「資料符合這次研究契約」；
3. 逐檔比對契約 checksum、產品 manifest 與磁碟實檔；
4. 讀取人與機器都能核對的 readiness report，作出 go／no-go 決定；
5. 在 checksum、版本、涵蓋範圍或必要資料不一致時 fail closed，並知道修復後要重建哪些證據。

## 問題情境：`status` 是綠燈，為什麼仍不能開始研究

前五章已把研究問題、四條管線、時間可見性、結構品質與 PIT 規則時代分開檢查。
現在要把資料交給另一位研究者。只傳一個目錄不夠：對方無法知道它是不是本次契約
要求的市場、期間與版本，也不知道檔案是否在交接途中改變。

更危險的是把單一綠燈當成完整 readiness。`v0.3.0` 的 `quant-data status` 能報告一個
分區的 coverage、列數、品質 findings 與 manifest 中的逐檔 SHA-256；但空分區也可能
沒有結構 finding。若研究契約要求 2024-01-01 到 2024-01-03 的 48 根一小時 K 線，
「現有資料本身沒有缺口」仍不能回答「必要的兩天是否都存在」。

本章建立最後一道明示 gate：產品狀態是輸入，研究契約才決定是否可繼續。

## 執行前預測

先寫下答案與理由：

1. `quant-data status` 回傳 `ok=true`，但 coverage 比研究窗口短一天，可以開始嗎？
2. 檔名與列數相同，契約 checksum 與實檔不同，可以把它當成同一資料集嗎？
3. 資料檔未改，但 schema version 或規則快照版本改了，舊報告仍有效嗎？
4. 固定離線樣本通過，能否宣稱外部下載或 Binance 當下資料可用？
5. 修復缺檔後，只重跑策略、不重建 manifest 與 readiness report，證據鏈完整嗎？

## 核心概念一：manifest 是資料集身分證，不是資料本身

Dataset manifest 是一份版本化引用。它至少回答：

| 欄位 | 本章固定案例 | 要防止的歧義 |
|---|---|---|
| `dataset_id` | `ch15-fixed-um-btcusdt-1h-2024-01-01-02` | 不同輸入共用模糊名稱 |
| `source` | `emmet-qt-book`／`fixed-offline`／未觸網 | 把教學樣本冒充外部下載 |
| `partition` | `um`、`BTCUSDT`、`klines`、`1h` | 市場、標的、資料種類或 interval 混用 |
| `coverage` | `[1704067200000, 1704240000000)` | 「有資料」被誤讀成研究窗口完整 |
| `data_schema` | `quant.data.schema.KLINES_SCHEMA` version 1 | 相同檔名承載不同欄位契約 |
| `rules_snapshot` | `teaching-rules-v1@1704067200000` + checksum | 用另一個規則時代解釋資料 |
| `files` | 逐檔檔名、列數與 SHA-256 | 檔案被替換或交接錯版 |
| `generation` | helper、兩個 UTC 日、步長、固定 OHLCV、refresh 時點 | 無法重建輸入 |
| `contract.sha256` | 固定契約檔的 SHA-256 | 研究要求本身悄悄改變 |

配套分區中的 `_manifest.json` 是由 Parquet 重建得出的派生 metadata；本章輸出的
`dataset-manifest.json` 則是研究交接層的版本化引用。兩者責任不同，不能因名稱相似
就把它們當成同一層。

## 核心概念二：readiness 是一組 AND gate

本章把資料可用性寫成明示合取條件：

\[
R = Q \land C \land N \land F \land S \land V \land M
\]

其中：

- \(Q\)：產品 `status` 沒有已知結構 finding；
- \(C\)：observed coverage 精確符合研究契約；
- \(N\)：必要列數完整；
- \(F\)：檔案集合與逐檔 checksum 同時符合；
- \(S\)：資料 schema 受支援；
- \(V\)：規則 snapshot 版本與 payload checksum 相符；
- \(M\)：來源模式是契約指定的固定離線模式。

任何一項為 false，決定就是 `NO-GO`。不能用「其餘八項都過」平均掉一項失敗，也不能
把 warning 藏在長篇日誌中仍輸出成功。

## 核心概念三：checksum 必須連回磁碟實檔

`v0.3.0` 的 `quant-data status` 會讀產品 manifest 中的逐檔 SHA-256；它不會在每次
status 查詢時重新雜湊實檔。因此本章 helper 做三方比對：

1. 契約中的 expected checksum；
2. `quant-data status` 回傳的產品 manifest checksum；
3. helper 對磁碟 Parquet 重新計算的 SHA-256。

三者相等才通過。這道獨立雜湊是交接證據，不是第二套 K 線品質校驗器；缺口、重複、
OHLC 等 finding 仍由正式 `quant-data status` 負責。

## 系統對照：正式入口與章內 gate 的責任

| 邊界 | `v0.3.0` 正式能力 | 本章如何使用 | 不代表什麼 |
|---|---|---|---|
| 分區寫入 | `write_partition_file` 原子寫 Parquet 並更新產品 manifest | 只用來建立固定離線教學分區 | 不是讀者外部下載入口 |
| 分區狀態 | `quant-data status`／`get_data_status` 回傳 coverage、findings、列數與檔案清單 | 作為 readiness 的產品證據輸入 | `ok=true` 不自動等於研究窗口完整 |
| Dataset manifest | 本章 helper 彙整契約、status 與規則版本 | 輸出可交接的 `dataset-manifest.json` | `v0.3.0` 沒有同名產品 CLI |
| Readiness report | 本章明示 AND gate | 輸出 `GO` 或列出 `NO-GO` failures | 不宣稱已發布研究引擎或 MCP readiness 入口 |

Helper 只編排已發布入口、重算交接 checksum 並套用章內研究契約。它沒有下載資料、
沒有修補 finding，也沒有把未發布的 Phase 5 報告能力寫成可操作產品入口。

## 動手驗證一：固定版本、lockfile 與已發布邊界

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
  tests/unit/test_data_store.py \
  tests/unit/test_data_status.py \
  tests/unit/test_cli_data.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，Git status 應沒有輸出。
執行前 oracle 是：manifest 寫入／重建、只讀 status 與 CLI JSON／退出碼測試全數通過。
本章實測為：

```text
Python 3.12.3
31 passed in 1.36s
```

## 動手驗證二：先固定研究契約

固定契約是
[ch15-dataset-contract.json](../assets/ch15-dataset-contract.json)，執行 helper 是
[ch15-readiness-oracle.py](../assets/ch15-readiness-oracle.py)。先核對契約身分：

```bash
(cd "$BOOK_DIR" && sha256sum manuscript/assets/ch15-dataset-contract.json)
```

預期且實測：

```text
8ca6240326c0dd9005e7bee21523ffc92db8e164198212c62a370436ad9bbb55  manuscript/assets/ch15-dataset-contract.json
```

契約固定兩個 UTC 日、每小時一列、共 48 列；資料來源明示為不觸網的教學生成器。
其中規則 payload 由字串值組成，canonical JSON checksum 為
`08c542035e836d95c24989704a76ea4a2bfbe4e93ba625e412ca3dc87b80e3f0`。
契約 checksum 不同就停止，不能先執行再用新 checksum 覆寫預期。

## 動手驗證三：產生 manifest 與人機共讀報告

仍在配套 `v0.3.0` worktree 執行：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch15-readiness-oracle.py" \
  --output-dir /tmp/emmet-ch15-evidence
```

Helper 在暫存資料根建立兩個固定 Parquet，呼叫正式 `quant-data status`，再把輸出與研究
契約、規則版本及磁碟 checksum 對照。預期且實測摘要：

```text
dataset-id=ch15-fixed-um-btcusdt-1h-2024-01-01-02
source-mode=fixed-offline,network-false
coverage=1704067200000..1704240000000,rows-48
files=2024-01-01.parquet:5ace1f4d0adc6936c55b1e4567f17c897688f1b9539a0d15bf795edc1ce6b4cf,2024-01-02.parquet:baf2f84450b08ae358b10b5aa036b045af6e02d84d80334d9d43a5ee236e9dad
contract-sha256=8ca6240326c0dd9005e7bee21523ffc92db8e164198212c62a370436ad9bbb55
readiness-matching-manifest=GO
readiness-checksum-mismatch=NO-GO,status_checksums_match_contract,disk_checksums_match_contract
readiness-required-day-missing=NO-GO,coverage_exact,required_rows_present
evidence-dir=/tmp/emmet-ch15-evidence
chapter-15-readiness-oracle=PASS
```

`/tmp/emmet-ch15-evidence` 會得到：

- `dataset-manifest.json`：資料集身分、來源模式、coverage、schema／rules 版本、逐檔 checksum
  與生成條件；
- `readiness-pass.json`：所有 checks 為 true，`decision=GO`；
- `readiness-checksum-mismatch.json`：契約 checksum 不符，列出兩個 checksum failure；
- `readiness-required-day-missing.json`：必要窗口多一天時，列出 coverage 與列數 failure。

輸出是 JSON，機器可檢查 `decision` 與 `failures`；人也能追到每一項布林判斷。最後的
`PASS` 表示 helper 正確辨識 go 與兩個 no-go 案例，不表示 no-go 資料可以開始研究。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| status 乾淨，coverage／列數完整，三方 checksum、schema、rules 與模式全相符 | 固定教學資料通過本章 readiness gate | `GO`；可把 manifest 與報告交給下一流程 |
| status manifest 與磁碟檔相符，但契約 checksum 不同 | 現有資料不是契約指定版本 | `NO-GO`；找回正確資料或明示建立新契約 |
| status `ok=true`，但研究契約要求多一天 | 現有分區本身無結構 finding，但必要資料不足 | `NO-GO`；補齊資料後重建全部證據 |
| schema／rules version 或 payload checksum 不符 | 不能用契約指定語義解讀資料 | `NO-GO`；選回正確版本或重新審核契約 |
| source mode 為固定離線 | 這次重現不依賴網路 | 不得宣稱外部下載、市場現況或地區可用性 |
| contract、檔案或生成器改變 | 舊 manifest 不再識別目前輸入 | 重建 manifest、readiness report 與下游結果 |

## 常見陷阱

- 只保存檔名，不保存 checksum、rows、coverage 與 schema／rules version。
- 看到 `status ok=true` 就忽略研究要求的窗口與最低資料量。
- 只比對產品 manifest，不重新雜湊交接時的磁碟實檔。
- checksum 不同時直接更新 expected 值，沒有先判斷資料為何改變。
- 把 manifest 當資料備份；manifest 能識別檔案，不能取代 Parquet 本身。
- 用牆鐘產生不固定的 `generated_at`，使相同輸入每次得到不同身分。
- 把固定離線 `GO` 冒充外部資料最新、完整或適合任何研究問題。
- 修復一個檔案後沿用舊 readiness report 或下游策略輸出。
- 將章內 `dataset-manifest.json`／readiness gate 誤寫成 `v0.3.0` 已發布 CLI。

## 對系統的回饋

每次資料交接至少保存：配套 `tag@commit`、dataset id、來源與模式、研究窗口、market／
symbol／kind／interval、schema 與 rules snapshot 身分、逐檔 rows／checksum、產品 status
原始輸出、契約 checksum、readiness checks／decision，以及生成與重驗命令。

若 failure 修復後改變任何資料檔、契約、schema 或規則版本，應產生新的 manifest 與
readiness report，再使所有下游結果引用新身分；不能原地覆寫後聲稱舊結果仍由相同輸入
產生。

## 小結與練習

複製固定契約到自己的實驗目錄，每次只改一項：把第二個檔案 checksum 改成 64 個零、
把 coverage 終點延後一天、把 data schema version 改成 2、再把 rules payload 的 tick
改掉但保留舊 checksum。執行前先寫出失敗 check 與 `GO`／`NO-GO`，執行後比較 JSON
report；不要把修改後契約冒充本章固定 checksum。

你的專業成果是一組 dataset handoff package：固定契約、dataset manifest、readiness
report 與重驗命令。接手者不必相信「資料應該沒問題」，可以自己核對資料是什麼、是否
完整、版本是否一致，以及哪一項不符就必須停止。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：`uv lock --check`；`uv sync --locked --dev`；上述 store／status／CLI 測試；固定契約的 `sha256sum`；`uv run python "$BOOK_DIR/manuscript/assets/ch15-readiness-oracle.py" --output-dir /tmp/emmet-ch15-evidence`。
- 通過結果：配套 tag 與 HEAD 相符且 worktree 乾淨；`31 passed`；固定契約 checksum 相符；兩日 48 列的產品 manifest、磁碟實檔與契約 checksum 一致，matching case 為 `GO`；checksum 不符與必要日缺失均列出精確 failure 並 `NO-GO`。
- 待處理差異：固定 OHLCV 與規則 payload 是離線教學輸入，不是 Binance 下載或市場事實；`v0.3.0` 的 `quant-data status` 不會每次重算實檔 checksum，章內 helper 才為交接重算；dataset manifest 與 readiness report 是章內研究契約產物，不宣稱配套已有同名產品 CLI、研究引擎或 MCP 入口。
