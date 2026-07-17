# E. 永續合約與 Binance 市場機制

本附錄目前只收錄第 7–9 章與[第 20 章](../chapters/20-funding-liquidation-micro-ledger.md)
直接需要的 U 本位線性永續、單向持倉、資金費、現貨／期貨雙錢包、價格角色、
Cross／Isolated、分層保證金、強平邊界與 funding 後清算因果查表。
最後查證日期為 2026-07-15。完整手算、驗收 oracle 與系統邊界見
[第 7 章](../chapters/07-perpetual-dual-wallet-funding.md)與
[第 8 章](../chapters/08-leverage-margin-liquidation.md)，以及
[第 9 章](../chapters/09-two-strategy-risk-maps.md)；第 20 章另以 Phase 3 golden
核對 funding、撤單與清算順序。本頁不提供交易所帳戶
設定或下單步驟。

所有費率、價格與結算時點都必須取自相符市場與時點的第一手資料。第 7 章的
`0.0005` 是固定教學輸入，不是目前 Binance 費率、下一期預測或收益承諾。

## 現貨與 U 本位線性永續

| 項目 | 現貨 | 本附錄的 U 本位線性永續 |
|---|---|---|
| 持有內容 | 錢包中的 base／quote 資產餘額 | 以 quote 資產結算的合約價格曝險 |
| 方向 | 無借貸案例的 base 餘額不得為負 | `signed_qty > 0` 多頭、`< 0` 空頭、`= 0` 零持倉 |
| 曝險規模 | `price × quantity` | `abs(signed_qty) × mark_price` |
| 價格未實現 PnL | 尚未處分部位相對成本的估值差 | `(mark_price - entry_price) × signed_qty` |
| Funding | 不適用 | 在指定 funding time 形成另一筆期貨錢包現金流 |

永續多單不會讓現貨錢包自動增加 BTC；永續空單也不等於現貨錢包借入 BTC。
Binance 官方 Academy 的
[資金費說明](https://www.binance.com/en/academy/articles/what-are-funding-rates-in-crypto-markets)
將永續合約描述為沒有到期日的衍生品，並說明資金費是多空持倉者之間的週期性
支付。這只支持產品與支付方向，不支持教學案例數值。

## One-way Mode：一個標的一個淨方向

第 7 章採單向持倉：同一標的在同一快照只有一個帶正負號的淨數量。多頭、空頭
與零持倉是三個互斥案例，不是同一帳戶同時存在的三條腿。

Binance USDⓈ-M 官方
[變更持倉模式文件](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode)
以 `dualSidePosition=false` 表示 One-way Mode；該模式的訂單欄位使用
`positionSide=BOTH`。這項查證只對齊名詞，不代表 Emmet `v0.3.0` 已發布切換帳戶
模式或送出私人訂單的讀者入口。

## Funding：保存方向、金額與時點

以帳戶觀點定義：

\\[
\mathrm{CF}\_{\mathrm{funding}}
=-Q\times m\_f\times r\_f
\\]

| `signed_qty` | 正費率 | 負費率 | 零費率 |
|---:|---|---|---|
| `> 0` 多頭 | 支付，現金流為負 | 收取，現金流為正 | `0` |
| `< 0` 空頭 | 收取，現金流為正 | 支付，現金流為負 | `0` |
| `= 0` 零持倉 | `0` | `0` | `0` |

Binance USDⓈ-M 官方
[資金費歷史文件](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)
分列 `fundingRate`、`fundingTime` 與相符的 `markPrice`；
[資金費資訊文件](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info)
另列 `fundingIntervalHours`。研究紀錄至少要保存標的、費率、funding time、結算
標記價格與來源，不能只保存費率後自行補成固定八小時。

## Emmet `v0.3.0` 的雙錢包邊界

| 已發布模型 | 保存內容 | 一筆 funding posting 可以改變什麼 |
|---|---|---|
| `SpotWallet` | 各現貨資產的 `free`／`locked` 餘額 | 不改變任何現貨資產餘額 |
| `FuturesWallet` | `wallet_balance` 與合約 `positions` | `wallet_balance += funding_cash_flow`，並以結算 mark 更新相符部位 |
| `AccountState` | 分開保存 spot 與 futures | 不把兩個錢包折成可任意挪用的一欄 |

錢包間移動資產必須是另一筆明示 transfer；funding posting 不是 transfer。以
`W_before`／`W_after` 表示期貨 wallet balance、`U` 表示價格未實現 PnL：

\\[
\begin{aligned}
W\_{\mathrm{after}}
  &=W\_{\mathrm{before}}+\mathrm{CF}\_{\mathrm{funding}} \\\\
E\_{\mathrm{futures}}
  &=W\_{\mathrm{after}}+U \\\\
  &=W\_{\mathrm{before}}+\mathrm{CF}\_{\mathrm{funding}}+U
\end{aligned}
\\]

`W_after` 已含資金費，因此不能再加一次。合約名義價值只是曝險規模，也不能加進
權益。

## 成交、指數與標記價格

| 價格 | 官方／研究角色 | 不可偷換成 |
|---|---|---|
| 成交價格 | 實際撮合與已實現 PnL 的交易事實 | 強平風險重估價 |
| 指數價格 | 多個現貨市場組合的參考 | 帳戶實際成交 |
| 標記價格 | 公平參考；未實現 PnL、名義價值與強平判斷 | 保證可成交價格 |

Binance 的
[標記價格與指數價格 FAQ](https://www.binance.com/en/support/faq/detail/360033525071)
於本次查證標示 2026-07-09 更新；USDⓈ-M 公開
[Mark Price 端點](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price)
也分列 `markPrice` 與 `indexPrice`。這只支持欄位與角色；任何即時值都必須在
研究時重新取得並保存 exchange timestamp。

## 槓桿、分層與保證金

第 8 章對齊的最小公式為：

\\[
\begin{aligned}
N&=|Q|\times m \\\\
\mathrm{IM}\_{\mathrm{position}}
  &=\frac{|Q|p\_{\mathrm{entry}}}{L} \\\\
\mathrm{MM}&=N\times\mathrm{MMR}-A \\\\
\mathrm{MB}&=W+U \\\\
\mathrm{MR}&=\frac{\mathrm{MM}}{\mathrm{MB}},
  \qquad \mathrm{MB}>0
\end{aligned}
\\]

Binance 的
[槓桿與保證金 FAQ](https://www.binance.com/en/support/faq/detail/360033162192)
於本次查證標示 2026-03-27 更新，說明 initial margin 隨 leverage 改變，維持
保證金則由 notional bracket、MMR 與 maint amount 決定。
[開倉成本 FAQ](https://www.binance.com/en/support/faq/detail/87fa7ee33b574f7084d42bd2ce2e463b)
另顯示訂單成本可包含 open loss；所以 Emmet 的 position target 不是完整交易所
order cost。

第 8 章使用 `v0.3.0` 的版本化 `BTCUSDT` bracket fixture：
`snapshot_ts=1700000000000`，不是現行 Binance 帳戶規則。真實研究必須保存
symbol、snapshot time、每檔 floor／cap、MMR、maint amount 與最大初始槓桿。

## Cross 與 Isolated

| 模式 | 可承擔資金 | 其他部位如何影響 |
|---|---|---|
| Cross | cross wallet balance 加所有 Cross 未實現 PnL | 其他部位的 PnL 與維持保證金都進風險邊界 |
| Isolated | 指派給該部位的 isolated margin 加該部位 PnL | 不帶入其他部位 PnL／維持保證金 |

Binance Academy 的
[Cross／Isolated 說明](https://www.binance.com/en/academy/articles/what-are-isolated-margin-and-cross-margin-in-crypto-trading)
於本次查證標示 2026-05-07 更新；USDⓈ-M 官方
[變更保證金模式文件](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type)
使用 `ISOLATED`／`CROSSED` 欄位。本文只核對名詞，不呼叫私人端點，也不表示
`v0.3.0` 已提供讀者帳戶設定入口。

## 強平邊界

固定 bracket 下，Emmet `v0.3.0` 的單向持倉邊界為：

\\[
p\_{\mathrm{liq}}
=\frac{W-\mathrm{TMM}\_{\mathrm{other}}+U\_{\mathrm{other}}+A
       -Qp\_{\mathrm{entry}}}
      {|Q|\mathrm{MMR}-Q}
\\]

Isolated 以該部位 isolated margin 取代 wallet，other MM／UPNL 都為零。Binance
[強平價格公式 FAQ](https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93)
也分列 wallet balance、其他部位維持保證金與未實現 PnL；官方
[Position Information V3](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Position-Information-V3)
分列 `markPrice`、`liquidationPrice`、`isolatedMargin`、`notional`、
`initialMargin` 與 `maintMargin`。

`p_liq` 是模型風險邊界，不是保證成交價。缺少或過期 mark、分層超界、身分錯配、
非正 margin balance 或非正候選價格都必須 fail closed；ADL、保險基金、下架與
實際清算成交不由本附錄公式涵蓋。

## Funding 入帳後的清算因果

第 20 章使用 `v0.3.0` Phase 3 golden 核對同一 funding cause 內的最小順序：

```text
FundingDue evidence
  → FundingPostedEvent：唯一帳本入帳並更新 mark
  → risk re-evaluation
  → canonical cancel：釋放受清算影響的 active reservations
  → LiquidationEvent：唯一帳本套用強平腿、已實現 PnL 與 fee
```

同一 frame timestamp 的 effects 仍以單調 `effect_seq` 分出先後。Cross 清算只包含
所有非零 Cross positions；Isolated position 逐倉判斷，不能因同帳戶而混入 Cross leg。
強平費以 `qty × liquidation execution price × fee rate` 核對，execution price 不可
偷換成 mark basis。這是固定 harness 的本地模擬語義，不是交易所 user stream、真實
清算成交、保險基金或 ADL 證據。

## 期現基差與 funding 案例邊界

第 9 章定義 `basis = perpetual reference price - spot reference price`。同一 base
數量的現貨多頭與永續空頭可能從正基差收斂取得價格損益，也可能因基差擴大虧損：

\\[
\mathrm{PnL}\_{\mathrm{portfolio,net}}
=\mathrm{PnL}\_{\mathrm{spot,price}}
+\mathrm{PnL}\_{\mathrm{perp,price}}
+\sum\_i\mathrm{CF}\_{\mathrm{funding},i}
-\sum\_jF\_j
\\]

spread／slippage 若已包含在 fill price，就只作歸因。研究至少保存兩市場 symbol、
價格來源與時點、每腿 fill、signed quantity、fees、funding time、rate、settlement
mark、雙錢包與 `Q_net`。funding history 分列 `fundingRate`、`fundingTime` 與
`markPrice`；funding info 另列 `fundingIntervalHours`，不能把本期外推到未來。

`Q_net=0` 不消除 basis、兩腿執行、funding、流動性、保證金與強平風險。Phase 4
多腿協調尚未在本基線發布。

## 永續網格產品邊界

第 9 章網格是固定成交序列，用 signed quantity、average entry、wallet、
realized／unrealized PnL 與 fee 建立最小帳本。它不表示 LIMIT／maker 保證成交，
也不表示 `v0.3.0` 已交付網格策略。至少還要檢查：

- 單邊趨勢下累積的 inventory 與 average entry；
- open orders 全數成交時的 worst inventory／margin／liquidation exposure；
- mark、funding、fees、流動性、部分成交、跳價與取消回報；
- wallet 加 unrealized PnL 後的 equity，而不是只看 realized PnL 或勝率。

## 使用邊界

- 本頁不提供 API key、私人帳戶端點、模式切換、下單或自動劃轉操作。
- 本頁只到第 7–9 章的槓桿、Cross／Isolated、初始／維持保證金、模型強平邊界、
  期現／網格案例最小風險查表，以及第 20 章直接需要的 funding→撤單→清算因果；
  ADL、保險基金、下架、多資產／Portfolio Margin 與完整交易所清算不在範圍。
- 本頁不提供多腿協調、網格策略、績效報告或自動處置入口；第 9 章只有固定手算與
  風險圖。
- 外部文件與交易所規則可能改變；發布前必須重新查證第一手來源。
- 本頁不提供會計、稅務或法律上的個別意見。
