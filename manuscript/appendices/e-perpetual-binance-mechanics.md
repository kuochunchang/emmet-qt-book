# E. 永續合約與 Binance 市場機制

本附錄目前只收錄第 7 章直接需要的 U 本位線性永續、單向持倉、資金費與
現貨／期貨雙錢包查表。最後查證日期為 2026-07-15。完整手算、驗收 oracle 與
系統邊界見[第 7 章](../chapters/07-perpetual-dual-wallet-funding.md)；本頁不提供
交易所帳戶設定或下單步驟。

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

```text
funding_cash_flow = -signed_qty × settlement_mark_price × funding_rate
```

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

```text
W_after = W_before + funding_cash_flow
E_futures = W_after + U
          = W_before + funding_cash_flow + U
```

`W_after` 已含資金費，因此不能再加一次。合約名義價值只是曝險規模，也不能加進
權益。

## 使用邊界

- 本頁不提供 API key、私人帳戶端點、模式切換、下單或自動劃轉操作。
- 槓桿、Cross／Isolated、初始／維持保證金、強制平倉、ADL 與下架不在本附錄
  目前範圍；不能從本頁公式推測其系統行為。
- 基差、多腿對沖、策略收益圖與績效結論不在本附錄目前範圍。
- 外部文件與交易所規則可能改變；發布前必須重新查證第一手來源。
- 本頁不提供會計、稅務或法律上的個別意見。
