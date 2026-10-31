"""Maintenance: backup, quality report, job log, reset, standalone bootstrap guard.
Official data fetching (MOPS) moved to the engine; its tests moved with it."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from stockcrm import core, db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    core.set_current_user(1)
    c = db.connect(tmp_path / "m.db")
    core.upsert_company(c, "TW", "2330", "台積電")
    core.upsert_company(c, "TW", "2382", "廣達")
    core.upsert_company(c, "US", "NVDA", "輝達")
    core.upsert_position(c, "TW:2330", "hold", "core", position_pct=10)
    core.add_edge(c, "US:NVDA", "TW:2382", 0.2)
    return c


def test_connect_refuses_db_without_engine_tables(tmp_path):
    with pytest.raises(RuntimeError, match="engine-owned tables"):
        db.connect(tmp_path / "empty.db", bootstrap=False)


def test_schema_registry_lists_crm_tables(conn):
    rows = {r["table_name"]: r["owner"] for r in conn.execute("SELECT table_name, owner FROM schema_registry")}
    assert rows["personal_positions"] == "crm" and rows["crm_maintenance_log"] == "crm"
    assert "knowledge_companies" not in rows   # the engine registers its own


def test_tw_companies_includes_cards_and_edge_members_but_not_etfs(conn):
    core.upsert_company(conn, "TW", "00981A", "主動統一台股增長", sector="ETF")
    core.upsert_position(conn, "TW:00981A", "hold", "core", position_pct=50)
    assert [c["ticker"] for c in core.tw_companies(conn)] == ["2330", "2382"]


def test_backup_creates_and_prunes(conn, tmp_path):
    bdir = tmp_path / "bk"
    bdir.mkdir()
    (bdir / "m-20000101-000000.db").write_bytes(b"old")
    r = core.backup_db(conn, keep_days=30, backup_dir=bdir)
    assert pathlib.Path(r["path"]).exists() and r["bytes"] > 0
    assert r["pruned"] == ["m-20000101-000000.db"] and r["kept"] == 1
    import sqlite3
    assert sqlite3.connect(r["path"]).execute("SELECT COUNT(*) FROM knowledge_companies").fetchone()[0] == 3


def test_quality_report_and_markdown(conn):
    rep = core.data_quality_report(conn)
    keys = {k for k, v in rep.items() if isinstance(v, list) and v}
    assert {"edges_without_source", "holdings_without_invalidation", "holdings_without_drivers",
            "tw_companies_missing_recent_revenue"} <= keys
    assert rep["total"] >= 4
    md = core.quality_markdown(rep)
    assert "資料品質報告" in md and "沒有來源的供應鏈關係（1）" in md


def test_maintenance_log(conn):
    core.log_maintenance(conn, "quality", "ok", "5 issues", {"total": 5})
    with pytest.raises(ValueError):
        core.log_maintenance(conn, "quality", "meh", "x")
    rows = core.list_maintenance(conn)
    assert rows[0]["job"] == "quality" and rows[0]["details"] == {"total": 5}
    assert core.last_run(conn, "quality")["status"] == "ok"
    assert core.last_run(conn, "backup") is None


def test_reset_data_keeps_knowledge_users_rules_and_log(conn):
    core.log_maintenance(conn, "quality", "ok", "before")
    core.set_metric(conn, "TW:2330", "eps", "2026", "model", 50)
    counts = core.reset_data(conn)
    assert counts["personal_positions"] == 1 and counts["personal_model_values"] == 1
    assert core.list_positions(conn) == []
    assert len(core.search_companies(conn)) == 3                      # knowledge untouched
    assert conn.execute("SELECT COUNT(*) FROM personal_tier_rules").fetchone()[0] == 4
    jobs = [r["job"] for r in core.list_maintenance(conn)]
    assert jobs[0] == "reset" and "quality" in jobs
