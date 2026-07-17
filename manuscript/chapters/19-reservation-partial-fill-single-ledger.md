# 資金預留、部分成交與唯一帳本

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-17

## 學習目標

完成本章後，你能：

1. 區分現貨 `free`／`locked` 與期貨 order reservation 的用途；
2. 逐筆核對部分成交如何同時縮減剩餘數量與預留金額；
3. 解釋取消、拒絕、成交完成或過期時，哪些剩餘資金必須釋放；
4. 用逐資產守恆與非負餘額檢查現貨帳本；
5. 說明 `FillSimulator` 為何只能提出成交決定，不能成為第二個可寫帳本。

## 問題情境：訂單只成交一半，另外一半的錢在哪裡

你有 `1000 USDT`，提交一張買入 `2 BTC @ 100 USDT` 的固定教學訂單。系統接受時，
`200 USDT` 從 `free` 移到 `locked`。第一筆只成交 `1 BTC @ 90 USDT`，買入手續費為
`0.01 BTC`，之後剩餘訂單取消。

只看最後餘額，很容易漏掉三個不同事實：

- 接受訂單時只是預留資金，不是已經花掉 `200 USDT`；
- 第一筆成交實際花掉 `90 USDT`，該筆分配到但未使用的 `10 USDT` 應立即回到
  `free`；
- 取消後，剩餘訂單鎖住的 `100 USDT` 也必須釋放，但已取得的 `0.99 BTC` 不能消失。

如果撮合器、本地訂單簿與會計服務各自保存一份「餘額」，結果就可能隨呼叫順序分岔。
本章因此不只算答案，也要核對誰有權改帳。

## 執行前預測

先寫下答案與理由：

1. 訂單剛被接受時，USDT 的 `free`、`locked` 與總額各是多少？
2. 第一筆成交後，剩餘 reservation 的 quantity 與 amount 各是多少？
3. 成交價比預留價好 `10 USDT`，這 `10 USDT` 應留在 `locked` 還是回到 `free`？
4. 取消剩餘量後，USDT 與 BTC 的逐資產總額應是多少？
5. `FillSimulator` 產生 `FillDecision` 時，帳本是否已經入帳？

## 核心概念一：預留是可用資金的 earmark，不是費用

預留（reservation）是在訂單仍可能成交時，把同一筆資金標成不可再使用。它防止兩張
訂單各自看見同一份 `free` 後同時超支。

現貨錢包按資產保存兩個欄位：

| 欄位 | 意義 | 訂單接受時 | 成交或終態時 |
|---|---|---|---|
| `free` | 可供新訂單使用 | 減少預留額 | 接收 price improvement、未用預留或賣出淨收入 |
| `locked` | 已為 active order 保留 | 增加預留額 | 按成交切片消耗，終態釋放剩餘額 |

對現貨買單，限價單的預留為：

\[
R\_{\mathrm{spot,buy}}=q\times p\_{\mathrm{reserve}}
\]

LIMIT 的 `p_reserve` 是委託價。MARKET 沒有委託價，`v0.3.0` 要求使用提交 callback
可見的收盤參考價，再加明示的 `market_reserve_bps`；缺少當下 close 證據就拒絕，
不能沿用來源不明的舊價格。現貨賣單則鎖住 base quantity `q`。

期貨非 reduce-only 訂單不改現貨 `free/locked`，而是建立逐 order 的保證金 reservation：

\[
R\_{\mathrm{futures}}
=\frac{q\times p\_{\mathrm{reserve}}}{L}
+q\times p\_{\mathrm{reserve}}\times f\_{\mathrm{taker}}
\]

固定案例 `q=2`、`p_reserve=100`、`L=10`、`f_taker=0.001`，所以 reservation 是
`20.200 USDT`。這是保守的 order earmark，不等於已實現費用，也不會因另一張反向掛單
看似可以抵銷就互相淨額。reduce-only 的 reservation amount 為 `0`，但提交與每次成交
都要重驗它只能減少既有反向持倉，不能翻向。

## 核心概念二：先由帳本授權，再讓撮合器完成狀態

`v0.3.0` 把一次提交與成交固定成下列責任順序：

```text
Order intent
  → admission：規則檢查＋AccountingLedger.reserve
  → canonical NEW
  → FillSimulator 提出 FillDecision（尚未改帳）
  → AccountingLedger.authorize_and_apply_fill（原子授權並入帳）
  → FillSimulator.finalize（產生 PARTIALLY_FILLED／FILLED）
  → AccountingLedger.apply_order_event（更新 canonical 狀態；終態釋放）
```

這個順序讓「有成交價格」與「財務上可接受」保持分離。`FillSimulator` 持有本地撮合簿、
剩餘訂單與撮合設定，輸入只讀的 `AccountState`；它不取得可寫 `AccountingLedger`
reference。真正入帳前，Ledger 還要核對：

- order 已完成 admission、存在 reservation，且 canonical 狀態仍為 active；
- market、symbol、side 與 order identity 完全一致；
- `0 < fill_qty <= reservation.remaining_qty`；
- fee 非負，且現貨買／賣與期貨使用正確 fee asset；
- projected balances、positions 與 reservations 全部合法。

所有檢查先在 projected copy 上完成，再一次提交。任一條失敗都不能留下半套餘額或半筆
reservation。成交決定被拒絕時也不應消耗撮合 volume pool。

## 核心概念三：部分成交同比切開 reservation，再按實際價格入帳

每筆成交先按「這次成交量占成交前剩餘量」切出預留：

\[
A\_{\mathrm{allocated}}
=R\_{\mathrm{before}}\times
\frac{q\_{\mathrm{fill}}}{q\_{\mathrm{remaining,before}}}
\]

固定現貨案例在第一筆成交前為 `R_before=200`、`q_fill=1`、
`q_remaining,before=2`，所以分配 `100 USDT`。實際成交只花 `1 × 90 = 90 USDT`，
未用的 `10 USDT` 當筆回到 `free`：

| 時點 | USDT free | USDT locked | BTC free | reservation qty／amount |
|---|---:|---:|---:|---:|
| 初始 | `1000` | `0` | `0` | 無 |
| 接受 `2 @ 100` | `800` | `200` | `0` | `2 / 200` |
| 成交 `1 @ 90`、fee `0.01 BTC` | `810` | `100` | `0.99` | `1 / 100` |
| 取消剩餘量 | `910` | `0` | `0.99` | 無 |

若 MARKET gap 後實際成本高於 allocated，Ledger 只能從 quote `free` 原子補足；餘額不足
就拒絕整筆剩餘量，不允許 quote 變負。現貨買入手續費從取得的 base 扣，賣出手續費從
收到的 quote 扣；不能因報表想統一成 USDT 就自行改寫 fee asset。

期貨案例同樣同比縮減：`20.200 × 1 / 2 = 10.100 USDT` 被分配給第一筆成交，
reservation 留下 `qty=1, amount=10.100`。成交後 position 的 target initial margin 與
尚存 order reservation 是兩個不同用途；snapshot 的 `initial_margin` 包含兩者，而
`reservation_margin` 由總額扣除 position target 唯一導出，不另存第二份可漂移總數。

## 核心概念四：終態釋放剩餘額，不回滾既有成交

`FILLED`、`CANCELED`、`REJECTED` 與 `EXPIRED` 都是終態。canonical order event 到達
終態時，剩餘 reservation 必須為零：

- `FILLED`：最後一筆已消耗全部 remaining quantity，移除零額 reservation；
- `CANCELED`／`EXPIRED`：把尚未成交部分的 earmark 釋放；
- `REJECTED`：若是部分成交後的財務拒絕，只釋放剩餘量；先前 fill 仍是歷史事實；
- 初始 `REJECTED`：admission 根本未建立 reservation，不可假裝先鎖再放。

取消不是交易 reversal。固定案例取消後保留 `0.99 BTC`，因為 `1 BTC` 已成交、
`0.01 BTC` 已作買入手續費。唯一被釋放的是第二個尚未成交 BTC 對應的 `100 USDT`。

## 核心概念五：逐資產守恆是最小驗收 oracle

現貨不能只把所有資產換成單一計價貨幣後看總權益，因為標記價格可能掩蓋資產流錯誤。
固定買入案例至少同時核對：

\[
\begin{aligned}
Q\_{\mathrm{USDT,free}}+Q\_{\mathrm{USDT,locked}}
&=1000-(1\times90)=910 \\
Q\_{\mathrm{BTC,free}}+Q\_{\mathrm{BTC,locked}}
&=1-0.01=0.99
\end{aligned}
\]

此外每個 `free`、`locked`、reservation amount 與 remaining quantity 都不得為負，
active order 必須能找到唯一 reservation，終態則不能殘留 reservation。這組 oracle
不需要目前市場價格，能直接找出 double spend、重複扣費、漏釋放與錯誤 fee asset。

## 系統對照：每個元件只寫自己的事實

| 元件 | `v0.3.0` 已發布責任 | 本章核對 | 不負責 |
|---|---|---|---|
| `OrderAdmission` | 組合規則、證據與 reservation | 接受前先鎖資金 | 產生成交價格 |
| `FillSimulator` | 依證據產生／完成本地撮合決定 | 提出 decision 時 Ledger 不變 | 寫錢包、position 或 canonical order |
| `AccountingLedger` | 唯一原子寫入 reservation、wallet、position 與 canonical order | 授權 fill、同比縮減、終態釋放 | 決定 OHLC path 或排隊 |
| `AccountState` | 不可變帳戶快照 | 提供撮合與報告只讀輸入 | 接受局部更新 |
| `Reservation` | 每張 active order 的 asset、amount、remaining qty | 連結訂單與 earmark | 取代已實現 fee／PnL |

這些是 Phase 3 已發布的 Python 模型與服務，可由固定 harness 驅動；完整 Trading Engine
事件循環與正式讀者回測 CLI 仍不是本章入口。本章不把內部測試冒充可提交交易所訂單的
產品流程。

## 動手驗證一：固定版本與聚焦測試

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
  tests/unit/test_engine_accounting.py::TestReservations \
  tests/unit/test_engine_accounting.py::TestCanonicalOrderState \
  tests/unit/test_engine_accounting.py::TestSpotFills \
  tests/unit/test_engine_accounting.py::test_spot_buy_conserves_each_asset_and_clears_terminal_reservation \
  tests/unit/test_execution_backtest.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，status 應無輸出。
聚焦測試涵蓋現貨與期貨 reservation、取消與終態釋放、部分成交、gap top-up 原子性、
canonical identity、逐資產守恆，以及 execution 先授權 Ledger 再 finalize 的責任順序。
預期且實測：

```text
Python 3.12.3
..............................................                           [100%]
46 passed in 0.30s
```

測試數量與時間是固定環境觀察值；未來測試增減時須重驗，不能只複製這行輸出。

## 動手驗證二：輸出逐筆資金核對表

仍在配套 worktree 執行隨書 helper：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch19-reservation-ledger-oracle.py"
```

預期且實測：

```text
spot-admit=USDT-free:800,locked:200,reservation:200
spot-partial=qty:1@90,fee:0.01BTC,USDT-free:810,locked:100,BTC-free:0.99,reservation-qty:1,amount:100
spot-cancel=USDT-free:910,locked:0,BTC-free:0.99,reservations:0
spot-conservation=USDT-total:910,BTC-total:0.99,PASS
futures-admit=qty:2@100,leverage:10,fee-rate:0.001,reservation:20.200
futures-partial-cancel=remaining-qty:1,reservation-before-cancel:10.100,reservation-after-cancel:0
simulator-ledger-boundary=decision-only,ledger-unchanged:true
chapter-19-reservation-ledger-oracle=PASS
```

helper 直接呼叫已發布 `AccountingLedger`、`FillSimulator` 與資料模型，沒有複製第二套
會計邏輯。所有會計輸入由字串建立 `Decimal`；`ExecutionOpenEvent.open` 在已發布模型
中是 float64，這裡只用它產生 decision 並驗證 Ledger 尚未改變，不拿它計算本章帳本數字。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| 接受後 `free + locked` 不變 | 預留只是 earmark | 報告分列可用與鎖定，不把 locked 當成本 |
| partial 後 qty／amount 同比縮減 | 剩餘訂單仍有唯一資金邊界 | 每筆 fill 後保存 reservation trace |
| price improvement 當筆回 free | 實際成本而非限價決定資產流 | 用 fill price 入帳，不把整筆預留當支出 |
| cancel 後 reservation 歸零 | 終態釋放了未成交部分 | 若仍有 locked／reservation，fail closed |
| simulator decision 後 Ledger 不變 | 撮合器不是會計 writer | 只允許 Ledger 授權並原子套用 fill |
| 逐資產守恆通過 | 固定案例沒有遺失或憑空新增資產 | 再核對 canonical identity 與非負性後才接受 |

若 helper 或任一聚焦測試失敗，停止使用該結果；不要手動調成預期數字，也不要在章內
建立平行帳本掩蓋配套缺陷。保存最小重現並轉回配套 repository 追蹤。

## 常見陷阱

### 把 `locked` 當成已花掉

`free` 減少不代表費用或損失已發生。至少同時保存 `free`、`locked`、reservation 與
canonical order status。

### 部分成交只減 quantity，不減 amount

這會讓取消時多釋放或少釋放資金。每筆 fill 必須從成交前的 amount／remaining qty
同比切片，不能一直用原始 order quantity。

### 用限價而非實際成交價入帳

限價是可接受邊界，不一定是 fill price。price improvement 要回到 free；不利 gap
則只能在原子資金檢查通過後補足。

### 取消時回滾既有成交

`CANCELED` 只終止有效剩餘量。已完成的 fill、fee 與資產流仍須保留。

### 讓 FillSimulator 偷改 wallet

撮合器若直接扣款，Ledger 再套用一次就 double debit；若 Ledger 拒絕，撮合器的局部變更
又無法回滾。唯一 writer 與 projected-copy commit 是原子性的必要條件。

### 只用總權益核對

錯扣 BTC、漏放 USDT 可能剛好被標記價格抵銷。先做逐資產守恆，再做估值與 PnL。

## 對系統的回饋

本章完成後，應留下可機器比較的「訂單狀態與資金預留表」：每列至少包含 order ID、
canonical status、event time、remaining qty、reservation asset／amount、wallet
`free/locked`、fill qty／price／fee／fee asset，以及逐資產守恆結果。它可以成為：

- regression test 的逐步 oracle；
- 回測結果的 reservation trace；
- 發現 double spend、late terminal event 或 fee asset mismatch 的最小重現；
- 第 20 章加入資金費、重估與強平前的乾淨會計起點。

## 小結與練習

你現在可以把一張訂單拆成「預留、canonical 接受、成交決定、Ledger 授權、部分成交、
終態釋放」六種不同事實。固定案例的答案是：接受後 `800 free / 200 locked`；第一筆
成交後 `810 free / 100 locked / 0.99 BTC`；取消後 `910 free / 0 locked / 0.99 BTC`。

請完成以下練習，不使用真實資金或 API key：

1. 把第一筆成交價改為 `110`，先算出需要從 `free` 補足多少，再執行 helper 的副本核對；
2. 把初始 USDT 改成不足以補 gap，證明 snapshot 與 reservations 在拒絕前後完全相等；
3. 建立一張現貨 SELL 固定表，核對 base locked 減少、quote 淨收入與 quote fee；
4. 在期貨案例中把 fee rate 改成 `0`，重算 admission 與 partial 後 reservation；
5. 保存一份逐列紀錄，註明哪些欄位來自 order、fill decision、canonical event 與 Ledger。

專業成果是一張可逐筆重跑、每列都能回答「誰改了哪個資產、依據是什麼」的資金預留表，
不是只有一個最終餘額。

## 作者驗證紀錄

- 對照 tag／commit：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：核對 tag、HEAD 與乾淨隔離 worktree；`uv lock --check`；`uv sync --locked --dev`；`uv run pytest tests/unit/test_engine_accounting.py::TestReservations tests/unit/test_engine_accounting.py::TestCanonicalOrderState tests/unit/test_engine_accounting.py::TestSpotFills tests/unit/test_engine_accounting.py::test_spot_buy_conserves_each_asset_and_clears_terminal_reservation tests/unit/test_execution_backtest.py -q`；`uv run python "$BOOK_DIR/manuscript/assets/ch19-reservation-ledger-oracle.py"`。
- 通過結果：聚焦測試 `46 passed`；固定 helper 核對現貨 free／locked、partial price improvement、取消釋放、逐資產守恆、期貨 leverage／fee buffer reservation、同比縮減，以及 simulator decision 前後 Ledger 不變，最終輸出 `chapter-19-reservation-ledger-oracle=PASS`。
- 待處理差異：本章使用已發布 Phase 3 Python 模型與固定 harness，不提供正式讀者回測 CLI、交易所 connector 或 live account。完整期貨 position 現金流、資金費、mark-to-market、isolated／cross 強平與連帶撤單由第 20 章續接。
