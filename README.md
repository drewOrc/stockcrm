# Stock CRM

供應鏈導向的個人投資日誌。每檔股票是一張卡片，公司之間的客戶 → 供應商關係是一張圖，
每張卡片的論點拆成可以驗證的驅動因子、檢查點與標準指標。AI agent 透過 MCP 讀同一份資料、
自動抓官方數據、提案；判斷留給你，在網頁上核准。

## 快速開始

```bash
pip install -r requirements.txt
python seed.py --reset        # 載入示範資料（可略過，從空的開始）
python -m stockcrm.api        # 開 http://127.0.0.1:8765
```

在這個資料夾啟動 Claude Code，`.mcp.json` 會自動接上 `stockcrm` MCP server（37 個工具），`CLAUDE.md` 告訴 agent 資料模型與寫入規則。試試看：

- 「我對 NVDA 的間接曝險有多少？」
- 「台積電的論點還撐得住嗎？」（它看驅動因子、檢查點、指標對照）
- 「幫我做早晨簡報」（查對手方新聞並提案，你到「提案」頁核准）
- 「我今天用 1200 加碼台積電到 14%」（先檢查核心層上限再記錄）

## 資料模型

| 物件 | 是什麼 | CRM 對應 |
|---|---|---|
| 公司 `companies` | 節點，id 是 `市場:代號`（`TW:2330`、`US:NVDA`） | 聯絡人 |
| 邊 `edges` | 客戶 → 供應商，帶客戶佔供應商營收比重、時間差、信心、來源；`relation=partner` 是產能或授權夥伴 | 關係 |
| 卡片 `positions` | 階段（觀察／持有／已出場）、分層（核心／衛星／觀察／黑名單，各有部位上限）、論點、失效條件、論點狀態、目標價與其依據 | 帳戶 |
| 互動紀錄 `interactions` | 買賣、新聞、財報、法說、想法、回顧；可掛到驅動因子或檢查點並標「支持／削弱／確認」 | 聯絡紀錄 |
| 提案 `proposals` | agent 與其他服務的寫入通道，核准後才套用 | 待辦 |

### 讓論點可以被驗證：三種通用容器

任何一檔股票的論點都能拆成這三種，差別只在填什麼：

| 容器 | 是什麼 | 例子 | 警訊 |
|---|---|---|---|
| 驅動因子 `drivers` | 1 到 3 個量化假設，模型值 vs 實際值的時間序列 | 出貨台數、SEMI 占比、CoWoS 產能、客戶資本支出年增率 | 關鍵因子落後模型 15% 或最近證據是「削弱」 |
| 檢查點 `checkpoints` | 到某日前該看到的證據 | 客戶認證、首批出貨、授權金入帳 | 逾期沒證據，或被標為未達成 |
| 標準指標 `metrics` | 月營收、季營收、毛利率、營業費用、EPS、股數 | 模型季營收 vs 月營收 run-rate | run-rate 低 15%、毛利率低 3 個百分點、EPS 低 15%、股數增加 2% |

健康度檢查把三者彙整成問題清單；早晨頁列出需要注意的卡片，由你決定要不要一鍵標為動搖。
建卡片時可以套用論點模板（轉型股、成長股、景氣循環股、供應鏈受惠股）預填驅動因子與檢查點。
期間格式：`2026`（年）、`2026Q2`（季）、`2026-05`（月）；營收與費用千元、毛利率 0..1、股數千股。

## 誰能寫什麼

| 資料 | 誰寫 | 怎麼寫 |
|---|---|---|
| 官方事實：月營收、季報六個數、股數（證交所／櫃買開放資料，免金鑰） | 腳本 | 直接寫入 `actual`，`created_by=system:mops`，帶來源網址 |
| 需要解讀：法說裡的台數與占比、新聞對檢查點的影響、其他服務的警示 | agent、引擎 | 提案（MCP `propose_*` 或 `POST /api/proposals`），你在網頁核准 |
| 判斷：論點內容與狀態、模型值、部位、階段、分層、目標價 | 你 | 網頁；agent 可以建議，不能改 |

## 維護：讓 agent 顧資料庫

```bash
python scripts/maintain.py all        # 備份 → 抓月營收／季報／股數 → 品質報告 → 健康度，寫 data/reports/maintenance-日期.md
python scripts/maintain.py quality    # 只看資料品質待辦
```

每個工作在 `maintenance_log` 留一筆，早晨頁最下方的「維護狀態」面板可以手動觸發。
備份在 `data/backups/`，保留 30 天。季報開放資料是年初至今的累計數，單季數字在連續兩季都抓到後才會出現。

Windows 排程（目前使用者，不需管理員）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -MorningAt 08:30 -EveningAt 19:30
```

- **08:30 StockCRM Morning**：`maintain.py all`，接著用 Claude Code 跑 `scripts/morning_prompt.md`（只允許 stockcrm MCP 工具與網路查詢），簡報寫到 `data/reports/morning-日期.md`。
- **19:30 StockCRM Evening Fetch**：只抓官方資料（月營收在每月 1 到 10 號收盤後陸續公布）。
- 找不到 `claude` 指令時會自動用 Claude 桌面版附帶的那份，或用環境變數 `STOCKCRM_CLAUDE` 指定；CLI 要先登入過一次（`claude` 然後 `/login`）。

### 伺服器常駐

本機的 8765 由 `twstock-tracker/scripts/service_install.ps1` 註冊的工作排程「TWStock CRM」常駐（登入時啟動、當機 5 秒內重啟）。
不要再手動 `python -m stockcrm.api`，會撞 port；要重啟用：

```powershell
Stop-ScheduledTask 'TWStock CRM'; Start-ScheduledTask 'TWStock CRM'
```

日誌在 `twstock-tracker/data/logs/crm-supervisor.log`。只在開發或沒裝那個任務的機器上才手動啟動。

## 畫面

- **看板**：依階段分欄，卡片顏色是分層，顯示論點狀態、幾天沒更新、健康度警示數。
- **卡片**：健康度、階段／分層／部位（會檢查分層上限）、論點與失效條件、驅動因子、檢查點、標準指標、供應鏈關係與間接曝險、提醒、互動紀錄。
- **關係圖**：終端客戶在左、供應商往右，線寬是營收比重，橘線是夥伴；下方是組合對每個終端客戶的合計曝險。
- **早晨**：需要注意的卡片、持股狀態、今天該查的對手方、提醒、給 agent 的任務、維護狀態。
- **提案**：agent 與引擎提出的互動紀錄、供應鏈關係、驅動因子數值、檢查點結果、論點狀態變更，核准後才寫入。

## 與 twstock-tracker 的分工

同一個 workspace 裡的 [`twstock-tracker`](../twstock-tracker) 是台股行情引擎（日 K、技術指標、三大法人、融資券、盤中報價、警示）。兩邊維持獨立的 repo、資料庫與 PR 流程，由引擎伺服一個統一前端：

- 引擎在 :8000 反向代理 `/api/crm/*` → 本服務 `/api/*`，瀏覽器只面對一個 origin，本服務不需要 CORS。
- **資料歸屬**：卡片、論點、供應鏈、提案在這裡；價格、籌碼、警示、掃描在引擎。彼此不寫對方的資料庫。
- 引擎要回寫（例如到價警示觸發）一律走 `POST /api/proposals`，`created_by=engine:twstock-tracker`。
- 之後本服務的 MCP 會直接呼叫引擎的 REST 取得報價與指標，不另接資料源。
- 公司身份在前端橋接：這裡的 `TW:2330` 對應引擎的 `2330`；美股節點引擎沒有對應。

## REST API 一覽

| 範圍 | 路由 |
|---|---|
| 卡片 | `GET /api/positions`、`GET/POST /api/positions/{ref}`（ref 可用 `TW:2330`、`2330`、名稱）、`POST …/{pid}/interactions`、`…/thesis_status`、`GET …/{pid}/tier_check?new_pct=`、`GET …/{pid}/health` |
| 驅動因子 | `POST /api/positions/{pid}/drivers`、`PATCH/DELETE /api/drivers/{id}`、`POST /api/drivers/{id}/values` |
| 檢查點 | `GET /api/checkpoints`、`POST /api/positions/{pid}/checkpoints`、`POST /api/checkpoints/{id}/resolve`、`DELETE /api/checkpoints/{id}` |
| 指標 | `GET/POST /api/metrics/{company_id}` |
| 模板與健康度 | `GET /api/templates`、`POST /api/positions/{pid}/template`、`POST /api/checks/apply` |
| 供應鏈 | `GET /api/companies`、`POST /api/companies`、`GET /api/graph`、`GET /api/graph.md`、`POST /api/edges`、`DELETE /api/edges/{id}`、`GET /api/relations/{cid}`、`GET /api/exposure/{cid}`、`GET /api/overlap` |
| 早晨與提醒 | `GET /api/morning`、`GET/POST /api/reminders`、`POST /api/reminders/{id}/done` |
| 提案 | `GET /api/proposals?status=`、`POST /api/proposals`、`POST /api/proposals/{id}/decide` |
| 維護 | `GET /api/maintenance`、`POST /api/maintenance/run {job}` |

MCP 工具與 REST 共用 `stockcrm/core.py`，分三類：讀取（`get_*`／`list_*`）、使用者口述的直接寫入（`log_interaction`、`add_*`、`set_*`、`fetch_official_data`）、需核准的提案（`propose_*`）。

## 開發與 PR 流程

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m ruff check .
python -m pytest -q             # 30 個測試
python scripts/check_mcp.py     # MCP server 能用 stdio 啟動並列出工具
```

所有變更走功能分支與 PR，GitHub Actions 在每個 PR 上跑 ruff → pytest → MCP 檢查 → seed。`.github/CODEOWNERS` 指定 review 人。

## 程式結構

```
stockcrm/db.py          schema、列舉值、舊資料庫遷移
stockcrm/core.py        全部商業邏輯，回傳 dict，REST 與 MCP 共用
stockcrm/mops.py        證交所／櫃買開放資料解析與寫入
stockcrm/mcp_server.py  MCP 工具（stdio）
stockcrm/api.py         FastAPI + 靜態網頁
web/                    單頁前端
scripts/maintain.py     維護總入口；morning.ps1 / install_schedule.ps1 排程；morning_prompt.md agent 早晨任務
tests/                  pytest
seed.py                 示範資料
data/                   stockcrm.db（唯一真相來源）、backups/、reports/；不進 git
```

環境變數 `STOCKCRM_DB` 可以改資料庫路徑。

## 示範資料

供應鏈比重是概略估計，請依年報「主要客戶」段落、法說會與券商報告修正，每條邊都有 `source` 與 `confidence`。
轉型股示範卡片（科嶠 4542）的驅動因子與檢查點來自一份外部估值模型，數字僅供示範。

## 還沒做的

- 報價與技術指標進卡片：等統一前端與引擎整合 PR，MCP 再加 `get_quote` 等工具呼叫引擎。
- 季報歷史回補：目前只有開放資料的最新一季 YTD，缺的季度靠 agent 從季報提案。
- 交易回顧統計（勝率、各想法來源與行為標籤的表現）。資料欄位已留好。
- 多人與同步。目前是單機 SQLite。
