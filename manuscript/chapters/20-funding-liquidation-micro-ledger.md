# 資金費、強平與微型交易核帳

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-17

## 學習目標

完成本章後，你能：

1. 把資金費拆成帶時點、標記價格、方向與金額的獨立會計事件；
2. 解釋 funding 入帳為何可能在同一 cause 立即觸發風險重估與強平；
3. 逐筆核對強平前撤單、強平腿、已實現損益、強平費與終態帳戶；
4. 用版本化 golden 比對事件數、因果順序、最終錢包、持倉與 reservation；
5. 區分「測試支撐已發布會計語義」與「讀者可使用的完整回測產品入口」。

## 問題情境：一筆資金費如何變成強平事件

`v0.3.0` 的 Phase 3 微型案例先建立 BTCUSDT Cross 多倉、ETHUSDT Isolated 多倉與
現貨避險成交，再於同一 funding time 收到兩個 funding due。BTC 固定測試費率刻意設為
`0.26`，用來製造足以跨越強平邊界的壓力；它不是交易所現行費率、合理預測或可交易建議。

BTC funding 前的 Cross margin balance 為 `49.08720025000 USDT`。多倉
`Q=0.02 BTC`、結算 mark `9900 USDT/BTC`、固定率 `0.26`，所以現金流為
`-51.4800 USDT`。若只把這筆金額放進報表最後一欄，會漏掉三個因果事實：

- funding posting 先由唯一帳本入帳並更新相符 mark；
- 入帳後 margin balance 變成 `-2.39279975000 USDT`，風險邊界已被跨越；
- 同一 cause 的後續 effect 先撤掉會受清算影響的 active orders，再原子套用
  `LiquidationEvent`。

本章的目標不是背下最終數字，而是能從原因一路對到每一筆資產與終態。

## 執行前預測

先寫下答案與理由：

1. 正費率下，BTC 多倉的 funding cash flow 是正還是負？
2. `49.08720025000 - 51.4800` 是否等於強平事件保存的 margin balance？
3. BTC funding cause 內，posting、撤單與 liquidation 應以什麼順序發生？
4. 強平費應由 mark、entry 還是 liquidation execution price 計算？
5. Cross BTC 被強平時，Isolated ETH 是否應被同一 liquidation leg 一起平掉？
6. 全案例完成後，active position 與 reservation 各應剩多少？

## 核心概念一：Funding 是帶身分的現金流，不是績效註腳

以帳戶觀點表示 signed quantity `Q`，單筆資金費為：

\[
\mathrm{CF}\_{\mathrm{funding}}=-Q\times m\_f\times r\_f
\]

其中 `m_f` 是該次結算 mark，`r_f` 是該次費率。三者都必須是字串建立的
`Decimal`，posting 還要保存 symbol 與 timestamp。BTC 固定案例逐格計算：

\[
-0.02\times 9900\times 0.26=-51.4800\ \mathrm{USDT}
\]

ETH Isolated 多倉則為：

\[
-0.2\times 1015\times 0.001=-0.2030\ \mathrm{USDT}
\]

`AccountingLedger.apply_funding` 會核對 posting 的 signed quantity 與帳本持倉完全一致，
再重算 cash flow；同一 `(symbol, timestamp)` 重複入帳會 fail closed。Cross funding
只改期貨 wallet；Isolated funding 還會同步改該部位的 isolated bucket，若穿透至負數則
留下 `pending_isolated_deficit`，不能把缺口藏成零。

## 核心概念二：同時點不等於沒有先後

外部 funding due 是 cause；posting、撤單與強平是它造成的 inline effects。BTC 固定
golden 在 `1700000240000` 保存下列次序：

| `effect_seq` | effect | 核帳意義 |
|---:|---|---|
| `0` | `FundingPostedEvent` | `-51.4800 USDT` 已由唯一帳本入帳 |
| `1` | BTC resting order `CANCELED` | 釋放會受 Cross liquidation 影響的 reservation |
| `2` | ETH reduce-only resting order `CANCELED` | liquidation 前凍結相符 active order set |
| `3` | `LiquidationEvent` | 原子平掉 decision scope 內的 Cross position |

四筆 effect 的 frame timestamp 相同，但 `effect_seq` 讓原因先於結果。若先產生
liquidation、最後才補 funding，或者把撤單重新插回已走過的外部事件 queue，重跑時就
可能得到不同 reservation 或成交；那不是相同帳本。

## 核心概念三：風險重估要保留 funding 前後橋接

BTC funding 前後的最小核對式是：

\[
49.08720025000+(-51.4800)=-2.39279975000\ \mathrm{USDT}
\]

`LiquidationChecker` 對 Cross 單向簿使用 bar 內不利 mark 口徑；對實際對沖的 Cross 簿
使用對齊 close，另輸出 worst-case 壓力值；Isolated 則逐部位判斷。資料缺 symbol、mark
過期或 bracket 不相符時回傳 data gap，不能用最後已知值悄悄繼續。

本固定案例的 BTC Cross margin balance 已非正，所以 trigger reason 是
`margin_balance`。這是版本化回測假設下的清算決定，不是 Binance 真實清算回報，也不
代表 bar 粒度可以重建真實保險基金、ADL 或撮合深度。

## 核心概念四：Decision 與入帳仍是兩個責任

`LiquidationChecker` 產生 decision，`AccountingLedger.apply_liquidation` 才是唯一會計
writer。Ledger 在提交前核對：

- Cross scope 必須包含當下全部非零 Cross positions；Isolated scope 只能包含指定部位；
- 每條 leg 的 symbol、原 signed quantity、margin type 與帳本一致；
- `fee = qty × execution_price × fee_rate`；
- canceled order IDs 已排序、唯一，且 reservation 已由 canonical 取消流程釋放；
- projected wallet、positions 與 deficit 全部合法後才一次提交。

BTC leg 以 `0.02 BTC`、execution price
`10059.87950552208835341365462 USDT/BTC` 與固定清算費率 `0.005` 計算：

\[
0.02\times10059.87950552208835341365462\times0.005
=1.005987950552208835341365462\ \mathrm{USDT}
\]

強平後 BTC Cross position 被移除。ETH Isolated 不屬於這個 Cross liquidation leg；它先
獨立承受 `-0.2030 USDT` funding，之後再由自己的正常 reduce-only fill 關閉。模式邊界
不能因兩個 symbol 恰好在同一帳戶就被抹平。

## 核心概念五：Golden 是逐位 oracle，不是產品入口

Phase 3 IT-3 由 HistoricalDataSource、BacktestExecution、Router 與 Engine-owned
AccountingLedger 組成固定 harness。版本化 golden 的 canonical JSON SHA-256 是
`f9e65a281e5fe242a75475e5c99832247b00f8d97a380a735cb99ffb908ef7c7`，並保存：

| 證據 | 固定數量／結果 |
|---|---:|
| external causes | `40` |
| inline effects | `19` |
| fills | `6` |
| funding postings | `2` |
| liquidations | `1` |
| final positions | `0` |
| final reservations | `0` |

正向 feed 順序、反向 feed 順序與同輸入雙跑都必須逐位等於同一份 golden。這證明已發布
的 Phase 3 元件在固定 harness 下具可重現會計語義；它不會把尚未發布的完整
TradingEngine、正式讀者回測 CLI、Paper／Testnet／Live 或 reconciliation 入口變出來。

## 系統對照：誰決定、誰入帳、誰保存證據

| 元件 | `v0.3.0` 已發布責任 | 本章核對 | 不代表 |
|---|---|---|---|
| `FundingSettlement` | 從 due、position 與 fresh mark 形成 `FundingPosting` | signed quantity、mark、rate、cash flow | 預測下一期 funding |
| `AccountingLedger.apply_funding` | 冪等且原子套用 funding | wallet、isolated bucket、deficit、mark | 外部交易所對帳完成 |
| `LiquidationChecker` | 依 bracket、book shape 與 risk frame 產 decision／sample | trigger、scope、close／worst 口徑 | 真實交易所清算成交 |
| `AccountingLedger.apply_liquidation` | 核對 leg 後套用 realized PnL、fee 與 position 終態 | cancellation、fee、wallet、deficit | 保險基金或 ADL 模型 |
| IT-3 harness／golden | 固定資料流與逐位 expected result | 因果序、會計軌跡、雙跑 hash | 正式回測產品入口 |

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
  tests/unit/test_engine_funding.py \
  tests/unit/test_fill_funding.py \
  tests/unit/test_models_liquidation.py \
  tests/unit/test_fill_liquidation_frames.py \
  tests/unit/test_fill_liquidation_checker.py \
  tests/unit/test_engine_liquidation.py \
  tests/integration/test_it3_fill_execution.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，status 應無輸出。
預期且實測：

```text
Python 3.12.3
........................................................................ [ 64%]
........................................                                 [100%]
112 passed in 0.56s
```

測試數量與時間是固定環境觀察值；未來測試增減時須重驗。

## 動手驗證二：逐筆核對會計 golden

仍在配套 worktree 執行隨書 helper：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch20-funding-liquidation-golden-oracle.py"
```

預期且實測：

```text
golden-sha256=f9e65a281e5fe242a75475e5c99832247b00f8d97a380a735cb99ffb908ef7c7
golden-counts=external:40,effects:19,fills:6,funding:2,liquidations:1
funding=BTCUSDT,qty:0.02,mark:9900,rate:0.26,cash-flow:-51.4800,margin-balance:49.08720025000->-2.39279975000
liquidation=scope:cross_account,reason:margin_balance,cancel:BT-F-00000004+BT-F-00000006,close:BTCUSDT-0.02,fee:1.005987950552208835341365462
isolated=ETHUSDT,funding:-0.2030,position-final:closed
final=futures-wallet:24.69955390988955823293172694,positions:0,reservations:0,spot-BTC:0.98,spot-USDT:1199.7001000000
chapter-20-funding-liquidation-golden-oracle=PASS
```

helper 只讀配套 repository 版本管理內的 golden，核對 canonical hash、Decimal 公式、
effect sequence 與終態；真正產生這份結果的已發布實作由前一組單元／整合測試重跑。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| funding 公式與 posting 完全一致 | 該筆固定 cash flow 可追溯 | 保存 symbol、time、mark、rate、qty |
| margin bridge 精確相等 | 觸發前後沒有漏掉另一筆現金流 | 不相等就停止強平解讀 |
| effect sequence 為 `0→1→2→3` | 同 cause 內先入帳、再撤單、再清算 | 順序改變即視為結果不可比 |
| liquidation fee 公式通過 | 固定 leg 的 fee 沒有重複或漏扣 | 用 execution price，不偷換 mark |
| final positions／reservations 都是 `0` | 終態沒有殘留可寫狀態 | 任一殘留都 fail closed |
| canonical hash 與雙跑測試通過 | 固定 harness 結果可重現 | 只接受相符 tag 與 golden |

若任一測試或 helper 失敗，不要手改 JSON 或在書稿複製另一套 ledger 來湊答案。保存最小
重現與差異，回到配套 repository 追蹤；在修正正式發布前，本章應標為「需重驗」。

## 常見陷阱

### 把壓力費率當市場現況

`0.26` 是讓固定案例跨過 liquidation gate 的測試輸入。把它寫成 Binance 現行費率會把
oracle 變成錯誤市場資訊。

### Funding 加兩次

`wallet_after` 已包含 funding。報表再把 `cash_flow` 加到 equity 就會 double count。

### 用成交 K 線 close 取代結算 mark

Funding 與強平需要相符時點的 mark evidence。缺失或過期時應 data gap，不可沿用舊值。

### 強平時不先處理 active orders

若 Cross position 已清空，舊掛單與 reservation 卻仍有效，下一個 market event 可能重開
曝險或留下幽靈資金。取消身分與順序必須進事件證據。

### 把 Cross 與 Isolated 混成帳戶總清算

Cross scope 不應吞掉 Isolated position；Isolated deficit 也不能由 Cross wallet 靜默補平。

### 只比最終餘額

兩條錯誤路徑可能碰巧得到相同終值。至少同時比對 cause、effect sequence、posting、
canceled IDs、liquidation leg、final positions／reservations 與 canonical hash。

## 對系統的回饋

本章應留下可機器比較的「微型交易核帳包」：

- 配套 `tag@commit`、golden schema 與 canonical hash；
- external cause 與 effect 的 processing key；
- 每筆 fill、fee、funding posting 與 liquidation leg；
- funding 前後 margin bridge、close／worst risk sample 與 bracket snapshot time；
- 每次取消的 order ID、reservation trace、最終雙錢包與 positions；
- 已知模型差異與重驗條件。

它能成為會計回歸的 oracle，也能在未來完整引擎發布後指出「產品結果從哪一筆開始偏離」。
本章不啟動 W1-final 的全章驗收或新手試讀。

## 小結與練習

固定案例中，BTC funding 把 margin balance 從 `49.08720025000` 推到
`-2.39279975000 USDT`；同 cause 依序 posting、取消兩張 active orders，再套用一條
Cross liquidation leg。ETH Isolated 獨立入帳 funding，最後由正常成交關閉。全案例的
終態是 `0 positions / 0 reservations`，canonical golden hash 固定。

請完成以下練習，不使用真實資金或 API key：

1. 把 BTC rate 改成 `0.01`，只做 Decimal 手算，判斷新的 margin bridge 是否仍跨 gate；
2. 從 golden 找出兩張 canceled order 的原始 reservation，說明各自為何必須釋放；
3. 重算 liquidation fee，故意改用 `mark_basis=9900`，量出錯誤差額；
4. 列出 ETH Isolated funding 前後的 risk sample，確認它不在 BTC Cross liquidation legs；
5. 寫一張六欄核帳表：cause、effect seq、event、wallet delta、position delta、reservation delta。

專業成果是一份能從外部證據追到終態、每筆 Decimal 都可重算的微型交易核帳包，而不是
一句「最後帳戶餘額正確」。

## 作者驗證紀錄

- 對照 tag／commit：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：核對 tag、HEAD 與乾淨隔離 worktree；`uv lock --check`；`uv sync --locked --dev`；執行 funding、liquidation 與 IT-3 golden 的 7 個測試檔；`uv run python "$BOOK_DIR/manuscript/assets/ch20-funding-liquidation-golden-oracle.py"`。
- 通過結果：聚焦測試 `112 passed`；canonical SHA-256 為 `f9e65a281e5fe242a75475e5c99832247b00f8d97a380a735cb99ffb908ef7c7`；helper 核對 40 causes／19 effects／6 fills／2 funding／1 liquidation、BTC margin bridge、撤單與強平順序、fee、Cross／Isolated 邊界，以及最終 `0 positions / 0 reservations`，輸出 `chapter-20-funding-liquidation-golden-oracle=PASS`。
- 待處理差異：BTC `0.26` funding 與所有價格、餘額、fee 都是版本化測試輸入；Phase 3 harness 與 golden 是支撐證據，不是完整 TradingEngine、正式讀者回測 CLI、交易所 connector、Paper／Testnet／Live、保險基金、ADL 或 reconciliation 入口。
