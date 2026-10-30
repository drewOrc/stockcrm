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

Every thesis can be made testable with three generic containers:
- drivers: 1-3 quantitative assumptions (model value vs actual value per period, e.g. "出貨台數 2027 model 80").
- checkpoints: "by <date> we should see <evidence>". Overdue pending checkpoints shake the thesis.
- metrics: standard financials (revenue_m, revenue_q, gross_margin, opex, eps, shares), model vs actual.
  Monthly revenue actuals are compared with the model's quarterly revenue as a run-rate.
get_thesis_health summarises all three; get_morning_context lists positions needing attention.

Rules:
- Use log_interaction / add_to_watchlist / add_reminder / add_driver / add_checkpoint / set_driver_value /
  set_metric ONLY for what the user explicitly asked to record or dictated (e.g. model values from their spreadsheet).
- Anything you infer from external information (news, filings, transcripts, monthly revenue releases) goes
  through propose_*; the user approves it in the web UI. Never change a thesis or position size yourself.
- When you log or propose an interaction that bears on a driver or checkpoint, link it (driver_id /
  checkpoint_id) and set effect: supports | weakens | confirms. That is how evidence accumulates.
- Company ids are MARKET:TICKER, e.g. TW:2330, US:NVDA. Most tools also accept a bare ticker or name.
- revenue_share is a 0..1 fraction of the SUPPLIER's revenue coming from the CUSTOMER.
- Periods: "2026" (year), "2026Q2" (quarter), "2026-05" (month). Revenue/opex in thousands of local currency,
  gross_margin as 0..1, shares in thousands.
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
                    ts: str | None = None, driver_id: int | None = None, checkpoint_id: int | None = None,
                    effect: str | None = None) -> str:
    """Record something the USER told you: a trade or a thought. kind: buy|sell|add|trim|thought|review
    (news|earnings|call also allowed when the user dictates it). behavior_tags examples:
    chasing, averaging_down, revenge, fomo, panic. ts: 'YYYY-MM-DD HH:MM:SS' UTC, default now.
    Link to a driver or checkpoint with effect supports|weakens|confirms when relevant
    (confirms on a checkpoint marks it confirmed). For information you found yourself, use propose_interaction."""
    with _conn() as c:
        return _j(core.add_interaction(c, _pos_id(c, position), kind, content, price, behavior_tags,
                                       source_url, created_by=AGENT, ts=ts, driver_id=driver_id,
                                       checkpoint_id=checkpoint_id, effect=effect))


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
                        source_url: str | None = None, price: float | None = None,
                        driver_id: int | None = None, checkpoint_id: int | None = None,
                        effect: str | None = None) -> str:
    """Propose adding an interaction based on information YOU found (news, filing, transcript).
    kind usually news|earnings|call. rationale: why it matters for this position's thesis.
    Link it to the driver or checkpoint it bears on, with effect supports|weakens|confirms.
    The user approves it in the UI."""
    with _conn() as c:
        payload = {"position_id": _pos_id(c, position), "kind": kind, "content": content,
                   "source_url": source_url, "price": price, "driver_id": driver_id,
                   "checkpoint_id": checkpoint_id, "effect": effect}
        return _j(core.create_proposal(c, "interaction", payload, rationale, AGENT))


@server.tool()
def propose_edge(customer: str, supplier: str, rationale: str, revenue_share: float | None = None,
                 lag_days: int | None = None, confidence: str = "medium", source: str | None = None,
                 relation: str = "customer") -> str:
    """Propose a new or updated edge. relation=customer: customer buys from supplier; revenue_share is the
    fraction (0..1) of the SUPPLIER's revenue from this customer. relation=partner: capacity or licensing
    partner (put the partner as customer, our company as supplier; revenue_share = share of our revenue
    that depends on the partner). confidence: high|medium|low. Both companies must exist."""
    with _conn() as c:
        payload = {"customer_id": core.resolve_company(c, customer)["id"],
                   "supplier_id": core.resolve_company(c, supplier)["id"],
                   "revenue_share": revenue_share, "lag_days": lag_days,
                   "confidence": confidence, "source": source, "relation": relation}
        return _j(core.create_proposal(c, "edge", payload, rationale, AGENT))


# ------------------------------------------------------------------ drivers / checkpoints / metrics

@server.tool()
def get_thesis_health(position: str) -> str:
    """Is this thesis still on track? Returns drivers (model vs actual, signal), checkpoints (overdue?),
    metric comparisons and run-rate, and a list of issues with severity. suggest_shaken=true means
    a high-severity issue exists on an intact thesis; tell the user, do not change status yourself."""
    with _conn() as c:
        p = core.get_position(c, position)
        if not p:
            return f"no position card for {position!r}"
        return _j({"company_id": p["company_id"], "name": p["company"]["name"], "thesis_status": p["thesis_status"],
                   "health": p["health"], "drivers": p["drivers"], "checkpoints": p["checkpoints"],
                   "metrics": p["metrics"]})


@server.tool()
def list_drivers(position: str) -> str:
    """Drivers (key quantitative assumptions) of a position with their model/actual series and signal
    (no_data|on_track|ahead|behind|weakened). Use the driver id when linking interactions."""
    with _conn() as c:
        return _j(core.list_drivers(c, _pos_id(c, position)))


@server.tool()
def add_driver(position: str, name: str, unit: str | None = None, critical: bool = True,
               sensitivity: str | None = None, higher_is_better: bool = True, notes: str | None = None) -> str:
    """Add a driver the USER asked for (e.g. '幫台積電加一個 CoWoS 月產能的驅動因子'). critical drivers
    shake the thesis when weakened or behind model. For drivers you think should exist, use propose_driver."""
    with _conn() as c:
        return _j(core.add_driver(c, _pos_id(c, position), name, unit, int(critical), sensitivity, notes,
                                  int(higher_is_better), created_by=AGENT))


@server.tool()
def set_driver_value(driver_id: int, period: str, kind: str, value: float, source: str | None = None) -> str:
    """Record a driver value the USER dictated, typically model values from their spreadsheet.
    kind: model|actual. period: 2026 | 2026Q2 | 2026-05. For actuals you found in filings or news,
    use propose_driver_value instead."""
    with _conn() as c:
        return _j(core.set_driver_value(c, driver_id, period, kind, value, source, created_by=AGENT))


@server.tool()
def list_checkpoints(position: str | None = None, status: str | None = None) -> str:
    """Checkpoints: dated expected evidence. status: pending|confirmed|missed. Omit position for all.
    Each row has overdue (bool) and days_left."""
    with _conn() as c:
        pid = _pos_id(c, position) if position else None
        return _j(core.list_checkpoints(c, pid, status))


@server.tool()
def add_checkpoint(position: str, title: str, expected_by: str, evidence_needed: str | None = None,
                   driver_id: int | None = None) -> str:
    """Add a checkpoint the USER asked for. expected_by YYYY-MM-DD. For checkpoints you infer a thesis
    needs (e.g. from a company's own roadmap), use propose_checkpoint."""
    with _conn() as c:
        return _j(core.add_checkpoint(c, _pos_id(c, position), title, expected_by, evidence_needed, driver_id,
                                      created_by=AGENT))


@server.tool()
def set_metric(company: str, metric: str, period: str, kind: str, value: float, source: str | None = None) -> str:
    """Record a standard metric the USER dictated (usually model values). metric: revenue_m|revenue_q|
    gross_margin|opex|eps|shares. kind: model|actual. revenue_m needs a month period (2026-05); the others
    a quarter (2026Q2) or year. Revenue/opex in thousands, gross_margin 0..1, shares in thousands.
    For actuals you fetched (monthly revenue release, quarterly report) use propose_metric."""
    with _conn() as c:
        return _j(core.set_metric(c, core.resolve_company(c, company)["id"], metric, period, kind, value, source,
                                  created_by=AGENT))


@server.tool()
def get_metric_comparison(company: str) -> str:
    """Model vs actual for every standard metric, quarter run-rate from monthly revenue, and monthly YoY."""
    with _conn() as c:
        return _j(core.metric_comparison(c, core.resolve_company(c, company)["id"]))


@server.tool()
def list_templates() -> str:
    """Thesis templates (transformation|growth|cyclical|supply_chain) that pre-fill drivers and checkpoints."""
    return _j(core.list_templates())


@server.tool()
def apply_template(position: str, template: str) -> str:
    """Apply a thesis template to a position when the USER asks. Creates its drivers and checkpoints
    (checkpoint dates are offsets from today; tell the user to adjust them)."""
    with _conn() as c:
        return _j(core.apply_template(c, _pos_id(c, position), template, created_by=AGENT))


@server.tool()
def propose_driver(position: str, name: str, rationale: str, unit: str | None = None, critical: bool = True,
                   sensitivity: str | None = None, higher_is_better: bool = True) -> str:
    """Propose a driver the thesis should track (e.g. after reading a call transcript)."""
    with _conn() as c:
        payload = {"position_id": _pos_id(c, position), "name": name, "unit": unit, "critical": int(critical),
                   "sensitivity": sensitivity, "higher_is_better": int(higher_is_better)}
        return _j(core.create_proposal(c, "driver", payload, rationale, AGENT))


@server.tool()
def propose_driver_value(driver_id: int, period: str, value: float, rationale: str, source: str | None = None,
                         kind: str = "actual") -> str:
    """Propose an actual (or revised model) value for a driver from information you found.
    Say where it came from in source and why it matters in rationale."""
    with _conn() as c:
        payload = {"driver_id": driver_id, "period": period, "kind": kind, "value": value, "source": source}
        return _j(core.create_proposal(c, "driver_value", payload, rationale, AGENT))


@server.tool()
def propose_checkpoint(position: str, title: str, expected_by: str, rationale: str,
                       evidence_needed: str | None = None, driver_id: int | None = None) -> str:
    """Propose a checkpoint the thesis implicitly depends on (a milestone the company or a partner
    announced, a date by which a driver must show up in results)."""
    with _conn() as c:
        payload = {"position_id": _pos_id(c, position), "title": title, "expected_by": expected_by,
                   "evidence_needed": evidence_needed, "driver_id": driver_id}
        return _j(core.create_proposal(c, "checkpoint", payload, rationale, AGENT))


@server.tool()
def propose_checkpoint_resolution(checkpoint_id: int, status: str, rationale: str, evidence: str | None = None) -> str:
    """Propose marking a checkpoint confirmed or missed based on evidence you found. status: confirmed|missed."""
    with _conn() as c:
        payload = {"checkpoint_id": checkpoint_id, "status": status, "evidence": evidence}
        return _j(core.create_proposal(c, "checkpoint_resolve", payload, rationale, AGENT))


@server.tool()
def get_maintenance_status() -> str:
    """Last run of each maintenance job (backup, fetch_monthly, fetch_quarterly, fetch_shares, quality, morning)
    and the current data-quality report: edges without source, critical drivers without model values, holdings
    without invalidation or drivers, proposals pending >14 days, stale holdings, overdue checkpoints, TW
    companies whose monthly revenue is not current. Use it to tell the user what needs upkeep."""
    with _conn() as c:
        return _j({"last": {j: core.last_run(c, j) for j in ("backup", "fetch_monthly", "fetch_quarterly",
                                                             "fetch_shares", "quality", "morning")},
                   "quality": core.data_quality_report(c)})


@server.tool()
def fetch_official_data(job: str = "fetch-monthly") -> str:
    """Pull official facts from TWSE/TPEx open data and write them directly as actuals (created_by system:mops):
    job = fetch-monthly (monthly revenue for all tracked TW companies, plus previous month and last year's month)
        | fetch-quarterly (YTD income statement; single-quarter revenue_q/gross_margin/opex/eps derived when the
          previous quarter is stored) | fetch-shares (shares outstanding, thousands) | backup | quality.
    Allowed without a proposal because these are sourced facts, not judgments. Needs network."""
    import sys as _sys
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in _sys.path:
        _sys.path.insert(0, scripts)
    import maintain  # noqa: E402
    if job not in maintain.JOBS:
        return f"job must be one of {list(maintain.JOBS)}"
    key, fn = maintain.JOBS[job]
    with _conn() as c:
        try:
            summary, details = fn(c)
        except Exception as e:
            core.log_maintenance(c, key, "error", f"{type(e).__name__}: {e}")
            return f"error: {type(e).__name__}: {e}"
        core.log_maintenance(c, key, "ok", summary, details)
        return _j({"summary": summary, "details": details})


@server.tool()
def propose_metric(company: str, metric: str, period: str, value: float, rationale: str,
                   source: str | None = None, kind: str = "actual") -> str:
    """Propose an actual metric you fetched (monthly revenue release, quarterly report).
    metric: revenue_m|revenue_q|gross_margin|opex|eps|shares. Units as in set_metric."""
    with _conn() as c:
        payload = {"company_id": core.resolve_company(c, company)["id"], "metric": metric, "period": period,
                   "kind": kind, "value": value, "source": source}
        return _j(core.create_proposal(c, "metric", payload, rationale, AGENT))


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
