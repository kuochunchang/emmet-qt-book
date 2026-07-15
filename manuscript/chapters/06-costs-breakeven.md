# 成本決定策略能不能活下來

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：穩定概念
> 最後驗證日期：2026-07-15

## 學習目標

讀完本章後，你應該能夠：

- 分開計算 maker／taker 手續費、bid／ask spread 與滑點，不把同一成本扣兩次；
- 逐格核對一筆現貨往返的名義價值、計費資產、最終現金與淨損益；
- 算出明示成本假設下的損益兩平成交價與參考價格變動門檻；
- 由勝率、賠率與每輪成本計算毛期望值和淨期望值；
- 把換手率、流動性與容量當成 go／no-go 輸入，而不是事後解釋。

## 問題情境：看對方向，也可能不值得交易

第 5 章的零成本案例從 `20,000` 買進、`20,800` 賣出，能建立資產流與損益的
共同語言。但策略真正要活下來，不能只問「賣出價有沒有高於買入價」。你還需要
回答：

- 決策時看到的是中間價、bid、ask，還是最後成交價？
- 買賣各自是 maker 還是 taker，費率是多少，費用扣哪一項資產？
- 成交價已經包含多少 spread 與滑點？是否又在 PnL 後面重扣一次？
- 一輪看似很小的成本，經過高換手後會累積多少？
- 勝率很高時，每次獲利與虧損扣完成本後還剩多少？
- 委託數量超過明示的流動性情境時，是否應直接 no-go？

本章用固定教學輸入回答這些問題，不下載行情、不查私人帳戶，也不宣稱任何
數字是 Binance 或其他交易所的現行費率、深度或可成交價格。具體費率與深度會
依帳戶、標的、時間和市場狀態改變；若要用於真實決定，必須在相符時點重新取得
第一手資料。

## 執行前預測

先看以下往返交易的摘要；暫時不要往下算：

| 輸入 | 進場 | 出場 |
|---|---:|---:|
| 參考中間價 | `20,000.00 USDT/BTC` | `20,200.00 USDT/BTC` |
| 最佳報價 | ask `20,010.00` | bid `20,190.00` |
| 實際成交價 | 買入 `20,020.00` | 賣出 `20,180.00` |
| 流動性分類 | taker | taker |
| 教學費率 | `0.001` | `0.001` |
| 毛買入數量 | `0.50000000 BTC` | 賣出扣除進場費後的全部 BTC |

初始資產為 `15,000.00 USDT` 與 `0 BTC`。本案例明定買入手續費以 BTC 扣除，
賣出手續費以 USDT 扣除；所有數值先保留 `Decimal` 精度，不在中途四捨五入。

請先預測：

1. 參考中間價從 `20,000` 升到 `20,200`，理想毛損益是多少？
2. 買入後實際收到多少 BTC？最後能賣多少 BTC？
3. spread、滑點與兩筆手續費各是多少？哪些已在成交價或資產流裡？
4. 最終 USDT 與淨損益是多少？
5. 如果每輪總成本不變，`55%` 勝率、毛利 `+120 USDT`、毛損 `-80 USDT`
   的策略，淨期望值是正還是負？

每個金額都要標示單位，每項費用都要標示計費資產。只寫一個「成本百分比」，
無法證明資產流正確，也無法抓出重複扣除。

## 核心概念：先決定每個數字在哪一層

### 參考價格、報價與成交價格

固定同一時點的最高買價 `b`（bid）與最低賣價 `a`（ask）後，可定義：

```text
spread S = a - b                         [quote/base]
中間價 m = (a + b) / 2                   [quote/base]
```

買方若立即取得流動性，通常從 ask 一側開始；賣方則從 bid 一側開始。實際成交價
還可能因可用數量、下單大小與成交路徑偏離最佳報價。本章把相對最佳報價的額外
不利差稱為滑點（slippage）：

```text
買入滑點成本 = (p_buy - ask) × q        [quote]
賣出滑點成本 = (bid - p_sell) × q       [quote]
```

這兩式只適用於本章的「不利滑點為非負」教學情境。若實際成交優於參考報價，
應保存帶方向的 price improvement，不要用 `abs` 抹掉方向。

Binance 官方 Spot API 固定文件的 order-book 回應分開提供 `bids` 與 `asks`，
commission schema 也分開提供 `maker` 與 `taker` 欄位；本章只用這些欄位語義，
不採用文件範例中的費率或價格作市場現況。固定來源為官方文件 commit
[`4987e707`](https://github.com/binance/binance-spot-api-docs/blob/4987e707f84f20d736ee6a2bcb71396111cffee1/rest-api.md)，
驗證命令與重驗條件另見中央台帳。

### 一條不可破壞的規則：價格內成本不再重扣

若 gross PnL 已用實際成交價計算：

```text
實際成交價毛損益 = (p_sell - p_buy) × q
```

那麼 spread 與滑點已經反映在 `p_buy`、`p_sell` 裡。它們可以另外列出做歸因，
卻不能再從這個毛損益扣一次。反過來，若從中間價的理想毛損益開始，就必須扣除
spread 與滑點，才能走到實際成交價毛損益。

兩條路必須得到同一結果：

```text
路徑 A：中間價理想毛損益 - spread 成本 - 滑點成本
路徑 B：直接使用實際買賣成交價計算毛損益
```

手續費若是獨立資產流，則在兩條路得到相同毛損益後再扣一次。這個分層就是本章
防止雙扣的主要 oracle。

### Maker／taker 是成交分類，不是獲利承諾

- maker 提供掛單簿上的流動性；
- taker 取得既有流動性；
- 費率函數必須同時知道成交分類與明示費率；
- 費用 `F = N × r` 只有在費率以名義價值計算、且計費資產是 quote 時才直接成立。

若買入費用以 base 扣除，本章改用：

```text
買入費用 F_buy,base = q_buy × r_buy     [base]
可賣數量 q_net = q_buy - F_buy,base      [base]
```

「我送了一張 maker 意圖的單」不等於「它一定成交」或「一定拿到 maker 費率」。
例如官方 `LIMIT_MAKER` 語義會拒絕立即成為 taker 的委託，但這仍不保證委託之後
會成交。本章實際現金 oracle 使用已觀察到的 taker 分類；maker 費率只放在獨立
敏感度列，不偷換實際路徑。

### 換手率一定要先寫分母

本章把單輪換手率定義為：

```text
T = (entry notional + exit notional) / initial equity
```

分子是兩邊實際成交名義價值，分母是本輪開始時的 USDT 權益。這是本章明定的
研究指標，不是唯一通用定義。若別份報告用平均權益、單邊名義價值或日均資產
作分母，數字不能直接比較。

## 固定案例：逐格建立成本表

### 輸入全部是教學情境

| 類別 | 輸入 | 單位／前提 |
|---|---:|---|
| 初始 USDT | `15,000.00` | USDT |
| 毛買入數量 | `0.50000000` | BTC |
| 進場 mid／ask／fill | `20,000.00／20,010.00／20,020.00` | USDT/BTC |
| 出場 mid／bid／fill | `20,200.00／20,190.00／20,180.00` | USDT/BTC |
| 進場 taker 費率 | `0.001` | 費用以 BTC 扣除 |
| 出場 taker 費率 | `0.001` | 費用以 USDT 扣除 |
| maker 比較費率 | `0.0002` | 只作敏感度，不代替實際成交分類 |
| 中途四捨五入 | 無 | 保留 `Decimal` 全精度 |

這不是即時訂單簿或帳戶費率。選用整齊數字是為了讓讀者逐格抓出雙扣、漏扣與
計費資產錯置；它不能支持「真實市場可以成交 `0.5 BTC`」的結論。

### 第一步：買入與進場費

```text
entry notional
= 0.50000000 BTC × 20,020.00 USDT/BTC
= 10,010.00 USDT

buy fee
= 0.50000000 BTC × 0.001
= 0.00050000 BTC

買入後 USDT = 15,000.00 - 10,010.00
             = 4,990.00 USDT

買入後可賣 BTC = 0.50000000 - 0.00050000
                = 0.49950000 BTC
```

進場費用不是從 USDT 再扣 `10.01`；本情境已明定它從收到的 BTC 扣除。若同時
減少 USDT 與 BTC，就把一筆費用扣了兩次。

### 第二步：賣出與出場費

```text
exit notional
= 0.49950000 BTC × 20,180.00 USDT/BTC
= 10,079.910000 USDT

sell taker fee
= 10,079.910000 USDT × 0.001
= 10.079910 USDT

最終 USDT
= 4,990.00 + 10,079.910000 - 10.079910
= 15,059.830090 USDT

最終 BTC = 0 BTC
淨損益 = 15,059.830090 - 15,000.00
       = 59.830090 USDT
```

### 第三步：把成本歸因，但不重扣

| 層 | 項目 | 算式 | 成本 | 已包含在哪裡？ |
|---|---|---|---:|---|
| 價格 | 進場半邊 spread | `(20,010-20,000)×0.5` | `5.000000 USDT` | 已在買入成交價 |
| 價格 | 進場滑點 | `(20,020-20,010)×0.5` | `5.000000 USDT` | 已在買入成交價 |
| 價格 | 出場半邊 spread | `(20,200-20,190)×0.5` | `5.000000 USDT` | 已在賣出成交價 |
| 價格 | 出場滑點 | `(20,190-20,180)×0.5` | `5.000000 USDT` | 已在賣出成交價 |
| 資產流 | 進場 taker 費 | `0.0005 BTC×20,180` | `10.090000 USDT` | 先扣 BTC；此列以出場價換算終值 |
| 資產流 | 出場 taker 費 | `10,079.91×0.001` | `10.079910 USDT` | 另扣 USDT |

價格層固定用原始毛數量 `0.5 BTC` 比較 mid 與 fill；因進場費少掉的
`0.0005 BTC` 則只在資產流層以出場價換算。如此數量差只出現一次，兩層相加才會
回到最終現金。

進場 BTC 費用為了與最終 USDT PnL 對帳，使用實際出場價換算成
`10.090000 USDT`。若改用進場價，會得到不同的估值目的；不能把兩個換算值一起
扣除。

完整分解為：

```text
中間價理想毛損益
= (20,200 - 20,000) × 0.5
= 100.000000 USDT

spread 成本 = 10.000000 USDT
滑點成本   = 10.000000 USDT

實際成交價毛損益
= (20,180 - 20,020) × 0.5
= 80.000000 USDT

費用成本
= 10.090000 + 10.079910
= 20.169910 USDT

淨損益
= 100.000000 - 10.000000 - 10.000000 - 20.169910
= 59.830090 USDT
```

最後必須同時成立：

```text
中間價理想毛損益 - spread - 滑點
= 實際成交價毛損益

實際成交價毛損益 - 費用
= 最終現金 - 初始現金
= 59.830090 USDT
```

這兩條等式就是「spread／滑點／手續費沒有重複扣除」的現金差額 oracle。

## 損益兩平：要說清楚是哪一個價格

在本案例的計費資產與費率前提下，實際出場成交價 `p_exit` 必須讓賣出淨收入
等於進場名義價值：

```text
q × (1 - r_buy) × p_exit × (1 - r_sell)
= q × p_buy

p_exit,BE
= p_buy / ((1 - r_buy) × (1 - r_sell))
= 20,060.100140180 USDT/BTC
```

這是「實際出場成交價」門檻。若規劃時仍假設出場半邊 spread 與滑點合計為
`20 USDT/BTC`，參考中間價門檻則是：

```text
exit mid,BE = 20,060.100140180 + 20
            = 20,080.100140180 USDT/BTC

相對進場 mid 的門檻
= (20,080.100140180 - 20,000) / 20,000
= 0.400500701%
```

這個門檻只對固定數量、費率、計費資產與價格摩擦情境成立。數量改變可能改變
滑點；費率、深度或成交分類改變也必須重算，不能把 `0.400500701%` 當市場常數。

## Maker 敏感度：比較可以，偷換不可以

若只把已成交的出場費率由 taker `0.001` 改成 maker `0.0002`，並且不改成交價、
數量與其他輸入：

```text
maker 比較費 = 10,079.910000 × 0.0002
             = 2.015982 USDT

maker 比較淨損益
= 80.000000 - 10.090000 - 2.015982
= 67.894018 USDT
```

這只能回答「若同一成交取得另一個費率，算術會怎麼變」。它不能證明 maker 單
會成交，更不能證明成交價、等待時間與機會成本仍相同。正式紀錄必須保留實際
流動性分類；沒有成交證據時，不得用較低費率美化回測。

## 換手率：小成本如何被反覆放大

本輪實際成交名義價值為：

```text
entry notional + exit notional
= 10,010.00 + 10,079.910000
= 20,089.910000 USDT

T = 20,089.910000 / 15,000.00
  = 1.339327333
```

也就是依本章定義，單輪成交名義價值約為初始權益的 `133.9327333%`。這不表示
帳戶借了 `133%` 的錢；分子把買賣兩邊都加總。它表示只要策略頻繁完成相同往返，
每輪約 `40.169910 USDT` 的價格摩擦與費用會反覆進入結果。

回測若只報每筆費率，卻不報成交名義價值、輪數與換手率，就無法檢查成本是否
隨交易活動合理累積。

## 勝率、賠率與期望值：成本只扣一次

再看一個固定的每輪結果分布：

| 輸入 | 數值 |
|---|---:|
| 勝率 `p` | `0.55` |
| 毛獲利 `W_g` | `+120.00 USDT` |
| 毛虧損 `L_g` | `-80.00 USDT` |
| 每輪總成本 `K` | `40.169910 USDT` |

毛賠率（平均獲利絕對值 ÷ 平均虧損絕對值）為 `120/80 = 1.5`，毛期望值為：

```text
EV_gross
= 0.55 × 120 + 0.45 × (-80)
= 30.000000 USDT
```

先把成本一次扣進每個完整往返的 outcome：

```text
net win  = 120.00 - 40.169910 = 79.830090 USDT
net loss = -80.00 - 40.169910 = -120.169910 USDT

EV_net
= 0.55 × 79.830090 + 0.45 × (-120.169910)
= -10.169910 USDT
```

也可以直接檢查 `EV_gross - K = EV_net`。若先把成本扣進 win／loss，最後又從
期望值扣一次，就是雙扣；若只從贏家扣成本，則是漏扣虧損交易的成交成本。

在這組固定 outcome 下，淨損益兩平勝率為：

```text
p_BE = |net loss| / (net win + |net loss|)
     = 60.084955%
```

所以 `55%` 勝率與 `1.5` 毛賠率看似不差，扣完成本後仍應 no-go。勝率不能單獨
代替淨 outcome 分布。

## 流動性與容量：用情境做限制，不冒充市場事實

流動性（liquidity）描述在特定時間、價格與數量下完成交易的能力；容量
（capacity）則問策略在成本和風險仍可接受時最多能部署多少。兩者都不是單一
固定數字。

本章只建立一個明示的容量壓力情境：

```text
容量上限數量 = 情境深度 × 參與率上限
```

| 情境 | 假設可用深度 | 參與率上限 | 容量上限 | `0.5 BTC` 決定 |
|---|---:|---:|---:|---|
| 基準 | `2.00 BTC` | `25%` | `0.50 BTC` | 恰達上限；僅可繼續研究 |
| 壓力 | `1.00 BTC` | `25%` | `0.25 BTC` | no-go；先縮量或停止 |

`2.00 BTC`、`1.00 BTC` 與 `25%` 都是教學輸入，不是下載的訂單簿，也不代表
真實市場衝擊函數。即使一張 snapshot 顯示足夠數量，委託延遲、撤單、其他參與者
與多檔成交仍可能改變結果。真正的容量結論需要版本化的深度資料、成交模型、
規模敏感度與壓力證據；本章只能訓練「輸入不足時不宣稱容量」。

## 動手驗證：用 `Decimal` 重算四組 oracle

先完成[實作準備](../front-matter/setup.md)，在固定配套 worktree 核對版本與乾淨
狀態後執行：

```bash
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv run python - <<'PY'
from decimal import Decimal as D

cash0 = D("15000.00")
qty = D("0.50000000")
entry_mid, entry_ask, entry_fill = D("20000.00"), D("20010.00"), D("20020.00")
exit_mid, exit_bid, exit_fill = D("20200.00"), D("20190.00"), D("20180.00")
buy_rate = D("0.001")
sell_taker_rate = D("0.001")
sell_maker_rate = D("0.0002")

entry_notional = qty * entry_fill
buy_fee_base = qty * buy_rate
net_base = qty - buy_fee_base
exit_notional = net_base * exit_fill
sell_fee = exit_notional * sell_taker_rate
cash_after_buy = cash0 - entry_notional
cash_final = cash_after_buy + exit_notional - sell_fee
net_pnl = cash_final - cash0

ideal_gross = (exit_mid - entry_mid) * qty
spread_cost = ((entry_ask - entry_mid) + (exit_mid - exit_bid)) * qty
slippage_cost = ((entry_fill - entry_ask) + (exit_bid - exit_fill)) * qty
fill_gross = (exit_fill - entry_fill) * qty
buy_fee_quote_at_exit = buy_fee_base * exit_fill
fee_cost = buy_fee_quote_at_exit + sell_fee
total_cost = spread_cost + slippage_cost + fee_cost
turnover = (entry_notional + exit_notional) / cash0

exit_fill_be = entry_fill / ((D("1") - buy_rate) * (D("1") - sell_taker_rate))
exit_mid_be = exit_fill_be + (exit_mid - exit_fill)
mid_return_be = (exit_mid_be - entry_mid) / entry_mid

maker_sell_fee = exit_notional * sell_maker_rate
maker_net = fill_gross - buy_fee_quote_at_exit - maker_sell_fee

win_rate = D("0.55")
gross_win, gross_loss = D("120.00"), D("-80.00")
gross_ev = win_rate * gross_win + (D("1") - win_rate) * gross_loss
net_win, net_loss = gross_win - total_cost, gross_loss - total_cost
net_ev = win_rate * net_win + (D("1") - win_rate) * net_loss
expectancy_be = abs(net_loss) / (net_win + abs(net_loss))

capacity = D("2.00") * D("0.25")
stress_capacity = D("1.00") * D("0.25")

assert ideal_gross - spread_cost - slippage_cost == fill_gross
assert fill_gross - fee_cost == net_pnl
assert cash_final - cash0 == net_pnl
assert gross_ev - total_cost == net_ev
assert capacity == qty and stress_capacity < qty

print(f"entry_notional={entry_notional:.2f} USDT")
print(f"buy_fee={buy_fee_base:.8f} BTC")
print(f"net_base={net_base:.8f} BTC")
print(f"exit_notional={exit_notional:.6f} USDT")
print(f"sell_taker_fee={sell_fee:.6f} USDT")
print(f"ideal_mid_gross={ideal_gross:.6f} USDT")
print(f"spread_cost={spread_cost:.6f} USDT")
print(f"slippage_cost={slippage_cost:.6f} USDT")
print(f"fill_gross={fill_gross:.6f} USDT")
print(f"buy_fee_at_exit={buy_fee_quote_at_exit:.6f} USDT")
print(f"total_fee_cost={fee_cost:.6f} USDT")
print(f"total_cost={total_cost:.6f} USDT")
print(f"final_cash={cash_final:.6f} USDT")
print(f"net_pnl={net_pnl:.6f} USDT")
print(f"turnover={turnover:.9f}")
print(f"exit_fill_breakeven={exit_fill_be:.9f} USDT/BTC")
print(f"exit_mid_breakeven={exit_mid_be:.9f} USDT/BTC")
print(f"mid_return_breakeven={mid_return_be:.9%}")
print(f"maker_comparison_fee={maker_sell_fee:.6f} USDT")
print(f"maker_comparison_net={maker_net:.6f} USDT")
print(f"gross_expectancy={gross_ev:.6f} USDT")
print(f"net_win={net_win:.6f} USDT")
print(f"net_loss={net_loss:.6f} USDT")
print(f"net_expectancy={net_ev:.6f} USDT")
print(f"net_breakeven_win_rate={expectancy_be:.6%}")
print(f"capacity_base={capacity:.8f} BTC")
print(f"stress_capacity_base={stress_capacity:.8f} BTC")
print("cash-oracle=PASS")
print("cost-decomposition=PASS")
print("expectancy-cost-once=PASS")
print("capacity-scenario=PASS")
PY
```

版本命令應輸出完整
`c999965e5cc923281541409cda9502beb93b8a60`，`status --short` 應無輸出。
固定案例的關鍵輸出為：

```text
entry_notional=10010.00 USDT
buy_fee=0.00050000 BTC
net_base=0.49950000 BTC
exit_notional=10079.910000 USDT
sell_taker_fee=10.079910 USDT
ideal_mid_gross=100.000000 USDT
spread_cost=10.000000 USDT
slippage_cost=10.000000 USDT
fill_gross=80.000000 USDT
buy_fee_at_exit=10.090000 USDT
total_fee_cost=20.169910 USDT
total_cost=40.169910 USDT
final_cash=15059.830090 USDT
net_pnl=59.830090 USDT
turnover=1.339327333
exit_fill_breakeven=20060.100140180 USDT/BTC
exit_mid_breakeven=20080.100140180 USDT/BTC
mid_return_breakeven=0.400500701%
maker_comparison_fee=2.015982 USDT
maker_comparison_net=67.894018 USDT
gross_expectancy=30.000000 USDT
net_win=79.830090 USDT
net_loss=-120.169910 USDT
net_expectancy=-10.169910 USDT
net_breakeven_win_rate=60.084955%
capacity_base=0.50000000 BTC
stress_capacity_base=0.25000000 BTC
cash-oracle=PASS
cost-decomposition=PASS
expectancy-cost-once=PASS
capacity-scenario=PASS
```

完整公式與適用前提可查[附錄 B](../appendices/b-trading-pnl-formulas.md)，期望值與
容量的最小查表見[附錄 C](../appendices/c-performance-risk-metrics.md)，bid／ask、
maker／taker 與滑點邊界見[附錄 D](../appendices/d-market-microstructure.md)。

## 系統對照：模型能保存成本證據，不等於已交付成本報告

固定在 `v0.3.0` 時，可核對到：

| 本章概念 | 已發布位置 | 能支持的宣稱 | 不能延伸成的宣稱 |
|---|---|---|---|
| maker／taker 費率 | `quant.common.fill.config.FeeSchedule` | maker／taker 與 spot／futures 費率分欄，且要求 `Decimal`、非負 | 預設值不是讀者帳戶的現行真實費率 |
| 成交成本證據 | `quant.common.fill.models.FillDecision` | 保存 `base_price`、`final_price`、`liquidity`、`fee_rate`、`fee`、`fee_asset` | 不代表正式讀者報告已完成成本歸因 |
| 買賣計費資產 | `quant.common.fill.simulator.FillSimulator` | v0.3.0 spot BUY 費用以 base、SELL 費用以 quote 計算；聚焦測試覆蓋兩者 | 不代表所有交易所、折扣或帳戶都採相同計費資產 |
| 滑點與容量代理 | `FillConfig.slippage_bps`、`volume_cap_pct` | 固定假設會進入撮合，volume cap 可限制 bar 成交量 | bar volume proxy 不是 live order-book 深度，也不能證明真實容量 |
| spot maker 邊界 | `Order`、`AdmissionPolicy` | spot `post_only` 在模型層拒絕；spot LIMIT 預設 admission 也拒絕，避免假裝安全撤單能力存在 | 不能把本章 maker 敏感度寫成 v0.3.0 可操作 spot maker 路徑 |

作者在固定 worktree 實跑 fee schedule、四種流動性／市場／方向組合與 volume-cap
聚焦測試，共 `6 passed`。這些測試支持模型邊界，不證明本章固定價格會在真實
市場成交，也不把 `v0.3.0` 的內部撮合元件冒充已發布的策略績效報告入口。

## 證據顯示什麼

- 中間價上漲 `1%` 產生 `100 USDT` 的理想毛損益，但價格摩擦與費用使淨損益只剩
  `59.830090 USDT`；
- 進場費扣 BTC，所以可賣數量是 `0.49950000 BTC`，不能仍用 `0.5 BTC` 建立
  最終現金流；
- spread 與滑點合計 `20 USDT` 已反映在實際成交價；費用才是成交價毛損益之後
  另扣的 `20.169910 USDT`；
- maker 費率敏感度較好，但沒有 maker 成交證據就不能取代實際 taker 路徑；
- `55%` 勝率與 `1.5` 毛賠率的範例，淨期望值仍為 `-10.169910 USDT`；
- 假設深度減半時，`0.5 BTC` 超過容量情境上限，因此結果是 no-go，不是提高
  滑點參數後繼續假裝成交。

這些證據只支持明示輸入下的算術與決策流程，不支持任何真實策略績效、即時費率
或市場容量宣稱。

## 結果解讀與決定

| 結果 | 判定 | 下一步 |
|---|---|---|
| 現金與成本分解 oracle 都通過，淨期望值大於零，容量壓力仍可接受 | 成本研究可繼續 | 保存輸入版本，再測試不同費率、滑點與規模 |
| 毛期望值為正、淨期望值為負 | 策略在此成本情境 no-go | 降低換手、改善可證明的成交方式，或淘汰假設 |
| 最終現金正確但分解式不平 | 有雙扣、漏扣或計費資產錯置 | 停止績效判讀，逐項重建資產流 |
| maker 假設沒有成交分類證據 | 證據不足 | 以保守 taker 情境評估，maker 只留敏感度 |
| 訂單大小超過壓力容量 | no-go | 縮量後完整重算；不能沿用原成本 |
| 只知道勝率，不知道 outcome 與成本 | 證據不足 | 不做策略晉級決定 |

本章通過代表你能建立成本表，不代表可以真實下單。部分成交、價格規則、訂單
生命週期與更精確的市場衝擊，會在後續 active gate 逐步加入。

## 常見陷阱

**陷阱一：用實際成交價算 PnL，又重扣 spread 與滑點。** 它們可以作歸因欄，
不能再作第二筆現金流。

**陷阱二：假設手續費永遠以 quote 扣除。** 本例買入費扣 BTC；計費資產一錯，
可賣數量與最終現金都會錯。

**陷阱三：maker 意圖等於 maker 成交。** 委託可能拒絕、等待、部分成交或不成交；
費率只能跟著實際分類。

**陷阱四：勝率高就忽略賠率與成本。** `55%` 勝率在本章固定 outcome 下仍是負
淨期望值。

**陷阱五：成本從期望值扣兩次。** 若 `net win` 與 `net loss` 已扣每輪成本，
最後不能再扣一次。

**陷阱六：換手率沒有分母。** 同名指標若分母不同，不能直接比較。

**陷阱七：把情境深度寫成市場容量。** 沒有版本、時點、標的與資料證據時，只能
稱為假設或壓力輸入。

**陷阱八：中途先四捨五入。** 本章保留完整 `Decimal`，只在顯示時指定小數位；
真實交易還要另遵守 tick、step 與計費精度。

## 對系統的回饋

一份可審核的成本報告至少應保存：

- 決策參考價、bid、ask、實際成交價、數量與時間；
- maker／taker 的實際分類，而非原始意圖；
- 費率、費用、計費資產，以及折扣或特殊費率來源；
- 每筆成交的 spread／滑點歸因方法，並標示是否已包含在成交價；
- entry／exit 名義價值、換手率定義與分母；
- gross outcome、net outcome、成本合計與最終資產流 oracle；
- 容量情境的資料版本、參與率規則與 no-go 門檻。

若報告無法讓另一位審核者重建 `cash_final - cash_initial = net PnL`，就應形成
schema、測試或文件 finding，而不是讓策略自行維護另一套帳。

## 小結與練習

成本不是最後在報酬率旁邊加一個負號。spread 與滑點先改變成交價，手續費再依
成交分類與計費資產改變資產流；換手把每輪成本反覆帶入結果，容量則限制哪些
數量根本不應假裝成交。

請做兩個互斥情境，每次只改一組輸入：

1. 保留實際 taker 路徑，把進出場費率都改成 `0.0005`；
2. 保留費率，把壓力深度改成 `0.80 BTC`、參與率上限改成 `20%`。

對情境 1 重算最終現金、淨損益、實際成交價損益兩平點與 `55%` 勝率案例的淨
期望值；對情境 2 先算容量上限，再作 continue／no-go 決定。不得一邊改費率、
一邊假設成交價與容量也自動改善。

## 專業紀錄：交易成本與損益兩平表

在自己的工作區保存，不提交帳戶費率、API key 或私人交易輸出：

| 欄位 | 你的紀錄 |
|---|---|
| 情境名稱、日期與資料版本 |  |
| base／quote、數量與初始權益 |  |
| entry／exit mid、bid、ask 與實際成交價 |  |
| 每腿 maker／taker 實際分類 |  |
| 每腿費率、費用與計費資產 |  |
| entry／exit 名義價值 |  |
| spread、滑點與是否已包含在成交價 |  |
| gross PnL、總成本、net PnL |  |
| 最終資產流與現金差額 oracle |  |
| 損益兩平成交價／參考價與前提 |  |
| 換手率公式、分子與分母 |  |
| gross／net 勝負 outcome、勝率、賠率與期望值 |  |
| 流動性／容量輸入與壓力決定 |  |
| continue／no-go 與理由 |  |
| 可形成的系統或報告改進 |  |

最低通過條件：

- 每項成本只有一個入帳位置，歸因欄與現金流欄不重複扣除；
- 費用同時記錄費率、金額與計費資產；
- 最終資產流、成本分解與期望值三組 oracle 都成立；
- 損益兩平點標明是成交價或參考價，並保存全部前提；
- 容量資料若只是情境，就明標情境，不冒充真實市場深度；
- 任一 oracle 失敗或淨期望值不正時，結果可以且應該是 no-go。

## 作者驗證紀錄

- 驗證對象：固定 `Decimal` 現貨往返、spread／滑點／費用單次入帳、計費資產、換手率、損益兩平、maker 敏感度、gross／net 期望值與容量情境
- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證命令：在乾淨隔離 worktree 核對 HEAD、`status --short` 與 `uv lock --check`；執行本章 `uv run python` 固定案例；執行 fee schedule、流動性／計費資產與 volume-cap 三組聚焦 pytest；查閱固定於 `4987e707f84f20d736ee6a2bcb71396111cffee1` 的 Binance 官方 Spot API 文件
- 通過結果：固定案例輸出 `cash-oracle=PASS`、`cost-decomposition=PASS`、`expectancy-cost-once=PASS`、`capacity-scenario=PASS`；系統聚焦測試 `6 passed`；官方 schema 分列 `bids`／`asks` 與 maker／taker commission
- 待處理差異：費率、價格、深度與參與率都是教學輸入；未驗證真實成交或市場容量；`v0.3.0` 沒有正式讀者成本報告，spot post-only 也不是本章可操作路徑
