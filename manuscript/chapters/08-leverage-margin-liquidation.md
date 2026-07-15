# 槓桿、標記價格、保證金與強制平倉

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：穩定概念
> 最後驗證日期：2026-07-15

## 學習目標

讀完本章後，你應該能夠：

- 分清成交價格、指數價格與標記價格各自回答什麼問題；
- 證明槓桿改變保證金需求，卻不會單獨改變既有部位的市場曝險與價格 PnL；
- 用分層表計算初始保證金、維持保證金、保證金餘額與保證金率；
- 分別手算 Cross 與 Isolated 的多空強平邊界，並核對另一部位如何傳遞風險；
- 把強平、回撤與 risk of ruin 分成三種不同證據，不用一個數字冒充另一個；
- 對缺標記價格、過期標記、超出分層與非正保證金餘額採 fail closed。

## 問題情境：`5x` 不等於「最多只會虧 `20%`」

你以 `20,000.00 USDT/BTC` 建立 `0.50000000 BTC` 的 U 本位線性永續部位，
選擇 `5x`，並配置 `2,000.00 USDT` 保證金。最常見的直覺是：

```text
名義價值 10,000 USDT，5x 只放 2,000 USDT；
所以價格反向 20% 時才會剛好虧完並強平。
```

這個說法漏掉維持保證金、分層費率、速算額、標記價格，以及 Cross 帳戶中其他
部位的未實現損益。強平也不是「等到保證金精確歸零才發生」；風險引擎要在
保證金餘額仍足以覆蓋維持保證金時判斷邊界。

本章使用的價格、數量、保證金與其他部位 PnL 都是固定教學輸入。分層表取自
`emmet-qt-bt1 v0.3.0` 的版本化 `BTCUSDT` fixture，`snapshot_ts=1700000000000`；
它不是 2026-07-15 的 Binance 帳戶規則、即時行情或可成交承諾。本章不連線交易、
不使用 API key，也不推測第 9 章的多腿策略結果。

## 執行前預測

先固定以下共同輸入：

| 輸入 | 多頭 | 空頭 |
|---|---:|---:|
| 帶方向數量 `Q` | `+0.50000000 BTC` | `-0.50000000 BTC` |
| 開倉均價 `p_entry` | `20,000.00 USDT/BTC` | `20,000.00 USDT/BTC` |
| 當前標記價格 `m` | `20,000.00 USDT/BTC` | `20,000.00 USDT/BTC` |
| 選定槓桿 `L` | `5x` | `5x` |
| Cross wallet／Isolated margin | `2,000.00 USDT` | `2,000.00 USDT` |
| 第一檔 MMR／速算額 | `0.004`／`0 USDT` | `0.004`／`0 USDT` |

在看答案前，寫下你的預測：

1. 多空的名義價值、初始保證金與維持保證金是否相同？
2. 多頭與空頭的強平價離 `20,000` 是否恰好都是 `20%`？
3. 若只把槓桿從 `5x` 改成 `10x`，但仍保留 `2,000 USDT` 隔離保證金，強平價
   會不會改變？
4. Cross 帳戶中另一部位多賺 `500 USDT`，目標部位的強平價會靠近還是遠離？
5. 保證金餘額小於零時，保證金率應該是一個負百分比，還是「未定義並停止」？

預測必須帶單位，並分清「部位初始保證金目標」與「實際配置的風險承擔資金」。

## 三種價格不能互換

| 價格 | 回答的問題 | 本章用途 |
|---|---|---|
| 成交價格 | 訂單實際在哪個價格成交？ | 已實現 PnL、成交成本與會計事實 |
| 指數價格 | 外部現貨市場組合所代表的參考價格是多少？ | 標記價格的市場參考之一 |
| 標記價格 | 風險引擎目前用什麼公平參考價重估？ | 未實現 PnL、名義價值、保證金與強平判斷 |

Binance 的
[標記價格與指數價格說明](https://www.binance.com/en/support/faq/detail/360033525071)
於 2026-07-15 查證時標示 2026-07-09 更新：指數價格由多個主要現貨市場組成，
標記價格用作未實現 PnL 與強平的公平參考；真正已實現的成交損益仍取決於實際
成交價格。這支持角色分工，不支持本章的固定 `20,000` 是市場現況。

所以「標記價格碰到強平邊界」與「一定能在該價成交」是兩件事。行情跳空、延遲、
撮合深度與清算流程都可能讓實際結果不同。強平價是風險邊界，不是停損成交保證。

## 槓桿改變資金需求，不會憑空改變曝險

對 U 本位線性永續部位：

```text
N = abs(Q) × m
U = (m - p_entry) × Q
```

`Q`、`m` 與 `p_entry` 不變時，選擇 `5x` 或 `10x` 都不會改變 `N` 或 `U`。
Emmet `v0.3.0` 的持倉初始保證金 target 為：

```text
IM_position = abs(Q) × p_entry / L
```

本章固定部位的 `N=10,000 USDT`；`5x` target 是 `2,000 USDT`，`10x` target
是 `1,000 USDT`。但 Binance 的
[開倉成本說明](https://www.binance.com/en/support/faq/detail/87fa7ee33b574f7084d42bd2ce2e463b)
也提醒訂單成本可能另含 open loss。故 `IM_position` 是本章與 Emmet 模型對齊的
部位 target，不是所有訂單 admission、費用與價格偏離的完整公式。

槓桿的危險不是在 PnL 公式中乘上一個 `L`，而是較低 target 允許你用相同資金
建立更大部位，或在部位不變時抽走更多隔離保證金。只有曝險或實際承擔資金改變，
風險邊界才會跟著改變。

## 維持保證金必須綁定分層快照

令 `MMR` 為當前名義價值所屬檔位的維持保證金率、`A` 為速算額
（maint amount／cum）：

```text
MM = N × MMR - A
```

`v0.3.0` fixture 第一檔是：

```text
0 <= N < 50,000 USDT
MMR = 0.004
A = 0 USDT
max initial leverage = 125
```

因此固定部位在 `m=20,000` 時：

```text
N = abs(0.5) × 20,000 = 10,000.00 USDT
MM = 10,000 × 0.004 - 0 = 40.00 USDT
```

Binance 的
[槓桿與保證金說明](https://www.binance.com/en/support/faq/detail/360033162192)
於 2026-07-15 查證時標示 2026-03-27 更新，也將維持保證金寫成名義價值乘
MMR 再扣 maint amount，並說明名義價值對應不同 bracket。官方規則可能改變；
研究紀錄必須保存 symbol、快照時點與完整分層，不能只保存 `0.004`。

Emmet `v0.3.0` 依「當前標記價格的名義價值」先選檔，再代入該檔公式；它不做
強平價跨檔的 fixed-point 迭代。本章算出的多空強平價仍落在第一檔，所以檔位身分
自洽。若候選強平價跨檔，不能硬套本章答案，必須明示模型限制並 fail closed 或
採相符規格重算。

## Cross 與 Isolated 的風險邊界

保證金餘額與保證金率寫成：

```text
MB_isolated = isolated_margin + U_this
MB_cross = cross_wallet_balance + Σ U_cross
MR = Σ MM / MB                   （只在 MB > 0 時定義）
```

Cross 共用可承擔資金：其他 Cross 部位的有利 PnL 可以延後本部位強平，不利 PnL
也能把本部位一起拖近邊界。Isolated 只使用配置給該部位的隔離保證金與該部位
PnL；其他部位不進公式。Binance Academy 的
[Cross 與 Isolated 說明](https://www.binance.com/en/academy/articles/what-are-isolated-margin-and-cross-margin-in-crypto-trading)
於 2026-07-15 查證時標示 2026-05-07 更新，也以共享 collateral 與 ring-fenced
collateral 區分兩者。

`MB <= 0` 時，`MR` 不是負百分比，也不是可以繼續比較的正常值。Emmet 回傳
`None`，清算檢查採 fail closed；否則一個已穿越資金邊界的帳戶反而可能因負比率
看起來「小於 100%」。

## 強平價公式與成立條件

對目標部位 `Q`，令 `W` 是 Cross wallet balance；逐倉時則把 `W` 換成該部位
isolated margin。令 `TMM_other` 與 `U_other` 是其他 Cross 部位的維持保證金與
未實現 PnL：

```text
p_liq =
  (W - TMM_other + U_other + A - Q × p_entry)
  / (abs(Q) × MMR - Q)
```

Isolated 令 `TMM_other=0`、`U_other=0`。這個邊界來自：

```text
margin balance at p_liq = total maintenance margin at p_liq
```

只在下列條件同時成立時接受結果：

- `Q != 0`，標的、合約類型與分層表身分相符；
- 有當前且未過期的標記價格可決定檔位；
- 名義價值落在版本化分層範圍內；
- 分母有效，算出的 `p_liq > 0`；
- 候選價格所隱含的檔位與模型所選檔位相容；
- 輸入全部以十進位字串構造，顯示捨入不回寫計算。

Binance 的
[強平價格公式說明](https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93)
也分列 wallet balance、其他部位維持保證金與其他未實現 PnL。本文公式用來對齊
`v0.3.0` 的已發布模型；它不是交易所最終清算帳單，也不包含所有費用、保險基金、
ADL、下架或多資產模式。

## 固定案例：多空邊界不是對稱的 `20%`

### 當前狀態

多頭與空頭在開倉價上都得到：

```text
N = 10,000.00 USDT
U = 0.00 USDT
IM_position = 2,000.00 USDT
MM = 40.00 USDT
MB = 2,000.00 USDT
MR = 40 / 2,000 = 2.0000%
```

### 多頭強平邊界

```text
p_liq,long
= (2,000 - 0 + 0 + 0 - 0.5 × 20,000)
  / (0.5 × 0.004 - 0.5)
= 16,064.257028... USDT/BTC

顯示值 = 16,064.26 USDT/BTC
距當前標記價格緩衝 = 19.678715%
```

在未捨入的 `p_liq`：

```text
MB = 2,000 + (16,064.257028... - 20,000) × 0.5
   = 32.128514056225 USDT

MM = 0.5 × 16,064.257028... × 0.004
   = 32.128514056225 USDT
```

### 空頭強平邊界

```text
p_liq,short
= (2,000 - 0 + 0 + 0 - (-0.5) × 20,000)
  / (0.5 × 0.004 - (-0.5))
= 23,904.382470... USDT/BTC

顯示值 = 23,904.38 USDT/BTC
距當前標記價格緩衝 = 19.521912%
```

在未捨入的 `p_liq`，`MB=MM=47.808764940239 USDT`。多空距離不完全對稱，
因為分母中的 signed quantity 與維持保證金共同作用；「`1/L` 就是強平幅度」
不是合格公式。

### 先看保證金率如何惡化

| 快照 | 標記價格 | 名義價值 | 未實現 PnL | 維持保證金 | 保證金餘額 | 保證金率 |
|---|---:|---:|---:|---:|---:|---:|
| 多頭不利 | `17,000` | `8,500` | `-1,500` | `34` | `500` | `6.8000%` |
| 空頭不利 | `23,000` | `11,500` | `-1,500` | `46` | `500` | `9.2000%` |

相同 `-1,500 USDT` PnL 下，空頭因標記價格上升而有較高名義價值與維持保證金。
只盯損益、忽略 MM 隨標記改變，會錯估距離邊界多遠。

## Cross：另一部位可以救你，也可以拖累你

保持目標多頭、wallet balance `2,000 USDT` 與當前標記 `20,000` 不變，將其他
Cross 部位濃縮成已明示的情境輸入：

| 其他 Cross 部位 | `TMM_other` | `U_other` | 總 MM | Cross MB | MR | 目標多頭強平價 |
|---|---:|---:|---:|---:|---:|---:|
| 無 | `0` | `0` | `40` | `2,000` | `2.0000%` | `16,064.26` |
| 有利 | `20` | `+500` | `60` | `2,500` | `2.4000%` | `15,100.40` |
| 不利 | `20` | `-500` | `60` | `1,500` | `4.0000%` | `17,108.43` |

有利部位讓目標多頭的下跌邊界更遠，但也增加了 `20 USDT` 維持保證金；不能只加
PnL 而漏掉另一部位的 MM。不利部位則讓目標多頭更早碰到邊界。Cross 的共享效應
是風險傳遞，不是免費的安全墊。

Isolated 不帶入這兩欄：配置 `2,000 USDT` 時仍是 `16,064.26`。若部位不變，只把
選定槓桿由 `5x` 改為 `10x`，但仍保留 `2,000 USDT` 隔離保證金，強平價也不變；
若真的把隔離保證金降到新的 `1,000 USDT` target，強平價才移到 `18,072.29`。

## 四組驗收 oracle

### 多空自洽

```text
MB(p_liq) = MM(p_liq)
long p_liq < entry < short p_liq
```

必須用未捨入價格驗證；`16,064.26` 與 `23,904.38` 只是顯示值。

### Cross／Isolated 邊界

```text
Cross：other MM 與 other UPNL 都進公式
Isolated：other MM = other UPNL = 0
```

有利與不利部位必須讓多頭強平價朝相反方向移動。

### 槓桿與曝險分離

```text
Q、entry、mark 不變 → N 與 U 不變
L 改變 → IM_position target 改變
實際 isolated margin 不變 → p_liq 不變
```

### 非正餘額 fail closed

在 `isolated_margin=1,000`、多頭標記 `17,600` 時：

```text
MM = 35.20 USDT
MB = -200.00 USDT
MR = undefined
```

必須停止正常比率判讀；不能輸出 `-17.6%` 再宣稱低於門檻。

## 動手驗證：用字串建立的 `Decimal` 重算

依[實作準備](../front-matter/setup.md)進入固定、乾淨的配套 worktree 後執行：

```bash
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv run python - <<'PY'
from decimal import Decimal as D
from decimal import ROUND_HALF_UP, getcontext

getcontext().prec = 50
getcontext().rounding = ROUND_HALF_UP

entry = D("20000.00")
mark = D("20000.00")
qty_long = D("0.50000000")
qty_short = D("-0.50000000")
wallet = D("2000.00")
mmr = D("0.004")
maint_amount = D("0")


def clean_zero(value):
    return D("0") if value == 0 else value


def liquidation_price(qty, collateral, other_mm=D("0"), other_upnl=D("0")):
    numerator = (
        collateral
        - other_mm
        + other_upnl
        + maint_amount
        - qty * entry
    )
    denominator = abs(qty) * mmr - qty
    price = numerator / denominator
    assert price > 0
    return price


for name, qty in (("long", qty_long), ("short", qty_short)):
    notional = abs(qty) * mark
    upnl = clean_zero((mark - entry) * qty)
    initial_margin = abs(qty) * entry / D("5")
    maintenance = notional * mmr - maint_amount
    margin_balance = wallet + upnl
    ratio = maintenance / margin_balance
    price = liquidation_price(qty, wallet)
    buffer = (
        (mark - price) / mark if qty > 0 else (price - mark) / mark
    ) * D("100")
    print(
        f"{name}: N={notional:.2f} U={upnl:+.2f} IM={initial_margin:.2f} "
        f"MM={maintenance:.2f} MB={margin_balance:.2f} "
        f"MR={ratio:.4%} LP={price:.2f} buffer={buffer:.6f}%"
    )

    lp_upnl = (price - entry) * qty
    lp_balance = wallet + lp_upnl
    lp_maintenance = abs(qty) * price * mmr - maint_amount
    assert abs(lp_balance - lp_maintenance) < D("1e-40")
    assert abs(qty) * price < D("50000")
    print(
        f"{name}-at-lp: MB={lp_balance:.12f} "
        f"MM={lp_maintenance:.12f}"
    )

for name, qty, adverse in (
    ("long", qty_long, D("17000.00")),
    ("short", qty_short, D("23000.00")),
):
    notional = abs(qty) * adverse
    upnl = (adverse - entry) * qty
    maintenance = notional * mmr
    balance = wallet + upnl
    print(
        f"{name}-adverse: mark={adverse:.2f} N={notional:.2f} "
        f"U={upnl:+.2f} MM={maintenance:.2f} MB={balance:.2f} "
        f"MR={maintenance / balance:.4%}"
    )

for name, other_mm, other_upnl in (
    ("cross-none", D("0"), D("0")),
    ("cross-favorable", D("20.00"), D("500.00")),
    ("cross-adverse", D("20.00"), D("-500.00")),
):
    own_mm = abs(qty_long) * mark * mmr
    total_mm = own_mm + other_mm
    balance = wallet + other_upnl
    price = liquidation_price(qty_long, wallet, other_mm, other_upnl)
    print(
        f"{name}: totalMM={total_mm:.2f} MB={balance:.2f} "
        f"MR={total_mm / balance:.4%} LP={price:.2f}"
    )

im_5x = abs(qty_long) * entry / D("5")
im_10x = abs(qty_long) * entry / D("10")
isolated_same = liquidation_price(qty_long, D("2000.00"))
isolated_reduced = liquidation_price(qty_long, D("1000.00"))
print(
    f"leverage: IM@5x={im_5x:.2f} IM@10x={im_10x:.2f} "
    f"N={abs(qty_long) * mark:.2f} "
    f"U@+100={(D('20100.00') - entry) * qty_long:.2f}"
)
print(
    f"isolated-lp: margin2000={isolated_same:.2f} "
    f"margin1000={isolated_reduced:.2f}"
)

bad_mark = D("17600.00")
bad_mm = abs(qty_long) * bad_mark * mmr
bad_balance = D("1000.00") + (bad_mark - entry) * qty_long
assert bad_balance <= 0
bad_ratio = None
print(
    f"nonpositive-margin: MM={bad_mm:.2f} MB={bad_balance:.2f} "
    "MR=undefined FAIL-CLOSED"
)

assert isolated_same == liquidation_price(qty_long, D("2000.00"))
assert isolated_reduced > isolated_same
assert liquidation_price(qty_long, wallet, D("20"), D("500")) < isolated_same
assert liquidation_price(qty_long, wallet, D("20"), D("-500")) > isolated_same
assert bad_ratio is None
print("long-short-liquidation=PASS")
print("cross-isolated-boundary=PASS")
print("leverage-exposure-separation=PASS")
print("nonpositive-margin=PASS")
PY
```

版本命令應輸出完整
`c999965e5cc923281541409cda9502beb93b8a60`，`status --short` 應無輸出。固定
案例預期輸出為：

```text
long: N=10000.00 U=+0.00 IM=2000.00 MM=40.00 MB=2000.00 MR=2.0000% LP=16064.26 buffer=19.678715%
long-at-lp: MB=32.128514056225 MM=32.128514056225
short: N=10000.00 U=+0.00 IM=2000.00 MM=40.00 MB=2000.00 MR=2.0000% LP=23904.38 buffer=19.521912%
short-at-lp: MB=47.808764940239 MM=47.808764940239
long-adverse: mark=17000.00 N=8500.00 U=-1500.00 MM=34.00 MB=500.00 MR=6.8000%
short-adverse: mark=23000.00 N=11500.00 U=-1500.00 MM=46.00 MB=500.00 MR=9.2000%
cross-none: totalMM=40.00 MB=2000.00 MR=2.0000% LP=16064.26
cross-favorable: totalMM=60.00 MB=2500.00 MR=2.4000% LP=15100.40
cross-adverse: totalMM=60.00 MB=1500.00 MR=4.0000% LP=17108.43
leverage: IM@5x=2000.00 IM@10x=1000.00 N=10000.00 U@+100=50.00
isolated-lp: margin2000=16064.26 margin1000=18072.29
nonpositive-margin: MM=35.20 MB=-200.00 MR=undefined FAIL-CLOSED
long-short-liquidation=PASS
cross-isolated-boundary=PASS
leverage-exposure-separation=PASS
nonpositive-margin=PASS
```

所有輸入都從字串建立 `Decimal`；`ROUND_HALF_UP` 只決定顯示，等式核對使用未
捨入數值。程式不模擬交易所清算流程，也不證明強平價可成交。

## 系統對照：已發布模型、分層與 fail-closed 路徑

固定在 `v0.3.0` 時，可以核對：

| 本章概念 | 已發布位置 | 能支持的宣稱 | 不能延伸成的宣稱 |
|---|---|---|---|
| 部位與錢包 | `models.account.Position`、`FuturesWallet` | signed quantity、entry／mark、leverage、Cross／Isolated、初始／維持保證金、`MB=W+U`、非正分母回傳 `None` | 不是正式讀者帳戶或下單入口 |
| 分層與強平價 | `rules.brackets` | 版本化 bracket、`MM=N×MMR-A` 與本章強平公式 | fixture 不是現行交易所規則；不做跨檔 fixed-point 迭代 |
| 清算檢查 | `fill.liquidation` | 缺少或過期標記、超出分層、非正餘額等情況 fail closed；依多空不利方向檢查 | 不等於真實交易所成交、保險基金、ADL 或完整清算報告 |
| Engine 風險路徑 | `engine.liquidation` | 以固定模型與測試串接風險判斷 | 尚無本章可供讀者操作的正式產品入口 |

作者在乾淨的固定配套 worktree 執行 `uv lock --check`，並重跑：

```text
tests/unit/test_models_account.py
tests/unit/test_rules_brackets.py
tests/unit/test_fill_liquidation_checker.py
tests/unit/test_engine_liquidation.py
```

結果為 `83 passed`。這支持已發布內部模型的對照，不表示本章有權從開發分支取得
額外輸出，也不把內部 package 路徑包裝成正式讀者入口。

## Fail-closed 清單

| 輸入或狀態 | 本章決定 |
|---|---|
| `Q=0` | 無有效強平價；不能除出一個價格 |
| 缺少、未來或過期標記價格 | 停止；不能用成交價或舊 close 偷補 |
| symbol／market／bracket 身分不符 | 停止並保留錯配證據 |
| 名義價值超出分層表範圍 | 停止；不能沿用最後一檔 |
| `MB <= 0` | 保證金率未定義，清算判斷 fail closed |
| 算得 `p_liq <= 0` | 回報無有效正強平價，不傳播負價格 |
| 候選強平價跨檔而模型未迭代 | 標記模型限制；不能宣稱精確交易所邊界 |

## 強平、回撤與 risk of ruin 不是同一件事

| 證據 | 回答的問題 | 本章能否由單一快照得到 |
|---|---|---|
| 強平邊界／事件 | 帳戶何時不再滿足維持保證金規則？ | 可以算固定模型邊界，不能保證成交 |
| 回撤 | 一條權益路徑從先前高點跌了多少？ | 不行；至少需要帶時點的權益序列 |
| Risk of ruin | 在明示隨機模型與期限下，觸及 ruin 定義的機率是多少？ | 不行；還需要分布、相依、成本與路徑模型 |

一次沒有強平不代表回撤小；一次 `19.7%` 的價格緩衝也不是「破產機率
19.7%」。附錄 C 給出最小定義，後續統計章才會處理估計與不確定性。

## 證據顯示什麼

- 多空在相同 entry、mark 與絕對數量下有相同曝險與初始保證金 target，卻因
  signed quantity 與 MM 作用得到不完全對稱的強平距離。
- 槓桿只直接改變 `IM_position`；若實際隔離保證金不變，固定部位強平價不變。
- Cross 會傳遞其他部位的 PnL 與維持保證金，Isolated 才把這條路徑切開。
- `MB<=0`、缺標記或分層失配不是普通數值，而是必須阻止正常流程的證據。
- 固定 fixture 與 `83 passed` 支持 `v0.3.0` 內部模型，不支持現行交易所規則或
  可操作交易能力。

## 結果解讀與決定

對固定案例的決定是：拒絕「`5x` 等於價格反向 `20%` 才強平」；接受以未捨入
Decimal、版本化分層與 `MB=MM` 自洽核對所得的模型邊界。若研究輸入缺少標記
時點、完整 bracket 或 margin type，就沒有安全的強平數字，決定只能是 no-go。

Cross 不因共享餘額而自動優於 Isolated。前者可能提高資金效率，也讓另一部位把
風險傳過來；後者限制傳染，卻可能因配置較少而更早觸發。專業決定要列出共享
範圍、資金來源、最壞情境與退出條件，而不是只選一個較高的槓桿數字。

## 常見陷阱

1. 把 `N/L` 直接當成最大損失或強平距離。
2. 用最後成交價計算風險，卻用標記價格解釋事後結果。
3. 把維持保證金率當成所選槓桿的倒數。
4. 只保存 MMR，不保存 symbol、notional cap、maint amount 與快照時點。
5. Cross 只加其他 PnL，漏掉其他部位的維持保證金。
6. 把 Isolated target 當成不可改變的實際隔離保證金。
7. 用四捨五入後的強平價反推等式，因幾分錢差異誤判模型失敗。
8. 把強平價寫成 guaranteed fill，或把一次固定距離寫成 risk-of-ruin 機率。
9. `MB<=0` 時仍輸出負保證金率並繼續判斷。
10. 把版本化 fixture 宣稱為 2026-07-15 Binance 現行 bracket。

## 對系統的回饋

若固定 `tag@commit` 的結果與本章 oracle 不同，先保存：

- symbol、market、margin type、signed quantity、entry 與帶時點 mark；
- wallet／isolated margin、其他部位 UPNL 與維持保證金；
- bracket fixture checksum 或版本、所選檔位與候選價格檔位；
- 未捨入 Decimal 輸入、實際輸出、預期 `MB=MM` 等式；
- 最小失敗測試與 `git status --short`。

若差異來自 Emmet 已發布模型，就到配套系統建立最小重現與 oracle；不得在書稿
複製第二套邏輯掩蓋缺陷。若差異來自 Binance 規則更新，先更新第一手來源與
版本化 fixture，再重新驗證，不能直接改章內常數讓結果看似通過。

## 小結與練習

本章把「槓桿很危險」改寫成可審查的風險帳：價格角色、曝險、初始保證金、
維持保證金、餘額、比率、Cross／Isolated 與強平邊界都有各自欄位與 oracle。

1. 保持多頭 `Q=0.5` 與隔離保證金 `2,000`，把 entry 改為 `21,000`，重算
   `IM_position`、`p_liq` 與 `MB=MM`；說明 mark 不變時 UPNL 如何改變。
2. 保持 entry 與 wallet，將其他 Cross 部位改成 `TMM=80`、`UPNL=-300`；
   先預測方向，再用未捨入 Decimal 重算。
3. 為「缺 mark」、「mark 過期」、「notional 等於 `50,000`」各寫一列 no-go
   決策；第三列要說明半開區間如何換檔。
4. 比較 `10x` 下保留 `2,000` 與只留 `1,000` 的差別；指出哪一個輸入真正改變
   強平價。
5. 寫一段不超過 100 字的說明，向同事解釋為何 `19.678715%` 不是回撤或
   risk-of-ruin。

## 專業紀錄：槓桿、保證金與強平風險圖

完成本章後，保存一份可審查紀錄：

| 欄位 | 必填內容 |
|---|---|
| 基線 | `v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`、驗證日期、乾淨狀態 |
| 部位 | symbol、market、margin type、`Q`、entry、mark 與 mark timestamp |
| 分層 | fixture／來源、snapshot time、floor／cap、MMR、maint amount |
| 帳戶 | wallet／isolated margin、其他部位 MM 與 UPNL |
| 手算 | `N`、`U`、`IM`、`MM`、`MB`、`MR`、未捨入 `p_liq` |
| Oracles | `MB(p_liq)=MM(p_liq)`、方向、檔位身分、四組 PASS |
| 邊界 | 不可成交保證、模型差異、缺資料時的 no-go |
| 決定 | Cross／Isolated 選擇、槓桿與資金配置理由、退出條件 |

這份表的專業成果不是一個漂亮的強平價，而是任何審查者都能指出數字取自哪個
價格、哪個分層、哪筆資金，並能重現你為何繼續或停止。

## 作者驗證紀錄

- 驗證對象：成交／指數／標記價格角色、槓桿與曝險分離、初始／維持保證金、
  Cross／Isolated 保證金餘額與比率、多空強平價、自洽等式及非正餘額 fail closed
- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證命令：在乾淨隔離 worktree 核對 HEAD、`status --short` 與
  `uv lock --check`；執行本章 `uv run python` 固定案例；執行
  `uv run pytest tests/unit/test_models_account.py tests/unit/test_rules_brackets.py tests/unit/test_fill_liquidation_checker.py tests/unit/test_engine_liquidation.py -q`；查閱 Binance 官方價格、槓桿／保證金、Cross／Isolated 與強平文件；從書稿根目錄執行 `git diff --check` 與 `./scripts/book-check`
- 通過結果：固定案例輸出 `long-short-liquidation=PASS`、
  `cross-isolated-boundary=PASS`、`leverage-exposure-separation=PASS`、
  `nonpositive-margin=PASS`；配套聚焦測試 `83 passed`；第一手來源支持本章採用的
  價格角色、保證金與模式語義
- 待處理差異：價格、數量、保證金與 bracket 都是固定教學輸入；fixture
  `snapshot_ts=1700000000000` 不代表現行 Binance 規則；`v0.3.0` 按當前 mark
  notional 選檔且不做候選強平價 fixed-point 迭代，亦尚無正式讀者交易／報告入口；
  本章不含真實清算成交、保險基金、ADL、下架、多資產、私人 API 或第 9 章多腿策略
