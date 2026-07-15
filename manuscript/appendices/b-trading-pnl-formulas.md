# B. 交易損益與衍生品公式

本附錄目前只收錄第 5–6 章直接需要的現貨名義價值、部位、已實現／未實現損益、
標記權益、交易成本、換手率與損益兩平公式。永續、槓桿、保證金、強平、資金費
與多腿淨敞口會隨對應 active 正文補寫；尚未出現的公式不應由讀者自行推測成
系統行為。

正文的安全判斷與完整案例見[第 5 章](../chapters/05-spot-trade-ledger.md)與
[第 6 章](../chapters/06-costs-breakeven.md)。本頁是符號、單位與適用前提的延伸
查表，不能取代逐格資產流與成本稽核。

## 符號與單位

| 符號 | 意義 | 單位 | 符號規則 |
|---|---|---|---|
| `q` | 單筆成交數量的絕對值 | base asset | `q > 0`；不以負數表示賣出 |
| `s` | 成交方向 | 無單位 | 買入 `+1`，賣出 `-1` |
| `ΔQ` | 成交造成的部位變化 | base asset | `ΔQ = s × q` |
| `Q` | 成交後累積部位／餘額曝險 | base asset | 無借貸現貨案例要求 `Q >= 0` |
| `p_buy` | 買入成交價 | quote/base | 必須為正 |
| `p_sell` | 賣出成交價 | quote/base | 必須為正 |
| `m` | 指定時點的重估價 | quote/base | 是估值輸入，不保證可成交 |
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

## 適用邊界

- 期望值、勝率、賠率與容量查表見附錄 C；本附錄不把績效統計塞進資產流公式。
- maker／taker、bid／ask、滑點與容量的市場語義查表見附錄 D。
- 本頁的費率與計費資產是明示情境輸入，不代表任何帳戶的現行交易所費率。
- 本頁仍不包含永續、槓桿、保證金、強平、資金費、多腿或稅務成本。
- `m` 是現貨估值輸入，不等於永續合約標記價格，也不保證可成交。
- signed quantity 是方向記法，不提供借貸、槓桿或放空權限。
- 標記權益依估值價格改變；「資產流守恆」不表示權益數字固定。
- 報價資產名稱不代表法幣存款、固定匯率或無風險資產。
- 本附錄不提供會計、稅務或法律上的個別意見。
