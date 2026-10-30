# Stock CRM — 給 agent 的說明

這是一個「供應鏈導向的投資日誌」。把 CRM 的概念套在股票上：

- **公司（companies）** 是節點。id 格式 `MARKET:TICKER`，例如 `TW:2330`、`US:NVDA`。
- **邊（edges）** 是真實的客戶 → 供應商關係。`revenue_share` 是客戶佔**供應商**營收的比重（0..1）。
  NVDA 是台積電的客戶；AAPL 是鴻海、大立光、台積電的客戶。
- **卡片（positions）** 是使用者追蹤的每一檔，對應 CRM 的「帳戶」。
  - `stage`：watch（觀察）/ hold（持有）/ exited（已出場）
  - `tier`：core（核心，上限 15%）/ satellite（衛星，上限 5%）/ watch / blacklist。上限在 `tier_rules`。
  - `thesis`（投資論點）、`invalidation`（失效條件）、`thesis_status`：intact / shaken / broken
- **互動紀錄（interactions）** 是 CRM 的聯絡紀錄：buy / sell / add / trim / news / earnings / call / thought / review。
- **提案（proposals）** 是 agent 的寫入通道。使用者在網頁上核准後才會套用。

## 寫入規則（最重要）

| 情況 | 用什麼 |
|---|---|
| 使用者**明確要你記錄**的事（我今天買了 2330、幫我加到觀察清單、提醒我 11/19） | `log_interaction`、`add_to_watchlist`、`add_reminder` 直接寫 |
| 你**自己從外部資訊推斷**的事（新聞、財報、法說逐字稿、年報） | `propose_interaction`、`propose_edge`、`propose_thesis_status`、`propose_reminder` |
| 改論點內容、改部位大小、改階段或分層 | 不要做。告訴使用者，請他在網頁上改 |

每個提案都要有 `rationale`，說明它和哪檔持股的論點或失效條件有關。

## 常見任務怎麼做

**早晨簡報**：`get_morning_context` → 對 `counterparties_to_check` 裡每家公司查新聞 → 重大的用 `propose_interaction` → 若某持股的 `invalidation` 看起來被觸發，`propose_thesis_status` → 用繁體中文寫簡報，先講影響論點的事。

**「如果 Apple 砍單，我哪些持股受影響？」**：`get_indirect_exposure("AAPL")`。exposure_pct = 部位% × 沿路 revenue_share 的乘積。

**「我是不是其實都押在 NVDA 上？」**：`get_portfolio_overlap`。

**使用者要加碼**：先 `check_tier_limit`，超過分層上限要明講，再 `log_interaction(kind="add")`。

**讀法說會或年報**：找「主要客戶」段落，用 `propose_edge` 新增或修正邊，`source` 寫清楚出處，不確定就 `confidence="low"`。

## 慣例

- 時間一律 UTC，`YYYY-MM-DD HH:MM:SS`。顯示給使用者時轉台北時間。
- 自由文字是 Markdown。
- 回覆使用者用繁體中文。
- 台股月營收每月 10 號前公布，`get_morning_context` 的 `tw_monthly_revenue_window` 為 true 時順便查。

## 程式結構

```
stockcrm/db.py          schema 與列舉值（所有封閉清單都在這裡）
stockcrm/core.py        全部商業邏輯，回傳 dict，REST 與 MCP 共用
stockcrm/mcp_server.py  MCP 工具（stdio）：python -m stockcrm.mcp_server
stockcrm/api.py         FastAPI + 靜態網頁：python -m stockcrm.api  → http://127.0.0.1:8765
web/                    單頁前端（看板、關係圖、早晨、提案）
seed.py                 示範資料：python seed.py --reset
data/stockcrm.db        唯一真相來源（SQLite）。備份 = 複製檔案。
```

環境變數 `STOCKCRM_DB` 可以改資料庫路徑。
