# 回測撮合不是交易所

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-17

## 學習目標

完成本章後，你能：

1. 把 K 線提供的歷史證據與撮合模型補上的反事實假設分開；
2. 解釋普通市價單為何只能使用下一根 `ExecutionOpenEvent`；
3. 比較限價單的 strict cross、touch 與 gap-through 定價；
4. 核對固定 bar path、price-time 與全 bar 成交量池如何裁決多張訂單；
5. 保存一份可重現的撮合假設表，不把模擬成交冒充交易所成交。

## 問題情境：K 線碰到限價，不代表你的單成交

你在一根 K 線開始前掛了買單 `100`。該根 K 線為
`O=105, H=110, L=100, C=108`，成交量為 `10`。圖上最低價剛好碰到 `100`。

只有這五個數字，仍不知道：

- 價格在 `100` 停留多久、排在你前面的數量有多少；
- 成交量中有多少真的打到 `100` 或更低；
- 你的委託是否已經進簿、是否被取消，或是否輪到你的排隊位置；
- K 線內先走高還是先走低。

因此「low 碰到 limit」不是歷史成交事實。回測必須選一組可重現的撮合假設
（matching assumptions），再回答「如果採用這組模型，會判成什麼」。模型可以一致，
卻不能因此升格為交易所當時真的成交。

## 執行前預測

先寫下答案與理由：

1. 策略在 `10:00` 收盤看到 close 後送出市價單，能否填在同一根的 open？
2. 買入限價 `100`，bar low 恰好等於 `100`；strict 與 touch 會給相同答案嗎？
3. 買入限價 `100`，下一根 open gap 到 `98`；成交 base 應是 `100` 還是 `98`？
4. 同一根 bar 內有四張可成交限價單；只靠呼叫順序決定先後，重跑是否可靠？
5. bar volume 是 `10`、cap 是 `10%`；兩張單能各自取得 `1`，還是共用 `1`？

## 核心概念一：先分開證據與模型

歷史 K 線只證明某期間的 OHLCV 摘要。`v0.3.0` 的 bar 撮合另外明示：

| 決定 | 歷史輸入能證明 | 模型另外假設 | 不可宣稱 |
|---|---|---|---|
| 市價成交 | 下一根 open 的資料值 | 普通市價單在下一 `ExecutionOpenEvent` 以 open 為 base | 真實訂單在該價完整成交 |
| 限價資格 | bar high／low 是否跨過價位 | strict 或 touch 門檻 | 知道真實排隊位置 |
| gap-through | bar open 已優於限價 | 以 open 給價格改善，仍到 close 才完成證據 | 交易所一定給同樣 fill |
| 多單順序 | 同一 bar 的 OHLC | 依曝險選固定 path，再用 price-time | 知道真實 bar 內逐筆路徑 |
| 成交量上限 | 完整 bar volume | `volume × cap` 是全 bar 共用代理池 | 這是當時可見訂單簿深度 |
| 費用與滑點 | 沒有帳戶費率或逐筆衝擊 | 使用 `FillConfig` 的固定 Decimal 輸入 | 這是目前帳戶真實成本 |

專業報告不能只寫「成交」。最低限度應寫成：「在 `v0.3.0`、bar 粒度、strict
limit、固定 path、`10%` volume cap 與指定費率／滑點下，模擬器產生這筆 fill。」

## 核心概念二：下一根 execution-open 保住因果

策略在 bar 收盤回呼時才看見該根 close。若把這時提出的普通市價單填回同一根 open，
就使用了決策之前的價格，形成回填（backfill）與前視偏誤。

配套資料流把相鄰 bar 交界拆成兩個同時刻事件：

```text
上一根 MarketEvent(close) → 策略決定 → 下一根 ExecutionOpenEvent(open) → 撮合
```

`ExecutionOpenEvent` 只攜帶 open，不攜帶該根尚未完成的 high／low／close。普通
`MARKET` 以 open 為 base，買入套用不利滑點
`base × (1 + bps / 10000)`，賣出套用 `base × (1 - bps / 10000)`，入帳時間為
`open_time`。

固定案例的 base 是 `100.0`，預設 `5 bps` 買入後為 `100.05000`。這是模型價格，
不是市場衝擊估計。若開啟 volume cap，下一 open 也不能偷看尚未收盤的本根 volume；
模型改用前一根完整 volume 作流動性代理，並在結果 metadata 寫入
`market_open_volume_proxy=previous_closed_bar`。

## 核心概念三：strict、touch 與 gap-through 是三個決定

對已在 bar 開始前存在的 resting limit：

| 模式／情況 | BUY 資格 | SELL 資格 | base price | 入帳時間 |
|---|---|---|---|---|
| strict cross | `low < limit` | `high > limit` | 若無 gap，使用 limit | bar close |
| optimistic touch | `low <= limit` | `high >= limit` | 若無 gap，使用 limit | bar close |
| gap-through | `open <= limit` | `open >= limit` | 使用較有利的 open | bar close |

touch 只擴大資格集合，不改變排序與定價。它較樂觀，因為「碰到」無法證明排隊已輪到；
strict 要求真正穿越價位，仍只是 bar 模型，不是逐筆成交證據。

gap-through 以買入 `limit=100, open=98` 為例，base 是 `98`，不會故意用較差的
`100`。但完整 bar 證據到 close 才成立，所以 `accounting_ts` 仍是 bar close。
本地簿上的 limit fill 分類為 maker，`FillSimulator` 不再額外施加 taker 滑點。
這個「marketable limit 在本地接受後以 maker 處理」可能偏樂觀，故另一個固定
metadata 是 `marketable_limit_liquidity=maker_after_local_book_acceptance`。

剛在本次 close 回呼才提交的 limit 不得回填本 bar。`placed_ts <= bar.open_time`
只是第一道時間 gate；正式 execution driver 還會核對 accepted processing key 早於
目前 close cause。

## 核心概念四：固定 path 與 price-time 讓重跑唯一

OHLC 不含 bar 內順序。模型必須選一條固定路徑，否則同一根同時碰到買賣兩側時，
結果會隨迴圈或容器順序漂移。`v0.3.0` 先處理 open gap，再依 bar 開始前曝險選：

| bar 前曝險 | 固定路徑 |
|---|---|
| long | `O → L → H → C` |
| short | `O → H → L → C` |
| flat | `O → H → L → C` |

每段再用 price-time：上行段的 SELL 從低 limit 到高 limit；下行段的 BUY 從高
limit 到低 limit；同價位使用 `placed_seq`。固定 flat 案例因此依序為
`sell-101 → sell-102 → buy-99 → buy-98`，不是訂單傳入列表的偶然結果。

這條 path 是唯一可執行的反事實，不是重建真實逐筆路徑。更保守的研究仍要換用
逐筆或 footprint 證據、測量上下界，不能替 OHLC 填一段看似合理的故事。

## 核心概念五：全 bar 只有一個代理成交量池

close 撮合使用：

\[
Q\_{\mathrm{pool}}=V\_{\mathrm{bar}}\times c\_{\mathrm{cap}}
\]

固定案例 `V_bar=10.0`、`c_cap=0.1`，所以同一 bar 的池只有 `1.00`。先選出的
limit 使用 `0.6` 後，同 frame 的 optimistic market 最多只剩 `0.40`，不是再取得
另一個 `1.00`。pre-existing limits、same-frame optimistic market 與 remedial market
共用這個池；被財務授權拒絕的決定不消耗池。

volume cap 截完為零時，不產生零量 fill，也不把殘量刪掉；market 殘量等下一 open，
limit 殘量留在本地簿等下一份證據。這讓部分成交可以重現，但 bar volume 仍不是
該價位成交量、訂單簿深度或你的真實可成交量。

## 系統對照：誰負責哪一種決定

| 元件 | `v0.3.0` 已發布責任 | 本章使用 | 不負責 |
|---|---|---|---|
| `ExecutionOpenEvent` | 只攜帶下一 open 與身分／時間 | 保住普通市價單因果 | 揭露未來 OHLC |
| `MatchingPolicy` | 選擇 optimistic market／limit touch | 比較 strict 與 touch | 證明真實成交 |
| `BarFillMode` | 選資格、base、固定 path、price-time | 產生瘦 `FillProposal` | 費率、滑點、會計 |
| `FillSimulator` | 套 volume pool、taker 滑點、fee 與 gap metadata | 產生 `FillDecision` | 直接改寫帳戶 |
| `BacktestExecution` | 持有每份 evidence 的 consumed／handled state | 確保全 bar pool 唯一 | 建立第二套帳本 |
| `AccountingLedger` | 授權並原子套用 canonical fill | 第 19 章續接逐筆會計 | 決定誰可撮合 |

本章 helper 呼叫已發布 `BarFillMode` 與 `FillSimulator`，不複製一套撮合器。
完整 financial authorization 與唯一帳本由已發布聚焦測試支撐，本章只稽核撮合決定；
第 19 章才把資金預留、部分成交與帳本逐筆串起來。

## 動手驗證一：固定版本與逐行撮合測試

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
  tests/unit/test_fill_base.py \
  tests/unit/test_fill_mode.py \
  tests/unit/test_fill_matrix.py \
  tests/unit/test_fill_simulator_edges.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，status 應無輸出。
這四組測試直接覆蓋 next-open、strict／touch、gap-through、固定 path、price-time、
共用 volume pool、部分成交、費率、滑點方向與假設 metadata。預期且實測：

```text
Python 3.12.3
........................................................................ [ 80%]
.................                                                        [100%]
89 passed in 0.48s
```

測試數量與時間是固定環境的觀察值，不是跨機器效能承諾；未來測試增減時須重驗。

## 動手驗證二：輸出一張撮合假設核對表

仍在配套 worktree 執行隨書 helper：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch18-bar-matching-oracle.py"
```

預期且實測：

```text
market=next-execution-open,base=100.0,final=100.05000,slippage_bps=5,liquidity=taker,fee_rate=0.0005,fee=0.050025000,fee_asset=USDT,accounting_ts=1700000000000
limit-threshold=strict:NO-FILL,touch:limit-touch@100
gap-through=BUY,limit=100,open=98.0,base=98.0,accounting=bar-close
flat-bar-path=O-H-L-C,sequence=sell-101>sell-102>buy-99>buy-98
volume-pool=bar-volume:10.0,cap:0.1,pool:1.00,limit:0.6,optimistic-market:0.40
metadata=market_open_volume_proxy=previous_closed_bar,marketable_limit_liquidity=maker_after_local_book_acceptance
chapter-18-bar-matching-oracle=PASS
```

所有訂單、費率、滑點與 cap 都由字串建立 `Decimal`；行情 OHLCV 在已發布模型中是
float64，進入 fill 邊界時使用其最短 round-trip 十進位表示。輸出中的預設 fee rate
與 `5 bps` 都是版本化模型輸入，不是目前 Binance、任意交易所或私人帳戶費率。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| next-open、因果順序與滑點 oracle 通過 | 普通 market 在明示模型下可重現 | 保存基線與設定；不稱為真實 fill |
| strict 不成交、touch 成交 | 結果依排隊樂觀度敏感 | 至少並列兩種情境；不可只挑較好者 |
| gap-through 使用較有利 open | 模型不以較差於 limit 的 base 成交 | 仍標 bar-close evidence 與 maker 假設 |
| 固定 path 改變先後與資金需求 | OHLC 路徑假設會改變結果 | 做替代 path／逐筆敏感度，不猜真實路徑 |
| cap 截出 partial fill | 單一代理池限制本 bar 模擬數量 | 殘量留待下一 evidence；不補成完整成交 |
| 無 previous closed volume 卻開 cap | 下一 open 缺少代理證據 | fail closed，不偷看本根未來 volume |
| 成本參數不是版本化輸入 | 結果不可重現 | 停止績效判讀，先補假設表 |

## 常見陷阱

- 用同一根 close 做決定，又填回同一根 open。
- 看到 low 等於買入 limit，就把 touch 當成交易所成交事實。
- gap-through 一律以 limit 成交，丟掉模型已定義的價格改善。
- 依 Python list、dict 或策略提交迴圈的偶然順序裁決同 bar 多單。
- 每張訂單各自取得完整 `volume × cap`，讓總成交超過代理池。
- 用本根完整 volume 決定本根 open 的 market fill，造成未來資訊洩漏。
- 把 bar volume cap 稱為訂單簿容量或市場衝擊模型。
- 把 limit 意圖當 maker fill，或把模型 maker fee 當私人帳戶現行費率。
- 用實際／模擬 fill price 算 PnL 後，再把同一滑點扣第二次。
- 只保存最終 PnL，不保存 strict／touch、path、cap、費率、滑點與 metadata。

## 對系統的回饋

每次回測至少輸出以下 append-only 撮合假設：

| 欄位群 | 最小內容 |
|---|---|
| 基線 | tag／commit、資料 manifest、bar interval、fill granularity |
| market | next-open 或 same-frame optimistic、slippage bps、volume proxy |
| limit | strict／touch、gap-through 定價、marketable-limit liquidity |
| 排序 | pre-bar exposure、固定 path、price-time／placed sequence |
| 容量 | volume cap、pool、逐筆 consumed、partial remaining |
| 成本 | liquidity、fee rate、fee、fee asset、是否已含於 fill price |
| 因果 | order created／accepted、evidence、accounting timestamp |
| 邊界 | 模擬結果、資料缺口、未建模排隊／衝擊與 fail-closed 原因 |

若報告只顯示成交率與 PnL，卻無法回答是哪組撮合假設產生，就不具備重現性。
應把缺少欄位形成報告 schema 或測試 finding，不讓策略各自寫一套隱藏撮合規則。

## 小結與練習

回測 fill 是「歷史資料加上明示模型」的反事實結果。next-open 保住時間因果；
strict／touch 說明限價資格；gap-through、固定 path 與 price-time 讓結果唯一；
共用 volume pool、滑點與費用則把樂觀程度寫成可核對數字。這些規則越清楚，越不能
把結果誤稱為交易所當時真的成交。

請完成兩組互斥情境：

1. 保留固定 bar，分別用 strict 與 touch 判斷買入 limit `100`、`low=100`；
2. 保留 `volume=10`，依序把 cap 改為 `0.05` 與 `0.20`，重算兩張 `0.6` 訂單的
   fill 與 remaining。

每組都保存假設、逐筆 pool、成交／未成交理由與不得宣稱事項。不得同時改 threshold、
path、費率與 cap，否則無法知道差異來自哪一項。

你的專業成果是一份「回測撮合假設與逐筆核對表」：另一位審核者能從版本、bar、
訂單與設定重播每一個 selection、price、quantity、fee 與 remaining 決定，並清楚看見
哪些是歷史輸入、哪些只是模型。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：核對 tag、HEAD 與乾淨隔離 worktree；`uv lock --check`；`uv sync --locked --dev`；`uv run pytest tests/unit/test_fill_base.py tests/unit/test_fill_mode.py tests/unit/test_fill_matrix.py tests/unit/test_fill_simulator_edges.py -q`；`uv run python "$BOOK_DIR/manuscript/assets/ch18-bar-matching-oracle.py"`。
- 通過結果：聚焦測試 `89 passed`；固定 helper 核對 next execution-open、strict／touch、gap-through、flat 固定 path、price-time、全 bar 共用 volume pool、預設 taker 滑點／費用與兩項假設 metadata，最終輸出 `chapter-18-bar-matching-oracle=PASS`。
- 待處理差異：本章只驗證 `v0.3.0` 的 bar 粒度本地撮合；沒有真實排隊、逐筆深度、市場衝擊、私人費率、交易所訂單或實盤成交。spot LIMIT 預設 admission 仍禁用；逐筆 footprint 模式只是設計預留，不是已發布讀者入口。資金預留、canonical partial-fill 會計與終態釋放由第 19 章續接。
