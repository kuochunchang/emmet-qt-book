# 一筆現貨交易的完整故事

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc9`
> 內容狀態：穩定概念
> 最後驗證日期：2026-07-15

## 學習目標

讀完本章後，你應該能夠：

- 從交易對辨認基礎資產（base asset）與報價資產（quote asset）；
- 寫出價格、數量、名義價值、現金與部位各自的單位；
- 用正負號記錄買入、賣出與累積部位，不把賣出名義價值誤當虧損；
- 分開核對現金流、資產數量、未實現損益、已實現損益與標記權益；
- 使用資產流與損益恆等式判斷一份現貨手算帳能否通過。

## 問題情境：價格上漲，不代表錢已經入帳

假設你有 `10,000.00 USDT`，以固定教學價格買入 `0.25 BTC`，持有一段時間後
看到價格上漲，最後全部賣出。有人把中途的帳戶寫成：

```text
買入花了 5,000 USDT
現在 BTC 值 5,300 USDT
所以現金是 5,300 USDT，已經賺 300 USDT
```

這段話混在一起的至少有三件事：錢包裡還剩多少報價資產、持有多少基礎資產，
以及若用某個價格重估，部位的價值是多少。價格上漲會改變重估價值，但在尚未
賣出時，不會自動把報價資產存入錢包。

本章只處理一個單一買入批次、之後全部賣出的現貨案例。為了先把帳務骨架看清楚，
案例明確假設手續費、spread、滑點、利息、稅務與外部入出金皆為零。這些成本不是真的
不存在；它們會在第 6 章加入。在成本尚未加入前，本章數字不能拿來判斷策略是否值得
交易，也不構成任何歷史價格或投資建議。

## 執行前預測

固定輸入如下：

| 時點 | 動作或估值 | 價格 | 數量 |
|---|---|---:|---:|
| `t0` | 初始狀態 | — | `0 BTC` |
| `t1` | 買入 | `20,000.00 USDT/BTC` | `0.25 BTC` |
| `t2` | 持有並重估 | `21,200.00 USDT/BTC` | `0.25 BTC` |
| `t3` | 全部賣出 | `20,800.00 USDT/BTC` | `0.25 BTC` |

先不要往下算。請預測並寫下：

1. `t1` 買入後，各剩多少 BTC 與 USDT？
2. `t2` 的未實現損益與標記權益各是多少？錢包裡的 USDT 是否改變？
3. `t3` 賣出後，已實現損益、未實現損益與最終 USDT 各是多少？
4. 哪兩條等式可以抓出數量正負號或現金流方向寫反的錯誤？

每個答案都要帶單位。只寫 `5,000` 或 `+300`，無法判斷那是 BTC、USDT、部位價值
還是損益，不算可複核的預測。

## 核心概念：先固定報價方向與單位

### 基礎資產與報價資產

在 `BTC/USDT` 這個寫法中：

- BTC 是基礎資產，也就是交易數量所描述的資產；
- USDT 是報價資產，也就是用來表達價格與本章損益的資產；
- `20,000 USDT/BTC` 表示每 `1 BTC` 的價格是 `20,000 USDT`。

Binance 官方 Spot API 的 `exchangeInfo` 回應把兩者分成 `baseAsset` 與
`quoteAsset` 欄位；本章查證的是固定於官方文件 commit
[`4987e707`](https://github.com/binance/binance-spot-api-docs/blob/4987e707f84f20d736ee6a2bcb71396111cffee1/rest-api.md#exchange-information)
的欄位語義，不是當下價格、可交易狀態或帳戶資訊。交易所規則與標的狀態會改變，
真正下單前仍須重新取得相符時點的規則快照。

### 價格乘數量，單位必須消掉

令 `p` 是價格、`q` 是非負成交數量，名義價值（notional）為：

\\[
\begin{aligned}
N &= p \times q \\\\
\left(\frac{\mathrm{USDT}}{\mathrm{BTC}}\right)\times \mathrm{BTC}
  &= \mathrm{USDT}
\end{aligned}
\\]

因此本例買入名義價值為：

\\[
20{,}000.00\ \frac{\mathrm{USDT}}{\mathrm{BTC}}
\times 0.25\ \mathrm{BTC}
= 5{,}000.00\ \mathrm{USDT}
\\]

名義價值是這筆成交交換了多少報價資產，不是獲利。賣出時也用正的名義價值；
現金流方向另外記錄，不能把價格或原始成交數量寫成負數來暗示方向。

### 成交數量與部位的正負號

本章把每次成交造成的部位變化記為 `ΔQ`：

\\[
\begin{aligned}
\text{買入：}\quad \Delta Q &= +q \\\\
\text{賣出：}\quad \Delta Q &= -q \\\\
Q\_{\mathrm{after}} &= Q\_{\mathrm{before}} + \Delta Q
\end{aligned}
\\]

所以買入 `0.25 BTC` 後，`Q = +0.25 BTC`；全部賣出時，`ΔQ = -0.25 BTC`，
最後 `Q = 0 BTC`。原始成交數量 `q` 仍然是正數。

正負號是曝險的會計記法，不是借幣或放空授權。本章是沒有借貸的現貨帳戶，
基礎資產餘額不得小於零；若賣出會讓 `Q < 0`，這個案例就應拒絕，而不是把它
悄悄解釋成永續空單。

### 現金、部位價值與權益是三個不同欄位

令 `C` 是 USDT 餘額，`m` 是用來重估的 BTC 價格：

\\[
\begin{aligned}
V &= Q \times m &&[\mathrm{USDT}] \\\\
E &= C + V &&[\mathrm{USDT}] \\\\
U &= (m-p\_{\mathrm{buy}})\times Q &&[\mathrm{USDT}]
\end{aligned}
\\]

此處的 `m` 只是本例固定的現貨重估輸入，不是第 8 章會討論的永續合約標記價格。
標記權益是「若用 `m` 換算」的估值，不等於錢包現金，也不保證能以 `m` 全數成交。

本例只有一個買入批次，且最後一次全部賣出，所以已實現損益可以寫成：

\\[
R=(p\_{\mathrm{sell}}-p\_{\mathrm{buy}})\times q
\quad[\mathrm{USDT}]
\\]

這條式子的適用前提是同一批數量、全部平掉、沒有費用或其他現金流。多批買入時
必須先固定成本基礎方法；稅務成本認定也不能由本章公式代替。完整符號與適用前提
可查[附錄 B](../appendices/b-trading-pnl-formulas.md)。

## 手算帳：從買入追到全部賣出

### `t0`：初始狀態

\\[
\begin{aligned}
C\_0 &= 10{,}000.00\ \mathrm{USDT} \\\\
Q\_0 &= 0\ \mathrm{BTC} \\\\
E\_0 &= 10{,}000.00\ \mathrm{USDT}
\end{aligned}
\\]

### `t1`：買入 `0.25 BTC`

\\[
\begin{aligned}
N\_{\mathrm{buy}}
  &= 0.25\ \mathrm{BTC}
     \times 20{,}000.00\ \frac{\mathrm{USDT}}{\mathrm{BTC}} \\\\
  &= 5{,}000.00\ \mathrm{USDT} \\\\
C\_1 &= 10{,}000.00-5{,}000.00
     = 5{,}000.00\ \mathrm{USDT} \\\\
Q\_1 &= 0+0.25
     = 0.25\ \mathrm{BTC}
\end{aligned}
\\]

以成交價重估時，`0.25 BTC` 價值仍是 `5,000.00 USDT`，因此 `E1` 仍為
`10,000.00 USDT`。只是資產組成從全 USDT 變成 USDT 加 BTC；在本章零成本假設下，
買入本身不創造損益。

### `t2`：價格升至 `21,200.00 USDT/BTC`

\\[
\begin{aligned}
V\_2 &= 0.25\times21{,}200.00
     = 5{,}300.00\ \mathrm{USDT} \\\\
U\_2 &= (21{,}200.00-20{,}000.00)\times0.25
     = 300.00\ \mathrm{USDT} \\\\
E\_2 &= 5{,}000.00+5{,}300.00
     = 10{,}300.00\ \mathrm{USDT}
\end{aligned}
\\]

此時 `C2` 仍是 `5,000.00 USDT`。`300.00 USDT` 是未實現損益；把它再加進
現金，或同時加進部位價值與權益，會重複計算。

### `t3`：以 `20,800.00 USDT/BTC` 全部賣出

\\[
\begin{aligned}
N\_{\mathrm{sell}} &= 0.25\times20{,}800.00
                    = 5{,}200.00\ \mathrm{USDT} \\\\
C\_3 &= 5{,}000.00+5{,}200.00
     = 10{,}200.00\ \mathrm{USDT} \\\\
Q\_3 &= 0.25-0.25
     = 0\ \mathrm{BTC} \\\\
R\_3 &= (20{,}800.00-20{,}000.00)\times0.25
     = 200.00\ \mathrm{USDT} \\\\
U\_3 &= 0\ \mathrm{USDT} \\\\
E\_3 &= 10{,}200.00\ \mathrm{USDT}
\end{aligned}
\\]

`t2` 的 `300.00 USDT` 並沒有被鎖定。實際賣出價低於重估價，所以最後實現的是
`200.00 USDT`。已實現與未實現損益是狀態分類，不是兩筆可以相加的收入。

### 逐格稽核表

| 時點 | BTC 變化 | BTC 餘額 | USDT 現金流 | USDT 餘額 | 部位價值 | 未實現 PnL | 已實現 PnL | 標記權益 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `t0` | `0` | `0` | `0` | `10,000.00` | `0` | `0` | `0` | `10,000.00` |
| `t1` | `+0.25` | `0.25` | `-5,000.00` | `5,000.00` | `5,000.00` | `0` | `0` | `10,000.00` |
| `t2` | `0` | `0.25` | `0` | `5,000.00` | `5,300.00` | `300.00` | `0` | `10,300.00` |
| `t3` | `-0.25` | `0` | `+5,200.00` | `10,200.00` | `0` | `0` | `200.00` | `10,200.00` |

表頭中的 BTC 欄位單位都是 BTC，其餘數值欄位都是 USDT；`t2` 的部位價值使用
重估價，其餘成交列使用該列成交價。

## 兩組驗收 oracle

第一組只檢查資產流，不使用市場重估：

\\[
\begin{aligned}
Q\_{\mathrm{final}}
  &= Q\_{\mathrm{initial}}+Q\_{\mathrm{buy}}-Q\_{\mathrm{sell}} \\\\
  &= 0+0.25-0.25
   = 0\ \mathrm{BTC} \\\\
C\_{\mathrm{final}}
  &= C\_{\mathrm{initial}}-N\_{\mathrm{buy}}+N\_{\mathrm{sell}} \\\\
  &= 10{,}000.00-5{,}000.00+5{,}200.00 \\\\
  &= 10{,}200.00\ \mathrm{USDT}
\end{aligned}
\\]

第二組檢查損益與權益分類：

\\[
\begin{aligned}
\text{持有時：}\quad
E\_2 &= E\_0+U\_2
    = 10{,}000.00+300.00
    = 10{,}300.00\ \mathrm{USDT} \\\\
\text{平倉後：}\quad
E\_3 &= E\_0+R\_3
    = 10{,}000.00+200.00
    = 10{,}200.00\ \mathrm{USDT}
\end{aligned}
\\]

任一等式不成立，都先停止：常見根因是交易方向寫反、把 BTC 與 USDT 相加、把
未實現損益當成現金，或漏掉一筆外部入出金。這裡的「守恆」是依明示交易與外部
現金流核對每項資產，不是聲稱標記權益不會隨價格變動。

## 動手驗證：用字串建立的 `Decimal` 重算

先完成[實作準備](../front-matter/setup.md)，然後在固定配套 worktree 執行。這段
程式只重算本章固定數字，不呼叫交易所、不下單，也不使用 API key：

```bash
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv run python - <<'PY'
from decimal import Decimal as D

cash0 = D("10000.00")
qty = D("0.25")
buy_price = D("20000.00")
mark_price = D("21200.00")
sell_price = D("20800.00")

buy_delta = qty
sell_delta = -qty
buy_notional = abs(buy_delta) * buy_price
cash_after_buy = cash0 - buy_notional
base_after_buy = buy_delta

mark_value = base_after_buy * mark_price
unrealized = (mark_price - buy_price) * base_after_buy
marked_equity = cash_after_buy + mark_value

sell_notional = abs(sell_delta) * sell_price
cash_final = cash_after_buy + sell_notional
base_final = base_after_buy + sell_delta
realized = (sell_price - buy_price) * qty
final_equity = cash_final + base_final * sell_price

assert base_final == D("0")
assert cash_final == D("10200.00")
assert marked_equity == cash0 + unrealized
assert final_equity == cash0 + realized

print(f"buy_delta={buy_delta:+.8f} BTC")
print(f"sell_delta={sell_delta:+.8f} BTC")
print(f"buy_notional={buy_notional:.2f} USDT")
print(f"after_buy_cash={cash_after_buy:.2f} USDT")
print(f"mark_value={mark_value:.2f} USDT")
print(f"unrealized_pnl={unrealized:.2f} USDT")
print(f"marked_equity={marked_equity:.2f} USDT")
print(f"sell_notional={sell_notional:.2f} USDT")
print(f"realized_pnl={realized:.2f} USDT")
print(f"final_cash={cash_final:.2f} USDT")
print(f"final_base={base_final:.8f} BTC")
print("asset-flow=PASS")
print("pnl-equity=PASS")
PY
```

版本命令應輸出完整
`c999965e5cc923281541409cda9502beb93b8a60`，`status --short` 應無輸出。固定案例
預期輸出為：

```text
buy_delta=+0.25000000 BTC
sell_delta=-0.25000000 BTC
buy_notional=5000.00 USDT
after_buy_cash=5000.00 USDT
mark_value=5300.00 USDT
unrealized_pnl=300.00 USDT
marked_equity=10300.00 USDT
sell_notional=5200.00 USDT
realized_pnl=200.00 USDT
final_cash=10200.00 USDT
final_base=0.00000000 BTC
asset-flow=PASS
pnl-equity=PASS
```

使用 `Decimal("0.25")` 而不是 `Decimal(0.25)`，是為了讓十進位輸入與帳面數值
一致，避免先把二進位浮點近似帶進會計。顯示到小數位不等於可以任意四捨五入
下單；交易所 tick、step 與 minimum notional 規則會在後續章節另行處理。

## 系統對照：已發布的是資產會計，不是本章損益報告

固定在 `v0.3.0` 時，可以核對到下列邊界：

| 本章概念 | 已發布系統位置 | 可以支持的宣稱 | 不能延伸成的宣稱 |
|---|---|---|---|
| base／quote | `quant.common.rules.filters.SymbolRules` | 規則快照明確保存 `base_asset`、`quote_asset`，會計欄位使用 `Decimal` | 不代表目前交易所狀態或規則永遠不變 |
| 現貨資產餘額 | `quant.common.models.account.Balance`、`SpotWallet` | 每項資產分開保存 `free` 與 `locked`；現貨是餘額制 | 不能把 BTC 與 USDT 原始數量直接相加 |
| signed exposure 與重估 | `quant.common.models.account.Position` | `notional = abs(signed_qty) × mark_price`，`unrealized_pnl = (mark_price - entry_price) × signed_qty`；spot view 槓桿固定為 1、禁帶保證金欄位 | 不表示 spot wallet 可以無借貸形成負餘額，也不代表已發布讀者報告會自動選定成本基礎 |
| 買賣資產流 | `quant.common.engine.accounting.AccountingLedger` | 現貨買入扣報價資產並增加基礎資產；賣出扣基礎資產並增加報價資產；property test 核對逐資產守恆與非負餘額 | 不證明成交價格可得、策略獲利，或費用可以忽略 |

本章的已實現損益是對單一買入批次的獨立手算 oracle。`v0.3.0` 的現貨
`AccountingLedger` 保存資產流與成交證據，但沒有在正式讀者入口交付一份會自動
選擇 spot 成本基礎的損益報告。兩者不能混稱為「系統已算出同一份報告」。作者
另在固定 worktree 執行四項聚焦測試，核對 spot view、買入、賣出與逐資產守恆。

## 證據顯示什麼

把程式輸出與手算表逐格比對，應得到：

- 買入與賣出的 `ΔQ` 正負相反，最後 BTC 回到零；
- 持有期間 USDT 現金停在 `5,000.00`，只有部位估值與未實現損益改變；
- 中途最高看到的 `300.00` 未實現損益，不是最後必然實現的金額；
- 全部賣出後，`200.00` 已實現損益恰好等於最終權益減初始權益；
- 資產流 oracle 與損益／權益 oracle 同時通過，這份零成本手算帳才閉合。

這些證據只證明算術、單位和分類在明示前提下自洽。它們沒有測試真實成交、
費率、spread、滑點、流動性或稅務，也沒有證明這筆交易值得做。

## 結果解讀與決定

| 結果 | 判定 | 下一步 |
|---|---|---|
| 兩組 oracle 與程式輸出全數一致 | 本章零成本手算帳通過 | 保存紀錄；到第 6 章加入成本後重新判定 |
| 資產流不平 | 帳務失敗 | 檢查 base／quote、`ΔQ` 正負號、成交數量與外部入出金，不計算績效 |
| 資產流平但權益式不平 | 分類或估值失敗 | 檢查重估價、成本基礎及未實現損益是否重複入帳 |
| 只有報酬結論，沒有逐格帳 | 證據不足 | no-go；不能用最終 PnL 取代交易與餘額軌跡 |

即使本章通過，決定仍是「可以進入成本練習」，不是「可以交易」。只要加入費用、
部分成交、多批部位、其他報價資產或外部現金流，這組 oracle 就必須擴充後重驗。

## 常見陷阱

**陷阱一：把交易對讀反。** `20,000 USDT/BTC` 乘上 BTC 才會得到 USDT；若單位
沒有消掉，公式方向就錯了。

**陷阱二：把名義價值當獲利。** 買入與賣出都有正的名義價值；損益來自同一批
成本與處分價值之差，不是賣出收到的全部 USDT。

**陷阱三：把未實現損益當現金。** 持有期間錢包沒有多出 `300 USDT`。估值要和
現金分欄，否則會重複計算。

**陷阱四：看到 signed quantity 就假設可以放空現貨。** 負的成交增量只表示賣出；
沒有借貸時，累積現貨餘額不能低於零。

**陷阱五：只看最終 PnL。** 即使最後 `+200` 正確，中途的數量、現金或估值寫錯，
仍可能只是兩個錯誤互相抵消。

**陷阱六：把零成本案例當成策略評估。** 本章刻意把費用與成交摩擦設為零；
第 6 章加入成本後，損益與損益兩平點都會改變。

## 對系統的回饋

如果一份報告只顯示總 PnL，卻無法重建本章表格，可以形成一項具體改進需求：

- 每筆成交保存 base、quote、價格、數量、方向與各自單位；
- 分開呈現現金、資產餘額、成本基礎、重估價、已實現與未實現 PnL；
- 標明成本基礎方法，以及重估價的來源與時點；
- 對每項資產提供成交前後的流量 oracle，不只提供總報酬；
- 在費用、部分成交或外部入出金缺失時拒絕產生看似完整的績效結論。

這不是要求策略自行維護第二套帳本。改善應落在共用會計、報告 schema 或驗證
測試，並以本章固定案例作為最小 oracle。

## 小結與練習

一筆現貨交易同時改變兩項資產：買入增加 base、減少 quote；賣出相反。價格乘
數量得到名義價值，持有部位用重估價換算後才形成標記權益。未實現 PnL 不是現金，
賣出名義價值也不是獲利。

把賣出價分別改成 `19,600.00` 與 `21,500.00 USDT/BTC`，不要改其他輸入。對每個
情境先預測，再重算 `t3` 的賣出名義價值、最終現金、已實現 PnL 與兩組 oracle。
若答案沒有單位或無法從逐格帳回推，不算完成。

## 專業紀錄：現貨手算帳

在自己的工作區保存一份紀錄；不要把帳戶資料、API key 或私人交易紀錄提交到
本書 repository。

| 欄位 | 你的紀錄 |
|---|---|
| 教學案例名稱與日期 |  |
| 配套 `tag@commit` |  |
| base／quote 與價格單位 |  |
| 初始兩項資產餘額 |  |
| 每筆 `ΔQ`、價格、數量與名義價值 |  |
| 每個時點的現金、部位價值與標記權益 |  |
| 已實現／未實現 PnL 與成本前提 |  |
| 資產流 oracle |  |
| 損益／權益 oracle |  |
| 通過／停止決定與理由 |  |
| 可形成的系統或報告改進 |  |

最低通過條件：

- base、quote、價格、數量與每個金額都有單位；
- 買入與賣出的成交數量本身為正，方向由 `ΔQ` 與現金流表達；
- 現金、部位價值、已實現與未實現 PnL 分欄，沒有重複入帳；
- 資產流與損益／權益兩組 oracle 都留下算式與結果；
- 明示零成本前提及其限制，不把結果寫成獲利承諾；
- 任何 oracle 失敗時，決定為停止並找出差異，不用最終 PnL 掩蓋。

## 作者驗證紀錄

- 驗證對象：base／quote 第一手來源、現貨資產與部位模型、買賣資產流、逐資產守恆，以及固定 `Decimal` 手算帳
- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證命令：在乾淨隔離 worktree 核對 HEAD 與 lockfile；執行本章 `uv run python` 固定案例；執行 spot view、買入、賣出與逐資產守恆四項聚焦 pytest；查閱固定於 `4987e707f84f20d736ee6a2bcb71396111cffee1` 的 Binance 官方 Spot API 文件
- 通過結果：固定案例輸出 `asset-flow=PASS`、`pnl-equity=PASS`；四項聚焦測試通過；官方 schema 與 `SymbolRules` 均明列 base／quote 欄位
- 待處理差異：本章刻意不含費率、spread、滑點、多批成本基礎、借貸、稅務或真實成交；`v0.3.0` 未提供自動選定 spot 成本基礎的正式讀者損益報告
