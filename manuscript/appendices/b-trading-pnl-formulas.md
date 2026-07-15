# B. 交易損益與衍生品公式

本附錄目前只收錄第 5–8 章直接需要的現貨名義價值、部位、已實現／未實現損益、
標記權益、交易成本、換手率與損益兩平公式，以及 U 本位線性永續的 signed
position、名義價值、未實現損益、資金費現金流、槓桿、保證金與強平邊界。多腿
淨敞口會隨對應 active 正文補寫；尚未出現的公式不應由讀者自行推測成系統行為。

正文的安全判斷與完整案例見[第 5 章](../chapters/05-spot-trade-ledger.md)、
[第 6 章](../chapters/06-costs-breakeven.md)與
[第 7 章](../chapters/07-perpetual-dual-wallet-funding.md)，以及
[第 8 章](../chapters/08-leverage-margin-liquidation.md)。本頁是符號、單位與
適用前提的延伸查表，不能取代逐格資產流、成本稽核、雙錢包或強平邊界核對。

## 符號與單位

| 符號 | 意義 | 單位 | 符號規則 |
|---|---|---|---|
| `q` | 單筆成交數量的絕對值 | base asset | `q > 0`；不以負數表示賣出 |
| `s` | 成交方向 | 無單位 | 買入 `+1`，賣出 `-1` |
| `ΔQ` | 成交造成的部位變化 | base asset | `ΔQ = s × q` |
| `Q` | 成交後累積部位／餘額曝險 | base asset | 無借貸現貨案例要求 `Q >= 0` |
| `Q_perp` | U 本位線性永續的帶方向合約數量；第 7 章正文簡寫為 `Q` | base asset | 多頭 `> 0`、空頭 `< 0`、零持倉 `= 0` |
| `p_buy` | 買入成交價 | quote/base | 必須為正 |
| `p_sell` | 賣出成交價 | quote/base | 必須為正 |
| `m` | 指定時點的重估價 | quote/base | 是估值輸入，不保證可成交 |
| `p_entry,perp` | 永續部位的開倉均價 | quote/base | 必須為正 |
| `m_f` | 資金費結算時點的永續標記價格 | quote/base | 必須為正，且時點須與該次結算相符 |
| `r_f` | 單次資金費率 | 無單位 | 十進位帶正負號比例；方向不可省略 |
| `CF_funding` | 從帳戶觀點記錄的資金費現金流 | quote asset | 收取為正、支付為負 |
| `W_before`、`W_after` | 資金費入帳前／後的期貨 wallet balance | quote asset | 不包含未實現 PnL |
| `E_futures` | 資金費入帳並按 `m_f` 重估後的期貨權益核對值 | quote asset | `W_after + U_perp` |
| `N_perp` | 永續合約名義價值 | quote asset | `abs(Q_perp) × m_f`，一律非負 |
| `U_perp` | 永續合約價格未實現損益 | quote asset | `(m_f - p_entry,perp) × Q_perp` |
| `C` | 報價資產現金餘額 | quote asset | 與 base 餘額分開保存 |
| `N` | 成交名義價值 | quote asset | 一律非負 |
| `V` | 部位重估價值 | quote asset | 依 `m` 改變 |
| `U` | 未實現損益 | quote asset | 尚未處分部位的估值差 |
| `R` | 已實現損益 | quote asset | 已處分且成本基礎已知的差額 |
| `E` | 標記權益 | quote asset | 現金加按 `m` 換算的部位價值 |
| `b`、`a` | 同一時點的最佳 bid、ask | quote/base | 本附錄情境要求 `a >= b > 0` |
| `r_buy`、`r_sell` | 買入、賣出費率 | 無單位 | 十進位比例且 `0 <= r < 1` |
| `F` | 手續費 | 明示的 fee asset | 不可只記金額而漏掉資產 |
| `q_net` | 扣除 base 計價買入費後可賣數量 | base asset | `q_net = q × (1-r_buy)` |
| `K` | 一個完整往返的總成本 | quote asset | 成本歸因不可和現金流重複扣除 |
| `T` | 本附錄定義的單輪換手率 | 無單位 | 分母必須明示為初始權益 |
| `L` | 永續部位選定槓桿 | 無單位 | 正數；不直接乘進 PnL |
| `m_risk` | 風險重估用標記價格 | quote/base | 必須帶時點、為正且未過期 |
| `IM_position` | Emmet 持倉初始保證金 target | quote asset | `abs(Q_perp) × p_entry,perp / L` |
| `MMR` | 當前名義價值檔位的維持保證金率 | 無單位 | 必須綁定 symbol 與分層快照 |
| `A` | 維持保證金速算額（maint amount／cum） | quote asset | 由同一檔位提供 |
| `MM` | 維持保證金 | quote asset | `N_perp × MMR - A` |
| `MB` | 保證金餘額 | quote asset | Cross／Isolated 的分子集合不同 |
| `MR` | 保證金率 | 無單位 | `MM / MB`；`MB <= 0` 時未定義 |
| `p_liq` | 模型強平價格邊界 | quote/base | 必須為正且通過檔位／身分核對 |

`quote/base` 是單位比值。例如 `USDT/BTC × BTC = USDT`。若乘法後單位無法消成
報價資產，通常代表交易對或公式方向讀反。

## 名義價值與部位更新

```text
N = p × q                       [quote]
ΔQ = s × q                      [base]
Q_after = Q_before + ΔQ         [base]
```

買賣兩邊的 `N` 都是正數；方向由 `s`、`ΔQ` 與現金流表達。對沒有借貸的現貨帳戶，
若更新後 `Q_after < 0`，應拒絕案例，不得直接解釋成空頭部位。

## 零成本下的現金流

買入：

```text
C_after = C_before - p_buy × q  [quote]
```

賣出：

```text
C_after = C_before + p_sell × q [quote]
```

這兩式只適用於手續費、spread、滑點、借貸利息、稅務與外部入出金皆明確為零的
教學案例。任一項存在時，必須新增獨立現金流欄位，不得暗中塞進價格或數量。

## 部位價值、未實現損益與權益

對單一買入批次、尚未賣出的多頭現貨：

```text
V = Q × m                       [quote]
U = (m - p_buy) × Q             [quote]
E = C + V                       [quote]
```

`V` 是全部部位按 `m` 換算的價值；`U` 只取相對成本的差。因為 `V` 已經包含成本與
估值變化，計算 `E` 時不能再把 `U` 加一次。

## 全部賣出時的已實現損益

對同一買入批次、賣出數量恰等於買入數量、零成本且沒有其他現金流的案例：

```text
R = (p_sell - p_buy) × q        [quote]
Q_final = 0                     [base]
U_final = 0                     [quote]
E_final = C_final = E_initial + R
```

多批買入、部分賣出或資產轉入時，必須先指定成本基礎與批次歸屬；本式不能自行
決定加權平均、FIFO 或任何稅務認定。

## 兩組最小核對式

在零成本、無外部入出金的完整買入再賣出案例中：

```text
Q_final = Q_initial + Σ(buy q) - Σ(sell q)
C_final = C_initial - Σ(buy N) + Σ(sell N)
```

對只有一個尚未處分批次的持有時點：

```text
E_marked = E_initial + U
```

全部處分後：

```text
E_final = E_initial + R
```

第一組核對資產流，第二組核對損益分類。兩組必須同時成立；其中一組通過不能
抵銷另一組的差異。

## Spread、滑點與實際成交價

對固定同一時點的最佳 bid `b` 與最佳 ask `a`：

```text
spread S = a - b                         [quote/base]
mid m = (a + b) / 2                      [quote/base]
```

在第 6 章的非負不利滑點情境中：

```text
買入半邊 spread 成本 = (a - m) × q      [quote]
買入滑點成本 = (p_buy - a) × q          [quote]

賣出半邊 spread 成本 = (m - b) × q      [quote]
賣出滑點成本 = (b - p_sell) × q         [quote]
```

這裡的 `m`、`a`、`b` 必須屬於各自進場或出場時點，不能把不同時點的報價拼成
一個 spread。若成交優於參考報價，保留帶方向的 price improvement；不要取絕對值
把改善偽裝成成本。

若毛損益已直接使用 `p_buy` 與 `p_sell`，上述價格摩擦已包含在成交價，不能再
扣一次。只有從參考中間價毛損益開始時，才以 spread／滑點歸因走到成交價毛損益：

```text
參考中間價毛損益 - spread 成本 - 滑點成本
= 實際成交價毛損益
```

## 手續費與計費資產

費率以成交名義價值計算且費用扣 quote 時：

```text
F_quote = p × q × r                       [quote]
```

第 6 章買入費用扣 base 的情境則是：

```text
F_buy,base = q × r_buy                    [base]
q_net = q - F_buy,base
      = q × (1 - r_buy)                   [base]
```

全部賣出 `q_net`，且賣出費用扣 quote：

```text
N_exit = q_net × p_sell                   [quote]
F_sell,quote = N_exit × r_sell            [quote]
C_final = C_initial - q × p_buy
          + N_exit - F_sell,quote          [quote]
```

若要把 base 費用納入最終 quote PnL 歸因，必須明示換算價格。第 6 章使用實際出場價：

```text
F_buy,quote-at-exit = F_buy,base × p_sell [quote]
```

同一筆 base 費不能又以進場價、又以出場價各扣一次。不同換算價回答不同估值問題，
不是兩筆費用。

## 成本分解與現金差額 oracle

令 `G_mid` 為進出場參考中間價算出的理想毛損益、`G_fill` 為實際成交價毛損益：

```text
G_mid - K_spread - K_slippage = G_fill
G_fill - K_fee = PnL_net
PnL_net = C_final - C_initial             （全部平倉且無其他現金流）
```

因此：

```text
K = K_spread + K_slippage + K_fee
PnL_net = G_mid - K
```

三條等式必須同時成立。歸因表只解釋已發生在哪一層的成本，不新增第二份現金流。

## 損益兩平成交價

對第 6 章「買入費扣 base、賣出費扣 quote、全部賣出」的固定情境：

```text
q × (1-r_buy) × p_exit × (1-r_sell) = q × p_buy

p_exit,BE = p_buy / ((1-r_buy) × (1-r_sell))
```

`p_exit,BE` 是實際出場成交價門檻。若研究問題要的是參考中間價門檻，還要加回
明示的出場半邊 spread 與預期滑點；若數量會改變滑點，就不能把它們當常數。

## 單輪換手率

第 6 章明定：

```text
T = (N_entry + N_exit) / E_initial
```

分子同時計入買賣兩邊的實際成交名義價值，分母是本輪開始時的 quote 權益。其他
報告若採單邊名義價值、平均權益或日均資產，必須改名或明示定義，不能直接比較。

## U 本位線性永續：signed position、名義價值與資金費

本節只適用於第 7 章的單向持倉 U 本位線性永續。`Q_perp` 以 base asset 表示
合約數量，價格、名義價值、損益與期貨錢包則以 quote asset 表示：

```text
N_perp = abs(Q_perp) × m_f                         [quote]
U_perp = (m_f - p_entry,perp) × Q_perp            [quote]
CF_funding = -Q_perp × m_f × r_f                  [quote]
```

名義價值一律非負；方向只保留在 `Q_perp`、價格損益與資金費現金流。以帳戶觀點
判讀資金費時：

| 條件 | `CF_funding` 的方向 |
|---|---|
| `Q_perp > 0` 且 `r_f > 0` | `< 0`，多頭支付 |
| `Q_perp < 0` 且 `r_f > 0` | `> 0`，空頭收取 |
| `Q_perp = 0` 或 `r_f = 0` | `= 0` |

負費率時多空方向反轉。每筆紀錄必須保存 funding time、`r_f` 與相符的 `m_f`；
不能把某一期的費率或固定八小時間隔外推到下一期。

資金費是期貨錢包的一筆獨立現金流，不是現貨／期貨 transfer。令 `U_perp` 已用
同一結算標記價格重估：

```text
W_after = W_before + CF_funding                    [quote]
E_futures = W_after + U_perp
          = W_before + CF_funding + U_perp         [quote]
```

`W_after` 已包含資金費，不能再加一次 `CF_funding`；`N_perp` 只是曝險規模，也
不能加進權益。產品模式與雙錢包最小查表見
[附錄 E](e-perpetual-binance-mechanics.md)。

## U 本位線性永續：槓桿、保證金與強平邊界

本節只對齊第 8 章與 Emmet `v0.3.0` 的固定單向持倉模型。以帶方向數量
`Q_perp`、開倉均價 `p_entry,perp` 與當前標記價格 `m_risk` 定義：

```text
N_perp = abs(Q_perp) × m_risk
U_perp = (m_risk - p_entry,perp) × Q_perp
IM_position = abs(Q_perp) × p_entry,perp / L
MM = N_perp × MMR - A
```

`IM_position` 是 Emmet 的持倉 target；訂單 admission 還可能需要 reservation、
open loss 與費用，不能把這一式冒充完整開倉成本。`Q_perp`、entry 與 mark 不變
時，改 `L` 只改 target，不改 `N_perp` 或 `U_perp`。

Isolated 與 Cross 的保證金餘額為：

```text
MB_isolated = isolated_margin + U_this
MB_cross = cross_wallet_balance + Σ U_cross
MR = Σ MM / MB                         （只在 MB > 0 時定義）
```

`MB <= 0` 時 `MR` 未定義，必須 fail closed，不能讓負比率進入正常門檻比較。

令 `TMM_other`、`U_other` 分別為其他 Cross 部位的維持保證金與未實現 PnL：

```text
p_liq =
  (W - TMM_other + U_other + A - Q_perp × p_entry,perp)
  / (abs(Q_perp) × MMR - Q_perp)
```

Cross 的 `W` 是 cross wallet balance；Isolated 則以該部位 isolated margin 代入，
並令 `TMM_other=0`、`U_other=0`。有效答案還要滿足：

```text
Q_perp != 0
p_liq > 0
MB(p_liq) = total MM(p_liq)
symbol、market、mark 時點與分層表身分相符
```

`v0.3.0` 以當前 mark notional 選檔，不做候選強平價跨檔 fixed-point 迭代。第 8 章
固定案例的候選價仍在同一第一檔；其他案例若跨檔，必須揭露模型限制，不能把公式
外推成交易所精確邊界。所有等式用未捨入 Decimal 核對，顯示值才套用捨入。

多頭可用 `(m_risk-p_liq)/m_risk`、空頭可用
`(p_liq-m_risk)/m_risk` 記錄當前價格緩衝，但這只是固定模型下的距離，不是最大
回撤、成交滑點或 risk-of-ruin 機率。

## 適用邊界

- 期望值、勝率、賠率與容量查表見附錄 C；本附錄不把績效統計塞進資產流公式。
- maker／taker、bid／ask、滑點與容量的市場語義查表見附錄 D。
- 本頁的費率與計費資產是明示情境輸入，不代表任何帳戶的現行交易所費率。
- 本頁的永續範圍只到第 8 章公式；仍不包含 ADL、下架、多腿、多資產模式、
  保險基金結算、完整訂單成本或稅務成本。
- `m` 是現貨估值輸入；`m_f` 是指定 funding time 的永續標記價格。兩者都不保證
  可以成交，也不能互相偷換。
- 現貨 `Q` 與永續 `Q_perp` 的符號前提不同；方向記法本身不提供借貸、槓桿、
  放空、帳戶模式切換或下單權限。
- `p_liq` 是版本固定模型的風險邊界，不是保證成交價或交易所最終清算帳單。
- 標記權益依估值價格改變；「資產流守恆」不表示權益數字固定。
- 報價資產名稱不代表法幣存款、固定匯率或無風險資產。
- 本附錄不提供會計、稅務或法律上的個別意見。
