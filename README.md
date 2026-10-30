# Stock CRM

供應鏈導向的個人投資日誌。每檔股票是一張卡片，公司之間的客戶 → 供應商關係是一張圖，
AI agent 透過 MCP 讀同一份資料並提案，你在網頁上核准。

## 安裝與啟動

```bash
pip install -r requirements.txt
python seed.py --reset        # 載入示範資料（可略過，從空的開始）
python -m stockcrm.api        # 開 http://127.0.0.1:8765
```

## 接上 Claude Code

專案根目錄已有 `.mcp.json`。在這個資料夾啟動 Claude Code，它會自動載入 `stockcrm` MCP server。
`CLAUDE.md` 告訴 agent 資料模型與寫入規則。

試試看：

- 「我對 NVDA 的間接曝險有多少？」
- 「幫我做早晨簡報」（它會查對手方新聞並提案，你到「提案」頁核准）
- 「我今天用 1200 加碼台積電到 14%」（它會先檢查核心層上限再記錄）

## 讓論點可以被驗證：三種通用容器

任何一檔股票的論點都能拆成這三種，差別只在填什麼：

| 容器 | 是什麼 | 例子 | 警訊 |
|---|---|---|---|
| 驅動因子 | 1 到 3 個量化假設，模型值 vs 實際值 | 出貨台數、SEMI 占比、CoWoS 產能、客戶資本支出年增率 | 關鍵因子落後模型 15% 或被證據削弱 |
| 檢查點 | 到某日前該看到的證據 | 客戶認證、首批出貨、授權金入帳 | 逾期沒證據 |
| 標準指標 | 月營收、季營收、毛利率、營業費用、EPS、股數 | 模型季營收 vs 月營收 run-rate | run-rate 低 15%、毛利率低 3 個百分點、股數增加 2% |

互動紀錄可以掛到驅動因子或檢查點上，標「支持 / 削弱 / 確認」。健康度檢查把三者彙整，早晨頁列出需要注意的卡片，
由你決定要不要一鍵標為動搖。建卡片時可以套用論點模板（轉型股、成長股、景氣循環股、供應鏈受惠股）。

## 維護：讓 agent 顧資料庫

| 資料 | 誰寫 | 怎麼寫 |
|---|---|---|
| 官方事實：月營收、季報六個數、股數（證交所／櫃買開放資料，免金鑰） | 腳本 | 直接寫入，`created_by=system:mops`，帶來源 |
| 需要解讀：法說裡的台數與占比、新聞對檢查點的影響 | agent | 提案，你在網頁核准 |
| 判斷：論點狀態、模型值、部位、目標價 | 你 | 網頁 |

```bash
python scripts/maintain.py all        # 備份 → 抓官方資料 → 品質報告 → 健康度，寫 data/reports/maintenance-日期.md
python scripts/maintain.py quality    # 只看資料品質
```

Windows 排程（目前使用者，不需管理員）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -MorningAt 08:30 -EveningAt 19:30
```

早上那次會接著用 Claude Code 跑 `scripts/morning_prompt.md`（只允許 stockcrm MCP 工具與網路查詢），簡報在 `data/reports/morning-日期.md`。
找不到 `claude` 指令時會自動用 Claude 桌面版附帶的那份，或用環境變數 `STOCKCRM_CLAUDE` 指定。

季報開放資料是年初至今的累計數，單季數字在連續兩季都抓到之後才會出現。早晨頁最下方有「維護狀態」面板，可以手動觸發每個工作。

## 開發

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
python scripts/check_mcp.py     # MCP server 能用 stdio 啟動並列出工具
```

GitHub Actions 在每個 PR 上跑同樣四步。

## 畫面

- **看板**：依階段分欄，卡片顏色是分層，顯示論點狀態與幾天沒更新。
- **關係圖**：終端客戶在左、供應商在右，線寬是營收比重；下方是組合對每個終端客戶的合計曝險。
- **早晨**：持股狀態、今天該查的對手方、提醒，以及一段可以直接貼給 Claude Code 的任務。
- **提案**：agent 提出的互動紀錄、供應鏈關係、論點狀態變更，核准後才會寫入。

## 示範資料的供應鏈比重是概略估計

請依年報「主要客戶」段落、法說會與券商報告修正。每條邊都有 `source` 與 `confidence` 欄位。

## 還沒做的

- 報價串接（目前手動填）。建議台股 FinMind、美股 Finnhub 或 Yahoo Finance。
- 月營收自動抓取（公開資訊觀測站）。目前靠 agent 提案或手動填。
- 交易回顧統計（勝率、各想法來源與行為標籤的表現）。資料欄位已留好。
- 多人與同步。目前是單機 SQLite。
