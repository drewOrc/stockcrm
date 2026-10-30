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
  可以掛到一個驅動因子或檢查點上，並標 `effect`：supports / weakens / confirms。這是證據累積的方式。
- **提案（proposals）** 是 agent 的寫入通道。使用者在網頁上核准後才會套用。

每張卡片的論點可以用三種通用容器變成「可驗證的」：

- **驅動因子（drivers）**：1 到 3 個量化假設，每個有模型值與實際值的時間序列。例如「自製設備出貨台數 2027 模型 80」。
  `critical=1` 的因子被削弱或落後模型超過 15%，論點就該動搖。`signal`：no_data / on_track / ahead / behind / weakened。
- **檢查點（checkpoints）**：「到某日前應該看到某個證據」。待確認且逾期 = 高嚴重度警訊。
- **標準指標（metrics）**：每家公司都有的數字：revenue_m（月營收）、revenue_q、gross_margin、opex、eps、shares。
  模型值 vs 實際值；月營收實際值會和模型的季營收算 run-rate。
- 期間格式：`2026`（年）、`2026Q2`（季）、`2026-05`（月）。營收與費用用千元，毛利率 0..1，股數千股。
- `get_thesis_health` 把三者彙整成 issues；`get_morning_context` 的 `attention` 列出需要注意的卡片。
- 邊有 `relation`：customer（客戶向供應商採購）或 partner（產能或授權夥伴）。

## 寫入規則（最重要）

| 情況 | 用什麼 |
|---|---|
| **官方事實**（證交所／櫃買開放資料：月營收、季報六個數、股數） | `fetch_official_data` 或 `scripts/maintain.py` 直接寫入 `actual`，`created_by=system:mops`，帶來源網址。不經提案，因為沒有判斷成分 |
| 使用者**明確要你記錄或口述**的事（我今天買了 2330、幫我加到觀察清單、提醒我 11/19、把 Excel 裡的模型值填進去） | `log_interaction`、`add_to_watchlist`、`add_reminder`、`add_driver`、`set_driver_value`、`add_checkpoint`、`set_metric`、`apply_template` 直接寫 |
| 你**自己從外部資訊推斷**的事（新聞、財報、法說逐字稿、年報、月營收公告） | `propose_interaction`、`propose_edge`、`propose_thesis_status`、`propose_reminder`、`propose_driver`、`propose_driver_value`、`propose_checkpoint`、`propose_checkpoint_resolution`、`propose_metric` |
| 改論點內容、改部位大小、改階段或分層、直接改論點狀態 | 不要做。告訴使用者，請他在網頁上改（健康度警示有「標為動搖」按鈕） |

每個提案都要有 `rationale`，說明它和哪檔持股的哪個驅動因子、檢查點或失效條件有關。
提案或記錄互動時，能掛到驅動因子或檢查點就掛，並填 `effect`。

## 常見任務怎麼做

**早晨簡報**：`get_morning_context` → 先處理 `attention`（逾期檢查點、落後或被削弱的驅動因子、偏離的指標），能確認或推翻的用
`propose_checkpoint_resolution` / `propose_driver_value` → 對 `counterparties_to_check` 裡每家公司查新聞 → 重大的用
`propose_interaction`（掛 driver_id / checkpoint_id + effect）→ 若某持股的 `invalidation` 看起來被觸發，`propose_thesis_status`
→ 用繁體中文寫簡報，先講影響論點的事。`tw_monthly_revenue_window` 為 true 時查台股持股月營收，`propose_metric(metric="revenue_m")`。

**讀法說會逐字稿**：先 `list_drivers` 看這張卡片追哪些假設，找逐字稿裡對應的句子，`propose_driver_value`（實際值）或
`propose_interaction`（effect=supports/weakens）。公司或夥伴講到的時程用 `propose_checkpoint`。

**使用者給你他的 Excel 模型數字**：`add_driver` + `set_driver_value(kind="model")`、`set_metric(kind="model")`。目標價的組成請他填在卡片的「目標價依據」。

**「這檔論點還撐得住嗎？」**：`get_thesis_health`。有 `suggest_shaken` 就明講，但不要自己改狀態。

**「如果 Apple 砍單，我哪些持股受影響？」**：`get_indirect_exposure("AAPL")`。exposure_pct = 部位% × 沿路 revenue_share 的乘積。

**「我是不是其實都押在 NVDA 上？」**：`get_portfolio_overlap`。

**使用者要加碼**：先 `check_tier_limit`，超過分層上限要明講，再 `log_interaction(kind="add")`。

**讀法說會或年報**：找「主要客戶」段落，用 `propose_edge` 新增或修正邊，`source` 寫清楚出處，不確定就 `confidence="low"`。

## 維護模式（agent 負責維護資料庫）

三種資料、三種做法：官方事實腳本直接寫；需要解讀的（法說裡的數字、新聞對檢查點的影響）走提案；判斷（論點狀態、模型值、部位）只有使用者能改。

- **每天早上**（排程 `scripts/morning.ps1`）：`maintain.py all`（備份 → 抓月營收／季報／股數 → 品質報告 → 健康度）再跑 `scripts/morning_prompt.md` 的 agent 任務，簡報寫到 `data/reports/morning-日期.md`。
- **每天傍晚**：只抓官方資料（月營收在 1 到 10 號收盤後陸續公布）。
- 季報開放資料是**年初至今（YTD）**，單季數字要前一季也在庫裡才推得出來；缺的那季請使用者給或你從季報提案。
- `get_maintenance_status` 看每個工作最後一次執行與資料品質問題；`fetch_official_data(job)` 可以立刻抓。
- 備份在 `data/backups/`，保留 30 天；每次 `maintain.py all` 先備份再寫入。
- 所有自動寫入都在 `maintenance_log` 留紀錄；回答「這個數字哪來的」時查 metrics 的 `source` 與 `created_by`。
- 品質報告裡的項目是你的待辦：沒有來源的邊去找年報補；沒有模型值的關鍵驅動因子提醒使用者填；放太久的提案提醒使用者批。

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
