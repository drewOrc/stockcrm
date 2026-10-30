"""Seed the database with demo data. Run: python seed.py [--reset]

示範用的供應鏈比重是概略估計，請依年報與法說會資料自行修正。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stockcrm import core, db  # noqa: E402

if "--reset" in sys.argv:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db.DB_PATH) + suffix)
        if p.exists():
            p.unlink()

conn = db.connect()

companies = [
    ("TW", "2330", "台積電", "TSMC", "晶圓代工"),
    ("TW", "2317", "鴻海", "Hon Hai / Foxconn", "電子組裝"),
    ("TW", "3008", "大立光", "Largan", "光學鏡頭"),
    ("TW", "2308", "台達電", "Delta Electronics", "電源與散熱"),
    ("TW", "2382", "廣達", "Quanta", "伺服器與筆電 ODM"),
    ("TW", "6669", "緯穎", "Wiwynn", "資料中心伺服器"),
    ("TW", "2454", "聯發科", "MediaTek", "IC 設計"),
    ("US", "NVDA", "輝達", "NVIDIA", "GPU"),
    ("US", "AAPL", "蘋果", "Apple", "消費電子"),
    ("US", "AMD", "超微", "AMD", "CPU/GPU"),
    ("US", "MSFT", "微軟", "Microsoft", "雲端"),
    ("US", "META", "Meta", "Meta Platforms", "雲端"),
    ("US", "TSM", "台積電 ADR", "TSMC ADR", "晶圓代工"),
]
for m, t, n, en, s in companies:
    core.upsert_company(conn, m, t, n, en, s)

# (customer, supplier, share_of_supplier_revenue, lag_days, confidence, source)
edges = [
    ("US:AAPL", "TW:2330", 0.25, 60, "high", "台積電年報：最大客戶約 25%"),
    ("US:NVDA", "TW:2330", 0.11, 60, "medium", "媒體估計"),
    ("US:AMD",  "TW:2330", 0.07, 60, "low", "媒體估計"),
    ("TW:2454", "TW:2330", 0.05, 60, "low", "估計"),
    ("US:AAPL", "TW:2317", 0.50, 45, "high", "鴻海年報：最大客戶約五成"),
    ("US:NVDA", "TW:2317", 0.05, 45, "low", "AI 伺服器組裝，估計"),
    ("US:AAPL", "TW:3008", 0.45, 45, "medium", "市場共識，年報未具名"),
    ("US:NVDA", "TW:2382", 0.15, 45, "low", "AI 伺服器，估計"),
    ("US:MSFT", "TW:2382", 0.20, 45, "low", "雲端伺服器，估計"),
    ("US:META", "TW:6669", 0.45, 30, "medium", "緯穎年報：最大客戶約四成"),
    ("US:MSFT", "TW:6669", 0.30, 30, "medium", "緯穎年報：第二大客戶"),
    ("US:NVDA", "TW:2308", 0.08, 60, "low", "電源與散熱，估計"),
    ("US:MSFT", "US:NVDA", 0.15, 90, "low", "雲端巨頭合計占 NVDA 資料中心營收近半，估計"),
    ("US:META", "US:NVDA", 0.10, 90, "low", "估計"),
]
for c, s, share, lag, conf, src in edges:
    core.add_edge(conn, c, s, share, lag, conf, src)

pos = core.upsert_position(
    conn, "TW:2330", "hold", "core",
    thesis="先進製程壟斷地位，AI 需求讓 CoWoS 產能吃緊到 2027。毛利率守住 53% 以上。",
    invalidation="毛利率連續兩季低於 50%；或 Intel 18A 拿到主要外部客戶。",
    target_price=1500, stop_price=800, position_pct=12, idea_source="self", tags=["AI", "半導體"])
core.add_interaction(conn, pos["id"], "buy", "首次建倉，法說會後上修資本支出。", price=1020,
                     ts="2026-07-18 02:00:00")
core.add_interaction(conn, pos["id"], "earnings", "Q3 法說：毛利率 57%，上修全年營收成長至 30% 以上。論點強化。",
                     price=1180, ts="2026-10-16 08:00:00")

pos = core.upsert_position(
    conn, "TW:2382", "hold", "satellite",
    thesis="AI 伺服器機櫃出貨放量，GB300 主要組裝廠之一。",
    invalidation="NVDA 下修資料中心展望；或廣達 AI 伺服器營收占比連兩季下滑。",
    target_price=400, stop_price=240, position_pct=4, idea_source="券商報告", tags=["AI", "伺服器"])
core.add_interaction(conn, pos["id"], "buy", "GB300 量產時程確認後進場。", price=290, ts="2026-08-05 02:00:00")
core.add_interaction(conn, pos["id"], "thought", "追高了一點，但論點還在。", price=320,
                     behavior_tags=["chasing"], ts="2026-08-20 02:00:00")

pos = core.upsert_position(
    conn, "TW:6669", "hold", "satellite",
    thesis="Meta 與微軟資本支出持續成長，緯穎直接受惠。",
    invalidation="Meta 或 MSFT 任一家宣布資本支出下修；或客戶集中度風險實現。",
    target_price=3500, stop_price=2000, position_pct=3, idea_source="朋友", tags=["AI", "資料中心"])
core.add_interaction(conn, pos["id"], "buy", "Meta 法說上修 capex 後買進。", price=2400, ts="2026-08-01 02:00:00")

core.upsert_position(
    conn, "TW:3008", "watch", "watch",
    thesis="潛望式鏡頭滲透率提升，但單一客戶風險極高。等 Apple 新機銷售數字。",
    invalidation="Apple 導入第二供應商比重超過 50%。",
    target_price=2200, idea_source="self", tags=["Apple 供應鏈"])

pos = core.upsert_position(
    conn, "US:NVDA", "hold", "core",
    thesis="AI 訓練與推論晶片的事實標準，CUDA 生態系護城河。",
    invalidation="資料中心營收季增轉負；或主要雲端客戶自研晶片占比明顯上升。",
    target_price=250, stop_price=120, position_pct=10, idea_source="self", tags=["AI"])
core.add_interaction(conn, pos["id"], "buy", "建倉。", price=165, ts="2026-06-10 14:30:00")

core.upsert_position(
    conn, "TW:2317", "exited", "satellite",
    thesis="電動車與 AI 伺服器雙引擎。",
    invalidation="Apple 砍單。",
    position_pct=0, idea_source="新聞", tags=[])
ex = core.get_position(conn, "TW:2317")
core.add_interaction(conn, ex["id"], "buy", "買進。", price=180, ts="2026-03-01 02:00:00")
core.add_interaction(conn, ex["id"], "sell", "出場，獲利 15%。電動車進度慢於預期。", price=207,
                     ts="2026-06-20 02:00:00")
core.add_interaction(conn, ex["id"], "review",
                     "回顧：論點裡電動車那半沒實現，AI 伺服器那半才是漲的原因。下次論點要拆開寫。",
                     ts="2026-09-01 02:00:00")

core.upsert_position(
    conn, "US:TSM", "watch", "blacklist",
    thesis="與 2330 同一家公司，只用來觀察 ADR 溢價當情緒指標，不直接買。",
    invalidation="", idea_source="self", tags=["情緒指標"])

core.add_reminder(conn, "2026-11-10", "revenue", "台股 10 月營收公布，看廣達與緯穎 AI 伺服器占比",
                  position_id=core.get_position(conn, "TW:2382")["id"])
core.add_reminder(conn, "2026-11-19", "earnings", "NVDA Q3 財報，注意資料中心展望",
                  position_id=core.get_position(conn, "US:NVDA")["id"])
core.add_reminder(conn, "2027-01-15", "review", "季度回顧：所有持股論點是否仍成立")

core.create_proposal(
    conn, "interaction",
    {"position_id": core.get_position(conn, "TW:6669")["id"], "kind": "news",
     "content": "Meta 法說：2027 年資本支出預期顯著高於 2026 年，主要用於 AI 基礎設施。",
     "source_url": "https://investor.atmeta.com/"},
    rationale="Meta 是緯穎最大客戶（約 45%），資本支出上修直接支撐緯穎論點。建議記入互動紀錄。",
    created_by="agent:demo")
core.create_proposal(
    conn, "edge",
    {"customer_id": "US:AMD", "supplier_id": "TW:2382", "revenue_share": 0.05, "lag_days": 45,
     "confidence": "low", "source": "廣達法說提及 MI300 伺服器專案"},
    rationale="廣達法說會提到 AMD MI300 伺服器專案，目前圖上沒有這條邊。",
    created_by="agent:demo")

print(f"seeded {db.DB_PATH}")
print(f"companies={len(core.search_companies(conn))} positions={len(core.list_positions(conn))} "
      f"edges={len(core.export_graph(conn)['edges'])} proposals={len(core.list_proposals(conn))}")
