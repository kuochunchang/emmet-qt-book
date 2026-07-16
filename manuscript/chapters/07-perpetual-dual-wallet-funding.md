# 永續合約、做多做空與兩個錢包

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：穩定概念
> 最後驗證日期：2026-07-15

## 學習目標

讀完本章後，你應該能夠：

- 區分現貨資產與 U 本位線性永續合約，不把合約多單誤認為真的持有基礎資產；
- 用一個帶正負號的部位數量表示單向持倉下的多頭、空頭與零持倉；
- 分開計算合約名義價值、價格未實現損益與資金費現金流；
- 分列現貨錢包與期貨錢包，證明資金費入帳不會暗中挪用現貨餘額；
- 用方向、錢包與權益核對式抓出資金費正負號或重複入帳的錯誤。

## 問題情境：收到資金費，不代表整體正在賺錢

永續合約（perpetual futures contract）沒有到期日，可以建立多頭或空頭曝險，
卻不代表帳戶真的買進或借出了等量 BTC。持倉期間還可能在指定時點支付或收取
資金費（funding payment）。因此下面這句話證據不足：

```text
我做空永續，而且這一期收到資金費，所以帳戶一定獲利。
```

空頭可能收到正資金費，同時因標記價格上漲承受更大的價格損失；反過來，多頭
可能支付資金費，價格損益仍為正。若把價格損益、資金費與錢包餘額混成一欄，
很容易把同一筆收入加兩次，或把未實現損益當成已經可以轉出的現金。

本章只處理 U 本位線性永續、單向持倉、現貨／期貨雙錢包與單次資金費結算。
所有價格、費率與錢包數字都是固定教學輸入，不是即時行情、帳戶費率、可成交
價格或獲利承諾。槓桿、Cross／Isolated、初始／維持保證金與強制平倉留到第 8 章；
基差、多腿對沖與策略收益圖留到第 9 章。

## 執行前預測

把以下三列視為互斥的獨立快照；單向持倉不是讓同一帳戶同時持有這三列：

| 固定輸入 | 多頭快照 | 空頭快照 | 零持倉快照 |
|---|---:|---:|---:|
| `signed_qty` | `+0.50000000 BTC` | `-0.50000000 BTC` | `0 BTC` |
| 開倉均價 | `20,000.00 USDT/BTC` | `20,000.00 USDT/BTC` | 不適用 |
| 結算時點 `t_f` 標記價格 | `20,400.00 USDT/BTC` | `20,400.00 USDT/BTC` | `20,400.00 USDT/BTC` |
| `t_f` 教學資金費率 | `+0.0005` | `+0.0005` | `+0.0005` |
| 結算前期貨錢包餘額 | `2,000.00 USDT` | `2,000.00 USDT` | `2,000.00 USDT` |

每個快照另有完全相同的現貨錢包：`5,000.00 USDT` 與 `0.25000000 BTC`。先不要
往下計算，請寫下：

1. 三列的合約名義價值各是多少？正負方向會不會讓名義價值變成負數？
2. 標記價格由 `20,000` 升到 `20,400` 時，多頭與空頭的未實現損益各是多少？
3. 正資金費率下，哪一列支付、哪一列收取、哪一列現金流為零？
4. 結算後三列的期貨錢包餘額與標記後權益核對值各是多少？
5. 哪些現貨餘額應被資金費事件改變？

每個答案都要帶 USDT、BTC 或 USDT/BTC 單位，並從帳戶角度寫出正負號。只寫
「多方付、空方收」還不夠；沒有數量、結算價、費率與結算時點，就不能重現金額。

## 核心概念：三種數字、兩個錢包、一個結算事件

### 永續多單不是現貨 BTC

本章的 `BTCUSDT` U 本位線性永續，用 USDT 表達價格、名義價值與損益；數量以
BTC 表示。持有 `+0.5 BTC` 永續部位是在合約市場取得正向價格曝險，不會讓現貨
錢包自動增加 `0.5 BTC`。持有 `-0.5 BTC` 則是負向曝險，也不會讓現貨錢包出現
一筆借入 BTC。

Binance 官方 Academy 將永續合約描述為沒有到期日的衍生品，並將資金費描述為
多空持倉者之間的週期性支付；本章於 2026-07-15 重新查證其
[資金費說明](https://www.binance.com/en/academy/articles/what-are-funding-rates-in-crypto-markets)。
這項外部來源支持產品概念與支付方向，不支持本章教學價格、費率或任何真實交易
決定。

### 單向持倉只保留一個淨方向

Emmet 的 `Position.signed_qty` 使用：

\\[
\begin{aligned}
Q>0 &\Rightarrow \text{多頭} \\\\
Q<0 &\Rightarrow \text{空頭} \\\\
Q=0 &\Rightarrow \text{零持倉}
\end{aligned}
\\]

同一標的在一個快照只有一個帶方向的淨數量，所以本章的多頭、空頭與零持倉是
三個互斥案例。Binance 目前的 USDⓈ-M API 也明確區分 One-way Mode 與 Hedge Mode；
官方文件以 `dualSidePosition=false` 表示 One-way Mode，且下單欄位在該模式使用
`positionSide=BOTH`。這只用來核對名詞；`v0.3.0` 沒有讓讀者切換交易所模式的正式
入口，本章也不要求 API key 或呼叫私人端點。

### 名義價值永遠先取部位絕對值

令 `Q` 為帶方向的合約數量，`m` 為指定時點標記價格。U 本位線性案例的名義價值
為：

\\[
\begin{aligned}
N &= |Q|\times m \\\\
\mathrm{BTC}\times\frac{\mathrm{USDT}}{\mathrm{BTC}}
  &=\mathrm{USDT}
\end{aligned}
\\]

多頭 `+0.5 BTC` 與空頭 `-0.5 BTC` 在相同標記價格下有相同名義價值。名義價值
描述曝險規模，不是錢包餘額、保證金、成本或損益；不能因為空頭方向為負，就把
`N` 寫成負數。

### 價格損益保留部位正負號

令 `p_entry` 為開倉均價，未實現損益 `U` 為：

\\[
U=(m-p\_{\mathrm{entry}})\times Q
\\]

當 `m > p_entry`：

- `Q > 0` 的多頭得到正的未實現損益；
- `Q < 0` 的空頭得到負的未實現損益。

這是標記價格下的估值差，不是已經存入期貨錢包的現金。標記價格、指數價格與
成交價格為何不能混用，以及它們和強平的關係，會在第 8 章處理。

### 資金費是另一筆現金流

以帳戶為觀點，Emmet `v0.3.0` 的結算公式為：

\\[
\mathrm{CF}\_{\mathrm{funding}}=-Q\times m\_f\times r\_f
\\]

其中 `m_f` 是結算時點標記價格，`r_f` 是該次資金費率，`CF_funding` 的單位為
USDT。在本章正費率情境中：

\\[
\begin{aligned}
Q>0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}<0
  &&\text{（多頭支付）} \\\\
Q<0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}>0
  &&\text{（空頭收取）} \\\\
Q=0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}=0
\end{aligned}
\\]

負費率時方向反轉。Binance 官方 Academy 目前也說明正費率由多頭支付空頭、負
費率則相反。費率會改變，不能把「目前為正」外推成下一期仍為正，更不能把收到
資金費寫成保證收益。

### 結算週期不能硬編成固定八小時

每筆計算都要保存結算時點。Binance USDⓈ-M 官方開發者文件的
[資金費歷史](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)
分列 `fundingRate`、`fundingTime` 與該次費用對應的 `markPrice`；資金費資訊另有
`fundingIntervalHours`。這表示研究不能只保存一個費率後自行補固定週期。

Emmet 的 `FundingRate` 同樣把費率與交易所時戳綁在一起；結算時若缺少有效標記
價格或只拿到未來／過期價格，會 fail closed。這些是已發布內部模型與測試證據，
不是本章的讀者交易入口。

### 雙錢包不是一個可以任意混用的總額

`AccountState` 分成：

| 錢包 | 保存什麼 | 本章資金費事件的影響 |
|---|---|---|
| `SpotWallet` | 每項現貨資產各自的 `free`／`locked` 餘額 | 不改變 BTC 或 USDT 餘額 |
| `FuturesWallet` | 期貨已實現錢包餘額與合約部位 | `wallet_balance += CF_funding` |

現貨與期貨之間若要移動資金，必須是另一筆明示且受控的 transfer；資金費入帳
本身不是 transfer。`v0.3.0` 已有雙錢包與 in-flight transfer 的模型，但完整 Engine
操作入口仍未發布，因此本章不示範自動劃轉。

為核對是否重複入帳，本章定義一個期貨標記後權益核對值：

\\[
\begin{aligned}
W\_{\mathrm{after}}
  &=W\_{\mathrm{before}}+\mathrm{CF}\_{\mathrm{funding}} \\\\
E\_{\mathrm{futures}}
  &=W\_{\mathrm{after}}+U \\\\
  &=W\_{\mathrm{before}}+\mathrm{CF}\_{\mathrm{funding}}+U
\end{aligned}
\\]

`W_after` 已經包含資金費，所以不能再計算 `W_after + U + CF_funding`。`N` 只是
曝險規模，也不能加進權益。`v0.3.0` 的 `FuturesWallet.margin_balance` 使用同一條
`wallet_balance + unrealized_pnl` 恆等式；可用餘額、初始與維持保證金另屬第 8 章。

完整符號、單位與適用前提見[附錄 B](../appendices/b-trading-pnl-formulas.md)，產品
模式與雙錢包最小查表見[附錄 E](../appendices/e-perpetual-binance-mechanics.md)。

## 固定案例：逐格核對三個互斥快照

### 共同輸入

\\[
\begin{aligned}
p\_{\mathrm{entry}}&=20{,}000.00\ \frac{\mathrm{USDT}}{\mathrm{BTC}} \\\\
m\_f&=20{,}400.00\ \frac{\mathrm{USDT}}{\mathrm{BTC}} \\\\
r\_f&=+0.0005 \\\\
W\_{\mathrm{before}}&=2{,}000.00\ \mathrm{USDT} \\\\
\mathrm{SpotWallet}\_{\mathrm{before}}
  &=5{,}000.00\ \mathrm{USDT}+0.25000000\ \mathrm{BTC}
\end{aligned}
\\]

`0.0005`、價格與錢包餘額都是教學輸入。`t_f` 代表一筆固定但虛構的結算時點，
不是目前或歷史 Binance 費率快照。

### 多頭快照：價格賺 `200`，資金費付 `5.10`

\\[
\begin{aligned}
Q\_{\mathrm{long}}&=+0.50000000\ \mathrm{BTC} \\\\
N\_{\mathrm{long}}
  &=|+0.5|\times20{,}400
   =10{,}200.00\ \mathrm{USDT} \\\\
U\_{\mathrm{long}}
  &=(20{,}400-20{,}000)\times(+0.5)
   =+200.00\ \mathrm{USDT} \\\\
\mathrm{CF}\_{\mathrm{long}}
  &=-(+0.5)\times20{,}400\times0.0005
   =-5.10\ \mathrm{USDT} \\\\
W\_{\mathrm{after,long}}
  &=2{,}000.00-5.10
   =1{,}994.90\ \mathrm{USDT} \\\\
E\_{\mathrm{futures,long}}
  &=1{,}994.90+200.00
   =2{,}194.90\ \mathrm{USDT}
\end{aligned}
\\]

多頭在這個正費率事件支付資金費，但價格未實現損益更大，所以標記後核對值仍
高於結算前錢包。這不表示部位已平倉或 `200 USDT` 已實現。

### 空頭快照：資金費收 `5.10`，價格虧 `200`

\\[
\begin{aligned}
Q\_{\mathrm{short}}&=-0.50000000\ \mathrm{BTC} \\\\
N\_{\mathrm{short}}
  &=|-0.5|\times20{,}400
   =10{,}200.00\ \mathrm{USDT} \\\\
U\_{\mathrm{short}}
  &=(20{,}400-20{,}000)\times(-0.5)
   =-200.00\ \mathrm{USDT} \\\\
\mathrm{CF}\_{\mathrm{short}}
  &=-(-0.5)\times20{,}400\times0.0005
   =+5.10\ \mathrm{USDT} \\\\
W\_{\mathrm{after,short}}
  &=2{,}000.00+5.10
   =2{,}005.10\ \mathrm{USDT} \\\\
E\_{\mathrm{futures,short}}
  &=2{,}005.10-200.00
   =1{,}805.10\ \mathrm{USDT}
\end{aligned}
\\]

空頭確實收到資金費，整體標記後核對值卻下降。只展示 `+5.10 USDT` 而隱藏
`-200.00 USDT`，會把資金費收入誤寫成整體獲利。

### 零持倉快照：保留結算事實，金額為零

\\[
\begin{aligned}
Q\_{\mathrm{flat}}&=0\ \mathrm{BTC} \\\\
N\_{\mathrm{flat}}&=0\ \mathrm{USDT} \\\\
U\_{\mathrm{flat}}&=0\ \mathrm{USDT} \\\\
\mathrm{CF}\_{\mathrm{flat}}
  &=-(0)\times20{,}400\times0.0005
   =0\ \mathrm{USDT} \\\\
W\_{\mathrm{after,flat}}&=2{,}000.00\ \mathrm{USDT} \\\\
E\_{\mathrm{futures,flat}}&=2{,}000.00\ \mathrm{USDT}
\end{aligned}
\\]

在 Emmet 的帳戶快照裡，零持倉通常表示沒有該 `Position` 物件；上式把 `Q=0`
當作手算縮寫。`FundingSettlement` 仍可產生金額為零的已入帳事實，使時點證據不因
沒有部位而消失。

### 雙錢包手算帳

| 快照 | 方向／`Q` | 合約名義價值 | 價格未實現 PnL | 資金費現金流 | 結算後期貨錢包 | 期貨標記後權益核對值 | 結算後現貨餘額 |
|---|---:|---:|---:|---:|---:|---:|---|
| 多頭 | `+0.50000000 BTC` | `10,200.00 USDT` | `+200.00 USDT` | `-5.10 USDT` | `1,994.90 USDT` | `2,194.90 USDT` | `5,000.00 USDT + 0.25000000 BTC` |
| 空頭 | `-0.50000000 BTC` | `10,200.00 USDT` | `-200.00 USDT` | `+5.10 USDT` | `2,005.10 USDT` | `1,805.10 USDT` | `5,000.00 USDT + 0.25000000 BTC` |
| 零持倉 | `0 BTC` | `0 USDT` | `0 USDT` | `0 USDT` | `2,000.00 USDT` | `2,000.00 USDT` | `5,000.00 USDT + 0.25000000 BTC` |

三列的現貨 BTC 與 USDT 原始餘額完全相同。這不代表現貨資產的市場估值永遠不
變，而是只證明本章這筆 funding posting 沒有改寫 `SpotWallet`。

## 四組驗收 oracle

### 方向與名義價值

\\[
\begin{aligned}
\operatorname{sign}(U)
  &=\operatorname{sign}\!\left((m\_f-p\_{\mathrm{entry}})Q\right) \\\\
N&=|Q|\,m\_f\ge 0
\end{aligned}
\\]

相同價格下，多空名義價值相同；價格上漲時多頭 PnL 為正、空頭為負。

### 資金費正負號

在 `r_f > 0`、`m_f > 0` 時：

\\[
\begin{aligned}
Q>0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}<0 \\\\
Q<0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}>0 \\\\
Q=0 &\Rightarrow \mathrm{CF}\_{\mathrm{funding}}=0
\end{aligned}
\\]

### 雙錢包隔離

\\[
\begin{aligned}
\mathrm{SpotWallet}\_{\mathrm{after}}
  &=\mathrm{SpotWallet}\_{\mathrm{before}} \\\\
\mathrm{FuturesWallet}\_{\mathrm{after}}
  &=\mathrm{FuturesWallet}\_{\mathrm{before}}
    +\mathrm{CF}\_{\mathrm{funding}}
\end{aligned}
\\]

若 funding 事件同時改變現貨 USDT 或 BTC，必須停止；那是未說明的 transfer 或
第二筆資產流，不是本章資金費公式的一部分。

### 不重複入帳

\\[
E\_{\mathrm{futures}}
=W\_{\mathrm{before}}+\mathrm{CF}\_{\mathrm{funding}}+U
=W\_{\mathrm{after}}+U
\\]

左右兩條路必須完全相等。不能再加名義價值，也不能在 `W_after` 後再加一次
`CF_funding`。

## 動手驗證：用字串建立的 `Decimal` 重算

先完成[實作準備](../front-matter/setup.md)，在固定配套 worktree 核對版本與乾淨
狀態後執行。這段程式只重算三個互斥教學快照，不連交易所、不下單，也不使用
API key：

```bash
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv run python - <<'PY'
from decimal import Decimal as D

entry = D("20000.00")
mark = D("20400.00")
rate = D("0.0005")
futures_wallet_before = D("2000.00")
spot_before = (D("5000.00"), D("0.25000000"))  # USDT, BTC

quantities = {
    "long": D("0.50000000"),
    "short": D("-0.50000000"),
    "flat": D("0"),
}
rows = {}

for name, qty in quantities.items():
    notional = abs(qty) * mark
    unrealized = (mark - entry) * qty
    funding = -qty * mark * rate
    if funding == 0:
        funding = D("0")  # 顯示時把 Decimal 負零正規化為零
    futures_wallet_after = futures_wallet_before + funding
    marked_equity = futures_wallet_after + unrealized
    spot_after = spot_before
    rows[name] = (notional, unrealized, funding, futures_wallet_after, marked_equity)

    assert notional >= 0
    assert spot_after == spot_before
    assert marked_equity == futures_wallet_before + funding + unrealized
    print(
        f"{name}: qty={qty:+.8f} BTC "
        f"notional={notional:.2f} USDT "
        f"upnl={unrealized:+.2f} USDT "
        f"funding={funding:+.6f} USDT "
        f"futures_wallet={futures_wallet_after:.6f} USDT "
        f"marked_equity={marked_equity:.6f} USDT"
    )

assert rows["long"][1] == D("200.0000000000")
assert rows["short"][1] == D("-200.0000000000")
assert rows["long"][2] == D("-5.100000000000")
assert rows["short"][2] == D("5.100000000000")
assert rows["flat"][2] == 0

print(f"spot_usdt={spot_before[0]:.2f} USDT")
print(f"spot_btc={spot_before[1]:.8f} BTC")
print("directional-pnl=PASS")
print("funding-sign=PASS")
print("dual-wallet-isolation=PASS")
print("no-double-count=PASS")
PY
```

版本命令應輸出完整
`c999965e5cc923281541409cda9502beb93b8a60`，`status --short` 應無輸出。固定案例
預期輸出為：

```text
long: qty=+0.50000000 BTC notional=10200.00 USDT upnl=+200.00 USDT funding=-5.100000 USDT futures_wallet=1994.900000 USDT marked_equity=2194.900000 USDT
short: qty=-0.50000000 BTC notional=10200.00 USDT upnl=-200.00 USDT funding=+5.100000 USDT futures_wallet=2005.100000 USDT marked_equity=1805.100000 USDT
flat: qty=+0.00000000 BTC notional=0.00 USDT upnl=+0.00 USDT funding=+0.000000 USDT futures_wallet=2000.000000 USDT marked_equity=2000.000000 USDT
spot_usdt=5000.00 USDT
spot_btc=0.25000000 BTC
directional-pnl=PASS
funding-sign=PASS
dual-wallet-isolation=PASS
no-double-count=PASS
```

程式以 `Decimal("0.0005")` 等字串建立輸入，避免先把二進位 float 近似帶進會計。
顯示位數只服務本章核對；真實合約的數量精度、規則與費率必須依相符時點的
第一手資料重新取得。

## 系統對照：模型與會計已發布，讀者交易入口尚未發布

固定在 `v0.3.0` 時，可以核對到：

| 本章概念 | 已發布位置 | 能支持的宣稱 | 不能延伸成的宣稱 |
|---|---|---|---|
| 單向 signed position | `quant.common.models.account.Position` | 正多、負空；`notional = abs(signed_qty) × mark_price`；`unrealized_pnl = (mark-entry) × signed_qty` | 不表示 Hedge Mode 已支援，也不會替讀者切換交易所帳戶模式 |
| 現貨／期貨雙錢包 | `SpotWallet`、`FuturesWallet`、`AccountState` | 現貨逐資產保存餘額；期貨保存 wallet balance 與 positions；兩者是不同欄位 | 不表示資金可被策略任意跨錢包挪用，或完整 transfer 入口已發布 |
| 資金費結算 | `FundingSettlement`、`FundingPosting` | 使用結算時點、有效標記價格、signed quantity 與 Decimal 費率計算 `-Q×m×r`；零倉仍留下零現金流 posting | 不代表本章費率是現行 Binance 費率，也不預測下一次方向 |
| 唯一入帳 | `AccountingLedger.apply_funding` | 核對數量與現金流後，只更新期貨 wallet balance／position；重複 symbol＋timestamp 會拒絕 | 不等於已發布完整 TradingEngine、報告或讀者交易 CLI |

作者在固定、乾淨的配套 worktree 執行 `uv lock --check`，並完整重跑
`tests/unit/test_models_account.py` 與 `tests/unit/test_engine_funding.py`，結果為
`40 passed`。測試覆蓋多空未實現損益、雙錢包、正費率多方支付／空方收取、零倉
零現金流與期貨錢包入帳。測試也含第 8 章才會解釋的保證金路徑；本章不因測試
存在就提前教授那些內容。

## 證據顯示什麼

- 多頭與空頭的名義價值都是 `10,200.00 USDT`，方向不會讓曝險規模變成負數；
- 同一個價格上漲使多頭未實現 PnL 為 `+200.00`、空頭為 `-200.00 USDT`；
- 正費率下多頭支付 `5.10`、空頭收取 `5.10 USDT`，零倉現金流為零；
- funding posting 後只改變期貨錢包，現貨 `5,000 USDT` 與 `0.25 BTC` 原始餘額
  沒有被暗中挪用；
- 空頭雖收到資金費，標記後權益核對值仍只有 `1,805.10 USDT`，不能把 funding
  receipt 當成整體獲利；
- `W_after + U` 與 `W_before + CF_funding + U` 完全相等，資金費沒有重複入帳。

這些證據只支持固定輸入下的方向、算術、錢包邊界與已發布內部模型，不支持
真實成交、費率持續性、保證金安全或策略績效。

## 結果解讀與決定

| 結果 | 判定 | 下一步 |
|---|---|---|
| 四組 oracle 全部通過 | 雙錢包與 funding 手算可接受 | 保存時點、費率與標記價格；再進入第 8 章風險計算 |
| 名義價值因空頭而為負 | 方向與規模混淆 | 對 `Q` 取絕對值算名義價值，方向只留在 PnL／現金流 |
| 資金費方向與 `-Q×m×r` 不符 | 正負號錯誤 | 停止判讀，逐格核對帳戶觀點與費率符號 |
| funding 同時改變現貨餘額 | 資產流缺口 | 找出明示 transfer 或錯誤寫入；不能把它藏在 funding |
| `W_after + U + funding` 才對得上自述結果 | 資金費被重複計算 | 重建期貨錢包與權益欄，不發布績效結論 |
| 只看到資金費收入，沒有價格 PnL | 證據不足 | no-go；兩欄與結算後權益必須同時提供 |

本章通過只代表你能核對多空與資金費，不代表部位具備足夠保證金，更不代表應該
開倉。那些問題需要第 8 章的標記價格、保證金與強平證據。

## 常見陷阱

**陷阱一：永續多單等於現貨資產。** 合約曝險不會讓 `SpotWallet` 自動增加 BTC。

**陷阱二：空頭名義價值寫成負數。** 方向由 `signed_qty` 表示，名義價值使用
絕對值。

**陷阱三：把正資金費率寫成所有人都支付。** 正費率多頭支付、空頭收取；負
費率方向相反。

**陷阱四：收到資金費就宣稱獲利。** 價格未實現 PnL 可能更負，兩項必須分欄。

**陷阱五：把未實現 PnL 加進 wallet balance。** 它只進標記後權益核對值；尚未
平倉時不是已實現現金。

**陷阱六：資金費加兩次。** `W_after` 已含 funding，不能再加一次 cash flow。

**陷阱七：硬編每八小時結算。** 保存每筆 `fundingTime`；標的的結算間隔可能
調整。

**陷阱八：把現貨餘額當成期貨可用保證金。** 兩個錢包沒有明示 transfer 就不能
混用；聯合保證金或多資產模式也不是本章假設。

**陷阱九：用本章費率冒充市場事實。** `0.0005` 只為教學；真實費率與方向會變。

## 對系統的回饋

一份可審核的 funding 報告至少應保存：

- 標的、單向／雙向模式、帶符號數量與結算前後部位；
- funding timestamp、費率、結算標記價格與資料來源；
- 合約名義價值、價格未實現 PnL 與 funding cash flow 的獨立欄位；
- 現貨與期貨錢包結算前後快照，以及任何 transfer 的獨立事件 ID；
- `wallet_after = wallet_before + funding` 與 `marked = wallet_after + U` 兩條 oracle；
- 零倉與零費率也保留明確的零現金流結算事實；
- 重複 symbol＋timestamp、過期標記價格或現金流不一致時 fail closed。

若報告只有「funding income」而沒有價格 PnL、結算標記價格與雙錢包變化，應形成
schema、測試或文件 finding，不應讓策略自行維護另一套帳。

## 小結與練習

單向持倉用正數表示多頭、負數表示空頭；名義價值取絕對值，價格 PnL 與資金費
則保留方向。資金費是期貨錢包的獨立現金流，不是現貨 transfer，也不能替代價格
損益。結算後只要核對 `W_after + U = W_before + funding + U`，就能抓出最常見的
重複入帳。

請做兩個互斥情境，每次只改一項：

1. 把費率改成 `-0.0003`，重算多頭、空頭與零持倉的 funding cash flow；
2. 保持正費率，把標記價格改成 `19,600.00 USDT/BTC`，重算名義價值、價格 PnL、
   funding、期貨錢包與標記後權益核對值。

每個情境都要保留現貨錢包前後快照。若只改了最後總額、無法回推五個分欄，
不算完成。

## 專業紀錄：雙錢包、signed position 與資金費手算帳

在自己的工作區保存；不要提交 API key、帳戶餘額或私人交易輸出：

| 欄位 | 你的紀錄 |
|---|---|
| 情境名稱與驗證日期 |  |
| 配套 `tag@commit` |  |
| 標的、U 本位線性前提與價格單位 |  |
| 單向持倉模式與 `signed_qty` |  |
| 開倉均價、結算標記價格與結算時點 |  |
| 資金費率、來源與是否為教學輸入 |  |
| 合約名義價值 |  |
| 價格未實現 PnL |  |
| funding cash flow |  |
| 結算前後期貨 wallet balance |  |
| 結算前後現貨 BTC／USDT 餘額 |  |
| 標記後權益與不重複入帳 oracle |  |
| continue／no-go 與理由 |  |
| 可形成的系統或報告改進 |  |

最低通過條件：

- `signed_qty`、價格、名義價值、PnL、資金費與每項錢包餘額都有單位；
- 多頭、空頭、零持倉的正負號能由公式回推；
- 現貨與期貨錢包分欄，未說明的 transfer 為零；
- 價格 PnL 與 funding 分欄，標記後權益只各計一次；
- 結算時點與費率來源明示，教學數字不冒充現況；
- 任一 oracle 失敗時停止，不以資金費收入掩蓋差異。

## 作者驗證紀錄

- 驗證對象：U 本位線性永續的 signed quantity、名義價值與多空未實現 PnL、正費率多方支付／空方收取、零倉零資金費、現貨／期貨雙錢包隔離及資金費單次入帳
- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證命令：在乾淨隔離 worktree 核對 HEAD、`status --short` 與 `uv lock --check`；執行本章 `uv run python` 固定案例；執行 `uv run pytest tests/unit/test_models_account.py tests/unit/test_engine_funding.py -q`；查閱 Binance 官方 Academy 資金費說明與 USDⓈ-M 官方開發者文件的 position mode、funding history／info 欄位
- 通過結果：固定案例輸出 `directional-pnl=PASS`、`funding-sign=PASS`、`dual-wallet-isolation=PASS`、`no-double-count=PASS`；配套聚焦測試 `40 passed`；官方來源確認永續無到期、正費率多方支付空方、One-way Mode 與逐筆 funding 時點／標記價格欄位
- 待處理差異：價格、費率與錢包餘額皆為教學輸入；本章不含真實成交、槓桿、Cross／Isolated、初始／維持保證金、強平、ADL、下架、多腿或策略收益；`v0.3.0` 尚無正式讀者交易／報告入口
