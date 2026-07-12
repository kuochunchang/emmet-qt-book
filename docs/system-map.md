# 書籍與配套系統對照表

此表讓作者與讀者知道某項知識在系統裡落在哪裡，也防止把尚未完成的設計寫成
可操作功能。基線：配套專案 `0.3.0`，盤點日期 2026-07-12。

| 主題 | 主要系統位置 | 書中篇章 | 基線狀態 |
|---|---|---|---|
| 基礎型別、訂單、帳戶、事件 | `quant.common.models` | 市場篇、交易核心篇 | 可操作 |
| Clock、交易所規則、限頻 | `quant.common.clock/rules/ratelimit` | 資料篇、交易核心篇 | 可操作 |
| 歷史資料、驗證、PIT universe | `quant.data`、`quant-data` | 資料篇 | 可操作 |
| 多流合併與 HistoricalDataSource | `quant.common.datasource` | 交易核心篇、引擎篇 | 可操作 |
| 撮合、執行路由、帳本 | `quant.common.fill/execution/engine.accounting` | 交易核心篇 | 可操作 |
| TradingEngine、策略 runner | `quant.common.engine/runner` | 引擎篇 | 預覽（Phase 4） |
| 多腿協調、組合風控、指標 | `quant.common.engine/indicators` | 引擎篇 | 預覽（Phase 4） |
| Foundation 正式操作入口 | Phase 4.5 composition | 研究實戰篇 | 未開放 |
| Backtest Server、報告與 MCP | `quant.backtest/mcp` | 研究實戰篇 | 未開放（Phase 5） |
| 回放與故障注入框架 | Phase 6 | 研究實戰篇 | 未開放 |
| Paper Trading | `quant.paper` | 上線篇 | 未開放（Phase 7） |
| Live Trading | `quant.live` | 上線篇 | 未開放（Phase 8） |

## 同步原則

系統每次發布 tag 時，檢查受影響模組、正式入口、結果 schema、測試證據與風險
揭露。書稿只在相同操作可由讀者重現後，將狀態從「預覽／未開放」改為
「可操作」。
