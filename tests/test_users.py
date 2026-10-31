"""Multi-user step 1: users table, per-user scoping, proposal scope/approval, legacy migration."""
import pathlib
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
    assert "password_hash" not in u and "password_hash" not in core.list_users(conn)[0]
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


def test_dotenv_parse_and_load(tmp_path, monkeypatch):
    from stockcrm import env
    f = tmp_path / ".env"
    f.write_text("# comment\nexport STOCKCRM_FACADE_TOKEN='abc 123'\nSTOCKCRM_MULTIUSER=1 # on\nBAD LINE\nEMPTY=\n",
                 encoding="utf-8")
    vals = env.parse_env(f.read_text(encoding="utf-8"))
    assert vals == {"STOCKCRM_FACADE_TOKEN": "abc 123", "STOCKCRM_MULTIUSER": "1", "EMPTY": ""}
    monkeypatch.setenv("STOCKCRM_MULTIUSER", "0")
    monkeypatch.delenv("STOCKCRM_FACADE_TOKEN", raising=False)
    env.load_dotenv(f)
    import os
    assert os.environ["STOCKCRM_FACADE_TOKEN"] == "abc 123"
    assert os.environ["STOCKCRM_MULTIUSER"] == "0"      # existing variables win
