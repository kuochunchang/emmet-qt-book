# 四條 Foundation 資料管線與 `quant-data`

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-16

## 學習目標

完成本章後，你能：

1. 說明期貨 K 線、標記價格 K 線、資金費率與現貨 K 線各自回答什麼問題；
2. 區分 archive、REST 增量、固定離線樣本與外部資料模式的證據責任；
3. 使用正式的 `quant-data refresh`、`snapshot`、`status` 入口，並解讀退出碼；
4. 用版本、涵蓋範圍、列數、校驗結果與 SHA-256 判斷資料是否可進入研究；
5. 在網路、地區資格或資料品質不足時停止，不把替身或部分結果冒充外部下載成功。

## 問題情境：同一個研究問題需要四種資料

第 10 章把研究問題改寫為：「BTCUSDT 一小時 K 線收盤資料可見後，在第一個符合成交模型的機會決定是否進場。」現在要為 `2024-01-01` 到 `2024-01-03` 的半開區間準備資料。

只下載一張「價格表」不夠。訊號可能使用期貨 K 線；風險與強平邊界需要標記價格；永續持倉的現金流需要資金費率；期現比較則需要現貨 K 線。若四者的來源、時間範圍或驗證狀態沒有分開記錄，後續就無法知道差異來自策略，還是來自資料缺口。

本章的目標不是得到一條績效曲線，而是形成一份可審查的資料取得紀錄：別人能從命令、版本、涵蓋範圍、checksum 與退出碼判斷你拿到了什麼、沒拿到什麼。

## 執行前預測

先寫下你的判斷：

1. `um/klines` 與 `um/mark_price` 都有 OHLC，能否互相替代？
2. `funding` 沒有固定 K 線 interval，命令仍帶 `--interval 1h` 會發生什麼？
3. 固定替身測試全部通過，能否宣稱 data.binance.vision 今天可以下載？
4. `status` 回傳 `ok=false` 或命令退出碼 2 時，應該繼續回測還是先處理 finding？
5. 外部端點因網路或地區資格不可用時，能否改用空資料集並標成成功？

## 核心概念：四條管線不是四個別名

| 管線 | CLI 組合 | 最低用途 | 不能替代 |
|---|---|---|---|
| 期貨 K 線 | `--market um --kinds klines --interval 1h` | 永續合約訊號、成交假設的市場 bar | 標記價格、資金費現金流、現貨價格 |
| 標記價格 K 線 | `--market um --kinds mark_price --interval 1h` | 風險、未實現損益與強平邊界的價格軌跡 | 有成交量的市場 K 線或資金費率 |
| 資金費率 | `--market um --kinds funding` | 結算時點、費率與可選的標記價格 | 固定 interval 的 K 線；此管線不接受 `--interval` |
| 現貨 K 線 | `--market spot --kinds klines --interval 1h` | 現貨腿、期現基差與現貨成交假設 | 永續標記價格或資金費率 |

`um` 是資料目錄與 CLI 的市場段；配套模型中的市場值是 `futures`。不要把兩個字串直接混用。四條管線都寫入 Parquet 分區並由 manifest 保存檔名、列數與 SHA-256，但它們有不同 schema 與時間語義。

## 四種資料模式的責任邊界

| 模式 | 來源與用途 | 可以證明 | 不能證明 |
|---|---|---|---|
| Archive | data.binance.vision 的已發布月／日 zip 與 `.CHECKSUM` | 下載檔和官方 checksum 相符，適合歷史回補 | REST 當下可用、未發布月份已完整 |
| REST 增量 | Binance 公開 REST，經限頻器補 archive 未涵蓋的完整 UTC 日 | 指定窗口的增量回補結果 | 不受地區資格、限頻或服務中斷影響 |
| 固定離線 | 測試注入的固定來源，或本章建立的固定本地 Parquet | schema、路由、驗證、落盤與退出碼契約可重現 | 曾連到交易所、下載內容是真實市場資料 |
| 外部資料 | 真正執行 `refresh`／`snapshot` 的 Vision 或 REST 呼叫 | 只在該時間、地點與版本下觀察到的外部結果 | 未來仍可用；一次成功也不能取代 checksum 與 `status` |

配套管線對 K 線與標記價格採用「已終結完整月優先 monthly archive，404 再降為 daily，daily 404 才走 REST」；資金費率沒有 daily archive，monthly 404 或尾端完整日會走 REST。404 是降級訊號，不是「這段期間確定沒有資料」。若降級末端仍失敗，整次 `refresh` 應退出 1。

固定離線模式刻意不觸網，適合 CI 與教材重現；它必須明寫「替身」或「固定樣本」。外部模式才可以支持「本次真的下載」的宣稱，兩者不得互換名字。

## 系統對照：三個正式入口與三種退出碼

`v0.3.0` 的正式 console entry point 是 `quant-data`，共有三個子命令：

| 入口 | 責任 | 是否觸網 |
|---|---|---|
| `quant-data refresh` | 規劃 archive／REST 覆蓋，刷新一或多條管線，落盤後驗證 | 是；正式組合根使用 Vision 與 REST |
| `quant-data snapshot` | 採集 `exchangeInfo`，保存原始規則快照並維護生命週期；無 API key 時槓桿檔位明確降級 | 是 |
| `quant-data status` | 只讀既有分區與 manifest，重算涵蓋、缺口、列數及 checksum | 否 |

三者共同使用以下退出碼：

| 退出碼 | 意義 | 決定 |
|---|---|---|
| 0 | 命令完成，且本次校驗乾淨 | 保存 JSON 與版本證據，再進下一步 |
| 2 | 命令完成，但有缺口、重複、亂序、非法 OHLC 或間隔異常 | `no-go`；先處理 finding，不進研究 |
| 1 | 參數、網路、交易所、checksum 或其他運行錯誤 | `no-go`；保留 stderr 與外部條件，不標成功 |

`status` 對空分區會回傳 `ok=true` 與 `total_rows=0`；這只表示「空集合沒有內部違規」，不表示你的研究窗口已備妥。因此資料取得紀錄還必須比對預期窗口與最低列數，不能只看 `ok`。

## 動手驗證一：固定版本與正式入口

工作目錄是 setup 建立的隔離配套 worktree，不是正在開發的 `../emmet-qt-bt1`：

```bash
export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv sync --locked --dev
uv run python --version
uv run quant-data --help
```

本章基線的 HEAD 必須是：

```text
c999965e5cc923281541409cda9502beb93b8a60
```

`status --short` 應沒有輸出，Python 應為 `3.12.*`，help 應列出 `refresh`、`snapshot`、`status`。任一項不符就停止；不要用另一個 commit 的輸出填入本章紀錄。

## 動手驗證二：固定離線路徑不是外部下載

先執行配套版本內的固定離線測試。這些測試注入固定來源，不觸碰 Binance；它們驗證四種 `PipelineKind`、archive／REST 規劃、CLI 退出碼、Parquet 到模型的契約，以及 `snapshot` 到規則／生命週期的消費路徑。

```bash
uv run pytest \
  tests/unit/test_data_pipelines.py \
  tests/unit/test_cli_data.py \
  tests/integration/test_it2_pipeline_models.py -q
```

本章實測結果是：

```text
34 passed in 0.44s
```

這個結果只能寫成「固定離線契約通過」，不能寫成「四類外部資料下載成功」。

接著建立兩天的固定期貨 K 線分區，讓正式 `status` 入口讀取。建檔程式使用配套的正式 schema 與 store；資料值是教學固定值，不是市場行情。

```bash
export FIXED_ROOT=/tmp/emmet-ch11-fixed-c999965
uv run python - <<'PY'
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from quant.common.models.enums import Market
from quant.data.layout import DataPaths
from quant.data.schema import KLINES_SCHEMA
from quant.data.store import write_partition_file

root = Path("/tmp/emmet-ch11-fixed-c999965")
directory = DataPaths(root).kline_dir(Market.FUTURES, "BTCUSDT", "1h")
directory.mkdir(parents=True, exist_ok=True)

def ms(day: int) -> int:
    return int(datetime(2024, 1, day, tzinfo=UTC).timestamp() * 1000)

def table(start: int) -> pa.Table:
    rows = 24
    return pa.table(
        {
            "open_time": pa.array(
                [start + i * 3_600_000 for i in range(rows)], pa.int64()
            ),
            "open": pa.array([1.0] * rows, pa.float64()),
            "high": pa.array([2.0] * rows, pa.float64()),
            "low": pa.array([0.5] * rows, pa.float64()),
            "close": pa.array([1.5] * rows, pa.float64()),
            "volume": pa.array([10.0] * rows, pa.float64()),
            "quote_volume": pa.array([20.0] * rows, pa.float64()),
        },
        schema=KLINES_SCHEMA,
    )

for name, start in (
    ("2024-01-01.parquet", ms(1)),
    ("2024-01-02.parquet", ms(2)),
):
    target = directory / name
    if not target.exists():
        write_partition_file(directory, name, table(start), 1_706_745_600_000)
PY

uv run quant-data status \
  --root "$FIXED_ROOT" \
  --market um --symbol BTCUSDT --kind klines --interval 1h
```

實測 JSON 的關鍵欄位是：

```json
{"kind":"futures_klines","symbol":"BTCUSDT","interval":"1h","coverage":[[1704067200000,1704240000000]],"total_rows":48,"ok":true,"gaps":[],"files":[{"filename":"2024-01-01.parquet","sha256":"c9f136da552673c1eee839faf8619aacf6f3736fc9dcaa27caf349dd25ffce39","rows":24},{"filename":"2024-01-02.parquet","sha256":"0edfea51a82c1092919c7b71032255df0c678af55adc1eaba9ed67b746684eca","rows":24}]}
```

此處不只看 `ok=true`：涵蓋右界必須是 `2024-01-03T00:00:00Z`，總列數必須是 48，兩個檔案各 24 列，checksum 也要逐檔保存。任何一項不同，都不能沿用本章的通過紀錄。

## 動手驗證三：寫出四條外部命令，但分開執行與歸因

以下命令會觸碰外部服務。先選一個新的資料根目錄，不要和固定離線樣本混用：

```bash
export EXTERNAL_ROOT=/tmp/emmet-ch11-external

uv run quant-data refresh --root "$EXTERNAL_ROOT" \
  --market um --symbol BTCUSDT --interval 1h \
  --kinds klines,mark_price \
  --start 2024-01-01 --end 2024-01-03

uv run quant-data refresh --root "$EXTERNAL_ROOT" \
  --market um --symbol BTCUSDT \
  --kinds funding \
  --start 2024-01-01 --end 2024-01-03

uv run quant-data refresh --root "$EXTERNAL_ROOT" \
  --market spot --symbol BTCUSDT --interval 1h \
  --kinds klines \
  --start 2024-01-01 --end 2024-01-03

uv run quant-data snapshot --root "$EXTERNAL_ROOT" --markets um,spot
```

不要預先抄一份成功輸出。只有你所在環境實際得到退出碼 0，才能把該次命令的 JSON、檔案 checksum、執行日期與網路模式記為外部成功。`snapshot` 的公開 `exchangeInfo` 不需 API key；期貨槓桿檔位是簽名端點，缺 key 時輸出會明確表示 `brackets_skipped=true`，不能把「已跳過」寫成「已取得」。

外部刷新後，逐條執行 `status`，不要只查期貨 K 線：

```bash
uv run quant-data status --root "$EXTERNAL_ROOT" \
  --market um --symbol BTCUSDT --kind klines --interval 1h
uv run quant-data status --root "$EXTERNAL_ROOT" \
  --market um --symbol BTCUSDT --kind mark_price --interval 1h
uv run quant-data status --root "$EXTERNAL_ROOT" \
  --market um --symbol BTCUSDT --kind funding
uv run quant-data status --root "$EXTERNAL_ROOT" \
  --market spot --symbol BTCUSDT --kind klines --interval 1h
```

四次結果都要對照原研究窗口、列數與檔案 checksum。資金費率不是一小時固定網格，不能拿 K 線的 48 列 oracle 套用；它要依實際結算時點與 `interval_anomalies` 判讀。

## 動手驗證四：外部不可用時必須 fail closed

本章用一個受控的本機拒絕連線代理，重現「外部網路不可用」。這不是 Binance 地區限制的證據，而是驗證 CLI 不會把連線失敗冒充成功：

```bash
env \
  HTTP_PROXY=http://127.0.0.1:9 \
  HTTPS_PROXY=http://127.0.0.1:9 \
  ALL_PROXY=http://127.0.0.1:9 \
  NO_PROXY= \
  uv run quant-data refresh \
    --root /tmp/emmet-ch11-network-block \
    --market um --symbol BTCUSDT --interval 1h --kinds klines \
    --start 2026-07-14 --end 2026-07-15
```

本章實測退出碼是 1，stderr 的核心訊息是：

```text
下載失敗（已重試 3 次）：https://data.binance.vision/.../BTCUSDT-1h-2026-07-14.zip
```

決定是 `no-go`：保留錯誤、日期、版本與網路條件；不要建立空 manifest 來補成功證據，也不要拿前節的固定替身結果說外部下載已完成。

配套發布證據另記錄：GitHub-hosted 美國 runner 曾因 Binance Futures HTTP 451 無法完成 REST live，因此 hosted job 改驗 Vision monthly archive，REST live 留給合規地區的 manual／self-hosted runner。這是發布時的外部證據，不代表你今天所在環境一定得到 451；你的紀錄必須保存自己的實際狀態。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| 固定離線測試 `34 passed` | 四條管線與 CLI 契約在固定輸入下可重現 | 可以檢查程式契約；不能宣稱外部下載成功 |
| 固定 `status` 為 48 列、兩檔 checksum 相符 | 該固定樣本涵蓋兩個完整 UTC 日且內部校驗乾淨 | 可以作教材／CI 證據；不能當市場資料 |
| 外部 `refresh` 退出 0，四次 `status` 符合研究窗口 | 本次外部取得與本地落盤在指定環境下通過 | 保存 JSON、版本、日期與 checksum，再進研究 |
| 任一命令退出 2 | 已有資料品質 finding | `no-go`；先處理缺口、重複、亂序、OHLC 或間隔異常 |
| 任一外部命令退出 1、HTTP 451 或 checksum 失敗 | 本次外部證據不足 | `no-go`；不要以 mock、舊檔或空結果替代 |
| `status ok=true` 但 `total_rows=0` | 空分區沒有內部違規 | 若研究預期非空，仍是 `no-go` |

## 常見陷阱

- 把標記價格 K 線當成有成交量的市場 K 線，或拿期貨 K 線估算強平邊界。
- 對 funding 命令加 `--interval`；正式 CLI 會以退出碼 1 拒絕。
- 看到 archive 404 就宣稱沒有資料；正確行為是依管線走 daily／REST 降級鏈。
- 把固定替身測試的綠燈貼到外部下載紀錄。
- 只保存目錄名稱，不保存 tag、commit、涵蓋範圍與逐檔 SHA-256。
- 只看 `ok`，沒有比對研究所需的非空窗口與最低列數。
- 在外部失敗後改用正在開發的 worktree 重跑，卻仍標成 `v0.3.0`。
- 把 `brackets_skipped=true` 解讀成完整規則快照；它明確表示簽名資料未取得。

## 對系統的回饋

每次資料取得都保存一張 evidence card：

```text
研究窗口（UTC、半開）：
tag@commit：
資料模式：archive / REST / fixed-offline / external
命令與退出碼：
四條管線的 coverage / total_rows / ok：
檔案 SHA-256：
snapshot 時點與 brackets_skipped：
外部條件與錯誤：
決定：continue / no-go
```

若你發現正式 CLI 在退出 1 後仍留下可被 `status` 誤判為完整的分區，或某條管線無法區分外部失敗與空資料，保存最小重現與檔案清單，回報配套系統；不要在書稿另寫一套下載器掩蓋問題。

## 小結與練習

四條管線共同服務一個研究問題，但不共享相同語義。Archive、REST、固定離線與外部模式也不是四個可互換的下載方法，而是四種不同強度的證據。

練習：以你自己的兩日窗口建立 evidence card。先跑固定離線 oracle，再選擇是否執行外部命令。逐條記錄四個 `status`；故意讓其中一條資料根目錄為空。若你的決定只看 `ok=true` 而沒有因預期窗口非空而 `no-go`，表示 readiness 判斷仍不完整。

你的專業成果是一份可重現的四管線資料取得與失敗歸因紀錄，不是一句「資料已下載」。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：
  - `git -C ../emmet-qt-bt1 rev-parse 'v0.3.0^{commit}'`
  - `git -C ../emmet-qt-bt1-v0.3.0 rev-parse HEAD`
  - `git -C ../emmet-qt-bt1-v0.3.0 status --short`
  - `uv lock --check && uv sync --locked --dev && uv run python --version`
  - `uv run quant-data --help` 與三個子命令的 `--help`
  - `uv run pytest tests/unit/test_data_pipelines.py tests/unit/test_cli_data.py tests/integration/test_it2_pipeline_models.py -q`
  - 章內固定兩日樣本建立命令與 `uv run quant-data status ...`
  - 固定替身的 spot `refresh`／`status` 與 snapshot CLI boundary oracle
  - 章內受控拒絕連線代理的 `quant-data refresh` fail-closed 命令
- 通過結果：tag 與 HEAD 相符、worktree 乾淨、locked sync 成功；正式入口 help 相符；固定離線測試 `34 passed`；兩日 `status` 為 48 列且 checksum 相符；spot 固定路由與 snapshot boundary 通過；受控外部失敗重試三次後退出 1。
- 待處理差異：本次沒有把固定替身冒充外部下載，也沒有宣稱重跑真實 archive／REST 綠燈；受控代理只證明網路失敗會 fail closed，不證明特定地區會得到 HTTP 451。配套基線以已發布 tag 固定，目前沒有可引用的 GitHub Release 物件。
