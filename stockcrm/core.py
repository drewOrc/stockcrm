"""Business logic. Every function takes a sqlite3.Connection and returns plain dicts/lists
so the same code serves the REST API, the MCP server and scripts.

Write policy (enforced by callers, documented here):
  - Direct writes: things the USER asked to record (trades, thoughts, watchlist adds, reminders).
  - Proposals:     things an AGENT inferred from external information (news-derived interactions,
                   new supply-chain edges, thesis status changes). The user approves in the UI.
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone

from . import db

# ---------------------------------------------------------------- helpers

def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _check(value, allowed, field):
    if value not in allowed:
        raise ValueError(f"{field} must be one of {list(allowed)}, got {value!r}")


def _json_list(v) -> str:
    if v is None:
        return "[]"
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            v = [s.strip() for s in v.split(",") if s.strip()]
    return json.dumps(list(v), ensure_ascii=False)


def _decode(d: dict) -> dict:
    for k in ("tags", "behavior_tags"):
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    if "payload" in d and isinstance(d["payload"], str):
        d["payload"] = json.loads(d["payload"])
    return d


def company_id(market: str, ticker: str) -> str:
    return f"{market.upper()}:{ticker.upper()}"


# ---------------------------------------------------------------- companies

def upsert_company(conn, market, ticker, name, name_en=None, sector=None, notes=None) -> dict:
    _check(market, db.MARKETS, "market")
    cid = company_id(market, ticker)
    conn.execute(
        """INSERT INTO companies (id, market, ticker, name, name_en, sector, notes)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,
             name_en=COALESCE(excluded.name_en, companies.name_en),
             sector=COALESCE(excluded.sector, companies.sector),
             notes=COALESCE(excluded.notes, companies.notes)""",
        (cid, market.upper(), ticker.upper(), name, name_en, sector, notes),
    )
    conn.commit()
    return get_company(conn, cid)


def get_company(conn, cid: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM companies WHERE id=?", (cid,)))


def search_companies(conn, query: str = "", market: str | None = None) -> list[dict]:
    q = f"%{query}%"
    sql = ("SELECT c.*, p.stage, p.tier FROM companies c LEFT JOIN positions p ON p.company_id=c.id "
           "WHERE (c.id LIKE ? OR c.name LIKE ? OR IFNULL(c.name_en,'') LIKE ?)")
    args = [q, q, q]
    if market:
        sql += " AND c.market=?"
        args.append(market.upper())
    sql += " ORDER BY c.market, c.ticker"
    return _rows(conn.execute(sql, args))


def resolve_company(conn, ref: str) -> dict:
    """Accept 'TW:2330', '2330', 'NVDA', or a name. Returns the company or raises."""
    ref = ref.strip()
    c = get_company(conn, ref.upper())
    if c:
        return c
    rows = _rows(conn.execute(
        "SELECT * FROM companies WHERE ticker=? OR name=? OR name_en=?", (ref.upper(), ref, ref)))
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise ValueError(f"ambiguous company {ref!r}: {[r['id'] for r in rows]}; use MARKET:TICKER")
    raise ValueError(f"unknown company {ref!r}; create it first with upsert_company")


# ---------------------------------------------------------------- edges

def add_edge(conn, customer_id, supplier_id, revenue_share=None, lag_days=None,
             confidence="medium", source=None, notes=None, created_by="user") -> dict:
    _check(confidence, db.CONFIDENCES, "confidence")
    if customer_id == supplier_id:
        raise ValueError("a company cannot be its own customer")
    if revenue_share is not None and not (0 <= revenue_share <= 1):
        raise ValueError("revenue_share must be 0..1 (fraction of supplier revenue)")
    for cid in (customer_id, supplier_id):
        if not get_company(conn, cid):
            raise ValueError(f"unknown company {cid}")
    conn.execute(
        """INSERT INTO edges (customer_id, supplier_id, revenue_share, lag_days, confidence, source, notes, created_by)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(customer_id, supplier_id) DO UPDATE SET
             revenue_share=COALESCE(excluded.revenue_share, edges.revenue_share),
             lag_days=COALESCE(excluded.lag_days, edges.lag_days),
             confidence=excluded.confidence,
             source=COALESCE(excluded.source, edges.source),
             notes=COALESCE(excluded.notes, edges.notes)""",
        (customer_id, supplier_id, revenue_share, lag_days, confidence, source, notes, created_by),
    )
    conn.commit()
    return _row(conn.execute("SELECT * FROM edges WHERE customer_id=? AND supplier_id=?",
                             (customer_id, supplier_id)))


def delete_edge(conn, edge_id: int) -> None:
    conn.execute("DELETE FROM edges WHERE id=?", (edge_id,))
    conn.commit()


def relations(conn, cid: str) -> dict:
    """Customers (who buy from cid) and suppliers (whom cid buys from)."""
    customers = _rows(conn.execute(
        """SELECT e.*, c.name AS customer_name, c.market AS customer_market, p.stage AS customer_stage
           FROM edges e JOIN companies c ON c.id=e.customer_id
           LEFT JOIN positions p ON p.company_id=c.id
           WHERE e.supplier_id=? ORDER BY e.revenue_share DESC NULLS LAST""", (cid,)))
    suppliers = _rows(conn.execute(
        """SELECT e.*, c.name AS supplier_name, c.market AS supplier_market, p.stage AS supplier_stage
           FROM edges e JOIN companies c ON c.id=e.supplier_id
           LEFT JOIN positions p ON p.company_id=c.id
           WHERE e.customer_id=? ORDER BY e.revenue_share DESC NULLS LAST""", (cid,)))
    return {"company": get_company(conn, cid), "customers": customers, "suppliers": suppliers}


def customer_concentration(conn, cid: str) -> dict:
    """How dependent is cid on its largest customers? CRM-style concentration risk."""
    rel = relations(conn, cid)["customers"]
    known = [e for e in rel if e["revenue_share"] is not None]
    top = known[0] if known else None
    total_known = sum(e["revenue_share"] for e in known)
    flag = "low"
    if top and top["revenue_share"] >= 0.4:
        flag = "high"
    elif top and top["revenue_share"] >= 0.2:
        flag = "medium"
    return {
        "company_id": cid,
        "largest_customer": top["customer_id"] if top else None,
        "largest_customer_name": top["customer_name"] if top else None,
        "largest_customer_share": top["revenue_share"] if top else None,
        "top3_share": round(sum(e["revenue_share"] for e in known[:3]), 3),
        "known_share_total": round(total_known, 3),
        "flag": flag,
    }


# ---------------------------------------------------------------- positions

def list_positions(conn, stage: str | None = None, tier: str | None = None) -> list[dict]:
    sql = """SELECT p.*, c.name, c.market, c.ticker, c.sector,
                    (SELECT MAX(ts) FROM interactions i WHERE i.position_id=p.id) AS last_interaction,
                    (SELECT COUNT(*) FROM interactions i WHERE i.position_id=p.id) AS interaction_count
             FROM positions p JOIN companies c ON c.id=p.company_id WHERE 1=1"""
    args = []
    if stage:
        _check(stage, db.STAGES, "stage")
        sql += " AND p.stage=?"
        args.append(stage)
    if tier:
        _check(tier, db.TIERS, "tier")
        sql += " AND p.tier=?"
        args.append(tier)
    sql += (" ORDER BY CASE p.stage WHEN 'hold' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END, "
            "p.position_pct DESC, c.ticker")
    rows = [_decode(r) for r in _rows(conn.execute(sql, args))]
    for r in rows:
        r["days_since_update"] = _days_since(r["last_interaction"] or r["updated_at"])
    return rows


def _days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).days


def get_position(conn, ref: str | int) -> dict | None:
    if isinstance(ref, int) or str(ref).isdigit() and len(str(ref)) < 4:
        row = _row(conn.execute("SELECT * FROM positions WHERE id=?", (int(ref),)))
    else:
        c = resolve_company(conn, str(ref))
        row = _row(conn.execute("SELECT * FROM positions WHERE company_id=?", (c["id"],)))
    if not row:
        return None
    row = _decode(row)
    row["company"] = get_company(conn, row["company_id"])
    row["interactions"] = [_decode(r) for r in _rows(conn.execute(
        "SELECT * FROM interactions WHERE position_id=? ORDER BY ts DESC", (row["id"],)))]
    row["reminders"] = _rows(conn.execute(
        "SELECT * FROM reminders WHERE position_id=? AND done=0 ORDER BY due_date", (row["id"],)))
    rel = relations(conn, row["company_id"])
    row["customers"], row["suppliers"] = rel["customers"], rel["suppliers"]
    row["concentration"] = customer_concentration(conn, row["company_id"])
    row["tier_rule"] = _row(conn.execute("SELECT * FROM tier_rules WHERE tier=?", (row["tier"],)))
    last = row["interactions"][0]["ts"] if row["interactions"] else row["updated_at"]
    row["days_since_update"] = _days_since(last)
    return row


def upsert_position(conn, company_id: str, stage: str, tier: str, thesis=None, invalidation=None,
                    target_price=None, stop_price=None, position_pct=None, idea_source=None, tags=None,
                    thesis_status=None) -> dict:
    _check(stage, db.STAGES, "stage")
    _check(tier, db.TIERS, "tier")
    if thesis_status is not None:
        _check(thesis_status, db.THESIS_STATUSES, "thesis_status")
    if not get_company(conn, company_id):
        raise ValueError(f"unknown company {company_id}")
    existing = _row(conn.execute("SELECT * FROM positions WHERE company_id=?", (company_id,)))
    if existing:
        conn.execute(
            """UPDATE positions SET stage=?, tier=?, thesis=COALESCE(?,thesis),
               invalidation=COALESCE(?,invalidation),
               target_price=COALESCE(?,target_price), stop_price=COALESCE(?,stop_price),
               position_pct=COALESCE(?,position_pct), idea_source=COALESCE(?,idea_source),
               tags=COALESCE(?,tags), thesis_status=COALESCE(?,thesis_status), updated_at=? WHERE id=?""",
            (stage, tier, thesis, invalidation, target_price, stop_price, position_pct, idea_source,
             _json_list(tags) if tags is not None else None, thesis_status, _now(), existing["id"]))
    else:
        conn.execute(
            """INSERT INTO positions (company_id, stage, tier, thesis, invalidation, target_price, stop_price,
               position_pct, idea_source, tags, thesis_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, stage, tier, thesis, invalidation, target_price, stop_price, position_pct or 0,
             idea_source, _json_list(tags), thesis_status or "intact"))
    conn.commit()
    return get_position(conn, company_id)


def set_thesis_status(conn, position_id: int, status: str) -> dict:
    _check(status, db.THESIS_STATUSES, "thesis_status")
    conn.execute("UPDATE positions SET thesis_status=?, updated_at=? WHERE id=?",
                 (status, _now(), position_id))
    conn.commit()
    return get_position(conn, position_id)


def check_tier_limit(conn, position_id: int, new_pct: float) -> dict:
    p = _row(conn.execute(
        "SELECT p.*, t.max_pct, t.label FROM positions p JOIN tier_rules t ON t.tier=p.tier WHERE p.id=?",
        (position_id,)))
    if not p:
        raise ValueError("unknown position")
    over = new_pct > p["max_pct"]
    return {"position_id": position_id, "tier": p["tier"], "tier_label": p["label"], "max_pct": p["max_pct"],
            "current_pct": p["position_pct"], "new_pct": new_pct, "exceeds_limit": over,
            "message": f"{p['label']}層上限 {p['max_pct']}%，調整後 {new_pct}%"
                       + ("，超過上限" if over else "，在範圍內")}


# ---------------------------------------------------------------- interactions

def add_interaction(conn, position_id: int, kind: str, content: str, price=None, behavior_tags=None,
                    source_url=None, created_by="user", ts=None) -> dict:
    _check(kind, db.INTERACTION_KINDS, "kind")
    if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (position_id,))):
        raise ValueError("unknown position")
    cur = conn.execute(
        """INSERT INTO interactions (position_id, ts, kind, content, price, behavior_tags, source_url, created_by)
           VALUES (?,?,?,?,?,?,?,?)""",
        (position_id, ts or _now(), kind, content, price, _json_list(behavior_tags), source_url, created_by))
    conn.execute("UPDATE positions SET updated_at=? WHERE id=?", (_now(), position_id))
    conn.commit()
    return _decode(_row(conn.execute("SELECT * FROM interactions WHERE id=?", (cur.lastrowid,))))


def stale_positions(conn, days: int = 30) -> list[dict]:
    return [p for p in list_positions(conn, stage="hold") if (p["days_since_update"] or 0) >= days]


# ---------------------------------------------------------------- reminders

def add_reminder(conn, due_date: str, kind: str, content: str, position_id=None, created_by="user") -> dict:
    _check(kind, db.REMINDER_KINDS, "kind")
    datetime.strptime(due_date, "%Y-%m-%d")
    cur = conn.execute(
        "INSERT INTO reminders (position_id, due_date, kind, content, created_by) VALUES (?,?,?,?,?)",
        (position_id, due_date, kind, content, created_by))
    conn.commit()
    return _row(conn.execute("SELECT * FROM reminders WHERE id=?", (cur.lastrowid,)))


def due_reminders(conn, within_days: int = 7) -> list[dict]:
    limit = (datetime.now(timezone.utc) + timedelta(days=within_days)).strftime("%Y-%m-%d")
    return _rows(conn.execute(
        """SELECT r.*, c.name AS company_name, c.id AS company_id FROM reminders r
           LEFT JOIN positions p ON p.id=r.position_id LEFT JOIN companies c ON c.id=p.company_id
           WHERE r.done=0 AND r.due_date<=? ORDER BY r.due_date""", (limit,)))


def all_reminders(conn) -> list[dict]:
    return _rows(conn.execute(
        """SELECT r.*, c.name AS company_name, c.id AS company_id FROM reminders r
           LEFT JOIN positions p ON p.id=r.position_id LEFT JOIN companies c ON c.id=p.company_id
           WHERE r.done=0 ORDER BY r.due_date"""))


def complete_reminder(conn, reminder_id: int) -> None:
    conn.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()


# ---------------------------------------------------------------- exposure & graph

def indirect_exposure(conn, cid: str, max_depth: int = 2) -> dict:
    """How much of my portfolio depends on company cid as a CUSTOMER?
    Walk customer -> supplier edges from cid; for each held position reached, exposure =
    position_pct * product(revenue_share along path). Unknown shares count as 0 but are reported.
    """
    held = {p["company_id"]: p for p in list_positions(conn, stage="hold")}
    results: dict[str, dict] = {}
    unknown: list[dict] = []
    queue = deque([(cid, 1.0, [cid], 0)])
    seen_paths = set()
    while queue:
        node, weight, path, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for e in _rows(conn.execute("SELECT * FROM edges WHERE customer_id=?", (node,))):
            sup = e["supplier_id"]
            if sup in path:
                continue
            key = tuple(path + [sup])
            if key in seen_paths:
                continue
            seen_paths.add(key)
            share = e["revenue_share"]
            if share is None:
                unknown.append({"path": list(key), "edge_id": e["id"]})
                share = 0.0
            w = weight * share
            if sup in held:
                p = held[sup]
                r = results.setdefault(sup, {"company_id": sup, "name": p["name"],
                                             "position_pct": p["position_pct"],
                                             "exposure_pct": 0.0, "paths": []})
                r["exposure_pct"] += p["position_pct"] * w
                r["paths"].append({"path": list(key), "weight": round(w, 4)})
            queue.append((sup, w, list(key), depth + 1))
    out = sorted(results.values(), key=lambda r: -r["exposure_pct"])
    for r in out:
        r["exposure_pct"] = round(r["exposure_pct"], 2)
    direct_hold = held.get(cid)
    return {"company": get_company(conn, cid),
            "directly_held_pct": direct_hold["position_pct"] if direct_hold else 0,
            "indirect_total_pct": round(sum(r["exposure_pct"] for r in out), 2),
            "exposures": out, "unknown_shares": unknown}


def portfolio_overlap(conn) -> list[dict]:
    """For every customer company in the graph, total indirect exposure of the held portfolio.
    Reveals 'five positions that are really one NVDA bet'."""
    customers = _rows(conn.execute("SELECT DISTINCT customer_id FROM edges"))
    out = []
    for c in customers:
        ex = indirect_exposure(conn, c["customer_id"])
        if ex["exposures"]:
            out.append({"customer_id": c["customer_id"], "customer_name": ex["company"]["name"],
                        "directly_held_pct": ex["directly_held_pct"],
                        "indirect_total_pct": ex["indirect_total_pct"],
                        "combined_pct": round(ex["directly_held_pct"] + ex["indirect_total_pct"], 2),
                        "via": [{"company_id": e["company_id"], "name": e["name"],
                                 "exposure_pct": e["exposure_pct"]} for e in ex["exposures"]]})
    return sorted(out, key=lambda r: -r["combined_pct"])


def export_graph(conn) -> dict:
    nodes = _rows(conn.execute(
        """SELECT c.*, p.id AS position_id, p.stage, p.tier, p.position_pct, p.thesis_status FROM companies c
           LEFT JOIN positions p ON p.company_id=c.id ORDER BY c.market, c.ticker"""))
    edges = _rows(conn.execute("SELECT * FROM edges"))
    return {"nodes": nodes, "edges": edges, "tier_rules": _rows(conn.execute("SELECT * FROM tier_rules"))}


def graph_markdown(conn) -> str:
    g = export_graph(conn)
    name = {n["id"]: n["name"] for n in g["nodes"]}
    lines = ["# 供應鏈關係圖", "", "## 節點（公司）", "",
             "| id | 名稱 | 市場 | 階段 | 分層 | 部位% |", "|---|---|---|---|---|---|"]
    for n in g["nodes"]:
        pct = n['position_pct'] if n['position_pct'] is not None else '-'
        lines.append(f"| {n['id']} | {n['name']} | {n['market']} | {n['stage'] or '-'} | {n['tier'] or '-'} | {pct} |")
    lines += ["", "## 邊（客戶 → 供應商，revenue_share = 客戶佔供應商營收比重）", "",
              "| 客戶 | 供應商 | 比重 | 時間差(天) | 信心 | 來源 |", "|---|---|---|---|---|---|"]
    for e in g["edges"]:
        share = f"{e['revenue_share']:.0%}" if e["revenue_share"] is not None else "?"
        lines.append(f"| {name[e['customer_id']]} ({e['customer_id']}) | {name[e['supplier_id']]} ({e['supplier_id']}) "
                     f"| {share} | {e['lag_days'] or '?'} | {e['confidence']} | {e['source'] or ''} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- morning context

def morning_context(conn) -> dict:
    """Everything an agent needs to write the morning brief. No live data here: the agent
    fetches news for the listed counterparties itself, then proposes interactions."""
    held = list_positions(conn, stage="hold")
    watch = list_positions(conn, stage="watch")
    counterparties: dict[str, dict] = {}
    for p in held:
        rel = relations(conn, p["company_id"])
        for e in rel["customers"]:
            cp = counterparties.setdefault(e["customer_id"], {
                "company_id": e["customer_id"], "name": e["customer_name"],
                "market": e["customer_market"], "role_to_holdings": []})
            cp["role_to_holdings"].append({"holding": p["company_id"], "holding_name": p["name"],
                                           "role": "customer", "revenue_share": e["revenue_share"],
                                           "lag_days": e["lag_days"]})
        for e in rel["suppliers"]:
            cp = counterparties.setdefault(e["supplier_id"], {
                "company_id": e["supplier_id"], "name": e["supplier_name"],
                "market": e["supplier_market"], "role_to_holdings": []})
            cp["role_to_holdings"].append({"holding": p["company_id"], "holding_name": p["name"],
                                           "role": "supplier", "revenue_share": e["revenue_share"],
                                           "lag_days": e["lag_days"]})
    today = datetime.now(timezone.utc)
    keys = ("company_id", "name", "market", "tier", "position_pct", "thesis_status",
            "invalidation", "target_price", "stop_price", "days_since_update")
    return {
        "generated_at": _now(),
        "holdings": [{k: p[k] for k in keys} for p in held],
        "watchlist": [{k: p[k] for k in ("company_id", "name", "market", "thesis", "target_price")} for p in watch],
        "counterparties_to_check": sorted(counterparties.values(), key=lambda c: (c["market"], c["company_id"])),
        "shaken_or_broken": [{"company_id": p["company_id"], "name": p["name"],
                              "thesis_status": p["thesis_status"], "invalidation": p["invalidation"]}
                             for p in held if p["thesis_status"] != "intact"],
        "stale_holdings": [{"company_id": p["company_id"], "name": p["name"], "days": p["days_since_update"]}
                           for p in stale_positions(conn, 30)],
        "due_reminders": due_reminders(conn, 7),
        "pending_proposals": len(list_proposals(conn, "pending")),
        "tw_monthly_revenue_window": today.day <= 10,
        "overlap": portfolio_overlap(conn)[:5],
    }


# ---------------------------------------------------------------- proposals

def create_proposal(conn, kind: str, payload: dict, rationale: str, created_by: str) -> dict:
    _check(kind, db.PROPOSAL_KINDS, "kind")
    _validate_payload(conn, kind, payload)
    cur = conn.execute("INSERT INTO proposals (kind, payload, rationale, created_by) VALUES (?,?,?,?)",
                       (kind, json.dumps(payload, ensure_ascii=False), rationale, created_by))
    conn.commit()
    return _decode(_row(conn.execute("SELECT * FROM proposals WHERE id=?", (cur.lastrowid,))))


def _validate_payload(conn, kind, p: dict):
    if kind == "interaction":
        for k in ("position_id", "kind", "content"):
            if k not in p:
                raise ValueError(f"interaction payload needs {k}")
        _check(p["kind"], db.INTERACTION_KINDS, "payload.kind")
        if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (p["position_id"],))):
            raise ValueError("payload.position_id unknown")
    elif kind == "edge":
        for k in ("customer_id", "supplier_id"):
            if k not in p:
                raise ValueError(f"edge payload needs {k}")
            if not get_company(conn, p[k]):
                raise ValueError(f"unknown company {p[k]}")
        _check(p.get("confidence", "medium"), db.CONFIDENCES, "payload.confidence")
    elif kind == "thesis_status":
        for k in ("position_id", "status"):
            if k not in p:
                raise ValueError(f"thesis_status payload needs {k}")
        _check(p["status"], db.THESIS_STATUSES, "payload.status")
    elif kind == "reminder":
        for k in ("due_date", "kind", "content"):
            if k not in p:
                raise ValueError(f"reminder payload needs {k}")
        _check(p["kind"], db.REMINDER_KINDS, "payload.kind")


def list_proposals(conn, status: str | None = "pending") -> list[dict]:
    sql = "SELECT * FROM proposals"
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC"
    out = [_decode(r) for r in _rows(conn.execute(sql, args))]
    for pr in out:
        pr["summary"] = _proposal_summary(conn, pr)
    return out


def _pos_name(conn, position_id) -> str:
    r = _row(conn.execute(
        "SELECT c.name FROM positions x JOIN companies c ON c.id=x.company_id WHERE x.id=?", (position_id,)))
    return r["name"] if r else "?"


def _proposal_summary(conn, pr: dict) -> str:
    p = pr["payload"]
    if pr["kind"] == "interaction":
        return f"[{p['kind']}] {_pos_name(conn, p['position_id'])}：{p['content'][:60]}"
    if pr["kind"] == "edge":
        return f"新增邊：{p['customer_id']} → {p['supplier_id']}，比重 {p.get('revenue_share', '?')}"
    if pr["kind"] == "thesis_status":
        return f"論點狀態：{_pos_name(conn, p['position_id'])} → {p['status']}"
    if pr["kind"] == "reminder":
        return f"提醒 {p['due_date']}：{p['content'][:60]}"
    return pr["kind"]


def decide_proposal(conn, proposal_id: int, approve: bool) -> dict:
    raw = _row(conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)))
    if not raw:
        raise ValueError("unknown proposal")
    pr = _decode(raw)
    if pr["status"] != "pending":
        raise ValueError("proposal already decided")
    ref = None
    if approve:
        ref = apply_proposal(conn, pr)
    conn.execute("UPDATE proposals SET status=?, decided_at=?, applied_ref=? WHERE id=?",
                 ("approved" if approve else "rejected", _now(), ref, proposal_id))
    conn.commit()
    return _decode(_row(conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,))))


def apply_proposal(conn, pr: dict) -> str:
    p, by = pr["payload"], pr["created_by"]
    if pr["kind"] == "interaction":
        r = add_interaction(conn, p["position_id"], p["kind"], p["content"], p.get("price"),
                            p.get("behavior_tags"), p.get("source_url"), created_by=by, ts=p.get("ts"))
        return f"interaction:{r['id']}"
    if pr["kind"] == "edge":
        r = add_edge(conn, p["customer_id"], p["supplier_id"], p.get("revenue_share"), p.get("lag_days"),
                     p.get("confidence", "medium"), p.get("source"), p.get("notes"), created_by=by)
        return f"edge:{r['id']}"
    if pr["kind"] == "thesis_status":
        set_thesis_status(conn, p["position_id"], p["status"])
        if p.get("note"):
            add_interaction(conn, p["position_id"], "review", p["note"], created_by=by)
        return f"position:{p['position_id']}"
    if pr["kind"] == "reminder":
        r = add_reminder(conn, p["due_date"], p["kind"], p["content"], p.get("position_id"), created_by=by)
        return f"reminder:{r['id']}"
    raise ValueError(pr["kind"])
