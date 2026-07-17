# 訂單意圖、生命週期與交易所回報

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-17

## 學習目標

完成本章後，你能：

1. 分開記錄策略意圖、提交結果、訂單狀態與成交事實；
2. 比較 `MARKET`、`LIMIT`、`GTC`、`GTX`／post-only 與 reduce-only 的不同責任；
3. 判讀 `NEW`、`PARTIALLY_FILLED`、`FILLED`、`CANCELED`、`REJECTED`、
   `EXPIRED` 的合法轉移與終態；
4. 用 order identity、事件時間、累計成交量與最後成交量判斷回報是否前進；
5. 保存一份可逐事件重播的訂單生命週期稽核表，遇到重複、倒退或非法轉移時
   fail closed。

## 問題情境：按下撤單，不等於已經撤掉

策略提出一張買入 `1.000` BTC 的限價單。交易所接受後，先成交 `0.400` BTC，
再成交 `0.300` BTC。此時你送出撤單請求；畫面也顯示「撤單已送出」。

這張單到底成交多少？剩下的 `0.300` BTC 是否仍可能成交？答案不能由按鈕文字決定。
撤單請求只是新的意圖；只有回報確認 `CANCELED`，才能把剩餘量視為不再有效。若撤單
確認之前又到達一筆 fill，必須依可核對的事件順序處理，不能把它刪掉來配合畫面。

本章把一張訂單拆成四層事實：

| 層次 | 例子 | 能證明什麼 | 不能證明什麼 |
|---|---|---|---|
| 策略意圖 | 買 `1.000`、`LIMIT 100.00 GTC` | 策略想做什麼 | 已提交、已接受或已成交 |
| 提交結果 | 本地 admission 拒絕，或 venue 接受並給 order ID | 是否進入後續生命週期 | 一定成交 |
| 訂單回報 | `NEW`、`PARTIALLY_FILLED`、`CANCELED` | venue／模擬器報告的訂單進度 | 帳已正確入完 |
| 會計事實 | canonical ledger 已套用相符事件 | 系統目前承認的唯一狀態 | 真實交易所沒有漏報 |

## 執行前預測

先寫下答案與理由：

1. Python 裡成功建立一個 `Order(status=NEW)`，是否證明交易所已接受？
2. `LIMIT` 是否必然是 maker？`MARKET` 是否必然整筆成交？
3. 第一筆回報可以直接是 `REJECTED` 嗎？
4. `PARTIALLY_FILLED → PARTIALLY_FILLED` 是重複事件，還是可能是第二筆成交？
5. 收到 `CANCELED` 後又收到 `FILLED`，應該覆蓋狀態、忽略，還是停止並調查？

## 核心概念一：type、time in force 與限制條件不是同一件事

`MARKET`／`LIMIT` 是訂單類型（order type）；`GTC`／`GTX` 是有效方式
（time in force）；reduce-only 是用途限制。三者回答不同問題。

| 意圖 | 最小語義 | 本章的證據邊界 |
|---|---|---|
| `MARKET` | 不帶委託價，要求依可用市場流動性執行 | 不保證價格，也不保證在所有模型中整筆成交 |
| `LIMIT` | 指定可接受的限價 | 價格限制不等於已排隊、maker 或會成交 |
| `GTC` | 未成交部分持續有效，直到成交或取消 | 仍可能部分成交，也可能長時間沒有成交 |
| `GTX`／post-only | 期望只在簿上提供流動性；若會立即吃單則不成立 | 本地 `v0.3.0` 以期貨 `GTX` 表達，不能推論之後一定成交 |
| reduce-only | 只允許減少期貨既有部位，不可把部位翻向 | 不是訂單類型，也不代表送出時一定仍有可減部位 |

`v0.3.0` 的 `Order` 模型只發布 `MARKET` 與 `LIMIT`；`post_only=True` 只允許期貨
限價單，並歸一為 `GTX`；reduce-only 也只允許期貨。這些是模型結構防線，不是一次
真實交易所接受。模型的 `status` 預設為 `NEW`，只表示物件的初始領域值；必須等到
帶 order ID 的 `OrderEvent` 成功套用，才有本地接受事件的證據。

因此稽核表至少分開保存 `requested_type`、`time_in_force`、`post_only`、
`reduce_only` 與 `submission_result`。不要用一個「maker 單」欄位把五件事混在一起。

## 核心概念二：生命週期是一張有向圖

配套 `v0.3.0` 發布的狀態機如下：

```text
NEW ──────────► PARTIALLY_FILLED ──────────► FILLED
 │                    ├──────► PARTIALLY_FILLED  (self-loop)
 │                    ├─────────────────────► CANCELED
 │                    ├─────────────────────► REJECTED
 │                    └─────────────────────► EXPIRED
 ├──────────────────────────────────────────► FILLED
 ├──────────────────────────────────────────► CANCELED
 ├──────────────────────────────────────────► REJECTED
 └──────────────────────────────────────────► EXPIRED
```

`FILLED`、`CANCELED`、`REJECTED`、`EXPIRED` 是終態，沒有合法出口。圖中共有十條
合法轉移；`NEW → NEW`、`CANCELED → FILLED` 與任何終態後轉移都不是「晚一點再修」，
而是必須停止的因果衝突。

有兩個容易忽略的邊界：

- 本地 canonical ledger 的第一個事件只允許 `NEW` 或 `REJECTED`。首次就是
  `REJECTED` 表示提交沒有成為 active order，不是 `NEW → REJECTED` 被漏記。
- `PARTIALLY_FILLED → PARTIALLY_FILLED` 合法，因為一張單可以分多次成交；只有
  cumulative filled quantity 前進，第二個相同狀態才是新事實。

## 核心概念三：狀態名稱相同，不代表來源語義完全相同

本章固定的 Binance 官方 Spot 文件把 `NEW` 說明為撮合引擎已接受，
`PARTIALLY_FILLED` 為部分已成交、`FILLED` 為完成、`CANCELED` 為使用者取消，
`REJECTED` 為未被引擎接受，`EXPIRED` 為依訂單規則或交易所原因失效；
`executionReport` 同時分開 current execution type 與 current order status。

配套本地回測會把已通過 admission 的訂單產成 `NEW`，也會把 admission 失敗產成首筆
`REJECTED`。此外，本地財務授權若在部分成交後拒絕剩餘量，狀態機允許
`PARTIALLY_FILLED → REJECTED`，但這不是在宣稱 Binance Spot 對同一情況使用完全相同
的理由。稽核記錄必須另存 `environment`、`source` 與 `reason`，不能只看到同名 status
就抹平交易所、本地模擬器與會計層的差異。

## 核心概念四：回報要用身分、時間與累計量一起核對

一筆 `OrderUpdate` 至少包含：

- `market`、`symbol`、`client_order_id`、`order_id`；
- `timestamp` 與 `status`；
- 累計 `filled_qty` 與本次 `last_fill_qty`；
- `last_fill_price`、`avg_price`、`fee`、`fee_asset`。

判讀時按下列順序：

1. **身分一致**：order ID 對回原始 market／symbol；不能把另一張單的 fill 接進來。
2. **時間不倒退**：較早回報不能直接覆蓋較晚 canonical state。
3. **累計量不倒退**：`filled_qty` 不可下降，也不可超過原始 order quantity。
4. **相同狀態要前進**：兩筆 partial fill 若 cumulative quantity 相同，是重複或衝突；
   若增加，才可能是新成交。
5. **轉移合法**：終態後不得再寫入另一狀態。

`v0.3.0` 的 `AccountingLedger.apply_order_event` 實作上述 canonical guard：完全相同的
事件或同狀態且累計量未前進會拋 `DuplicateAccountingEventError`；時間、身分或累計量
倒退會拋資料完整性錯誤；非法狀態轉移會拋 `InvalidOrderTransition`。失敗事件不應偷偷
改寫 canonical state。

`OrderUpdate.replayed` 欄位雖已凍結在 schema，設計明確標示 Phase 7 才啟用恢復補投遞
語義。本章不把這個欄位冒充已發布的 live 去重、斷線回補或 REST 對帳能力。

## 系統對照：誰能改變哪一層事實

| 元件／資料 | `v0.3.0` 已發布行為 | 本章如何使用 | 不代表什麼 |
|---|---|---|---|
| `Order` | Decimal guard、type／TIF／post-only／reduce-only 結構檢查 | 建立固定意圖 | 交易所已接受 |
| `OrderUpdate` | 固定回報 schema | 保存狀態、累計量、最後成交與費用 | 回報已入帳 |
| `validate_transition` | 十條合法轉移；非法轉移拋錯 | 核對狀態圖與固定 trace | 自動處理網路重播 |
| `FillSimulator` | accept 後產 `NEW`，成交產 partial／filled，撤單或失效產終態 | 聚焦測試的支撐證據 | 正式讀者下單入口或真實交易所 |
| `AccountingLedger` | 唯一 canonical order state；拒絕重複、倒退與身分不符 | 核對回報因果 | Phase 7／8 replay、Testnet 或 Live 已發布 |

本章 helper 只呼叫已發布模型與 `validate_transition`。canonical ledger 的完整 guard
由已發布聚焦測試驗證；helper 不複製第二套 ledger 或自行「修好」非法事件。

## 動手驗證一：固定版本與 canonical guard

從書籍 repository 根目錄保存路徑，再切到[實作準備](../front-matter/setup.md)建立的
隔離 worktree：

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
  tests/unit/test_models_orders.py \
  tests/unit/test_engine_accounting.py::TestCanonicalOrderState -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，status 應沒有輸出。
執行前 oracle 是：模型轉移矩陣、終態不變量、canonical 身分、重複、倒退、部分成交
與撤單路徑都通過。預期且實測：

```text
Python 3.12.3
............................................                             [100%]
44 passed in 0.27s
```

測試數量屬於固定 tag 的證據；未來測試增減時要重驗，不能只把數字改成新的預期值。

## 動手驗證二：建立一條可核對的事件時間線

仍在配套 `v0.3.0` worktree 執行隨書 helper：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch17-order-lifecycle-oracle.py"
```

預期且實測：

```text
intents=LIMIT/GTC,LIMIT/GTX-post-only,MARKET/reduce-only,decimal-source=string
intent-model-status=NEW,exchange-ack=false
spot-post-only-model=FAIL-CLOSED
legal-transition-count=10,terminal-exits=0
trace=NEW>PARTIALLY_FILLED>PARTIALLY_FILLED>CANCELED,cumulative=0>0.400>0.700>0.700
same-status-partial=LEGAL-ONLY-WITH-CUMULATIVE-PROGRESS
direct-fill=NEW>FILLED,PASS
duplicate-new=FAIL-CLOSED,InvalidOrderTransition
terminal-late-fill=FAIL-CLOSED,InvalidOrderTransition
initial-rejected=canonical-first-event-not-a-transition
chapter-17-order-lifecycle-oracle=PASS
```

固定 trace 的剩餘量為 `1.000 - 0.700 = 0.300` BTC；`CANCELED` 只終止剩餘量，
不能抹掉前兩筆已成交的 `0.700` BTC。這一章還不做資金釋放與逐筆會計；那是第 19 章
唯一帳本練習的範圍。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| 意圖已建立，但沒有提交結果 | 只知道策略想送什麼 | 不標 `accepted`，不預記 fill |
| 首筆 canonical event 是 `NEW` | 本地／venue 接受了這個 order ID | 等待後續回報，不預記成交 |
| 首筆是 `REJECTED` | 這次提交沒有成為 active order | 保存原始意圖與 reason；不得改成新單沿用同 ID |
| partial 狀態相同且 cumulative 增加 | 有新的部分成交進度 | 記本次與累計量，重算 remaining |
| event 完全相同，或狀態相同但 cumulative 未前進 | 重複或因果衝突 | fail closed；不得二次入帳 |
| timestamp 或 cumulative 倒退、身分不符 | 事件序或路由證據不可信 | 停止並對帳，不以最後到達者覆蓋 |
| 到達 `FILLED`／`CANCELED`／`REJECTED`／`EXPIRED` | 訂單進入終態 | 終止剩餘量；保留既有成交事實 |
| 終態後又收到不同狀態 | 本地證據互相矛盾 | fail closed；不能靜默忽略或覆蓋 |

## 常見陷阱

- 把策略建立 `Order` 物件，當成交易所已接受。
- 把提交 API 回傳成功，當成 `FILLED`。
- 把 `LIMIT`、post-only 與 maker fill 當成同義詞。
- 把撤單 request、撤單 response 與 `CANCELED` 回報合成一個時間點。
- 只存 `last_fill_qty`，重播時無法核對 cumulative quantity 是否前進。
- 只看 status 去重，因而丟掉合法的第二筆 partial fill。
- 把完全相同的 fill 再入帳一次，或讓較舊回報覆蓋較新狀態。
- 收到終態後刪除整條歷史，連已成交數量與 fee 都一起消失。
- 把本地 `REJECTED` reason 冒充真實交易所錯誤碼。
- 看到 schema 有 `replayed`，就宣稱 live 斷線回補已發布。

## 對系統的回饋

每張訂單至少保存以下 append-only 稽核資料：

| 欄位群 | 最小內容 |
|---|---|
| 意圖 | strategy decision ID、type、TIF、side、Decimal price／qty、post-only、reduce-only |
| 提交 | environment、source、client order ID、venue order ID、request time、結果與 reason |
| 回報 | event time、arrival order、status、cumulative／last fill、price、fee 與 fee asset |
| canonical 決定 | 前狀態、後狀態、接受／拒絕、錯誤類型與當時配套版本 |

若外部回報可能重送，connector 需要可驗證的去重身分與對帳策略；但在該能力正式發布
以前，本章的決定是 fail closed 並保存衝突，不在書稿發明一個「看到相同 status 就忽略」
的捷徑。

## 小結與練習

把下列事件抄到自己的稽核表，先判斷，再用 helper 與狀態圖核對：

| arrival | status | cumulative filled | last fill | 你的決定 |
|---:|---|---:|---:|---|
| 1 | `NEW` | `0` | `0` | ？ |
| 2 | `PARTIALLY_FILLED` | `0.400` | `0.400` | ？ |
| 3 | `PARTIALLY_FILLED` | `0.400` | `0.400` | ？ |
| 4 | `PARTIALLY_FILLED` | `0.700` | `0.300` | ？ |
| 5 | `CANCELED` | `0.700` | `0` | ？ |
| 6 | `FILLED` | `1.000` | `0.300` | ？ |

第 3 列在固定假設下沒有前進，應作重複／衝突處理；第 4 列是合法的第二筆部分成交；
第 6 列發生在終態之後，不能直接覆蓋。實務上還要使用來源事件 ID、venue 查詢與對帳
證據決定如何恢復，本章不虛構尚未發布的 live 流程。

你的專業成果是一份「訂單生命週期稽核表」：審核者能從原始意圖開始，逐列重現
accepted／rejected／partial fill／cancel／filled 決定，並看見每個 fail-closed 缺口。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：`uv lock --check`；`uv sync --locked --dev`；訂單模型與 canonical order state 聚焦測試；`uv run python "$BOOK_DIR/manuscript/assets/ch17-order-lifecycle-oracle.py"`；Binance 官方 Spot API 固定文件的 status 與 `executionReport` 欄位核對。
- 通過結果：配套 tag、HEAD 與乾淨 worktree 相符；聚焦測試 `44 passed`；十條合法轉移與四個終態符合已發布矩陣；固定 partial／cancel trace 通過；重複 NEW 與終態後 fill 均 fail closed；最終輸出 `chapter-17-order-lifecycle-oracle=PASS`。
- 待處理差異：本章只用已發布內部模型、狀態機與 canonical ledger 測試建立可重現證據；沒有正式讀者下單 CLI，不呼叫私人端點，也不宣稱 Phase 7 replay、Testnet／Live user stream、斷線回補或真實交易所對帳已發布。固定官方文件只支持欄位與狀態語義，發布前仍須重新查證。
