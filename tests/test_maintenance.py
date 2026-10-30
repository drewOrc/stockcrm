"""Maintenance: open-data parsing (offline fixtures), YTD differencing, backup, quality report, job log."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from stockcrm import core, db, mops  # noqa: E402

MONTHLY = [
    {"出表日期": "1151017", "資料年月": "11509", "公司代號": "2330", "公司名稱": "台積電",
     "營業收入-當月營收": "511857399", "營業收入-上月營收": "514805337", "營業收入-去年當月營收": "330980920",
     "營業收入-去年同月增減(%)": "54.64861207105231", "備註": "因先進製程產品需求增加所致。"},
    {"出表日期": "1151017", "資料年月": "11509", "公司代號": "9999", "公司名稱": "別家",
     "營業收入-當月營收": "100", "營業收入-上月營收": "90", "營業收入-去年當月營收": "80", "備註": "-"},
    {"出表日期": "1151017", "資料年月": "", "公司代號": "", "公司名稱": "壞列"},
]
QUARTERLY_LISTED = {"出表日期": "1151030", "年度": "115", "季別": "2", "公司代號": "2330", "營業收入": "2404483690.00",
                    "營業毛利（毛損）淨額": "1611606116.00", "營業費用": "192987740.00",
                    "淨利（淨損）歸屬於母公司業主": "1279041690.00", "基本每股盈餘（元）": "49.33"}
QUARTERLY_OTC = {"Date": "1151030", "Year": "115", "Season": "1", "SecuritiesCompanyCode": "2330", "營業收入": "1000000000",
                 "營業毛利（毛損）淨額": "600000000", "營業費用": "90000000", "本期淨利（淨損）": "500000000",
                 "基本每股盈餘（元）": "20.00"}
SHARES = [{"出表日期": "1151029", "公司代號": "2330", "已發行普通股數或TDR原股發行股數": "25932370067"},
          {"出表日期": "1151029", "公司代號": "0000", "已發行普通股數或TDR原股發行股數": ""}]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    core.upsert_company(c, "TW", "2330", "台積電")
    core.upsert_company(c, "TW", "2382", "廣達")
    core.upsert_company(c, "US", "NVDA", "輝達")
    core.upsert_position(c, "TW:2330", "hold", "core", position_pct=10)
    core.add_edge(c, "US:NVDA", "TW:2382", 0.2)
    return c


def test_parse_monthly_rows_roc_dates_and_numbers():
    rows = mops.parse_monthly_rows(MONTHLY)
    assert len(rows) == 2
    r = rows[0]
    assert r["period"] == "2026-09" and r["revenue"] == 511857399 and r["revenue_last_year"] == 330980920
    assert r["remark"].startswith("因先進製程")
    assert rows[1]["remark"] == ""


def test_apply_monthly_writes_three_months_for_tracked_only(conn):
    res = mops.apply_monthly(conn, mops.parse_monthly_rows(MONTHLY))
    assert [w["company_id"] for w in res["written"]] == ["TW:2330"]
    assert res["missing"] == ["2382"]
    periods = {m["period"]: m["value"] for m in core.list_metrics(conn, "TW:2330", "revenue_m")}
    assert periods == {"2026-09": 511857399, "2026-08": 514805337, "2025-09": 330980920}
    assert core.metric_comparison(conn, "TW:2330")["monthly_yoy"][-1]["yoy"] == pytest.approx(0.5465, abs=1e-3)
    assert core.list_metrics(conn, "TW:2330", "revenue_m")[0]["created_by"] == "system:mops"


def test_tw_companies_includes_cards_and_edge_members(conn):
    assert [c["ticker"] for c in core.tw_companies(conn)] == ["2330", "2382"]


def test_quarterly_ytd_differencing(conn):
    q2 = mops.parse_quarterly_rows([QUARTERLY_LISTED])[0]
    q1 = mops.parse_quarterly_rows([QUARTERLY_OTC])[0]
    assert q2["year"] == 2026 and q2["quarter"] == 2 and q1["quarter"] == 1
    res = mops.apply_quarterly(conn, [q2])
    assert res["derived"] == [] and "need YTD for Q1" in res["skipped"][0]["reason"]
    res = mops.apply_quarterly(conn, [q1, q2])
    assert {d["period"] for d in res["derived"]} == {"2026Q1", "2026Q2"}
    cmp = {(t["metric"], t["period"]): t["actual"] for t in core.metric_comparison(conn, "TW:2330")["comparisons"]}
    assert cmp[("revenue_q", "2026Q2")] == pytest.approx(2404483690 - 1000000000)
    assert cmp[("gross_margin", "2026Q2")] == pytest.approx((1611606116 - 600000000) / (2404483690 - 1000000000), abs=1e-4)
    assert cmp[("eps", "2026Q2")] == pytest.approx(29.33)
    assert cmp[("eps", "2026Q1")] == pytest.approx(20.0)


def test_shares_in_thousands(conn):
    res = mops.apply_shares(conn, mops.parse_shares_rows(SHARES))
    assert res["written"][0]["shares_thousands"] == pytest.approx(25932370.067)
    assert core.list_metrics(conn, "TW:2330", "shares")[0]["value"] == pytest.approx(25932370.067, abs=1e-3)


def test_backup_creates_and_prunes(conn, tmp_path):
    bdir = tmp_path / "bk"
    bdir.mkdir()
    (bdir / "m-20000101-000000.db").write_bytes(b"old")
    r = core.backup_db(conn, keep_days=30, backup_dir=bdir)
    assert pathlib.Path(r["path"]).exists() and r["bytes"] > 0
    assert r["pruned"] == ["m-20000101-000000.db"] and r["kept"] == 1
    import sqlite3
    assert sqlite3.connect(r["path"]).execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 3


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
