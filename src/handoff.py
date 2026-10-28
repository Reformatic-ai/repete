"""One self-describing record per cycle: what changed, what it cost, what is
still at risk, what is unresolved, and what runs next.

Why this exists (2026-09-08)
----------------------------
Borrowed, deliberately, from a tutorial that got this part right: *"save what
changed, actual outcomes, remaining risk, unresolved issues, and the exact
next job — even on a no-trade day."* The ledger already holds every one of
those facts, scattered across decision rows, outcome rows, health, the
drawdown rail and the scheduler's job table. Reconstructing "what is this bot
doing and what happens next" meant joining five sources by hand, which is why
nobody did it and why a week-long outage went unnoticed in August.

**Even on a no-trade day** is the load-bearing clause. A bot that records
nothing when it does nothing is indistinguishable from a bot that is dead —
this project has now been bitten by that twice (the 2026-07-24 crashed cycle
that stamped a fresh heartbeat, and the 2026-08-21→28 blocked-entry outage).
A handoff row on a quiet day is the difference between "quiet" and "silent".

Contract
--------
- **Never raises.** `record()` swallows everything. Instrumentation that can
  take down a trading cycle is a worse bug than the one it reports.
- **Its own event.** `cycle_complete`'s position in the ledger is load-bearing
  (`_finalize_cycle`, and the watchdog keys off it); this sits outside that
  contract exactly as `cycle_timing` does, and cannot disturb it.
- **Absent is never zero.** Every field that could not be computed is `None`
  and says so. A handoff claiming `unresolved: []` when health could not be
  read would be the flattering-instrument failure this repo keeps cataloguing.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("handoff")

_ET = "America/New_York"

# How far ahead to look for the next scheduled job. A week covers every job in
# the table including the Sunday-only decaycheck; past that the answer is
# "nothing is scheduled", which is itself worth recording.
_LOOKAHEAD_MINUTES = 7 * 24 * 60


def next_job(now: datetime | None = None) -> dict | None:
    """The next scheduled job after `now`, as {name, at_et, in_minutes}.

    Reuses `scheduler.due()` rather than reimplementing the cron predicate.
    Two copies of that logic would drift, and the copy in a *reporting* module
    would drift silently — it would keep printing a next job the scheduler no
    longer agrees with.

    Returns None when the job table cannot be imported or nothing is scheduled
    in the lookahead. None means "unknown", never "nothing runs".
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scripts = os.path.join(root, "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import scheduler
    except Exception as e:  # noqa: BLE001 — a reporting import must not escape
        log.debug("handoff: scheduler unavailable (%s)", e)
        return None

    now = (now or datetime.now(ZoneInfo(_ET))).replace(second=0, microsecond=0)
    for step in range(1, _LOOKAHEAD_MINUTES + 1):
        t = now + timedelta(minutes=step)
        for job in scheduler.JOBS:
            try:
                if scheduler.due(job, t):
                    return {"name": job[0],
                            "at_et": t.strftime("%Y-%m-%d %H:%M"),
                            "in_minutes": step}
            except Exception:  # noqa: BLE001 — one malformed row must not blind the rest
                continue
    return None


def _today_et(now: datetime | None = None) -> str:
    return (now or datetime.now(ZoneInfo(_ET))).strftime("%Y-%m-%d")


def build(cfg: dict, records: list[dict], *, equity: float | None,
          n_positions: int | None, now: datetime | None = None,
          rail_equity: float | None = None, rail_problem: str = "") -> dict:
    """Assemble the five fields. Pure — reads records, writes nothing.

    `rail_equity` (2026-10-28) is broker equity less the operator cost charged
    so far — what the drawdown rail measures. Omitted, it is `equity`."""
    today = _today_et(now)

    changed = {"entries": 0, "exits": 0, "refused": 0}
    outcomes = {"closed_today": 0, "realized_today": 0.0}
    saw_any_today = False
    for r in records:
        ts = str(r.get("ts") or "")
        if not ts.startswith(today):
            continue
        saw_any_today = True
        if r.get("type") == "decision":
            if r.get("executed"):
                if r.get("action") == "buy":
                    changed["entries"] += 1
                else:
                    changed["exits"] += 1
            else:
                changed["refused"] += 1
        elif r.get("type") == "outcome":
            outcomes["closed_today"] += 1
            pnl = r.get("pnl")
            if isinstance(pnl, (int, float)):
                outcomes["realized_today"] += float(pnl)
    outcomes["realized_today"] = round(outcomes["realized_today"], 2)
    if not saw_any_today:
        # No rows at all for today is different from rows that summed to zero.
        outcomes["realized_today"] = None

    remaining_risk: dict = {"open_positions": n_positions, "equity": equity,
                            "drawdown_pct": None, "headroom_pp": None,
                            "entries_blocked": None}
    try:
        import risk
        limit = float((cfg.get("risk") or {}).get("max_drawdown_pct", 10.0))
        dd = risk.drawdown_state(
            equity, limit,
            rail_equity=equity if rail_equity is None and not rail_problem
            else rail_equity, rail_problem=rail_problem)
        remaining_risk["drawdown_pct"] = dd.get("drawdown_pct")
        remaining_risk["entries_blocked"] = dd.get("engaged")
        if dd.get("drawdown_pct") is not None:
            remaining_risk["headroom_pp"] = round(
                limit - float(dd["drawdown_pct"]), 2)
    except Exception as e:  # noqa: BLE001
        log.debug("handoff: drawdown unavailable (%s)", e)

    unresolved: list[str] | None
    try:
        import health
        unresolved = list(health.status(cfg, now=now)["problems"])
    except Exception as e:  # noqa: BLE001
        log.debug("handoff: health unavailable (%s)", e)
        unresolved = None      # unknown, NOT "no problems"

    return {"date_et": today, "changed": changed, "outcomes": outcomes,
            "remaining_risk": remaining_risk, "unresolved": unresolved,
            "next_job": next_job(now)}


def record(ledger, cfg: dict, *, equity: float | None,
           n_positions: int | None, now: datetime | None = None,
           rail_equity: float | None = None,
           rail_problem: str = "") -> dict | None:
    """Write one `handoff` event. Never raises; returns the payload or None."""
    try:
        payload = build(cfg, ledger.all_records(), equity=equity,
                        n_positions=n_positions, now=now,
                        rail_equity=rail_equity, rail_problem=rail_problem)
        ledger.log_event("handoff", json.dumps(payload))
        return payload
    except Exception as e:  # noqa: BLE001 — the cycle outranks its own reporting
        log.warning("handoff record failed (%s) — cycle unaffected", e)
        return None
