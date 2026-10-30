"""MCP server for Stock CRM. Run with: python -m stockcrm.mcp_server  (stdio transport)

Tool naming convention:
  get_* / list_* / search_* / export_*  read-only
  log_* / add_*                          direct writes for things the USER asked to record
  propose_*                              writes that need the user's approval in the UI
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from stockcrm import core, db  # noqa: E402

AGENT = "agent:claude-code"

INSTRUCTIONS = """Stock CRM: a supply-chain-aware investment journal. Companies are nodes; edges are
real customer -> supplier relationships with the customer's share of the supplier's revenue.
Positions are the user's cards (stage watch/hold/exited, tier core/satellite/watch/blacklist),
each with a thesis and an invalidation condition. Interactions are the contact log.

Rules:
- Use log_interaction / add_to_watchlist / add_reminder ONLY for what the user explicitly asked to record.
- Anything you infer from external information (news, filings, transcripts) goes through propose_*;
  the user approves it in the web UI. Never change a thesis or position size yourself.
- Company ids are MARKET:TICKER, e.g. TW:2330, US:NVDA. Most tools also accept a bare ticker or name.
- revenue_share is a 0..1 fraction of the SUPPLIER's revenue coming from the CUSTOMER.
- Times are UTC. Free text is Markdown. Reply to the user in their language (usually 繁體中文).
"""

server = MCPServer(
    "stockcrm",
    title="Stock CRM",
    instructions=INSTRUCTIONS,
)


def _conn():
    return db.connect()


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def _pos_id(conn, position: str | int) -> int:
    p = core.get_position(conn, position)
    if not p:
        raise ValueError(f"no position card for {position!r}; use add_to_watchlist first")
    return p["id"]


# ------------------------------------------------------------------ read

@server.tool()
def list_positions(stage: str | None = None, tier: str | None = None) -> str:
    """List position cards. stage: watch|hold|exited. tier: core|satellite|watch|blacklist.
    Each row includes position_pct, thesis_status and days_since_update (stale if >= 30)."""
    with _conn() as c:
        return _j(core.list_positions(c, stage, tier))


@server.tool()
def get_position(position: str) -> str:
    """Full card for one company: thesis, invalidation, thesis_status, tier rule, interactions
    (newest first), open reminders, customers, suppliers and customer-concentration risk.
    position: 'TW:2330', '2330', 'NVDA' or a name."""
    with _conn() as c:
        p = core.get_position(c, position)
        return _j(p) if p else f"no position card for {position!r}"


@server.tool()
def search_companies(query: str = "", market: str | None = None) -> str:
    """Search known companies by id, ticker or name. market: TW|US. Empty query lists all."""
    with _conn() as c:
        return _j(core.search_companies(c, query, market))


@server.tool()
def get_company_relations(company: str) -> str:
    """Customers and suppliers of a company with revenue_share, lag_days, confidence and source."""
    with _conn() as c:
        cid = core.resolve_company(c, company)["id"]
        out = core.relations(c, cid)
        out["concentration"] = core.customer_concentration(c, cid)
        return _j(out)


@server.tool()
def get_indirect_exposure(company: str) -> str:
    """How much of the held portfolio depends on this company as a CUSTOMER, walking
    customer -> supplier edges up to two hops. exposure_pct = position_pct * product(revenue_share).
    Use for questions like 'if Apple cuts orders 10%, what in my portfolio is hit?'"""
    with _conn() as c:
        cid = core.resolve_company(c, company)["id"]
        return _j(core.indirect_exposure(c, cid))


@server.tool()
def get_portfolio_overlap() -> str:
    """For every customer in the graph, the combined direct + indirect exposure of the held
    portfolio. Reveals several positions that are really one bet on the same end customer."""
    with _conn() as c:
        return _j(core.portfolio_overlap(c))


@server.tool()
def get_stale_positions(days: int = 30) -> str:
    """Held positions with no interaction for at least `days` days. These need a review."""
    with _conn() as c:
        return _j(core.stale_positions(c, days))


@server.tool()
def check_tier_limit(position: str, new_pct: float) -> str:
    """Would setting this position to new_pct (% of portfolio) exceed its tier's max? Call this
    before the user adds to a position and tell them the result."""
    with _conn() as c:
        return _j(core.check_tier_limit(c, _pos_id(c, position), new_pct))


@server.tool()
def get_morning_context() -> str:
    """Everything needed to write the morning brief: holdings with thesis status, the watchlist,
    counterparties_to_check (every customer/supplier of a holding, with its role and revenue share),
    shaken/broken theses, stale holdings, due reminders, pending proposals, portfolio overlap, and
    whether we are inside the Taiwan monthly-revenue window (days 1-10). Fetch news for the
    counterparties yourself, then propose_interaction for anything material."""
    with _conn() as c:
        return _j(core.morning_context(c))


@server.tool()
def export_graph(format: str = "markdown") -> str:
    """The whole supply-chain graph. format: markdown (compact, for reasoning) | json (nodes, edges)."""
    with _conn() as c:
        return core.graph_markdown(c) if format == "markdown" else _j(core.export_graph(c))


@server.tool()
def list_proposals(status: str = "pending") -> str:
    """Proposals waiting for the user. status: pending|approved|rejected|all."""
    with _conn() as c:
        return _j(core.list_proposals(c, None if status == "all" else status))


@server.tool()
def list_reminders() -> str:
    """All open reminders ordered by due date."""
    with _conn() as c:
        return _j(core.all_reminders(c))


# ------------------------------------------------------------------ direct writes (user asked)

@server.tool()
def log_interaction(position: str, kind: str, content: str, price: float | None = None,
                    behavior_tags: list[str] | None = None, source_url: str | None = None,
                    ts: str | None = None) -> str:
    """Record something the USER told you: a trade or a thought. kind: buy|sell|add|trim|thought|review
    (news|earnings|call also allowed when the user dictates it). behavior_tags examples:
    chasing, averaging_down, revenge, fomo, panic. ts: 'YYYY-MM-DD HH:MM:SS' UTC, default now.
    For information you found yourself, use propose_interaction instead."""
    with _conn() as c:
        return _j(core.add_interaction(c, _pos_id(c, position), kind, content, price, behavior_tags,
                                       source_url, created_by=AGENT, ts=ts))


@server.tool()
def add_to_watchlist(market: str, ticker: str, name: str, thesis: str | None = None,
                     invalidation: str | None = None, target_price: float | None = None,
                     name_en: str | None = None, sector: str | None = None,
                     idea_source: str | None = None, tags: list[str] | None = None) -> str:
    """Create the company if needed and a card in stage=watch, tier=watch. Only when the user asks.
    market: TW|US. Changing stage/tier/thesis later is the user's job in the UI."""
    with _conn() as c:
        comp = core.upsert_company(c, market, ticker, name, name_en, sector)
        if core.get_position(c, comp["id"]):
            return f"{comp['id']} already has a card; nothing changed"
        return _j(core.upsert_position(c, comp["id"], "watch", "watch", thesis, invalidation,
                                       target_price, None, 0, idea_source, tags))


@server.tool()
def upsert_company(market: str, ticker: str, name: str, name_en: str | None = None,
                   sector: str | None = None, notes: str | None = None) -> str:
    """Add or update a company node (no position card). Needed before proposing an edge to a
    company not yet in the graph. market: TW|US."""
    with _conn() as c:
        return _j(core.upsert_company(c, market, ticker, name, name_en, sector, notes))


@server.tool()
def add_reminder(due_date: str, kind: str, content: str, position: str | None = None) -> str:
    """Add a reminder the user asked for. due_date YYYY-MM-DD. kind: price|earnings|revenue|review|other."""
    with _conn() as c:
        pid = _pos_id(c, position) if position else None
        return _j(core.add_reminder(c, due_date, kind, content, pid, created_by=AGENT))


# ------------------------------------------------------------------ proposals (need approval)

@server.tool()
def propose_interaction(position: str, kind: str, content: str, rationale: str,
                        source_url: str | None = None, price: float | None = None) -> str:
    """Propose adding an interaction based on information YOU found (news, filing, transcript).
    kind usually news|earnings|call. rationale: why it matters for this position's thesis.
    The user approves it in the UI."""
    with _conn() as c:
        payload = {"position_id": _pos_id(c, position), "kind": kind, "content": content,
                   "source_url": source_url, "price": price}
        return _j(core.create_proposal(c, "interaction", payload, rationale, AGENT))


@server.tool()
def propose_edge(customer: str, supplier: str, rationale: str, revenue_share: float | None = None,
                 lag_days: int | None = None, confidence: str = "medium", source: str | None = None) -> str:
    """Propose a new or updated supply-chain edge: customer buys from supplier. revenue_share is the
    fraction (0..1) of the SUPPLIER's revenue from this customer. confidence: high|medium|low.
    Both companies must exist (use upsert_company first)."""
    with _conn() as c:
        payload = {"customer_id": core.resolve_company(c, customer)["id"],
                   "supplier_id": core.resolve_company(c, supplier)["id"],
                   "revenue_share": revenue_share, "lag_days": lag_days,
                   "confidence": confidence, "source": source}
        return _j(core.create_proposal(c, "edge", payload, rationale, AGENT))


@server.tool()
def propose_thesis_status(position: str, status: str, rationale: str, note: str | None = None) -> str:
    """Propose changing a position's thesis_status: intact|shaken|broken. Compare the card's
    invalidation condition against what you learned, and say so in rationale. note, if given,
    is logged as a review interaction when approved."""
    with _conn() as c:
        payload = {"position_id": _pos_id(c, position), "status": status, "note": note}
        return _j(core.create_proposal(c, "thesis_status", payload, rationale, AGENT))


@server.tool()
def propose_reminder(due_date: str, kind: str, content: str, rationale: str,
                     position: str | None = None) -> str:
    """Propose a reminder you think the user should have (earnings date, revenue release, review)."""
    with _conn() as c:
        payload = {"due_date": due_date, "kind": kind, "content": content,
                   "position_id": _pos_id(c, position) if position else None}
        return _j(core.create_proposal(c, "reminder", payload, rationale, AGENT))


if __name__ == "__main__":
    server.run("stdio")
