"""Multi-user step 1: users table, per-user scoping, proposal scope/approval, legacy migration."""
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from stockcrm import core, db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    core.set_current_user(1)
    c = db.connect(tmp_path / "u.db")
    core.upsert_company(c, "TW", "2330", "台積電")
    core.upsert_company(c, "US", "NVDA", "輝達")
    core.add_edge(c, "US:NVDA", "TW:2330", 0.1)
    yield c
    core.set_current_user(1)


def test_default_admin_and_tier_rules(conn):
    u = core.get_user(conn)
    assert u["id"] == 1 and u["role"] == "admin"
    assert len(core.export_graph(conn)["tier_rules"]) == 4


def test_positions_are_scoped_per_user(conn):
    core.upsert_position(conn, "TW:2330", "hold", "core", position_pct=10)
    fam = core.create_user(conn, "mom", "媽媽")
    assert fam["role"] == "member"
    core.set_current_user(fam["id"])
    assert core.list_positions(conn) == []
    assert core.get_position(conn, "2330") is None
    core.upsert_position(conn, "TW:2330", "watch", "watch")          # same company, own card
    assert core.list_positions(conn)[0]["stage"] == "watch"
    assert core.check_tier_limit(conn, core.get_position(conn, "2330")["id"], 20)["exceeds_limit"] is True
    core.set_current_user(1)
    assert core.list_positions(conn)[0]["stage"] == "hold"
    assert core.indirect_exposure(conn, "US:NVDA")["indirect_total_pct"] == pytest.approx(1.0)


def test_reminders_scoped(conn):
    core.add_reminder(conn, "2030-01-01", "review", "mine")
    fam = core.create_user(conn, "dad", "爸爸")
    core.set_current_user(fam["id"])
    assert core.all_reminders(conn) == []
    core.add_reminder(conn, "2030-01-01", "review", "his")
    assert [r["content"] for r in core.all_reminders(conn)] == ["his"]


def test_proposal_scope_and_approval_rights(conn):
    p = core.upsert_position(conn, "TW:2330", "hold", "core")
    personal = core.create_proposal(conn, "interaction", {"position_id": p["id"], "kind": "news", "content": "x"}, "r", "a")
    knowledge = core.create_proposal(conn, "edge", {"customer_id": "US:NVDA", "supplier_id": "TW:2330",
                                                   "revenue_share": 0.12}, "r", "a")
    assert personal["scope"] == "personal" and knowledge["scope"] == "knowledge"
    assert core.proposal_scope("metric", {"kind": "actual"}) == "knowledge"
    assert core.proposal_scope("metric", {"kind": "model"}) == "personal"
    fam = core.create_user(conn, "kid", "小孩")
    core.set_current_user(fam["id"])
    assert core.list_proposals(conn) == []                       # cannot see the admin's personal proposal
    with pytest.raises(ValueError):
        core.decide_proposal(conn, knowledge["id"], True)        # members cannot approve knowledge
    with pytest.raises(ValueError):
        core.decide_proposal(conn, personal["id"], True)         # nor another user's personal proposal
    core.set_current_user(1)
    done = core.decide_proposal(conn, knowledge["id"], True)
    assert done["approver_user_id"] == 1 and done["applied_ref"].startswith("edge:")


def test_admin_applies_member_proposal_into_member_layer(conn):
    fam = core.create_user(conn, "sis", "姊姊")
    core.set_current_user(fam["id"])
    p = core.upsert_position(conn, "TW:2330", "watch", "watch")
    pr = core.create_proposal(conn, "interaction", {"position_id": p["id"], "kind": "news", "content": "hers"}, "r", "a")
    core.set_current_user(1)
    assert pr["id"] in [x["id"] for x in core.list_proposals(conn)]   # admin sees it
    core.decide_proposal(conn, pr["id"], True)
    core.set_current_user(fam["id"])
    assert core.get_position(conn, "2330")["interactions"][0]["content"] == "hers"


def test_legacy_single_user_db_migrates_with_references_intact(tmp_path):
    old = tmp_path / "legacy.db"
    s = sqlite3.connect(old)
    s.executescript("""
      CREATE TABLE companies (id TEXT PRIMARY KEY, market TEXT, ticker TEXT, name TEXT, name_en TEXT, sector TEXT,
        notes TEXT, created_at TEXT);
      CREATE TABLE positions (id INTEGER PRIMARY KEY, company_id TEXT NOT NULL UNIQUE REFERENCES companies(id),
        stage TEXT NOT NULL, tier TEXT NOT NULL, thesis TEXT, invalidation TEXT, thesis_status TEXT NOT NULL DEFAULT 'intact',
        target_price REAL, stop_price REAL, position_pct REAL NOT NULL DEFAULT 0, idea_source TEXT,
        tags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE drivers (id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        name TEXT NOT NULL, unit TEXT, critical INTEGER NOT NULL DEFAULT 1, higher_is_better INTEGER NOT NULL DEFAULT 1,
        sensitivity TEXT, notes TEXT, created_by TEXT NOT NULL DEFAULT 'user',
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE interactions (id INTEGER PRIMARY KEY,
        position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        ts TEXT NOT NULL DEFAULT (datetime('now')), kind TEXT NOT NULL, content TEXT NOT NULL, price REAL,
        behavior_tags TEXT NOT NULL DEFAULT '[]', source_url TEXT, created_by TEXT NOT NULL DEFAULT 'user',
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE tier_rules (tier TEXT PRIMARY KEY, max_pct REAL NOT NULL, label TEXT NOT NULL, description TEXT NOT NULL);
      INSERT INTO tier_rules VALUES ('core', 20, '核心', 'x'), ('satellite', 5, '衛星', 'x'), ('watch', 0, '觀察', 'x'),
        ('blacklist', 0, '黑名單', 'x');
      INSERT INTO companies (id, market, ticker, name) VALUES ('TW:2330','TW','2330','台積電');
      INSERT INTO positions (company_id, stage, tier, position_pct) VALUES ('TW:2330','hold','core', 12);
      INSERT INTO drivers (position_id, name) VALUES (1, 'CoWoS');
      INSERT INTO interactions (position_id, kind, content) VALUES (1, 'buy', 'first');
    """)
    s.commit()
    s.close()
    core.set_current_user(1)
    c = db.connect(old)
    p = core.get_position(c, "2330")
    assert p["user_id"] == 1 and p["position_pct"] == 12
    assert [d["name"] for d in p["drivers"]] == ["CoWoS"]
    assert p["interactions"][0]["content"] == "first"           # not cascaded away by the rebuild
    assert p["tier_rule"]["max_pct"] == 20                        # customised rule survived, now owned by user 1
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    psql = c.execute("SELECT sql FROM sqlite_master WHERE name='positions'").fetchone()[0]
    assert "UNIQUE (user_id, company_id)" in psql
