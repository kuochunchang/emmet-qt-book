# 時間、K 線收盤與多資料流對齊

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
> 內容狀態：可操作
> 最後驗證日期：2026-07-16

## 學習目標

完成本章後，你能：

1. 把 UTC 毫秒時間戳、K 線半開區間、`open_time` 與 `close_time` 互相換算；
2. 分開記錄事件發生、資料到達與策略可見時間，不用其中一個冒充另外兩個；
3. 用已發布的歷史資料來源對齊期貨 K 線與標記價格 K 線，核對同時點的決定順序；
4. 判斷未收盤、尚未可見、late 或 backfill 資料是否能進入某次歷史決定；
5. 在配套版本尚未提供到達時間證據時縮小宣稱，而不是虛構即時可見性。

## 問題情境：兩條都標成 11:00 的資料，不一定同時可用

延續第 10 章的研究問題：BTCUSDT 一小時 K 線收盤價高於前一根時，準備在收盤資料可見後的第一個合法機會進場。現在同時加入期貨成交 K 線與標記價格 K 線：前者產生訊號，後者支持當時的風險判斷。

`10:00–11:00 UTC` 的兩根 K 線都以 `10:00:00` 為 `open_time`，由一小時 interval 推得 `11:00:00` 的 `close_time`。但這只回答市場區間何時結束，不回答資料何時到達，也不回答驗證何時完成。若成交 K 線直到 `11:00:04` 才可見，就不能倒填成 `11:00:00` 已知，再用同刻的下一根開盤成交。

本章用固定教學值驗證時間與排序契約，不下載市場資料、不計算績效，也不把歷史資料來源的確定性排序冒充即時網路延遲證據。

## 執行前預測

先寫下答案與理由：

1. `open_time=10:00:00`、`interval=1h` 的完整 OHLC，最早能在哪個事件時點交給策略？
2. 標記價格 K 線與成交 K 線都在 `11:00:00` 收盤時，先後順序能否依呼叫者傳入 feed 的排列改變？
3. 下一根 `open_time=11:00:00`，但訊號資料 `available_time=11:00:04`，能否宣稱在下一根開盤成交？
4. `12:00:05` 才到達、屬於 `11:00–12:00` 的回補 K 線，能否補觸發 `12:00:00` 已經做完的策略決定？
5. 歷史 Parquet 沒有保存 `arrival_time` 時，能否由 `open_time` 或檔案修改時間推測它？

## 核心概念：一根 K 線有區間，也有可見邊界

本書一律使用 UTC。K 線區間採左閉右開：

```text
[2024-01-01T10:00:00Z, 2024-01-01T11:00:00Z)
 open_time                         close_time
```

在已發布的 `v0.3.0` 中，`Bar.open_time` 是毫秒 UTC 的儲存鍵，`close_time` 由 `open_time + interval` 推導。完整 OHLC 涵蓋整段區間；若在 `10:30` 就把 `high`、`low` 或 `close` 當成已完成資料，等於偷看後半段。

正式的 `KlineFeed` 因此把每列拆成兩種證據：

- `ExecutionOpenEvent` 在 `open_time` 發生，只攜帶開盤價供撮合邊界使用，不把未來 high／low／close 暴露給策略；
- `MarketEvent` 在 `close_time` 發生，攜帶 `is_closed=true` 的完整 K 線。

標記價格的 `MarkPriceFeed` 也只在 `close_time` 產生完整 `MarkPriceEvent`。這個轉換避免把儲存鍵誤當策略可見時間。

## 三種時間不能互相代填

| 時間 | 回答的問題 | 本章固定例子 | 缺少時能否推測 |
|---|---|---|---|
| `event_time` | 市場區間或事件何時完成？ | 兩條 `10:00–11:00` K 線皆為 `11:00:00` | 可由已發布 interval 契約推導 K 線收盤 |
| `arrival_time` | 此次管線何時收到資料？ | 標記價格 `11:00:02`；成交 K 線 `11:00:03` | 不可由 `open_time`、下載日或檔案 mtime 代填 |
| `available_time` | 完整性檢查後，策略最早何時可讀？ | 標記價格 `11:00:03`；成交 K 線 `11:00:04` | 不可早於 arrival；缺驗證紀錄即未知 |

歷史模式與外部到達模式要分開記錄：

- 固定歷史快照在回測開始前已載入，但 `KlineFeed` 仍按事件時間隱藏未完成 OHLC；這驗證因果排序，不測量真實 arrival latency。
- 外部或即時研究若依賴「收盤後幾秒可交易」，必須另外保存 `arrival_time` 與驗證完成的 `available_time`。`v0.3.0` 的歷史 feed event 沒有這兩個欄位，因此本章不宣稱已發布 arrival-aware 即時入口。

## 多資料流要先對齊事件，再對齊可見集合

`v0.3.0` 的 canonical merge 不是把兩張表按列號拼起來。它先以事件時間與事件優先序排序，再以 feed 身分與序號形成穩定全序；呼叫者交換 feed 排列不會改變結果。

本章的 `11:00:00` 固定 oracle 是：

| 順序 | 事件 | 策略／系統在此刻能知道什麼 |
|---:|---|---|
| 1 | `MarkPriceEvent` | 已收盤的標記價格 K 線 |
| 2 | `MarketEvent` | 已收盤的期貨成交 K 線，可產生策略訊號 |
| 3 | `ExecutionOpenEvent` | 下一根開盤的撮合證據；不攜帶該根未來 OHLC |

這是固定歷史 feed 的事件全序。若研究模擬外部到達，決策集合還要加一個條件：每條輸入的 `available_time <= decision_time`。本章固定 arrival envelope 在 `11:00:04` 才同時備妥兩條資料流，所以 `11:00:00` 的 next-open 不合格；只有把問題改成「兩條資料都可見後的第一個合法機會」才能繼續。

## 系統對照與已發布邊界

| 已發布物件 | 本章使用方式 | 不代表 |
|---|---|---|
| `Bar`／`MarkPriceBar` | 保存 `open_time`、interval 與導出的 `close_time` | 已保存真實 arrival latency |
| `KlineFeed`／`MarkPriceFeed` | 把儲存列轉成收盤可見的外部事件 | 已發布 WebSocket 或斷線回補組合根 |
| `merge_evidence`／`merge_events` | 以 canonical key 合併多 feed，拒絕回退 | 任意缺流都可靜默前填 |
| `is_backfill`／`late` | 模型能標記資料性質 | `v0.3.0` 已完成 arrival-aware live 重播與凍結／恢復 |

`KlineFeed` 與 `MarkPriceFeed` 從歷史 Parquet 建立模型時，`is_backfill`、`late` 維持預設 `false`。目前已發布路徑會拒絕同一 feed 的重複或倒退時間，但不能替一筆外部資料證明它何時到達。需要真實 late／backfill 行為的章節必須等待相應正式入口發布並重新驗證。

## 動手驗證一：先固定版本，再跑既有契約測試

工作目錄是 setup 建立的隔離配套 worktree：

```bash
export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"
cd "$EMMET_QT_BT1_DIR"
git rev-parse HEAD
git status --short
uv lock --check
uv sync --locked --dev
uv run pytest \
  tests/unit/test_datasource_feeds.py \
  tests/unit/test_datasource_merge.py -q
```

HEAD 必須是 `c999965e5cc923281541409cda9502beb93b8a60`，`status --short` 應沒有輸出。測試 oracle 在執行前寫成：K 線只在收盤事件暴露完整 OHLC、重複／倒退時間被拒絕、同時點優先序固定，而且交換 feed 排列不改變合併結果。任一項不成立就停止。

本章實測結果是：

```text
28 passed in 0.51s
```

## 動手驗證二：兩條資料流的收盤與下一根開盤

以下固定程式使用正式模型與 datasource；表內價格只是教學值。執行前 oracle 是 `11:00:00` 依序得到 `MarkPriceEvent`、`MarketEvent`、`ExecutionOpenEvent`，而固定的成交 K 線 `available_time=11:00:04` 必須拒絕 `11:00:00` next-open。

```bash
uv run python - <<'PY'
from datetime import UTC, datetime

import pyarrow as pa

from quant.common.datasource import KlineFeed, MarkPriceFeed, merge_events
from quant.common.models import Market

def ms(hour: int, second: int = 0) -> int:
    return int(datetime(2024, 1, 1, hour, 0, second, tzinfo=UTC).timestamp() * 1000)

klines = pa.table({
    "open_time": [ms(10), ms(11)],
    "open": [100.0, 105.0], "high": [106.0, 108.0],
    "low": [99.0, 104.0], "close": [105.0, 107.0],
    "volume": [10.0, 11.0], "quote_volume": [1000.0, 1100.0],
})
marks = pa.table({
    "open_time": [ms(10)], "open": [100.0], "high": [105.0],
    "low": [98.0], "close": [104.0],
})

events = list(merge_events([
    KlineFeed([klines], market=Market.FUTURES, symbol="BTCUSDT", interval="1h"),
    MarkPriceFeed([marks], symbol="BTCUSDT", interval="1h"),
]))
at_close = [type(event).__name__ for event in events if event.timestamp == ms(11)]
assert at_close == ["MarkPriceEvent", "MarketEvent", "ExecutionOpenEvent"]

arrival_time = ms(11, 3)
available_time = ms(11, 4)
next_open_time = ms(11)
assert next_open_time < arrival_time <= available_time

late_arrival = ms(12, 5)
past_decision = ms(12)
assert late_arrival > past_decision

print("aligned-order=" + ",".join(at_close))
print("closed-bar-visible=PASS")
print("next-open=FAIL-CLOSED")
print("late-backfill=NO-RETROACTIVE-DECISION")
PY
```

預期且實測輸出：

```text
aligned-order=MarkPriceEvent,MarketEvent,ExecutionOpenEvent
closed-bar-visible=PASS
next-open=FAIL-CLOSED
late-backfill=NO-RETROACTIVE-DECISION
```

把 feed 在程式中的排列交換後，事件全序仍相同。這證明 canonical merge 的確定性；固定的 arrival／available 斷言則是章內研究 oracle，不是配套 event schema 的欄位。

## late 與 backfill：修資料版本，不回寫舊決定

若 `11:00–12:00` K 線在 `12:00:05` 才到達，它的 event time 仍是 `12:00:00`，arrival time 則是 `12:00:05`。兩者不能改成同一個時間。對 `12:00:00` 已完成的決定，本章採 fail-closed 規則：

1. 保留原決定使用的 dataset 版本、可見集合與缺流狀態；
2. 把遲到資料標成 late／backfill，不能補觸發舊回呼，也不能把舊決定改寫成「當時已知」；
3. 若資料補齊後要重新研究，建立新 dataset 版本，從固定起點完整重跑並產生另一份結果；
4. 比較新舊結果時明寫資料版本差異，不把新版績效覆蓋舊版證據。

對 arrival-sensitive 研究，沒有可信 `arrival_time` 或 `available_time` 就是 `no-go`。可以改做只依固定歷史快照與收盤事件排序的問題，但必須同步縮小研究宣稱。

## 結果解讀與決定

| 觀察 | 可以宣稱 | 決定 |
|---|---|---|
| 兩條固定 feed 在 `11:00` 依 canonical key 合併 | 已發布歷史 datasource 的收盤與同時點排序可重現 | 可用於固定快照的因果回測設計 |
| next-open 早於固定 `available_time` | 原 next-open 成交假設偷看尚未可見資料 | `no-go`；改成可見後首個合法機會 |
| feed 有重複、倒退或 event key regression | 輸入不能形成可信全序 | fail closed；先修資料，不自行排序掩蓋 |
| late／backfill 晚於既有 decision | 新資料不能成為舊決定當時的可見證據 | 保存新版本後完整重跑，不補觸發舊決定 |
| 只有歷史 `open_time`，沒有 arrival 記錄 | 可核對市場區間，不能核對真實傳輸延遲 | 不做 arrival-sensitive 宣稱 |

## 常見陷阱

- 把 `open_time` 當成完整 K 線可見時間，於區間起點使用未來 high／low／close。
- 以檔案 mtime、下載批次時間或今天的資料庫寫入時間冒充歷史 arrival time。
- 依 DataFrame 列順序拼接多流，沒有以事件時間、優先序與穩定 tie-breaker 建立全序。
- 某條流缺資料時向前填補，卻沒有記錄原決定使用的是舊值。
- 回補後只重算受影響的一列，讓指標狀態、持倉或風控沿用舊歷史。
- 看到模型已有 `late`／`is_backfill` 欄位，就宣稱即時斷線、凍結與恢復已在本版完成。
- 把 `28 passed` 寫成外部行情或網路延遲已驗證；這些是固定離線程式契約測試。

## 對系統的回饋

每次多流研究都應保存一張 alignment card：UTC 窗口、interval、每條 feed 身分、event／arrival／available time 的來源、同時點優先序、缺流政策、late／backfill 政策、dataset 版本與重跑決定。若正式資料源沒有 arrival 證據，就把缺口回報到資料契約，不在策略層猜一個延遲常數。

## 小結與練習

為三條一小時資料流畫出 `open_time → close_time → arrival_time → available_time → decision_time`。刻意讓其中一條晚五秒到達，說明原決定為何不能補觸發；再寫出新 dataset 版本的完整重跑條件。另一位審查者應能只看這張卡就判斷哪個結果可比較、哪個必須 `no-go`。

你的專業成果是一張可審查的多資料流 alignment card，以及一個不會讓 late／backfill 靜默改寫歷史決定的政策。

## 作者驗證紀錄

- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證命令：`uv lock --check`；`uv run pytest tests/unit/test_datasource_feeds.py tests/unit/test_datasource_merge.py -q`；章內兩流固定程式。
- 通過結果：配套 worktree 乾淨，`28 passed`；`11:00:00` 的固定事件順序為 mark close、market close、execution open；next-open 早於固定 available time 而 fail closed；late arrival 不補觸發舊決定。
- 待處理差異：arrival／available envelope 是章內固定研究 oracle，不是 `v0.3.0` event schema；本章沒有宣稱已發布 arrival-aware WebSocket、斷線回補、凍結或恢復入口。
