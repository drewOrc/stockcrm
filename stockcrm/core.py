"""Business logic. Every function takes a sqlite3.Connection and returns plain dicts/lists
so the same code serves the REST API, the MCP server and scripts.

Write policy (enforced by callers, documented here):
  - Direct writes: things the USER asked to record (trades, thoughts, watchlist adds, reminders).
  - Proposals:     things an AGENT inferred from external information (news-derived interactions,
                   new supply-chain edges, thesis status changes). The user approves in the UI.
"""
from __future__ import annotations

import json
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
             confidence="medium", source=None, notes=None, created_by="user", relation="customer") -> dict:
    _check(confidence, db.CONFIDENCES, "confidence")
    _check(relation, db.RELATIONS, "relation")
    if customer_id == supplier_id:
        raise ValueError("a company cannot be its own customer")
    if revenue_share is not None and not (0 <= revenue_share <= 1):
        raise ValueError("revenue_share must be 0..1 (fraction of supplier revenue)")
    for cid in (customer_id, supplier_id):
        if not get_company(conn, cid):
            raise ValueError(f"unknown company {cid}")
    conn.execute(
        """INSERT INTO edges (customer_id, supplier_id, revenue_share, lag_days, confidence, source, notes, created_by, relation)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(customer_id, supplier_id) DO UPDATE SET
             revenue_share=COALESCE(excluded.revenue_share, edges.revenue_share),
             lag_days=COALESCE(excluded.lag_days, edges.lag_days),
             confidence=excluded.confidence,
             relation=excluded.relation,
             source=COALESCE(excluded.source, edges.source),
             notes=COALESCE(excluded.notes, edges.notes)""",
        (customer_id, supplier_id, revenue_share, lag_days, confidence, source, notes, created_by, relation),
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
        r["health_issues"] = len(thesis_health(conn, r["id"])["issues"]) if r["stage"] != "exited" else 0
        r.update(brief_summary(conn, r["company_id"]))
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
    row["drivers"] = list_drivers(conn, row["id"])
    row["checkpoints"] = list_checkpoints(conn, row["id"])
    row["metrics"] = metric_comparison(conn, row["company_id"])
    row["health"] = thesis_health(conn, row["id"], _pos=row)
    row["briefs"] = briefs_for_card(conn, row["company_id"])
    return row


def upsert_position(conn, company_id: str, stage: str, tier: str, thesis=None, invalidation=None,
                    target_price=None, stop_price=None, position_pct=None, idea_source=None, tags=None,
                    thesis_status=None, target_basis=None, model_url=None, shares=None, avg_cost=None) -> dict:
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
               tags=COALESCE(?,tags), thesis_status=COALESCE(?,thesis_status),
               target_basis=COALESCE(?,target_basis), model_url=COALESCE(?,model_url),
               shares=COALESCE(?,shares), avg_cost=COALESCE(?,avg_cost), updated_at=? WHERE id=?""",
            (stage, tier, thesis, invalidation, target_price, stop_price, position_pct, idea_source,
             _json_list(tags) if tags is not None else None, thesis_status, target_basis, model_url,
             shares, avg_cost, _now(), existing["id"]))
    else:
        conn.execute(
            """INSERT INTO positions (company_id, stage, tier, thesis, invalidation, target_price, stop_price,
               position_pct, idea_source, tags, thesis_status, target_basis, model_url, shares, avg_cost)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, stage, tier, thesis, invalidation, target_price, stop_price, position_pct or 0,
             idea_source, _json_list(tags), thesis_status or "intact", target_basis, model_url, shares, avg_cost))
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
                    source_url=None, created_by="user", ts=None, driver_id=None, checkpoint_id=None,
                    effect=None) -> dict:
    """Log an interaction. Optionally link it to a driver or checkpoint with an effect
    (supports / weakens / confirms). effect=confirms on a checkpoint marks it confirmed."""
    _check(kind, db.INTERACTION_KINDS, "kind")
    if effect is not None:
        _check(effect, db.EFFECTS, "effect")
    if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (position_id,))):
        raise ValueError("unknown position")
    if driver_id and not _row(conn.execute("SELECT id FROM drivers WHERE id=? AND position_id=?",
                                           (driver_id, position_id))):
        raise ValueError("driver_id does not belong to this position")
    if checkpoint_id and not _row(conn.execute("SELECT id FROM checkpoints WHERE id=? AND position_id=?",
                                               (checkpoint_id, position_id))):
        raise ValueError("checkpoint_id does not belong to this position")
    cur = conn.execute(
        """INSERT INTO interactions (position_id, ts, kind, content, price, behavior_tags, source_url, created_by,
                                     driver_id, checkpoint_id, effect)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, ts or _now(), kind, content, price, _json_list(behavior_tags), source_url, created_by,
         driver_id, checkpoint_id, effect))
    conn.execute("UPDATE positions SET updated_at=? WHERE id=?", (_now(), position_id))
    if checkpoint_id and effect == "confirms":
        conn.execute("UPDATE checkpoints SET status='confirmed', evidence=?, interaction_id=?, decided_at=? "
                     "WHERE id=? AND status='pending'", (content, cur.lastrowid, _now(), checkpoint_id))
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
    attention = []
    for p in held + watch:
        h = thesis_health(conn, p["id"])
        if h["issues"]:
            attention.append({"company_id": p["company_id"], "name": p["name"], "position_id": p["id"],
                              "thesis_status": p["thesis_status"], "suggest_shaken": h["suggest_shaken"],
                              "issues": h["issues"]})
    soon = (today + timedelta(days=14)).strftime("%Y-%m-%d")
    upcoming = _rows(conn.execute(
        """SELECT k.*, c.name AS company_name, c.id AS company_id FROM checkpoints k
           JOIN positions p ON p.id=k.position_id JOIN companies c ON c.id=p.company_id
           WHERE k.status='pending' AND k.expected_by>=? AND k.expected_by<=? ORDER BY k.expected_by""",
        (_today(), soon)))
    return {
        "generated_at": _now(),
        "attention": attention,
        "upcoming_checkpoints": upcoming,
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


def _need(p: dict, *keys):
    for k in keys:
        if k not in p or p[k] is None:
            raise ValueError(f"payload needs {k}")


def _validate_payload(conn, kind, p: dict):
    if kind == "interaction":
        _need(p, "position_id", "kind", "content")
        _check(p["kind"], db.INTERACTION_KINDS, "payload.kind")
        if p.get("effect") is not None:
            _check(p["effect"], db.EFFECTS, "payload.effect")
        if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (p["position_id"],))):
            raise ValueError("payload.position_id unknown")
    elif kind == "driver":
        _need(p, "position_id", "name")
        if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (p["position_id"],))):
            raise ValueError("payload.position_id unknown")
    elif kind == "driver_value":
        _need(p, "driver_id", "period", "kind", "value")
        _check(p["kind"], db.VALUE_KINDS, "payload.kind")
        _parse_period(p["period"])
        if not _row(conn.execute("SELECT id FROM drivers WHERE id=?", (p["driver_id"],))):
            raise ValueError("payload.driver_id unknown")
    elif kind == "checkpoint":
        _need(p, "position_id", "title", "expected_by")
        datetime.strptime(p["expected_by"], "%Y-%m-%d")
        if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (p["position_id"],))):
            raise ValueError("payload.position_id unknown")
    elif kind == "checkpoint_resolve":
        _need(p, "checkpoint_id", "status")
        if p["status"] not in ("confirmed", "missed"):
            raise ValueError("payload.status must be confirmed or missed")
        if not _row(conn.execute("SELECT id FROM checkpoints WHERE id=?", (p["checkpoint_id"],))):
            raise ValueError("payload.checkpoint_id unknown")
    elif kind == "brief":
        _need(p, "company_id", "kind", "content")
        _check(p["kind"], db.BRIEF_KINDS, "payload.kind")
        if not get_company(conn, p["company_id"]):
            raise ValueError("payload.company_id unknown")
        if p.get("date"):
            datetime.strptime(p["date"], "%Y-%m-%d")
    elif kind == "metric":
        _need(p, "company_id", "metric", "period", "kind", "value")
        _check(p["metric"], db.METRICS, "payload.metric")
        _check(p["kind"], db.VALUE_KINDS, "payload.kind")
        _parse_period(p["period"])
        if not get_company(conn, p["company_id"]):
            raise ValueError("payload.company_id unknown")
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
    if pr["kind"] == "driver":
        return f"新增驅動因子：{_pos_name(conn, p['position_id'])}／{p['name']}"
    if pr["kind"] == "driver_value":
        d = _row(conn.execute("SELECT d.name, c.name AS cname FROM drivers d JOIN positions x ON x.id=d.position_id "
                              "JOIN companies c ON c.id=x.company_id WHERE d.id=?", (p["driver_id"],)))
        who = f"{d['cname']}／{d['name']}" if d else "?"
        return f"{'實際' if p['kind'] == 'actual' else '模型'}值：{who} {p['period']} = {p['value']}"
    if pr["kind"] == "checkpoint":
        return f"新增檢查點：{_pos_name(conn, p['position_id'])}／{p['title']}（{p['expected_by']} 前）"
    if pr["kind"] == "checkpoint_resolve":
        c = _row(conn.execute("SELECT title FROM checkpoints WHERE id=?", (p["checkpoint_id"],)))
        return f"檢查點{'確認' if p['status'] == 'confirmed' else '逾期未達'}：{c['title'] if c else '?'}"
    if pr["kind"] == "metric":
        kind = "實際" if p["kind"] == "actual" else "模型"
        return f"{db.METRIC_LABELS[p['metric']]} {p['period']} {kind} = {p['value']}（{p['company_id']}）"
    if pr["kind"] == "brief":
        c = get_company(conn, p["company_id"])
        label = "公司簡介" if p["kind"] == "profile" else "近期重點"
        return f"{label}：{c['name'] if c else p['company_id']}：{_first_sentence(p['content'], 60)}"
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
                            p.get("behavior_tags"), p.get("source_url"), created_by=by, ts=p.get("ts"),
                            driver_id=p.get("driver_id"), checkpoint_id=p.get("checkpoint_id"),
                            effect=p.get("effect"))
        return f"interaction:{r['id']}"
    if pr["kind"] == "edge":
        r = add_edge(conn, p["customer_id"], p["supplier_id"], p.get("revenue_share"), p.get("lag_days"),
                     p.get("confidence", "medium"), p.get("source"), p.get("notes"), created_by=by,
                     relation=p.get("relation", "customer"))
        return f"edge:{r['id']}"
    if pr["kind"] == "driver":
        r = add_driver(conn, p["position_id"], p["name"], p.get("unit"), p.get("critical", 1),
                       p.get("sensitivity"), p.get("notes"), p.get("higher_is_better", 1), created_by=by)
        return f"driver:{r['id']}"
    if pr["kind"] == "driver_value":
        r = set_driver_value(conn, p["driver_id"], p["period"], p["kind"], p["value"], p.get("source"), created_by=by)
        return f"driver_value:{r['id']}"
    if pr["kind"] == "checkpoint":
        r = add_checkpoint(conn, p["position_id"], p["title"], p["expected_by"], p.get("evidence_needed"),
                           p.get("driver_id"), created_by=by)
        return f"checkpoint:{r['id']}"
    if pr["kind"] == "checkpoint_resolve":
        resolve_checkpoint(conn, p["checkpoint_id"], p["status"], p.get("evidence"))
        return f"checkpoint:{p['checkpoint_id']}"
    if pr["kind"] == "metric":
        r = set_metric(conn, p["company_id"], p["metric"], p["period"], p["kind"], p["value"], p.get("source"), created_by=by)
        return f"metric:{r['id']}"
    if pr["kind"] == "brief":
        r = add_brief(conn, p["company_id"], p["kind"], p["content"], p.get("sources"), p.get("date"), created_by=by)
        return f"brief:{r['id']}"
    if pr["kind"] == "thesis_status":
        set_thesis_status(conn, p["position_id"], p["status"])
        if p.get("note"):
            add_interaction(conn, p["position_id"], "review", p["note"], created_by=by)
        return f"position:{p['position_id']}"
    if pr["kind"] == "reminder":
        r = add_reminder(conn, p["due_date"], p["kind"], p["content"], p.get("position_id"), created_by=by)
        return f"reminder:{r['id']}"
    raise ValueError(pr["kind"])


# ================================================================ generic thesis containers
# Three containers that fit any company: drivers (quantitative assumptions, model vs actual),
# checkpoints (dated expected evidence), metrics (standard financials, model vs actual).

RUNRATE_TOL = 0.15     # projected quarter revenue below model by more than this -> behind
DRIVER_TOL = 0.15      # actual vs model gap on a critical driver
MARGIN_TOL = 0.03      # gross margin, absolute points (0.03 = 3pp)
EPS_TOL = 0.15
SHARES_TOL = 0.02      # share count up by more than this -> dilution


def _parse_period(period: str) -> tuple[str, int, int]:
    """Return (granularity, year, index). '2026' -> ('year', 2026, 0); '2026Q2' -> ('quarter', 2026, 2);
    '2026-05' -> ('month', 2026, 5)."""
    p = period.strip().upper()
    if len(p) == 4 and p.isdigit():
        return "year", int(p), 0
    if len(p) == 6 and p[4] == "Q" and p[5] in "1234":
        return "quarter", int(p[:4]), int(p[5])
    if len(p) == 7 and p[4] == "-" and p[5:].isdigit() and 1 <= int(p[5:]) <= 12:
        return "month", int(p[:4]), int(p[5:])
    raise ValueError(f"bad period {period!r}; use 2026, 2026Q2 or 2026-05")


def _quarter_months(year: int, q: int) -> list[str]:
    return [f"{year}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)]


def _period_sort_key(period: str):
    g, y, i = _parse_period(period)
    return (y, {"year": 0, "quarter": 1, "month": 2}[g], i)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- drivers

def add_driver(conn, position_id: int, name: str, unit=None, critical=1, sensitivity=None, notes=None,
               higher_is_better=1, created_by="user") -> dict:
    if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (position_id,))):
        raise ValueError("unknown position")
    cur = conn.execute(
        """INSERT INTO drivers (position_id, name, unit, critical, higher_is_better, sensitivity, notes, created_by)
           VALUES (?,?,?,?,?,?,?,?)""",
        (position_id, name, unit, 1 if critical else 0, 1 if higher_is_better else 0, sensitivity, notes, created_by))
    conn.commit()
    return get_driver(conn, cur.lastrowid)


def update_driver(conn, driver_id: int, **fields) -> dict:
    allowed = {"name", "unit", "critical", "higher_is_better", "sensitivity", "notes"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if sets:
        assignments = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE drivers SET {assignments} WHERE id=?", (*sets.values(), driver_id))
        conn.commit()
    return get_driver(conn, driver_id)


def delete_driver(conn, driver_id: int) -> None:
    conn.execute("DELETE FROM drivers WHERE id=?", (driver_id,))
    conn.commit()


def set_driver_value(conn, driver_id: int, period: str, kind: str, value: float, source=None,
                     created_by="user") -> dict:
    _check(kind, db.VALUE_KINDS, "kind")
    _parse_period(period)
    if not _row(conn.execute("SELECT id FROM drivers WHERE id=?", (driver_id,))):
        raise ValueError("unknown driver")
    conn.execute(
        """INSERT INTO driver_values (driver_id, period, kind, value, source, created_by) VALUES (?,?,?,?,?,?)
           ON CONFLICT(driver_id, period, kind) DO UPDATE SET value=excluded.value,
             source=COALESCE(excluded.source, driver_values.source), created_by=excluded.created_by""",
        (driver_id, period.upper(), kind, float(value), source, created_by))
    conn.commit()
    return _row(conn.execute("SELECT * FROM driver_values WHERE driver_id=? AND period=? AND kind=?",
                             (driver_id, period.upper(), kind)))


def get_driver(conn, driver_id: int) -> dict | None:
    d = _row(conn.execute("SELECT * FROM drivers WHERE id=?", (driver_id,)))
    if not d:
        return None
    vals = _rows(conn.execute("SELECT * FROM driver_values WHERE driver_id=?", (driver_id,)))
    by_period: dict[str, dict] = {}
    for v in vals:
        by_period.setdefault(v["period"], {"period": v["period"], "model": None, "actual": None, "source": None})
        by_period[v["period"]][v["kind"]] = v["value"]
        if v["kind"] == "actual":
            by_period[v["period"]]["source"] = v["source"]
    series = sorted(by_period.values(), key=lambda r: _period_sort_key(r["period"]))
    for r in series:
        if r["model"] and r["actual"] is not None:
            r["gap_pct"] = round((r["actual"] - r["model"]) / abs(r["model"]), 4)
        else:
            r["gap_pct"] = None
    evidence = _rows(conn.execute(
        "SELECT id, ts, kind, effect, substr(content,1,120) AS content FROM interactions "
        "WHERE driver_id=? ORDER BY ts DESC LIMIT 10", (driver_id,)))
    latest_effect = next((e["effect"] for e in evidence if e["effect"]), None)
    compared = [r for r in series if r["gap_pct"] is not None]
    latest_cmp = compared[-1] if compared else None
    signal = "no_data"
    if latest_cmp:
        gap = latest_cmp["gap_pct"] if d["higher_is_better"] else -latest_cmp["gap_pct"]
        signal = "behind" if gap < -DRIVER_TOL else ("ahead" if gap > DRIVER_TOL else "on_track")
    if latest_effect == "weakens":
        signal = "weakened"
    d.update({"series": series, "evidence": evidence, "latest_effect": latest_effect,
              "latest_comparison": latest_cmp, "signal": signal})
    return d


def list_drivers(conn, position_id: int) -> list[dict]:
    ids = [r["id"] for r in _rows(conn.execute(
        "SELECT id FROM drivers WHERE position_id=? ORDER BY critical DESC, id", (position_id,)))]
    return [get_driver(conn, i) for i in ids]


# ---------------------------------------------------------------- checkpoints

def add_checkpoint(conn, position_id: int, title: str, expected_by: str, evidence_needed=None,
                   driver_id=None, created_by="user") -> dict:
    datetime.strptime(expected_by, "%Y-%m-%d")
    if not _row(conn.execute("SELECT id FROM positions WHERE id=?", (position_id,))):
        raise ValueError("unknown position")
    if driver_id and not _row(conn.execute("SELECT id FROM drivers WHERE id=? AND position_id=?",
                                           (driver_id, position_id))):
        raise ValueError("driver_id does not belong to this position")
    cur = conn.execute(
        """INSERT INTO checkpoints (position_id, driver_id, title, expected_by, evidence_needed, created_by)
           VALUES (?,?,?,?,?,?)""", (position_id, driver_id, title, expected_by, evidence_needed, created_by))
    conn.commit()
    return _row(conn.execute("SELECT * FROM checkpoints WHERE id=?", (cur.lastrowid,)))


def resolve_checkpoint(conn, checkpoint_id: int, status: str, evidence=None, interaction_id=None) -> dict:
    if status not in ("confirmed", "missed", "pending"):
        raise ValueError("status must be confirmed, missed or pending")
    conn.execute("UPDATE checkpoints SET status=?, evidence=?, interaction_id=?, decided_at=? WHERE id=?",
                 (status, evidence, interaction_id, _now() if status != "pending" else None, checkpoint_id))
    conn.commit()
    return _row(conn.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)))


def delete_checkpoint(conn, checkpoint_id: int) -> None:
    conn.execute("DELETE FROM checkpoints WHERE id=?", (checkpoint_id,))
    conn.commit()


def list_checkpoints(conn, position_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = """SELECT k.*, d.name AS driver_name, c.name AS company_name, c.id AS company_id FROM checkpoints k
             LEFT JOIN drivers d ON d.id=k.driver_id
             JOIN positions p ON p.id=k.position_id JOIN companies c ON c.id=p.company_id WHERE 1=1"""
    args = []
    if position_id:
        sql += " AND k.position_id=?"
        args.append(position_id)
    if status:
        _check(status, db.CHECKPOINT_STATUSES, "status")
        sql += " AND k.status=?"
        args.append(status)
    sql += " ORDER BY CASE k.status WHEN 'pending' THEN 0 ELSE 1 END, k.expected_by"
    rows = _rows(conn.execute(sql, args))
    today = _today()
    for r in rows:
        r["overdue"] = r["status"] == "pending" and r["expected_by"] < today
        r["days_left"] = (datetime.strptime(r["expected_by"], "%Y-%m-%d")
                          - datetime.strptime(today, "%Y-%m-%d")).days
    return rows


def overdue_checkpoints(conn) -> list[dict]:
    return [c for c in list_checkpoints(conn, status="pending") if c["overdue"]]


# ---------------------------------------------------------------- metrics

def set_metric(conn, company_id: str, metric: str, period: str, kind: str, value: float, source=None,
               created_by="user") -> dict:
    _check(metric, db.METRICS, "metric")
    _check(kind, db.VALUE_KINDS, "kind")
    g, _, _ = _parse_period(period)
    if metric == "revenue_m" and g != "month":
        raise ValueError("revenue_m needs a month period like 2026-05")
    if metric != "revenue_m" and g == "month":
        raise ValueError(f"{metric} needs a quarter (2026Q2) or year (2026) period")
    if not get_company(conn, company_id):
        raise ValueError(f"unknown company {company_id}")
    conn.execute(
        """INSERT INTO metrics (company_id, metric, period, kind, value, source, created_by) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(company_id, metric, period, kind) DO UPDATE SET value=excluded.value,
             source=COALESCE(excluded.source, metrics.source), created_by=excluded.created_by""",
        (company_id, metric, period.upper(), kind, float(value), source, created_by))
    conn.commit()
    return _row(conn.execute("SELECT * FROM metrics WHERE company_id=? AND metric=? AND period=? AND kind=?",
                             (company_id, metric, period.upper(), kind)))


def list_metrics(conn, company_id: str, metric: str | None = None) -> list[dict]:
    sql = "SELECT * FROM metrics WHERE company_id=?"
    args = [company_id]
    if metric:
        sql += " AND metric=?"
        args.append(metric)
    rows = _rows(conn.execute(sql, args))
    return sorted(rows, key=lambda r: (r["metric"], _period_sort_key(r["period"]), r["kind"]))


def metric_comparison(conn, company_id: str) -> dict:
    """Model vs actual per (metric, period), plus quarter run-rate from monthly revenue and
    monthly YoY. Only periods that have data are returned."""
    rows = list_metrics(conn, company_id)
    table: dict[tuple, dict] = {}
    for r in rows:
        t = table.setdefault((r["metric"], r["period"]), {
            "metric": r["metric"], "label": db.METRIC_LABELS[r["metric"]],
            "period": r["period"], "model": None, "actual": None, "source": None})
        t[r["kind"]] = r["value"]
        if r["kind"] == "actual":
            t["source"] = r["source"]
    comparisons = []
    for t in table.values():
        t["gap"], t["flag"] = None, False
        if t["model"] is not None and t["actual"] is not None:
            if t["metric"] == "gross_margin":
                t["gap"] = round(t["actual"] - t["model"], 4)          # absolute points
                t["flag"] = t["gap"] < -MARGIN_TOL
            elif t["model"]:
                t["gap"] = round((t["actual"] - t["model"]) / t["model"], 4)
                if t["metric"] == "shares":
                    t["flag"] = t["gap"] > SHARES_TOL
                elif t["metric"] != "opex":
                    t["flag"] = t["gap"] < -EPS_TOL
        comparisons.append(t)
    comparisons.sort(key=lambda t: (t["metric"], _period_sort_key(t["period"])))

    monthly = {r["period"]: r["value"] for r in rows if r["metric"] == "revenue_m" and r["kind"] == "actual"}
    run_rate = []
    for r in rows:
        if r["metric"] == "revenue_q" and r["kind"] == "model":
            _, y, q = _parse_period(r["period"])
            months = _quarter_months(y, q)
            have = [(m, monthly[m]) for m in months if m in monthly]
            if not have:
                continue
            actual_sum = sum(v for _, v in have)
            projected = actual_sum + (3 - len(have)) * have[-1][1]
            gap = (projected - r["value"]) / r["value"] if r["value"] else None
            run_rate.append({"period": r["period"], "model": r["value"], "months_available": len(have),
                             "actual_months": dict(have), "actual_sum": actual_sum, "projected": projected,
                             "gap_pct": round(gap, 4) if gap is not None else None,
                             "flag": gap is not None and gap < -RUNRATE_TOL,
                             "final": len(have) == 3})
    yoy = []
    for m, v in sorted(monthly.items()):
        prev = f"{int(m[:4]) - 1}{m[4:]}"
        if prev in monthly and monthly[prev]:
            yoy.append({"period": m, "value": v, "yoy": round(v / monthly[prev] - 1, 4)})
    return {"comparisons": comparisons, "run_rate": run_rate, "monthly_yoy": yoy[-12:],
            "monthly": [{"period": m, "value": v} for m, v in sorted(monthly.items())][-12:],
            "has_data": bool(rows)}


# ---------------------------------------------------------------- thesis health

def thesis_health(conn, position_id: int, _pos: dict | None = None) -> dict:
    """Issues that should make the user re-examine the thesis. Does NOT change thesis_status;
    apply_health_flags() does that explicitly."""
    pos = _pos or _row(conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)))
    if not pos:
        raise ValueError("unknown position")
    issues = []
    for c in list_checkpoints(conn, position_id):
        if c["overdue"]:
            issues.append({"type": "checkpoint_overdue", "severity": "high", "ref": f"checkpoint:{c['id']}",
                           "message": f"檢查點逾期：{c['title']}（預期 {c['expected_by']}）"})
        elif c["status"] == "missed":
            issues.append({"type": "checkpoint_missed", "severity": "high", "ref": f"checkpoint:{c['id']}",
                           "message": f"檢查點未達成：{c['title']}"})
    for d in list_drivers(conn, position_id):
        sev = "high" if d["critical"] else "low"
        if d["signal"] == "weakened":
            issues.append({"type": "driver_weakened", "severity": sev, "ref": f"driver:{d['id']}",
                           "message": f"驅動因子被削弱：{d['name']}"})
        elif d["signal"] == "behind":
            lc = d["latest_comparison"]
            issues.append({"type": "driver_behind", "severity": sev, "ref": f"driver:{d['id']}",
                           "message": (f"{d['name']} {lc['period']} 實際 {lc['actual']} vs 模型 {lc['model']}"
                                       f"（{lc['gap_pct']:+.0%}）")})
    mc = metric_comparison(conn, pos["company_id"])
    for rr in mc["run_rate"]:
        if rr["flag"]:
            issues.append({"type": "runrate_behind", "severity": "medium", "ref": f"metric:revenue_q:{rr['period']}",
                           "message": (f"{rr['period']} 月營收 run-rate 推估 {rr['projected']:,.0f}，"
                                       f"低於模型 {rr['model']:,.0f}（{rr['gap_pct']:+.0%}，"
                                       f"已有 {rr['months_available']} 個月）")})
    for t in mc["comparisons"]:
        if t["flag"]:
            kind = "dilution" if t["metric"] == "shares" else "metric_behind"
            gap_txt = f"{t['gap']:+.1%}" if t["metric"] != "gross_margin" else f"{t['gap'] * 100:+.1f} 個百分點"
            issues.append({"type": kind, "severity": "medium", "ref": f"metric:{t['metric']}:{t['period']}",
                           "message": f"{t['label']} {t['period']} 實際 {t['actual']} vs 模型 {t['model']}（{gap_txt}）"})
    suggest = pos["thesis_status"] == "intact" and any(i["severity"] == "high" for i in issues)
    return {"status": "attention" if issues else "ok", "issues": issues, "suggest_shaken": suggest}


def apply_health_flags(conn, created_by="system") -> list[dict]:
    """Set thesis_status=shaken on intact positions whose health suggests it, logging a review
    interaction that lists the issues. Returns what changed."""
    changed = []
    for p in list_positions(conn):
        if p["stage"] == "exited" or p["thesis_status"] != "intact":
            continue
        h = thesis_health(conn, p["id"])
        if h["suggest_shaken"]:
            set_thesis_status(conn, p["id"], "shaken")
            add_interaction(conn, p["id"], "review",
                            "系統依健康度檢查將論點標為動搖：\n" + "\n".join(f"- {i['message']}" for i in h["issues"]),
                            created_by=created_by)
            changed.append({"position_id": p["id"], "company_id": p["company_id"], "name": p["name"],
                            "issues": h["issues"]})
    return changed


# ---------------------------------------------------------------- templates

TEMPLATES = {
    "transformation": {
        "label": "轉型股", "description": "舊業務養新業務，論點靠新業務占比與關鍵產品出貨",
        "drivers": [
            {"name": "新業務營收占比", "unit": "%", "critical": 1},
            {"name": "關鍵產品出貨量", "unit": "台", "critical": 1, "sensitivity": "填每單位對 EPS 的貢獻"},
            {"name": "關鍵產品單價 ASP", "unit": "百萬", "critical": 0},
        ],
        "checkpoints": [
            {"title": "新業務首次貢獻營收", "months": 6, "evidence_needed": "季報或法說揭露新業務營收"},
            {"title": "關鍵客戶認證通過", "months": 9, "evidence_needed": "重訊、法說或客戶公告"},
            {"title": "首批出貨", "months": 12, "evidence_needed": "月營收跳升或出貨公告"},
        ],
    },
    "growth": {
        "label": "成長股", "description": "營收與毛利率同步擴張，看產能與訂單能見度",
        "drivers": [
            {"name": "營收年增率", "unit": "%", "critical": 1},
            {"name": "毛利率", "unit": "%", "critical": 1},
            {"name": "產能", "unit": "", "critical": 0},
        ],
        "checkpoints": [
            {"title": "產能擴張到位", "months": 9, "evidence_needed": "法說或資本支出公告"},
            {"title": "毛利率站上模型水準", "months": 6, "evidence_needed": "季報"},
        ],
    },
    "cyclical": {
        "label": "景氣循環股", "description": "看報價、利用率與庫存，論點是循環位置",
        "drivers": [
            {"name": "產品報價", "unit": "", "critical": 1},
            {"name": "產能利用率", "unit": "%", "critical": 1},
            {"name": "庫存天數", "unit": "天", "critical": 0, "higher_is_better": 0},
        ],
        "checkpoints": [
            {"title": "報價止跌回升", "months": 3, "evidence_needed": "產業報價資料"},
            {"title": "庫存回到正常水位", "months": 6, "evidence_needed": "季報存貨"},
        ],
    },
    "supply_chain": {
        "label": "供應鏈受惠股", "description": "論點掛在主要客戶的資本支出與自己在該客戶的份額",
        "drivers": [
            {"name": "主要客戶資本支出年增率", "unit": "%", "critical": 1},
            {"name": "在主要客戶的份額", "unit": "%", "critical": 1},
            {"name": "主要客戶營收占比", "unit": "%", "critical": 0, "sensitivity": "集中度風險，不是越高越好",
             "higher_is_better": 0},
        ],
        "checkpoints": [
            {"title": "主要客戶法說維持或上修資本支出", "months": 3, "evidence_needed": "客戶法說"},
            {"title": "月營收反映客戶拉貨", "months": 4, "evidence_needed": "月營收年增率"},
        ],
    },
}


def list_templates() -> list[dict]:
    return [{"key": k, "label": v["label"], "description": v["description"],
             "drivers": [d["name"] for d in v["drivers"]], "checkpoints": [c["title"] for c in v["checkpoints"]]}
            for k, v in TEMPLATES.items()]


def apply_template(conn, position_id: int, key: str, created_by="user") -> dict:
    if key not in TEMPLATES:
        raise ValueError(f"unknown template {key!r}; one of {list(TEMPLATES)}")
    t = TEMPLATES[key]
    today = datetime.now(timezone.utc)
    made = {"drivers": [], "checkpoints": []}
    for d in t["drivers"]:
        made["drivers"].append(add_driver(conn, position_id, d["name"], d.get("unit"), d.get("critical", 1),
                                          d.get("sensitivity"), None, d.get("higher_is_better", 1), created_by)["id"])
    for c in t["checkpoints"]:
        due = (today + timedelta(days=30 * c["months"])).strftime("%Y-%m-%d")
        made["checkpoints"].append(add_checkpoint(conn, position_id, c["title"], due, c.get("evidence_needed"),
                                                  created_by=created_by)["id"])
    return made


# ================================================================ maintenance
# Automated upkeep: backups, official-fact ingestion helpers, data-quality report, job log.
# Policy: scripts write official facts directly (created_by 'system:*'); anything needing judgment
# still goes through proposals; thesis/positions stay the user's.

def tw_companies(conn) -> list[dict]:
    """TW companies worth fetching: every TW company that has a card or sits on an edge.
    ETFs (sector 'ETF') are skipped: no revenue, no income statement."""
    return _rows(conn.execute(
        """SELECT DISTINCT c.id, c.ticker, c.name FROM companies c
           LEFT JOIN positions p ON p.company_id=c.id
           LEFT JOIN edges e ON e.customer_id=c.id OR e.supplier_id=c.id
           WHERE c.market='TW' AND IFNULL(c.sector,'')<>'ETF' AND (p.id IS NOT NULL OR e.id IS NOT NULL)
           ORDER BY c.ticker"""))


def log_maintenance(conn, job: str, status: str, summary: str, details=None) -> dict:
    if status not in ("ok", "error", "skipped"):
        raise ValueError("status must be ok, error or skipped")
    payload = json.dumps(details, ensure_ascii=False, default=str) if details is not None else None
    cur = conn.execute("INSERT INTO maintenance_log (job, status, summary, details) VALUES (?,?,?,?)",
                       (job, status, summary, payload))
    conn.commit()
    return _row(conn.execute("SELECT * FROM maintenance_log WHERE id=?", (cur.lastrowid,)))


def list_maintenance(conn, limit: int = 30) -> list[dict]:
    rows = _rows(conn.execute("SELECT * FROM maintenance_log ORDER BY ts DESC, id DESC LIMIT ?", (limit,)))
    for r in rows:
        if r["details"]:
            try:
                r["details"] = json.loads(r["details"])
            except json.JSONDecodeError:
                pass
    return rows


def last_run(conn, job: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM maintenance_log WHERE job=? ORDER BY ts DESC, id DESC LIMIT 1", (job,)))


def backup_db(conn, keep_days: int = 30, backup_dir=None) -> dict:
    """Consistent online copy of the database via the SQLite backup API; prunes copies older than keep_days."""
    import sqlite3 as _sq
    from pathlib import Path
    src_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    bdir = Path(backup_dir) if backup_dir else src_path.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = bdir / f"{src_path.stem}-{stamp}.db"
    with _sq.connect(dest) as out:
        conn.backup(out)
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    pruned = []
    for f in bdir.glob(f"{src_path.stem}-*.db"):
        try:
            when = datetime.strptime(f.stem.split("-", 1)[1], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            f.unlink()
            pruned.append(f.name)
    return {"path": str(dest), "bytes": dest.stat().st_size, "pruned": pruned,
            "kept": len(list(bdir.glob(f"{src_path.stem}-*.db")))}


def upsert_financials_ytd(conn, company_id, year, quarter, revenue, gross_profit, opex, net_income, eps, source=None):
    conn.execute(
        """INSERT INTO financials_ytd (company_id, year, quarter, revenue, gross_profit, opex, net_income, eps,
                                       source, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(company_id, year, quarter) DO UPDATE SET revenue=excluded.revenue, gross_profit=excluded.gross_profit,
             opex=excluded.opex, net_income=excluded.net_income, eps=excluded.eps, source=excluded.source,
             fetched_at=excluded.fetched_at""",
        (company_id, int(year), int(quarter), revenue, gross_profit, opex, net_income, eps, source, _now()))
    conn.commit()


def derive_quarterly_metrics(conn, company_id, year, quarter, created_by="system:mops") -> dict:
    """Turn YTD figures into single-quarter metrics: Q1 as is; Qn = YTD(Qn) - YTD(Qn-1) when stored."""
    cur = _row(conn.execute("SELECT * FROM financials_ytd WHERE company_id=? AND year=? AND quarter=?",
                            (company_id, year, quarter)))
    if not cur:
        return {"written": [], "reason": "no ytd row"}
    if quarter == 1:
        prev = {"revenue": 0, "gross_profit": 0, "opex": 0, "net_income": 0, "eps": 0}
    else:
        prev = _row(conn.execute("SELECT * FROM financials_ytd WHERE company_id=? AND year=? AND quarter=?",
                                 (company_id, year, quarter - 1)))
        if not prev:
            return {"written": [], "reason": f"need YTD for Q{quarter - 1} {year} to difference; stored YTD only"}
    period = f"{year}Q{quarter}"
    written = []

    def diff(k):
        if cur[k] is None or prev[k] is None:
            return None
        return cur[k] - prev[k]

    rev, gp, opex, eps = diff("revenue"), diff("gross_profit"), diff("opex"), diff("eps")
    if rev:
        set_metric(conn, company_id, "revenue_q", period, "actual", rev, cur["source"], created_by)
        written.append("revenue_q")
        if gp is not None:
            set_metric(conn, company_id, "gross_margin", period, "actual", round(gp / rev, 4), cur["source"], created_by)
            written.append("gross_margin")
    if opex is not None:
        set_metric(conn, company_id, "opex", period, "actual", opex, cur["source"], created_by)
        written.append("opex")
    if eps is not None:
        set_metric(conn, company_id, "eps", period, "actual", round(eps, 2), cur["source"], created_by)
        written.append("eps")
    return {"written": written, "period": period}


def data_quality_report(conn, stale_days: int = 30, proposal_days: int = 14) -> dict:
    """What a maintainer should fix or chase. Each list holds small dicts; counts are summed in `total`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=proposal_days)).strftime("%Y-%m-%d %H:%M:%S")
    rep = {
        "edges_without_source": _rows(conn.execute(
            "SELECT id, customer_id, supplier_id, revenue_share FROM edges WHERE source IS NULL OR source=''")),
        "critical_drivers_without_model": _rows(conn.execute(
            """SELECT d.id, d.name, c.name AS company FROM drivers d JOIN positions p ON p.id=d.position_id
               JOIN companies c ON c.id=p.company_id
               WHERE d.critical=1 AND NOT EXISTS (SELECT 1 FROM driver_values v WHERE v.driver_id=d.id AND v.kind='model')""")),
        "holdings_without_invalidation": _rows(conn.execute(
            """SELECT p.id, c.name FROM positions p JOIN companies c ON c.id=p.company_id
               WHERE p.stage='hold' AND (p.invalidation IS NULL OR p.invalidation='')""")),
        "holdings_without_drivers": _rows(conn.execute(
            """SELECT p.id, c.name FROM positions p JOIN companies c ON c.id=p.company_id
               WHERE p.stage='hold' AND NOT EXISTS (SELECT 1 FROM drivers d WHERE d.position_id=p.id)""")),
        "proposals_pending_too_long": _rows(conn.execute(
            "SELECT id, kind, created_by, created_at FROM proposals WHERE status='pending' AND created_at<?", (cutoff,))),
        "stale_holdings": [{"company_id": p["company_id"], "name": p["name"], "days": p["days_since_update"]}
                           for p in stale_positions(conn, stale_days)],
        "overdue_checkpoints": [{"id": c["id"], "company": c["company_name"], "title": c["title"],
                                 "expected_by": c["expected_by"]} for c in overdue_checkpoints(conn)],
    }
    # TW companies whose latest monthly revenue is older than ~45 days (should have a newer release by now)
    stale_rev = []
    for c in tw_companies(conn):
        last = _row(conn.execute(
            "SELECT MAX(period) AS p FROM metrics WHERE company_id=? AND metric='revenue_m' AND kind='actual'", (c["id"],)))
        p = last["p"] if last else None
        if p:
            y, m = int(p[:4]), int(p[5:])
            age = (datetime.now(timezone.utc).year - y) * 12 + datetime.now(timezone.utc).month - m
            if age > 2:
                stale_rev.append({"company_id": c["id"], "name": c["name"], "latest": p, "months_behind": age})
        else:
            stale_rev.append({"company_id": c["id"], "name": c["name"], "latest": None, "months_behind": None})
    rep["tw_companies_missing_recent_revenue"] = stale_rev
    rep["total"] = sum(len(v) for v in rep.values() if isinstance(v, list))
    rep["generated_at"] = _now()
    return rep


def quality_markdown(rep: dict) -> str:
    labels = {
        "edges_without_source": "沒有來源的供應鏈關係",
        "critical_drivers_without_model": "沒有模型值的關鍵驅動因子",
        "holdings_without_invalidation": "沒有失效條件的持股",
        "holdings_without_drivers": "沒有驅動因子的持股",
        "proposals_pending_too_long": "放超過兩週沒批的提案",
        "stale_holdings": "超過 30 天沒更新的持股",
        "overdue_checkpoints": "逾期檢查點",
        "tw_companies_missing_recent_revenue": "月營收不是最新的台股公司",
    }
    lines = [f"# 資料品質報告（{rep['generated_at']} UTC）", "", f"共 {rep['total']} 項。", ""]
    for key, label in labels.items():
        items = rep.get(key) or []
        lines.append(f"## {label}（{len(items)}）")
        for it in items:
            lines.append("- " + "，".join(f"{k}={v}" for k, v in it.items() if v is not None))
        lines.append("")
    return "\n".join(lines)


def rename_company(conn, old_id: str, market: str, ticker: str, name: str | None = None) -> dict:
    """Change a company's id (e.g. a placeholder ticker to the real one) across every table that
    references it. Positions, edges, metrics and YTD financials follow; the old id disappears."""
    _check(market, db.MARKETS, "market")
    old = get_company(conn, old_id)
    if not old:
        raise ValueError(f"unknown company {old_id}")
    new_id = company_id(market, ticker)
    if new_id != old_id and get_company(conn, new_id):
        raise ValueError(f"{new_id} already exists; merge by hand")
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE companies SET id=?, market=?, ticker=?, name=COALESCE(?, name) WHERE id=?",
                     (new_id, market.upper(), ticker.upper(), name, old_id))
        for table, col in (("positions", "company_id"), ("edges", "customer_id"), ("edges", "supplier_id"),
                           ("metrics", "company_id"), ("financials_ytd", "company_id"),
                           ("company_briefs", "company_id")):
            conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new_id, old_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return get_company(conn, new_id)


DATA_TABLES = ("proposals", "reminders", "interactions", "checkpoints", "driver_values", "drivers",
               "metrics", "financials_ytd", "company_briefs", "positions", "edges", "companies")


def reset_data(conn, keep_log: bool = True) -> dict:
    """Delete every user/agent data row. Keeps the schema, tier_rules and (by default) maintenance_log.
    Callers must back up first; this is irreversible."""
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DATA_TABLES}
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        for t in DATA_TABLES:
            conn.execute(f"DELETE FROM {t}")
        if not keep_log:
            conn.execute("DELETE FROM maintenance_log")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("VACUUM")
    log_maintenance(conn, "reset", "ok", "資料已清空：" + "，".join(f"{t} {n}" for t, n in counts.items() if n), counts)
    return counts


# ================================================================ company briefs
# Prose about a company: profile (what it does) and highlights (recent developments).
# Numbers and news feeds live in the engine; the written summary lives here. Append-only.

def add_brief(conn, company_id: str, kind: str, content: str, sources=None, date: str | None = None,
              created_by: str = "user") -> dict:
    _check(kind, db.BRIEF_KINDS, "kind")
    if not get_company(conn, company_id):
        raise ValueError(f"unknown company {company_id}")
    if not content or not content.strip():
        raise ValueError("content is empty")
    date = date or _today()
    datetime.strptime(date, "%Y-%m-%d")
    if sources is None:
        sources = []
    if isinstance(sources, str):
        sources = json.loads(sources)
    if not isinstance(sources, list):
        raise ValueError("sources must be a list of {title, url, date}")
    cur = conn.execute(
        "INSERT INTO company_briefs (company_id, date, kind, content, sources, created_by) VALUES (?,?,?,?,?,?)",
        (company_id, date, kind, content.strip(), json.dumps(sources, ensure_ascii=False), created_by))
    conn.commit()
    return get_brief(conn, cur.lastrowid)


def _decode_brief(r: dict) -> dict:
    try:
        r["sources"] = json.loads(r["sources"]) if isinstance(r["sources"], str) else r["sources"]
    except json.JSONDecodeError:
        r["sources"] = []
    return r


def get_brief(conn, brief_id: int) -> dict | None:
    r = _row(conn.execute("SELECT * FROM company_briefs WHERE id=?", (brief_id,)))
    return _decode_brief(r) if r else None


def list_briefs(conn, company_id: str, kind: str | None = None, limit: int = 20) -> list[dict]:
    sql = "SELECT * FROM company_briefs WHERE company_id=?"
    args: list = [company_id]
    if kind:
        _check(kind, db.BRIEF_KINDS, "kind")
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY date DESC, id DESC LIMIT ?"
    args.append(limit)
    return [_decode_brief(r) for r in _rows(conn.execute(sql, args))]


def _first_sentence(text: str, limit: int = 80) -> str:
    t = text.strip().lstrip("-*# ").replace("\n", " ")
    for sep in ("。", ". ", "！", "？", "；"):
        if sep in t:
            t = t.split(sep)[0] + (sep.strip() if sep != ". " else ".")
            break
    return t[:limit] + ("…" if len(t) > limit else "")


def brief_summary(conn, company_id: str) -> dict:
    """What a card header needs: latest highlight's first sentence + date, and whether a profile exists."""
    h = list_briefs(conn, company_id, "highlights", 1)
    p = list_briefs(conn, company_id, "profile", 1)
    return {"latest_brief": {"date": h[0]["date"], "text": _first_sentence(h[0]["content"])} if h else None,
            "has_profile": bool(p)}


def briefs_for_card(conn, company_id: str) -> dict:
    p = list_briefs(conn, company_id, "profile", 1)
    return {"profile": p[0] if p else None, "highlights": list_briefs(conn, company_id, "highlights", 5)}
