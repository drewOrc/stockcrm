"""Writes to the knowledge layer.

The engine is the only writer of knowledge_* tables. The CRM reads them with plain SQL (same file) but every
write goes through the engine's REST API with the service token and the acting user's id; the engine checks
that the user is an admin. For tests and a standalone database (STOCKCRM_KNOWLEDGE_WRITES=direct) the same
functions write the tables directly.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import db

ENGINE_URL = os.environ.get("STOCKCRM_ENGINE_URL", "http://127.0.0.1:8000")


def mode() -> str:
    m = os.environ.get("STOCKCRM_KNOWLEDGE_WRITES", "rest")
    return m if m in ("rest", "direct") else "rest"


class KnowledgeWriteError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _check(value, allowed, field):
    if value not in allowed:
        raise ValueError(f"{field} must be one of {list(allowed)}, got {value!r}")


def _row(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def _rest(method: str, path: str, body: dict | None, user_id: int) -> dict:
    token = os.environ.get("STOCKCRM_FACADE_TOKEN") or os.environ.get("CRM_FACADE_TOKEN")
    if not token:
        raise KnowledgeWriteError("STOCKCRM_FACADE_TOKEN is not set; cannot write to the knowledge layer")
    req = urllib.request.Request(
        f"{ENGINE_URL}{path}", method=method, data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json", "X-Service-Token": token,
                 "X-Facade-Token": token, "X-User-Id": str(user_id)})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise KnowledgeWriteError(f"engine {e.code} on {method} {path}: {detail}") from None
    except urllib.error.URLError as e:
        raise KnowledgeWriteError(f"engine unreachable at {ENGINE_URL}: {e}") from None


# ---------------------------------------------------------------- companies

def upsert_company(conn, user_id: int, market: str, ticker: str, name: str, name_en=None, sector=None,
                   notes=None, created_by="user") -> dict:
    _check(market, db.MARKETS, "market")
    cid = f"{market.upper()}:{ticker.upper()}"
    if mode() == "rest":
        _rest("PUT", f"/api/market/knowledge/companies/{cid}",
              {"market": market.upper(), "ticker": ticker.upper(), "name": name, "name_en": name_en,
               "sector": sector, "notes": notes, "created_by": created_by}, user_id)
    else:
        conn.execute(
            """INSERT INTO knowledge_companies (id, market, ticker, name, name_en, sector, notes, created_by)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                 name_en=COALESCE(excluded.name_en, knowledge_companies.name_en),
                 sector=COALESCE(excluded.sector, knowledge_companies.sector),
                 notes=COALESCE(excluded.notes, knowledge_companies.notes), updated_at=?""",
            (cid, market.upper(), ticker.upper(), name, name_en, sector, notes, created_by, _now()))
        conn.commit()
    return _row(conn.execute("SELECT * FROM knowledge_companies WHERE id=?", (cid,)))


# ---------------------------------------------------------------- edges

def add_edge(conn, user_id: int, customer_id, supplier_id, revenue_share=None, lag_days=None, confidence="medium",
             source=None, notes=None, created_by="user", relation="customer") -> dict:
    _check(confidence, db.CONFIDENCES, "confidence")
    _check(relation, db.RELATIONS, "relation")
    if customer_id == supplier_id:
        raise ValueError("a company cannot be its own customer")
    if revenue_share is not None and not (0 <= revenue_share <= 1):
        raise ValueError("revenue_share must be 0..1 (fraction of supplier revenue)")
    for cid in (customer_id, supplier_id):
        if not _row(conn.execute("SELECT id FROM knowledge_companies WHERE id=?", (cid,))):
            raise ValueError(f"unknown company {cid}")
    body = {"customer_id": customer_id, "supplier_id": supplier_id, "revenue_share": revenue_share,
            "lag_days": lag_days, "confidence": confidence, "relation": relation, "source": source,
            "notes": notes, "created_by": created_by}
    if mode() == "rest":
        _rest("POST", "/api/market/knowledge/edges", body, user_id)
    else:
        conn.execute(
            """INSERT INTO knowledge_edges (customer_id, supplier_id, revenue_share, lag_days, confidence, relation,
                                            source, notes, created_by)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(customer_id, supplier_id) DO UPDATE SET
                 revenue_share=COALESCE(excluded.revenue_share, knowledge_edges.revenue_share),
                 lag_days=COALESCE(excluded.lag_days, knowledge_edges.lag_days),
                 confidence=excluded.confidence, relation=excluded.relation,
                 source=COALESCE(excluded.source, knowledge_edges.source),
                 notes=COALESCE(excluded.notes, knowledge_edges.notes), updated_at=?""",
            (customer_id, supplier_id, revenue_share, lag_days, confidence, relation, source, notes, created_by, _now()))
        conn.commit()
    return _row(conn.execute("SELECT * FROM knowledge_edges WHERE customer_id=? AND supplier_id=?",
                             (customer_id, supplier_id)))


def delete_edge(conn, user_id: int, edge_id: int) -> None:
    if mode() == "rest":
        _rest("DELETE", f"/api/market/knowledge/edges/{edge_id}", None, user_id)
    else:
        conn.execute("DELETE FROM knowledge_edges WHERE id=?", (edge_id,))
        conn.commit()


def add_edge_weight(conn, user_id: int, edge_id: int, date: str, revenue_share: float, source=None,
                    created_by="user") -> dict:
    datetime.strptime(date, "%Y-%m-%d")
    if mode() == "rest":
        _rest("POST", f"/api/market/knowledge/edges/{edge_id}/weights",
              {"date": date, "revenue_share": revenue_share, "source": source, "created_by": created_by}, user_id)
    else:
        conn.execute(
            """INSERT INTO knowledge_edge_weights (edge_id, date, revenue_share, source, created_by) VALUES (?,?,?,?,?)
               ON CONFLICT(edge_id, date) DO UPDATE SET revenue_share=excluded.revenue_share,
                 source=COALESCE(excluded.source, knowledge_edge_weights.source)""",
            (edge_id, date, revenue_share, source, created_by))
        latest = _row(conn.execute("SELECT MAX(date) AS d FROM knowledge_edge_weights WHERE edge_id=?", (edge_id,)))
        if latest and latest["d"] == date:
            conn.execute("UPDATE knowledge_edges SET revenue_share=?, updated_at=? WHERE id=?", (revenue_share, _now(), edge_id))
        conn.commit()
    return _row(conn.execute("SELECT * FROM knowledge_edges WHERE id=?", (edge_id,)))


# ---------------------------------------------------------------- briefs

def add_brief(conn, user_id: int, company_id: str, kind: str, content: str, sources=None, date: str | None = None,
              created_by: str = "user") -> dict:
    _check(kind, db.BRIEF_KINDS, "kind")
    if not _row(conn.execute("SELECT id FROM knowledge_companies WHERE id=?", (company_id,))):
        raise ValueError(f"unknown company {company_id}")
    if not content or not content.strip():
        raise ValueError("content is empty")
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    datetime.strptime(date, "%Y-%m-%d")
    if sources is None:
        sources = []
    if isinstance(sources, str):
        sources = json.loads(sources)
    if not isinstance(sources, list):
        raise ValueError("sources must be a list of {title, url, date}")
    if mode() == "rest":
        r = _rest("POST", "/api/market/knowledge/briefs",
                  {"company_id": company_id, "date": date, "kind": kind, "content": content.strip(),
                   "sources": sources, "created_by": created_by}, user_id)
        bid = r.get("id")
    else:
        cur = conn.execute(
            "INSERT INTO knowledge_company_briefs (company_id, date, kind, content, sources, created_by) VALUES (?,?,?,?,?,?)",
            (company_id, date, kind, content.strip(), json.dumps(sources, ensure_ascii=False), created_by))
        conn.commit()
        bid = cur.lastrowid
    row = _row(conn.execute("SELECT * FROM knowledge_company_briefs WHERE id=?", (bid,))) if bid else None
    if row is None:  # engine answered without an id: fetch the newest matching row
        row = _row(conn.execute(
            "SELECT * FROM knowledge_company_briefs WHERE company_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (company_id, kind)))
    return row


# ---------------------------------------------------------------- metrics (actual)

def set_metric_actual(conn, user_id: int, company_id: str, metric: str, period: str, value: float, source=None,
                      created_by="user") -> dict:
    _check(metric, db.METRICS, "metric")
    if not _row(conn.execute("SELECT id FROM knowledge_companies WHERE id=?", (company_id,))):
        raise ValueError(f"unknown company {company_id}")
    if mode() == "rest":
        _rest("POST", "/api/market/knowledge/metrics",
              {"company_id": company_id, "metric": metric, "period": period.upper(), "value": float(value),
               "source": source, "created_by": created_by}, user_id)
    else:
        conn.execute(
            """INSERT INTO knowledge_metrics (company_id, metric, period, value, source, created_by) VALUES (?,?,?,?,?,?)
               ON CONFLICT(company_id, metric, period) DO UPDATE SET value=excluded.value,
                 source=COALESCE(excluded.source, knowledge_metrics.source), created_by=excluded.created_by""",
            (company_id, metric, period.upper(), float(value), source, created_by))
        conn.commit()
    return _row(conn.execute("SELECT * FROM knowledge_metrics WHERE company_id=? AND metric=? AND period=?",
                             (company_id, metric, period.upper())))
