# 精確數值、交易所規則與合法訂單

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-17

## 學習目標

完成本章後，你能：

1. 由數字字串建立 `Decimal`，避免先經二進位浮點數改變訂單值；
2. 用帶時間與版本的規則快照核對 `tickSize`、`stepSize`、`MIN_NOTIONAL` 與
   `MAX_NUM_ORDERS`；
3. 說明價格、數量、名義額與掛單數是不同 gate，不能只通過其中一項；
4. 讀取名義價值分層的最大初始槓桿，並在請求超過分層上限時停止；
5. 保存一份合法訂單稽核表，證明拒單沒有被靜默改成另一張訂單。

## 問題情境：差一個小數位，可以幫我修掉嗎

策略提出一張 BTCUSDT U 本位期貨限價買單：價格 `50000.15`、數量 `0.0015`。
規則快照的價格步長是 `0.10`，數量步長是 `0.001`。兩個值看起來只差一點；若程式
把價格截成 `50000.10`、數量截成 `0.001`，就得到一張能送出的訂單。

但這不是「修格式」。原始名義額約為 `75` USDT，截斷後只剩 `50.00010` USDT，兩者
又都低於本章固定快照的 `100` USDT 最小名義額。更重要的是，策略提出的意圖已被
改寫。若系統不留下拒絕證據，之後無法回答成交差異來自策略、交易所規則，還是某段
偷偷截斷的程式。

本章採用 fail closed：值不合法就拒絕，讓提出者用新的理由建立新訂單；不替它猜測。

## 執行前預測

先寫下答案與理由：

1. `Decimal("0.1")` 與先建立 `0.1` float 再轉成 `Decimal`，身分相同嗎？
2. 價格 `50000.10`、數量 `0.001` 都對齊步長，就一定是合法訂單嗎？
3. 目前已有 199 張掛單、上限 200 張時，再送一張應該是警告還是拒絕？
4. 名義額剛好從 `49999.99` 進入 `50000` 時，最大初始槓桿可能不變嗎？
5. 規則快照沒有 `snapshot_ts`，即使數值看起來合理，能否支持歷史訂單審核？

## 核心概念一：金額與數量從字串開始

Python 的 `0.1` 是二進位浮點近似值。`Decimal("0.1")` 則直接解析十進位字串，得到
精確的 `0.1`。交易系統不是只在畫面顯示幾位小數；它要做整除、邊界與資金守恆判斷，
所以數字從哪裡來也是證據的一部分。

```python
from decimal import Decimal

price = Decimal("50000.10")
qty = Decimal("0.010")
notional = price * qty
```

不要寫 `Decimal(0.1)`，也不要為了通過檢查先做 `float` 四捨五入。配套 `v0.3.0` 的
訂單與規則模型會拒絕直接傳入 float；規則 JSON 的十進位欄位也必須是字串。這條防線
使輸入錯誤在模型邊界就暴露，不會等到成交或對帳才出現。

`quant.common.models.dec` 也是由字串建立 `Decimal` 的已發布便利函式；本章 helper
刻意直接使用標準庫 `Decimal`，讓來源紀律清楚可見。

## 核心概念二：合法訂單是一串 AND gate

對本章固定的限價單，最小核對式為：

\\[
\begin{aligned}
(p-p\_{\min}) \bmod \Delta p &= 0 \\\\
(q-q\_{\min}) \bmod \Delta q &= 0 \\\\
p\_{\min} \le p &\le p\_{\max} \\\\
q\_{\min} \le q &\le q\_{\max} \\\\
p\times q &\ge N\_{\min}
\end{aligned}
\\]

其中 `p` 是限價、`q` 是數量、`Δp` 是 `tickSize`、`Δq` 是 `stepSize`，`N_min` 是
最小名義額。公式中的基準是 `minPrice` 與 `minQty`，不是一律假設從零開始。

市價單沒有委託價。`v0.3.0` 的 `validate_order` 要求呼叫方明示 `ref_price` 才能估算
最小名義額；期貨快照若有 `MARKET_LOT_SIZE`，市價單使用它的步長。缺少參考價不是
「先送再說」，而是呼叫方證據不足。

掛單數另走 `check_max_num_orders`：

- `open_count + 1 > cap` 才是 `REJECT`；
- 達上限 80%（可配置）為 `WARN`；
- 低於警戒線為 `OK`。

所以 199／200 時再送一張仍未超限，是 `WARN`；已有 200 張再送下一張才 `REJECT`。
這是單一 symbol 的快照上限，不等同每分鐘 API 訂單率、帳戶總曝險或策略自己的限頻。

## 核心概念三：分層槓桿不是一個永久常數

U 本位期貨的名義價值分層表為每段 `[notional_floor, notional_cap)` 提供最大初始槓桿、
維持保證金率與速算額。下界包含、上界不包含；因此跨過邊界時，可用最大槓桿可能立刻
下降。

本章固定 fixture 中：

| 名義價值 | 所在分層 | 最大初始槓桿 | 本章決定 |
|---:|---:|---:|---|
| `49999.99` | 1 | 125 | 請求 50 倍可繼續 |
| `50000` | 2 | 100 | 請求 100 倍可繼續 |
| `3000000` | 4 | 20 | 請求 50 倍必須停止 |

`v0.3.0` 已發布 `max_leverage_for` 查表能力，但 `validate_order` 本身不接收「請求槓桿」
參數。稽核表必須把過濾器結果與槓桿分層結果分列，不能因 `validate_order` 通過就宣稱
所有 admission、資金、風控與帳戶設定都通過。

規則與分層表都必須保存 `market`、`symbol`、`snapshot_ts` 與來源版本。規則會改，帳戶
分層還可能有使用者調整係數；本章 fixture 是 `v0.3.0` 的固定測試證據，不是 2026 年
任何帳戶的即時 BTCUSDT 規則。

## 系統對照：已發布能力與尚未成立的宣稱

| 邊界 | `v0.3.0` 已發布能力 | 本章如何使用 | 不代表什麼 |
|---|---|---|---|
| 數值模型 | `Order`／`SymbolRules` 的 Decimal guard | 浮點輸入在模型邊界 fail closed | 不替外部 JSON 自動修正 |
| 規則快照 | `RulesRepository` 讀取 symbol 與 bracket fixture | 核對身分、字串數值與 `snapshot_ts` | fixture 不是即時 exchangeInfo |
| 訂單過濾器 | `validate_order` 檢查 price、qty、min notional | 產生可核對的 filter 與錯誤碼 | 不證明已成交、已預留或已送交易所 |
| 掛單上限 | `check_max_num_orders` 回傳 OK／WARN／REJECT | 核對下一張訂單是否超限 | 不是 API rate limit |
| 槓桿分層 | `max_leverage_for` 依名義價值查表 | 把請求槓桿與分層上限分開比較 | 不切換帳戶槓桿、不查私人帳戶 |
| 產品入口 | 本章沒有新的下單 CLI | helper 只讀已提交 fixture、呼叫已發布 Python API | 不送單、不碰 API key 或真實資金 |

Binance 官方 USDⓈ-M 文件把 `PRICE_FILTER`、`LOT_SIZE`、`MARKET_LOT_SIZE`、
`MAX_NUM_ORDERS` 與 `MIN_NOTIONAL` 分列，並把 leverage bracket 的
`initialLeverage`、`notionalFloor`、`notionalCap`、`maintMarginRatio` 與 `cum` 分列。
本章只用官方文件支持欄位語義；固定數值與預期輸出一律來自配套 tag 內的 fixture。

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
  tests/unit/test_rules_filters.py \
  tests/unit/test_rules_brackets.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，status 應沒有輸出。
執行前 oracle 是：價格、數量、最小名義額、掛單上限、規則 repository 與分層邊界的
已發布測試全部通過。不要只挑一個正向案例。

再核對本章固定規則證據的檔案身分：

```bash
sha256sum \
  tests/fixtures/exchange_rules/futures/BTCUSDT.json \
  tests/fixtures/exchange_rules/brackets/BTCUSDT.json
```

預期且實測：

```text
30b8d84e7b4396fdb1b761e074b86e62285f0c9a1d3df1a1a33a566850464671  tests/fixtures/exchange_rules/futures/BTCUSDT.json
3c65e838938d9e65fd2384dc11d835f41df65ec8582b528676c1821c2e1bd895  tests/fixtures/exchange_rules/brackets/BTCUSDT.json
```

## 動手驗證二：逐張產生合法性稽核

仍在配套 `v0.3.0` worktree 執行隨書 helper：

```bash
uv run python "$BOOK_DIR/manuscript/assets/ch16-order-rules-oracle.py"
```

Helper 直接用 `RulesRepository`、`validate_order`、`check_max_num_orders` 與
`max_leverage_for`，不複製第二套過濾器。它預期輸出：

```text
rules=futures/BTCUSDT,snapshot-1700000000000,status-TRADING,tick-0.10,step-0.001,market-step-0.001,min-notional-100,max-orders-200
valid-limit=PASS,price-50000.10,qty-0.010,unchanged-true
bad-tick=PRICE_FILTER,code--4014,price-50000.15,qty-0.010,unchanged-true
bad-step=LOT_SIZE,code--4013,price-50000.10,qty-0.0015,unchanged-true
too-small=MIN_NOTIONAL,code--4164,price-50000.10,qty-0.001,unchanged-true
open-orders-159=OK,cap-200
open-orders-160=WARN,cap-200
open-orders-200=REJECT,cap-200
leverage-notional-49999.99=requested-50,max-125,PASS
leverage-notional-50000=requested-100,max-100,PASS
leverage-notional-3000000=requested-50,max-20,FAIL-CLOSED
float-quantity=TYPE-ERROR,FAIL-CLOSED
chapter-16-order-rules-oracle=PASS
```

四張限價單的 `unchanged-true` 是關鍵 oracle：不論通過或拒絕，驗證函式都沒有改寫原始
price／qty。錯誤碼是配套 `v0.3.0` 的本地錯誤語義；它不證明本輪曾對 Binance 私人下單
端點送單或收到同一回應。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| Decimal 來源、快照身分、所有 filter、掛單數與槓桿分層都相符 | 這張固定輸入通過本章合法性 gate | 保存稽核表；進入後續 admission 檢查 |
| `PRICE_FILTER`／`LOT_SIZE`／`MIN_NOTIONAL` 任一拒絕 | 原訂單不合法，且值未被改寫 | 拒絕；若意圖要改，建立新訂單與新理由 |
| `WARN` | 下一張未超過快照上限，但已達警戒區 | 降低掛單或升級人工檢查，不冒充安全綠燈 |
| `REJECT` | 下一張會超過 symbol 掛單上限 | 不提交 |
| 請求槓桿高於該名義分層上限 | 分層檢查不通過 | 降低槓桿／數量後重新完整驗證 |
| 快照缺失、身分不符或時點不適用 | 沒有足夠規則證據 | fail closed；取得正確快照，不沿用猜測值 |
| 所有本章檢查通過 | 只證明規則層合法 | 不宣稱有資金、會成交、已送單或會獲利 |

## 常見陷阱

- 先把外部數值解析成 float，再用 `str(float_value)` 包裝成看似乾淨的 Decimal。
- 只檢查小數位數，不按 `(value-minimum) % step == 0` 核對。
- 用 `quantize`、floor 或 round 讓訂單「自動通過」，卻沒有建立新意圖。
- 價格與數量對齊後，忘記重算名義額。
- 把限價單的委託價、期貨市價單的參考價與成交價混成同一欄。
- 把 `MAX_NUM_ORDERS` 當成 API 每分鐘 rate limit，或把警告當拒絕。
- 只記 `BTCUSDT`，不記 market、snapshot time、fixture checksum 與配套版本。
- 把公開文件範例或本章 fixture 當成自己的即時帳戶規則。
- 看到 `validate_order` 通過，就跳過槓桿、資金、reservation、reduce-only 與會話風控。
- 把本地錯誤碼對照冒充一次真實下單回應。

## 對系統的回饋

每次 admission 至少應保存：配套版本、規則來源與 `snapshot_ts`、market／symbol、原始
price／qty 字串、order type、用於名義額的價格角色、逐項 filter 結果、open order count、
requested leverage、bracket floor／cap 與 max leverage，以及最終 PASS／WARN／REJECT。

若調整 price、qty、order type、reference price 或 leverage，這是新的輸入；必須重新跑
全部 gate，不能只重跑剛才失敗的一項。這份稽核記錄會成為第 17 章訂單生命週期的起點，
但本章不提前假設 ack、fill、cancel 或 reservation 已發生。

## 小結與練習

複製下表到自己的實驗紀錄，先手算，再修改 helper 中的教學副本驗證：

| case | 原始 price | 原始 qty | price filter | lot size | notional | open orders | leverage tier | 決定 |
|---|---:|---:|---|---|---:|---:|---|---|
| A | `50000.10` | `0.010` | ？ | ？ | ？ | 159 | ？ | ？ |
| B | `50000.15` | `0.010` | ？ | ？ | ？ | 160 | ？ | ？ |
| C | `50000.10` | `0.001` | ？ | ？ | ？ | 200 | ？ | ？ |

再設計一個價格與數量都對齊、但請求槓桿超過對應分層上限的案例。每次只改一個輸入，
保留原值、快照身分與拒絕原因；不要把拒絕案例修到通過後仍沿用同一 case id。

你的專業成果是一份「合法訂單稽核表」：另一位審核者不必相信你看到的小數位，可以從
原始字串、版本化規則、逐項 gate 與未改寫 oracle 重現同一決定。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、uv locked environment、Python 3.12.3
- 驗證命令：`uv lock --check`；`uv sync --locked --dev`；規則 filters／brackets 聚焦測試；兩份 fixture 的 `sha256sum`；`uv run python "$BOOK_DIR/manuscript/assets/ch16-order-rules-oracle.py"`。
- 通過結果：配套 tag 與 HEAD 相符且 worktree 乾淨；聚焦測試 `57 passed`；fixture checksum 相符；合法限價單通過，tick／step／minimum notional 三例以原值拒絕；159／160／200 張掛單分別為 OK／WARN／REJECT；三組分層邊界與 float guard 皆符合 oracle，最終輸出 `chapter-16-order-rules-oracle=PASS`。
- 待處理差異：fixture 是已發布測試快照，不是即時交易所或私人帳戶規則；本章只驗數值、symbol filters、掛單數與分層上限，不宣稱已發布讀者下單 CLI，也不證明資金預留、成交、訂單生命週期、會話風控或實盤可用。
