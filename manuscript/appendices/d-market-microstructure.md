# D. 市場微結構

本附錄目前只收錄[第 6 章](../chapters/06-costs-breakeven.md)、
[第 9 章](../chapters/09-two-strategy-risk-maps.md)、
[第 16 章](../chapters/16-precise-values-valid-orders.md)與
[第 17 章](../chapters/17-order-intent-lifecycle-reports.md)直接需要的 bid／ask、spread、
maker／taker、滑點、流動性、容量、多腿不同步、網格庫存風險、版本化交易規則、
合法訂單 gate，以及訂單意圖／回報／終態的語義邊界。訂單簿排隊、市場衝擊，以及
bar、aggTrades、tick、order-book 模型的完整資訊差異，會隨對應 active 正文補寫。

正文的成本雙扣防線與 no-go 判斷必須自足；本頁只作語義與證據邊界查表。

## Bid、ask、spread 與 mid

對同一標的、同一時點與同一訂單簿：

- bid 是買方目前願意出的價格；最佳 bid `b` 是最高買價；
- ask 是賣方目前願意接受的價格；最佳 ask `a` 是最低賣價；
- spread 為 `a-b`；
- 中間價 `m=(a+b)/2` 是參考估值，不是可成交保證。

Binance 官方 Spot API 固定文件的 order-book schema 分列 `bids` 與 `asks`。這
支持欄位方向，不支持文件範例價格、當下深度或某個數量一定成交。使用資料時
仍要保存 symbol、取得時點、更新序號與來源版本。

## Maker 與 taker

| 分類 | 本附錄最小語義 | 不能推論 |
|---|---|---|
| maker | 該方委託先留在簿上並提供流動性，之後才被撮合 | 一定成交、一定較便宜、一定獲利 |
| taker | 該方委託立即與簿上既有流動性撮合 | 一定整筆成交、滑點固定 |

maker／taker 是成交證據，不只是下單意圖。Post-only／`LIMIT_MAKER` 類意圖可用來
避免立即成為 taker；若會立即撮合，官方語義可能拒絕該單。但「沒有立即成交」
仍不等於之後一定成交，所以不能在沒有 fill classification 時先套 maker 費率。

費率可能依帳戶、symbol、折扣、稅費與成交分類而異。本附錄不保存任何現行費率；
第 6 章的 `0.001`、`0.0002` 都是明示教學輸入。

## Spread 與滑點的分層

在第 6 章的簡化情境中：

\\[
\begin{aligned}
K\_{\mathrm{price,buy}}
  &=(a-m)+(p\_{\mathrm{buy}}-a) \\\\
K\_{\mathrm{price,sell}}
  &=(m-b)+(b-p\_{\mathrm{sell}})
\end{aligned}
\\]

乘上成交數量後才得到 quote 金額。第一段是半邊 spread，第二段是相對最佳報價的
不利滑點。

這個分解要求參考報價與成交具有可比較的標的、方向、時點和數量。若成交跨多檔、
報價在委託途中更新，或資料只有 bar，就不能假裝擁有逐筆最佳報價；應把模型假設
明列在結果中。

最重要的入帳邊界是：

- 用 mid 算理想毛損益時，可扣 spread 與滑點走到 fill PnL；
- 已用實際 fill 算毛損益時，spread 與滑點只作歸因，不再扣現金；
- 手續費是另外的資產流，仍須記 fee asset。

## 流動性不是一個布林值

流動性至少與下列輸入有關：

- symbol、買賣方向與時點；
- 價格範圍與每檔可見數量；
- 委託大小、延遲、排隊位置與其他參與者；
- 可見深度之外的撤單、補單與市場衝擊；
- 資料是 snapshot、逐筆事件、聚合成交或 bar。

因此「市場有流動性」不足以接受審核。最低紀錄應改寫成：「在某個版本化資料
時點、某個價格範圍與明示成交模型下，規劃數量是否低於研究上限。」

## 容量是策略與成本共同的限制

容量不是交易所提供的一個永久欄位。它問的是：當數量增加時，spread、滑點、
費用、成交率、持有風險與淨期望值仍可接受到哪裡。

第 6 章的

\\[
q\_{\mathrm{capacity}}
=D\_{\mathrm{scenario}}\times c\_{\mathrm{participation}}
\\]

只是壓力情境，不是市場衝擊模型。它有一個重要用途：當規劃數量超過明示上限時
先 no-go，避免把缺少的成交證據補成樂觀假設。要把它升級成真實容量結論，至少
需要版本化深度資料、規模階梯、成交模型、成本敏感度與失效條件。

## 規則快照與合法訂單 gate

交易所規則不是「價格保留幾位小數」的一句設定。最小 symbol 快照要把 market、symbol、
status、base／quote／margin asset、`snapshot_ts` 與下列欄位一起版本化：

| 欄位 | 核對問題 | 不能替代 |
|---|---|---|
| `minPrice`／`maxPrice`／`tickSize` | 限價是否在範圍內並由正確基準對齊 | 當下可成交價格 |
| `minQty`／`maxQty`／`stepSize` | 數量是否在範圍內並由正確基準對齊 | 市價單專用 `MARKET_LOT_SIZE` |
| `MIN_NOTIONAL` | 使用明示價格角色計算的名義額是否足夠 | 可用資金、保證金或風控上限 |
| `MAX_NUM_ORDERS` | 下一張單是否超過該 symbol 掛單數上限 | API rate limit、帳戶總單數或策略限頻 |
| leverage brackets | 名義額落在哪個 `[floor, cap)`，最大初始槓桿是多少 | 已切換帳戶槓桿或已有足夠 margin |

正式核對以 Decimal 進行：

\\[
\begin{aligned}
(p-p\_{\min}) \bmod \Delta p &= 0 \\\\
(q-q\_{\min}) \bmod \Delta q &= 0 \\\\
N &= p\times q
\end{aligned}
\\]

限價單的 `p` 是委託價；市價單沒有委託價，必須明示規則要求的參考價格。價格、數量與
名義額是三道不同 gate；前兩項通過不能推出第三項通過。

拒絕時保存原始 price／qty 字串與 filter 原因，不以 floor、round、`quantize` 或字串裁切
在原訂單上「修正」。若人或策略接受另一組值，應建立新意圖並重新跑全部 gate。這能把
「原單被拒」與「新單通過」保留成兩個可審核事實。

名義分層下界包含、上界不包含。分層表還必須綁定 symbol、快照時點與來源；交易所或
帳戶調整後，舊表只能支持舊證據，不能自動升格成目前帳戶規則。

## 訂單意圖、回報與終態

委託被建立、被接受與被成交是三個不同事實。最小生命週期查表如下：

| 名稱 | 本附錄最小語義 | 稽核時另需保存 |
|---|---|---|
| intent | 策略或人提出 type、TIF、price、qty 與限制條件 | decision ID、原始 Decimal 字串、提出時間 |
| accepted／`NEW` | 撮合引擎或本地 execution 接受該 order ID | environment、source、client／venue order ID |
| `PARTIALLY_FILLED` | 一部分已成交，剩餘量仍未成為終態 | cumulative／last fill、price、fee、event time |
| `FILLED` | 訂單要求數量已完成 | 完整成交與會計核對；不能只看 status |
| `CANCELED` | 有效剩餘量已取消 | cancel request 與確認時間；既有 fill 不消失 |
| `REJECTED` | 提交或本地授權未接受 | reason 與來源；不能冒充另一環境的錯誤碼 |
| `EXPIRED` | 依訂單規則或 venue／模型條件失效 | order type／TIF、原因與判定證據 |

`MARKET`／`LIMIT` 是 order type；`GTC`／`GTX` 是 time in force；post-only 與
reduce-only 是限制條件。`LIMIT` 不等於 maker，post-only 也不保證未來成交。

兩筆 `PARTIALLY_FILLED` 可以都是合法新事件，所以去重不能只比較 status。至少同時
核對 order identity、event time、cumulative filled quantity 與來源事件身分。終態不應
再轉出；遇到終態後 fill、累計量倒退或身分不符時，先保存衝突並 fail closed，不以
「最後到達者勝出」改寫歷史。

## 多腿不同步：數量相等也可能只是一張計畫

期現兩張委託分屬不同市場。`Q_spot + Q_perp = 0` 必須使用實際成交後的帶方向
數量與相符時間，不能使用 intended quantity。至少分開保存每腿 order intent、
ack、fill quantity／price／time、fee、終態、殘留 `Q_net` 與當時深度。

一腿成交而另一腿尚未完成時，basis 與共同方向曝險都可能改變。`v0.3.0` 尚未
發布 Phase 4 多腿協調入口；第 9 章只留下固定失衡 oracle。

## 網格庫存與剩餘掛單

網格 LIMIT／maker 意圖只有在 fill 後才改變 inventory、平均成本、wallet 與
realized PnL。未成交掛單不可以先記收益，但也不能從風險報告消失：

- 下跌時多張買單可能依序成交，使同方向庫存與保證金需求一起增加；
- 價格跳過格位、深度不足或排隊落後時，預期賣單可能沒有成交；
- 已完成循環勝率不含 open inventory 的 unrealized PnL；
- worst-open-order exposure 應假設同方向掛單成交，重算 inventory、費用、權益、
  margin 與 liquidation 緩衝。

## 第一手來源與固定邊界

本 slice 固定查閱 Binance 官方 Spot API 文件 commit `4987e707` 的
[`rest-api.md`](https://github.com/binance/binance-spot-api-docs/blob/4987e707f84f20d736ee6a2bcb71396111cffee1/rest-api.md)、
[`enums.md`](https://github.com/binance/binance-spot-api-docs/blob/4987e707f84f20d736ee6a2bcb71396111cffee1/enums.md)與
[`user-data-stream.md`](https://github.com/binance/binance-spot-api-docs/blob/4987e707f84f20d736ee6a2bcb71396111cffee1/user-data-stream.md)：

- order-book 回應包含 `bids` 與 `asks`；
- commission schema 分列 `maker` 與 `taker`；
- `LIMIT_MAKER` 是不立即以 taker 成交的訂單語義。
- order status 分列 `NEW`、`PARTIALLY_FILLED`、`FILLED`、`CANCELED`、
  `REJECTED` 與 `EXPIRED`；
- `executionReport` 分開 current execution type 與 current order status。

固定 commit 讓作者可以重驗欄位語義，但不讓舊文件變成現在的市場事實。出版前
仍須重新查證 API、費率與交易所規則；本附錄不呼叫私人端點，也不保存帳戶資料。

## 適用邊界

- 本頁沒有建立 live order-book、排隊或市場衝擊模型。
- maker 意圖、限價與 post-only 都不構成成交保證。
- bar volume cap 不是即時深度，單一 snapshot 也不是容量證明。
- 實際成交若優於參考價，應保存 price improvement 的方向，不能用絕對值改寫。
- 本頁新增的規則查表只補足第 16 章合法性稽核；它不證明資金預留、訂單已送出、
  交易所接受或成交。
- 本頁的生命週期查表只補足第 17 章意圖、回報與終態判讀；不發布 live user stream、
  重播去重、斷線回補或交易所對帳入口。
- 完整撮合假設、資金預留與逐筆會計仍留待後續 active 正文。
