"""Core logic tests. Each test gets a fresh temp SQLite database."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from stockcrm import core, db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    core.upsert_company(c, "TW", "2330", "台積電")
    core.upsert_company(c, "TW", "2382", "廣達")
    core.upsert_company(c, "US", "NVDA", "輝達")
    core.upsert_company(c, "US", "AAPL", "蘋果")
    core.add_edge(c, "US:NVDA", "TW:2330", 0.10, 60)
    core.add_edge(c, "US:AAPL", "TW:2330", 0.25, 60)
    core.add_edge(c, "US:NVDA", "TW:2382", 0.20, 45)
    core.upsert_position(c, "TW:2330", "hold", "core", thesis="t", position_pct=10)
    core.upsert_position(c, "TW:2382", "hold", "satellite", thesis="t", position_pct=4)
    core.upsert_position(c, "US:NVDA", "hold", "core", thesis="t", position_pct=8)
    return c


def pos(conn, ref):
    return core.get_position(conn, ref)


# ---------------------------------------------------------------- supply chain

def test_indirect_exposure_walks_customer_to_supplier(conn):
    ex = core.indirect_exposure(conn, "US:NVDA")
    assert ex["directly_held_pct"] == 8
    by = {e["company_id"]: e["exposure_pct"] for e in ex["exposures"]}
    assert by["TW:2330"] == pytest.approx(1.0)   # 10% * 0.10
    assert by["TW:2382"] == pytest.approx(0.8)   # 4% * 0.20
    assert ex["indirect_total_pct"] == pytest.approx(1.8)


def test_portfolio_overlap_sorted_by_combined(conn):
    ov = core.portfolio_overlap(conn)
    assert ov[0]["customer_id"] == "US:NVDA"
    assert ov[0]["combined_pct"] == pytest.approx(9.8)


def test_customer_concentration_flags_large_customer(conn):
    cc = core.customer_concentration(conn, "TW:2330")
    assert cc["largest_customer"] == "US:AAPL"
    assert cc["flag"] == "medium"


def test_partner_edge_relation(conn):
    core.upsert_company(conn, "US", "BRK", "Partner")
    e = core.add_edge(conn, "US:BRK", "TW:2382", 0.5, relation="partner")
    assert e["relation"] == "partner"
    with pytest.raises(ValueError):
        core.add_edge(conn, "US:BRK", "TW:2382", relation="friend")


# ---------------------------------------------------------------- tiers & proposals

def test_tier_limit(conn):
    p = pos(conn, "2382")
    assert core.check_tier_limit(conn, p["id"], 6)["exceeds_limit"] is True
    assert core.check_tier_limit(conn, p["id"], 4.5)["exceeds_limit"] is False


def test_proposal_approve_applies_interaction(conn):
    p = pos(conn, "2330")
    pr = core.create_proposal(conn, "interaction", {"position_id": p["id"], "kind": "news", "content": "x"}, "r", "agent:t")
    assert pr["status"] == "pending"
    done = core.decide_proposal(conn, pr["id"], True)
    assert done["status"] == "approved" and done["applied_ref"].startswith("interaction:")
    assert pos(conn, "2330")["interactions"][0]["created_by"] == "agent:t"
    with pytest.raises(ValueError):
        core.decide_proposal(conn, pr["id"], False)


def test_proposal_rejects_bad_enum(conn):
    p = pos(conn, "2330")
    with pytest.raises(ValueError):
        core.create_proposal(conn, "interaction", {"position_id": p["id"], "kind": "rumor", "content": "x"}, "r", "a")


# ---------------------------------------------------------------- drivers

def test_driver_signal_behind_and_weakened(conn):
    p = pos(conn, "2330")
    d = core.add_driver(conn, p["id"], "CoWoS", "k", 1)
    core.set_driver_value(conn, d["id"], "2026", "model", 100)
    assert core.get_driver(conn, d["id"])["signal"] == "no_data"
    core.set_driver_value(conn, d["id"], "2026", "actual", 90)
    assert core.get_driver(conn, d["id"])["signal"] == "on_track"
    core.set_driver_value(conn, d["id"], "2026", "actual", 70)
    assert core.get_driver(conn, d["id"])["signal"] == "behind"
    core.add_interaction(conn, p["id"], "news", "bad", driver_id=d["id"], effect="weakens")
    assert core.get_driver(conn, d["id"])["signal"] == "weakened"


def test_driver_lower_is_better(conn):
    p = pos(conn, "2330")
    d = core.add_driver(conn, p["id"], "庫存天數", "天", 1, higher_is_better=0)
    core.set_driver_value(conn, d["id"], "2026Q1", "model", 100)
    core.set_driver_value(conn, d["id"], "2026Q1", "actual", 70)
    assert core.get_driver(conn, d["id"])["signal"] == "ahead"


def test_driver_must_belong_to_position(conn):
    a, b = pos(conn, "2330"), pos(conn, "2382")
    d = core.add_driver(conn, a["id"], "x")
    with pytest.raises(ValueError):
        core.add_interaction(conn, b["id"], "news", "x", driver_id=d["id"], effect="supports")


def test_period_parsing():
    assert core._parse_period("2026") == ("year", 2026, 0)
    assert core._parse_period("2026q2") == ("quarter", 2026, 2)
    assert core._parse_period("2026-05") == ("month", 2026, 5)
    for bad in ("26", "2026Q5", "2026-13", "May 2026"):
        with pytest.raises(ValueError):
            core._parse_period(bad)


# ---------------------------------------------------------------- checkpoints

def test_checkpoint_overdue_and_confirm_via_interaction(conn):
    p = pos(conn, "2330")
    k = core.add_checkpoint(conn, p["id"], "首批出貨", "2020-01-01")
    cps = core.list_checkpoints(conn, p["id"])
    assert cps[0]["overdue"] is True
    core.add_interaction(conn, p["id"], "news", "shipped", checkpoint_id=k["id"], effect="confirms")
    k2 = [c for c in core.list_checkpoints(conn, p["id"]) if c["id"] == k["id"]][0]
    assert k2["status"] == "confirmed" and k2["overdue"] is False


def test_checkpoint_resolve_proposal(conn):
    p = pos(conn, "2330")
    k = core.add_checkpoint(conn, p["id"], "x", "2030-01-01")
    payload = {"checkpoint_id": k["id"], "status": "missed", "evidence": "none"}
    pr = core.create_proposal(conn, "checkpoint_resolve", payload, "r", "a")
    core.decide_proposal(conn, pr["id"], True)
    assert core.list_checkpoints(conn, p["id"])[0]["status"] == "missed"


# ---------------------------------------------------------------- metrics

def test_run_rate_projection(conn):
    core.set_metric(conn, "TW:2330", "revenue_q", "2026Q2", "model", 390_000)
    core.set_metric(conn, "TW:2330", "revenue_m", "2026-04", "actual", 108_121)
    core.set_metric(conn, "TW:2330", "revenue_m", "2026-05", "actual", 132_546)
    rr = core.metric_comparison(conn, "TW:2330")["run_rate"][0]
    assert rr["months_available"] == 2
    assert rr["projected"] == pytest.approx(108_121 + 132_546 * 2)
    assert rr["flag"] is False  # within 15%
    core.set_metric(conn, "TW:2330", "revenue_m", "2026-05", "actual", 100_000)
    rr = core.metric_comparison(conn, "TW:2330")["run_rate"][0]
    assert rr["flag"] is True


def test_metric_period_validation(conn):
    with pytest.raises(ValueError):
        core.set_metric(conn, "TW:2330", "revenue_m", "2026Q2", "actual", 1)
    with pytest.raises(ValueError):
        core.set_metric(conn, "TW:2330", "eps", "2026-05", "actual", 1)


def test_gross_margin_and_dilution_flags(conn):
    core.set_metric(conn, "TW:2330", "gross_margin", "2026Q1", "model", 0.56)
    core.set_metric(conn, "TW:2330", "gross_margin", "2026Q1", "actual", 0.50)
    core.set_metric(conn, "TW:2330", "shares", "2026", "model", 25_930)
    core.set_metric(conn, "TW:2330", "shares", "2026", "actual", 27_000)
    flags = {t["metric"]: t["flag"] for t in core.metric_comparison(conn, "TW:2330")["comparisons"]}
    assert flags["gross_margin"] is True and flags["shares"] is True


# ---------------------------------------------------------------- health

def test_thesis_health_and_apply_flags(conn):
    p = pos(conn, "2330")
    assert core.thesis_health(conn, p["id"])["status"] == "ok"
    core.add_checkpoint(conn, p["id"], "late", "2020-01-01")
    h = core.thesis_health(conn, p["id"])
    assert h["issues"][0]["type"] == "checkpoint_overdue" and h["suggest_shaken"] is True
    changed = core.apply_health_flags(conn)
    assert [c["company_id"] for c in changed] == ["TW:2330"]
    assert pos(conn, "2330")["thesis_status"] == "shaken"
    assert core.apply_health_flags(conn) == []  # idempotent


def test_non_critical_driver_does_not_suggest_shaken(conn):
    p = pos(conn, "2330")
    d = core.add_driver(conn, p["id"], "minor", critical=0)
    core.set_driver_value(conn, d["id"], "2026", "model", 100)
    core.set_driver_value(conn, d["id"], "2026", "actual", 50)
    h = core.thesis_health(conn, p["id"])
    assert h["issues"] and h["suggest_shaken"] is False


def test_morning_context_attention(conn):
    p = pos(conn, "2382")
    core.add_checkpoint(conn, p["id"], "late", "2020-01-01")
    m = core.morning_context(conn)
    assert [a["company_id"] for a in m["attention"]] == ["TW:2382"]
    assert "US:NVDA" in [c["company_id"] for c in m["counterparties_to_check"]]


def test_template_creates_drivers_and_checkpoints(conn):
    p = pos(conn, "2382")
    made = core.apply_template(conn, p["id"], "transformation")
    assert len(made["drivers"]) == 3 and len(made["checkpoints"]) == 3
    with pytest.raises(ValueError):
        core.apply_template(conn, p["id"], "nope")


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    old = tmp_path / "old.db"
    s = sqlite3.connect(old)
    s.executescript("""
      CREATE TABLE companies (id TEXT PRIMARY KEY, market TEXT, ticker TEXT, name TEXT, name_en TEXT, sector TEXT,
        notes TEXT, created_at TEXT);
      CREATE TABLE edges (id INTEGER PRIMARY KEY, customer_id TEXT, supplier_id TEXT, revenue_share REAL, lag_days INTEGER,
        confidence TEXT, source TEXT, notes TEXT, created_by TEXT, created_at TEXT, UNIQUE(customer_id, supplier_id));
      CREATE TABLE positions (id INTEGER PRIMARY KEY, company_id TEXT UNIQUE, stage TEXT, tier TEXT, thesis TEXT,
        invalidation TEXT,
        thesis_status TEXT DEFAULT 'intact', target_price REAL, stop_price REAL, position_pct REAL DEFAULT 0, idea_source TEXT,
        tags TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT);
      CREATE TABLE interactions (id INTEGER PRIMARY KEY, position_id INTEGER, ts TEXT, kind TEXT, content TEXT, price REAL,
        behavior_tags TEXT DEFAULT '[]', source_url TEXT, created_by TEXT, created_at TEXT);
      CREATE TABLE proposals (id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('interaction','edge','thesis_status','reminder')),
        payload TEXT NOT NULL, rationale TEXT NOT NULL, created_by TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')), decided_at TEXT, applied_ref TEXT);
      INSERT INTO proposals (kind, payload, rationale, created_by) VALUES ('reminder', '{}', 'r', 'a');
    """)
    s.commit()
    s.close()
    c = db.connect(old)
    cols = {r[1] for r in c.execute("PRAGMA table_info(interactions)")}
    assert {"driver_id", "checkpoint_id", "effect"} <= cols
    assert "relation" in {r[1] for r in c.execute("PRAGMA table_info(edges)")}
    c.execute("INSERT INTO proposals (kind, payload, rationale, created_by) VALUES ('metric', '{}', 'r', 'a')")
    assert c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2


def test_rename_company_moves_references(conn):
    core.set_metric(conn, "TW:2382", "revenue_m", "2026-09", "actual", 1)
    c = core.rename_company(conn, "TW:2382", "TW", "4542", "科嶠")
    assert c["id"] == "TW:4542" and c["name"] == "科嶠"
    assert core.get_company(conn, "TW:2382") is None
    assert core.get_position(conn, "TW:4542")["company_id"] == "TW:4542"
    assert any(e["supplier_id"] == "TW:4542" for e in core.export_graph(conn)["edges"])
    assert core.list_metrics(conn, "TW:4542", "revenue_m")[0]["value"] == 1
    with pytest.raises(ValueError):
        core.rename_company(conn, "TW:4542", "TW", "2330")


def test_holding_relation_and_migration_from_partner_only_check(tmp_path):
    import sqlite3
    old = tmp_path / "e.db"
    s = sqlite3.connect(old)
    s.executescript("""
      CREATE TABLE companies (id TEXT PRIMARY KEY, market TEXT, ticker TEXT, name TEXT, name_en TEXT, sector TEXT,
        notes TEXT, created_at TEXT);
      CREATE TABLE edges (id INTEGER PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES companies(id),
        supplier_id TEXT NOT NULL REFERENCES companies(id), revenue_share REAL, lag_days INTEGER,
        confidence TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
        relation TEXT NOT NULL DEFAULT 'customer' CHECK (relation IN ('customer','partner')),
        source TEXT, notes TEXT, created_by TEXT NOT NULL DEFAULT 'user',
        created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE (customer_id, supplier_id));
      INSERT INTO companies (id, market, ticker, name) VALUES ('TW:2330','TW','2330','台積電'), ('TW:00981A','TW','00981A','ETF');
      INSERT INTO edges (customer_id, supplier_id, revenue_share, relation) VALUES ('TW:2330','TW:00981A',0.5,'partner');
    """)
    s.commit()
    s.close()
    c = db.connect(old)
    e = core.add_edge(c, "TW:2330", "TW:00981A", 0.1184, relation="holding", source="MoneyDJ 2026-10-30")
    assert e["relation"] == "holding" and e["revenue_share"] == 0.1184
    assert len(core.export_graph(c)["edges"]) == 1  # upsert kept the single row


def test_etf_holding_exposure(conn):
    core.upsert_company(conn, "TW", "00981A", "ETF", sector="ETF")
    core.upsert_position(conn, "TW:00981A", "hold", "core", position_pct=100)
    core.add_edge(conn, "TW:2330", "TW:00981A", 0.1184, relation="holding")
    ex = core.indirect_exposure(conn, "TW:2330")
    assert [e["company_id"] for e in ex["exposures"]] == ["TW:00981A"]
    assert ex["exposures"][0]["exposure_pct"] == pytest.approx(11.84)
    # two hops: NVDA -> TSMC (10%) -> ETF (11.84%) on a 100% position
    nv = core.indirect_exposure(conn, "US:NVDA")
    etf = [e for e in nv["exposures"] if e["company_id"] == "TW:00981A"][0]
    assert etf["exposure_pct"] == pytest.approx(1.18, abs=0.01)


def test_briefs_profile_highlights_and_summary(conn):
    assert core.brief_summary(conn, "TW:2330") == {"latest_brief": None, "has_profile": False}
    core.add_brief(conn, "TW:2330", "profile", "全球最大晶圓代工。先進製程壟斷。",
                   [{"title": "年報", "url": "https://x", "date": "2026-03-31"}])
    core.add_brief(conn, "TW:2330", "highlights", "- Q3 毛利率 57%，上修全年。\n- CoWoS 擴產。", date="2026-10-16")
    core.add_brief(conn, "TW:2330", "highlights", "- 10 月營收創高。", date="2026-11-10")
    s = core.brief_summary(conn, "TW:2330")
    assert s["has_profile"] is True and s["latest_brief"]["date"] == "2026-11-10"
    assert s["latest_brief"]["text"] == "10 月營收創高。"
    card = core.briefs_for_card(conn, "TW:2330")
    assert card["profile"]["sources"][0]["title"] == "年報"
    assert [h["date"] for h in card["highlights"]] == ["2026-11-10", "2026-10-16"]
    assert core.list_positions(conn)[0]["latest_brief"]["text"] == "10 月營收創高。"
    with pytest.raises(ValueError):
        core.add_brief(conn, "TW:2330", "rumor", "x")
    with pytest.raises(ValueError):
        core.add_brief(conn, "TW:2330", "profile", "   ")


def test_brief_proposal_applies(conn):
    payload = {"company_id": "TW:2382", "kind": "highlights", "content": "- 終止收購廠房。",
               "sources": [{"title": "鉅亨", "url": "https://u", "date": "2026-10-30"}]}
    pr = core.create_proposal(conn, "brief", payload, "近況", "agent:t")
    assert core.list_proposals(conn)[0]["summary"].startswith("近期重點：廣達")
    done = core.decide_proposal(conn, pr["id"], True)
    assert done["applied_ref"].startswith("brief:")
    assert core.list_briefs(conn, "TW:2382")[0]["created_by"] == "agent:t"


def test_reset_and_rename_cover_briefs(conn):
    core.add_brief(conn, "TW:2382", "profile", "伺服器 ODM。")
    core.rename_company(conn, "TW:2382", "TW", "9999")
    assert core.list_briefs(conn, "TW:9999")[0]["content"] == "伺服器 ODM。"
    core.reset_data(conn)
    assert conn.execute("SELECT COUNT(*) FROM company_briefs").fetchone()[0] == 0
