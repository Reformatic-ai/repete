"""Hard risk rails — deterministic, enforced in code, NOT overridable by the LLM.

This module implements the controls the research identified as
non-negotiable (Knight Capital post-mortems, FINRA 2026 AI-agent
guidance, the Agentic Trading survey's 'supervisory controls'):

  * fixed-fractional position sizing
  * per-order dollar cap
  * max position concentration
  * max trades per day (order-rate circuit breaker)
  * daily loss limit -> flatten everything + write a HALT file
  * HALT file check: if present, the bot refuses to trade until you delete it
  * SWING GUARD: exits are blocked until a position is min_holding_days old,
    which makes same-day round trips (day trading) structurally impossible.
    The kill switch (flatten_all) is exempt — safety overrides the guard.
"""
import os
import json
import logging
import math
from datetime import date, datetime, timezone

from strategies.base import ENTRY_ACTIONS, EXIT_ACTIONS, sector_of

log = logging.getLogger("risk")

HALT_FILE = "HALT"
_TRADECOUNT_FILE = "memory/.trade_counts.json"


class RiskRejection(Exception):
    """Raised when a hard rail blocks an order.

    `rail` names WHICH rail, as a stable key — `heat`, `regime_exposure`,
    `zero_qty` and so on. The message already said this in prose, but
    `simulate_ensemble` caught every rejection in one handler and incremented a
    single undifferentiated counter, so the reason was thrown away at the exact
    moment it was known. Across four periods the bot sits ~91% in cash and
    nobody could say why (§40).

    Parsing the message string instead would be worse than the ignorance it
    replaces: messages carry formatted numbers and change freely, so a
    mis-bucketed census would read as a confident diagnosis. The tag is the
    fact; the message is the display.

    Defaults to `unattributed` rather than raising, so a new raise site that
    forgets a tag shows up as an unexplained bucket in the census instead of
    crashing a trading cycle. `tests/test_block_census.py` enumerates the raise
    sites so it also fails loudly in CI.
    """

    def __init__(self, message: str, rail: str = "unattributed"):
        super().__init__(message)
        self.rail = rail


# HALT is two different instructions wearing one filename (2026-08-02).
#
#   exits  — stop ADDING risk. Entries blocked; the cycle still runs and the
#            agent still works its exits. For "the market has gone mad" or "I
#            want out of this position but not at any price".
#   freeze — stop EVERYTHING. The cycle does not run at all. For when the bot
#            or the broker is the thing you distrust: halt.py's own usage
#            example is `./scripts/halt.sh "broker returning bad fills"`, and
#            sending sell orders into a broker you just halted over is the
#            opposite of a safety measure.
#
# Before this, HALT always meant `freeze` while halt.py's docstring claimed
# open positions kept "running to their normal stops and exits". They did not —
# PR #72 corrected the wording; this makes the original promise available as a
# real mode instead.
HALT_MODE_EXITS = "exits"
HALT_MODE_FREEZE = "freeze"
HALT_MODES = (HALT_MODE_EXITS, HALT_MODE_FREEZE)


def check_halt() -> bool:
    """Is a HALT engaged at all? Deliberately still a BOOLEAN.

    `health.py` and `watchdog.py` ask only this question, and `daily_posts.py`
    asks it through here. Widening what this returns would make one predicate
    mean different things in several modules — the §29 `max_order_value_usd: 0`
    trap. Callers that need the mode ask `halt_mode()`.

    (This docstring used to name `daily_posts.py` as a third module holding its
    own `HALT_FILE`. It never did — it calls this function. Corrected 2026-08-11
    while mapping the halt surface for F-14.)

    THIS READS THE CWD-RELATIVE `HALT_FILE`, AND THAT IS DELIBERATE. The
    monitors resolve their halt path from config (`statepaths.halt_path`) so a
    health check can be pointed at a QA fixture; the trading rail must not,
    because a config key able to move the kill switch is a way to disable it —
    `scripts/halt.py:80`, "a HALT engaged into some other directory is a stop
    button wired to nothing." The rail reads where halt.py writes, always, and
    `preflight._state_path_fails` refuses to start a cycle if the monitors have
    been pointed somewhere else.
    """
    return os.path.exists(HALT_FILE)


def halt_mode() -> str | None:
    """Which HALT is engaged, or None if there is no HALT file.

    Anything unreadable, unrecognised, or simply absent from the file reads as
    `freeze`, and that polarity is the whole point: a HALT engaged before this
    existed, or written by a version that did not know about modes, was pulled
    by someone who believed it stopped everything. Re-interpreting their halt as
    "actually, keep trading" on the strength of a missing line would be the
    worst possible default. An unknown halt is a total halt.
    """
    try:
        with open(HALT_FILE) as f:
            body = f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.critical("HALT file unreadable (%s) — treating it as a full freeze", e)
        return HALT_MODE_FREEZE
    for line in body.splitlines():
        if line.startswith("mode:"):
            mode = line.split(":", 1)[1].strip().lower()
            if mode in HALT_MODES:
                return mode
            log.critical("HALT file names unknown mode %r — treating it as a "
                         "full freeze", mode)
            return HALT_MODE_FREEZE
    return HALT_MODE_FREEZE          # legacy file, written before modes existed


def engage_halt(reason: str, mode: str = HALT_MODE_FREEZE):
    """Write the HALT file.

    `mode` defaults to FREEZE, not to the friendlier `exits`, so that a caller
    which never considered the question gets the conservative behaviour. The
    two callers that HAVE considered it say so explicitly: halt.py passes
    `exits` for an operator stop, and the daily-loss kill switch passes `freeze`
    (see _kill_switch_fired — it relies on HALT stopping the next cycle so it
    cannot re-enter its own flatten).
    """
    if mode not in HALT_MODES:
        log.critical("engage_halt got unknown mode %r — writing a full freeze",
                     mode)
        mode = HALT_MODE_FREEZE
    with open(HALT_FILE, "w") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} — {reason}\n"
                f"mode: {mode}\n"
                "Delete this file to re-enable trading (after you understand what happened).\n")
    log.critical("HALT ENGAGED (%s): %s", mode, reason)


# ---- a flatten the kill switch started and could not finish -----------------
#
# State lives IN THE HALT FILE, not in memory/, and that placement is the whole
# safety interlock rather than a convenience.
#
# The documented recovery for a kill-switch trip is `rm HALT` (docs/runbooks.md),
# and `halt.py --clear` does the same. If "a flatten is pending" lived in its own
# file it would SURVIVE that, and the next scheduled run would liquidate a book
# the operator had just decided to keep — an automatic sell nobody asked for, at
# the worst imaginable moment. Keeping it here makes "clear the halt" and "cancel
# the pending flatten" the same physical act, which is a guarantee rather than a
# convention. §29's rule again: one definition, one reader, no second file to
# fall out of sync.
_FLATTEN_MARK = "flatten: pending"
_ATTEMPT_MARK = "flatten_attempts:"


def _halt_body() -> str | None:
    try:
        with open(HALT_FILE) as f:
            return f.read()
    except OSError:
        return None


def flatten_pending() -> bool:
    """Did the kill switch fail to flatten, leaving positions open?

    NOTE THE POLARITY, which is the opposite of `halt_mode()` and deliberately
    so. There, an unreadable file means "we do not know, so STOP" — inaction is
    the safe side. Here the pending flag AUTHORISES AN AUTOMATIC LIQUIDATION, so
    an unreadable file must mean "do not act". Both resolve the same way
    underneath: when in doubt, take no risky action. Selling the book because a
    file failed to parse would be the worst possible reading of it.
    """
    body = _halt_body()
    return bool(body) and _FLATTEN_MARK in body


def flatten_attempts() -> int:
    """How many recovery attempts have already been spent."""
    for line in (_halt_body() or "").splitlines():
        if line.startswith(_ATTEMPT_MARK):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _rewrite_halt(transform) -> bool:
    """Apply `transform` to the HALT file's lines. False if there is no HALT.

    Never CREATES the file: every one of these markers is meaningless without an
    engaged halt, and a marker able to conjure its own HALT would be a way to
    start an automatic liquidation from nothing at all.
    """
    body = _halt_body()
    if body is None:
        return False
    lines = transform(body.splitlines())
    with open(HALT_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    return True


def mark_flatten_pending(detail: str) -> bool:
    """Record that positions remain open after the kill switch tried to flatten."""
    def _add(lines):
        if not any(ln.startswith(_FLATTEN_MARK) for ln in lines):
            lines.insert(1, f"{_FLATTEN_MARK} — {detail}")
            lines.insert(2, f"{_ATTEMPT_MARK} 0")
        return lines
    ok = _rewrite_halt(_add)
    if ok:
        log.critical("FLATTEN PENDING: %s", detail)
    return ok


def record_flatten_attempt() -> int:
    """Spend one attempt from the budget. Returns the new total."""
    n = flatten_attempts() + 1

    def _bump(lines):
        out = [ln for ln in lines if not ln.startswith(_ATTEMPT_MARK)]
        out.insert(1, f"{_ATTEMPT_MARK} {n}")
        return out
    _rewrite_halt(_bump)
    return n


def clear_flatten_pending() -> bool:
    """The book is flat. Drops the markers and LEAVES THE HALT ENGAGED — the
    daily-loss breach that caused all this is still a human's to clear."""
    def _drop(lines):
        return [ln for ln in lines
                if not ln.startswith(_FLATTEN_MARK)
                and not ln.startswith(_ATTEMPT_MARK)]
    return _rewrite_halt(_drop)


# A corrupt counter must not silently un-cap the day's trading. Absent file =
# genuinely no trades yet (fine); unparseable file = we do not know, so the
# rail assumes the worst. Same polarity as preflight: state integrity fails SAFE.
_TRADECOUNT_UNKNOWN = 10 ** 6


def _trades_today() -> int:
    try:
        with open(_TRADECOUNT_FILE) as f:
            data = json.load(f)
        return data.get(str(date.today()), 0)
    except FileNotFoundError:
        return 0                      # first run today — no trades yet
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log.critical("trade counter unreadable (%s) — treating the daily cap "
                     "as REACHED until it is repaired", e)
        return _TRADECOUNT_UNKNOWN    # fail CLOSED: the cap keeps binding


def record_trade():
    counts = {}
    try:
        with open(_TRADECOUNT_FILE) as f:
            counts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass                          # a fresh count is written below regardless
    key = str(date.today())
    counts = {key: counts.get(key, 0) + 1}   # keep only today
    os.makedirs(os.path.dirname(_TRADECOUNT_FILE), exist_ok=True)
    # Atomic: write a temp file, fsync, then rename. `open(..., "w")` truncates
    # first, so a crash mid-write previously left an empty/partial file — which
    # the reader then swallowed as 0 and the daily cap stopped binding.
    tmp = f"{_TRADECOUNT_FILE}.tmp{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(counts, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _TRADECOUNT_FILE)
    finally:
        if os.path.exists(tmp):       # rename failed — never leave litter
            os.unlink(tmp)


_HIGHWATER_FILE = os.path.join("memory", ".equity_highwater.json")

# Same polarity as _TRADECOUNT_UNKNOWN: if we cannot read the peak, we do not
# know how far below it we are, so the rail assumes the worst for one cycle.
# It self-heals — update_high_water() rewrites the file from live equity every
# cycle — so a corrupt file costs one cycle of entries, not a wedged bot.
_PEAK_UNKNOWN = float("inf")


def read_high_water() -> float:
    try:
        with open(_HIGHWATER_FILE) as f:
            v = float(json.load(f)["peak_equity"])
        return v if v > 0 else _PEAK_UNKNOWN
    except FileNotFoundError:
        return 0.0                    # never seeded — first run, no drawdown yet
    except (json.JSONDecodeError, ValueError, KeyError, TypeError, OSError) as e:
        log.critical("equity high-water unreadable (%s) — treating the drawdown "
                     "rail as BREACHED until the next cycle rewrites it", e)
        return _PEAK_UNKNOWN          # fail CLOSED


# ---- RAIL EQUITY (owner decision 2026-10-28) --------------------------------
#
# "Yes: make gates, backtests and the drawdown/retire rails use after-cost results
# first (repete4's override included). Entry cost filters later, bot by bot."
#
# The drawdown breaker, the daily-loss kill switch and the live kill measure the
# book AFTER the operator's simulated cost: rail equity = broker equity - every
# cost charged so far (memory/operator_fees.jsonl, read strictly). Broker equity
# stays the figure for sizing, the caps and every record the console reads — the
# console nets the cost off itself, and doing it here too would count it twice.
#
# main._rail_equity computes it ONCE per cycle, right after the broker read, and
# puts it on the cycle's account dict under RAIL_EQUITY_KEY. A dict without the key
# (the simulator before it charges anything, tests, a caller that predates the
# decision) has no operator cost, so rail equity IS broker equity. A key whose value
# is None means the fee ledger could not be read: the rails fail CLOSED.
RAIL_EQUITY_KEY = "rail_equity"
RAIL_LAST_EQUITY_KEY = "rail_last_equity"
COST_CHARGED_KEY = "operator_cost_charged"
RAIL_PROBLEM_KEY = "rail_problem"


def rail_equity(account: dict) -> float | None:
    """The equity the drawdown / daily-loss rails measure. None = unknown."""
    if RAIL_EQUITY_KEY in account:
        v = account[RAIL_EQUITY_KEY]
        return None if v is None else float(v)
    return float(account["equity"])


# The basis the stored peak is measured on. A file without it holds a GROSS peak
# written before 2026-10-28; update_high_water moves it once (see there).
HIGHWATER_BASIS = "after_cost"


def _high_water_record() -> dict | None:
    """The peak file as written, {} when it does not exist, None when unreadable."""
    try:
        with open(_HIGHWATER_FILE) as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def high_water_basis() -> str | None:
    """`after_cost`, or None for a legacy gross peak / no file / unreadable."""
    rec = _high_water_record()
    return (rec or {}).get("basis")


def update_high_water(equity: float, *, cost_charged: float | None = None,
                      gross_equity: float | None = None) -> float:
    """Ratchet the peak upward and persist it. Returns the current peak.

    Never ratchets DOWN: a drawdown rail measured against a peak that follows
    equity downward would never fire, which is the failure mode of every
    trailing metric that forgets to keep its high-water mark.

    `equity` is RAIL equity (after the operator's cost) since 2026-10-28.

    THE ONE-TIME MOVE. A peak file written before that decision holds a GROSS
    peak and no `basis`. The first call that knows the cost charged so far
    (`cost_charged`, which main passes every cycle) moves it once to
    `peak - cost_charged`, stamps `"basis": "after_cost"` and keeps the gross
    figure and the amount it moved by beside it, so the move is auditable and a
    rerun is a no-op: a stamped file is never moved again. A call without
    `cost_charged` cannot move a gross peak and leaves it unstamped, so the move
    can never be skipped by a caller that did not know the cost.

    `gross_equity` (broker equity) ratchets `peak_equity_gross` alongside, for
    the before-cost drawdown health shows. Display only: no rail reads it.
    """
    rec = _high_water_record()
    peak = read_high_water()
    if rec is None or peak is _PEAK_UNKNOWN or peak == _PEAK_UNKNOWN:
        peak, rec = 0.0, {}           # corrupt: reseed from live equity below
    out = {k: v for k, v in rec.items()
           if k in ("basis", "peak_equity_gross", "moved_from_gross",
                    "moved_by_cost", "moved_at")}
    if not rec:
        out["basis"] = HIGHWATER_BASIS          # seeded on the new basis
    elif rec.get("basis") != HIGHWATER_BASIS and cost_charged is not None:
        gross_peak = peak
        peak = gross_peak - float(cost_charged)
        out.update(basis=HIGHWATER_BASIS, peak_equity_gross=gross_peak,
                   moved_from_gross=gross_peak,
                   moved_by_cost=round(float(cost_charged), 4),
                   moved_at=datetime.now(timezone.utc).isoformat())
        log.warning("equity high-water moved to the after-cost basis once: "
                     "$%.2f - $%.2f operator cost = $%.2f",
                     gross_peak, float(cost_charged), peak)
    peak = max(peak, float(equity))
    if gross_equity is not None:
        out["peak_equity_gross"] = max(float(out.get("peak_equity_gross") or 0.0),
                                       float(gross_equity))
    os.makedirs(os.path.dirname(_HIGHWATER_FILE), exist_ok=True)
    tmp = f"{_HIGHWATER_FILE}.tmp{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump({"peak_equity": peak,
                       "updated": datetime.now(timezone.utc).isoformat(),
                       **out}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _HIGHWATER_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return peak


def drawdown_pct(equity: float, peak: float) -> float:
    """Peak-to-trough drawdown as a positive percentage. Pure — no I/O.

    Lives here rather than in pre_trade_checks so the backtester can call the
    exact same arithmetic against its own running peak. A drawdown rail that
    existed only on the live path would be divergence #9.
    """
    if peak is _PEAK_UNKNOWN or peak == _PEAK_UNKNOWN:
        return float("inf")
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100)


_SAME_AS_EQUITY = object()


def drawdown_state(equity: float | None, limit_pct: float, *,
                   rail_equity=_SAME_AS_EQUITY, rail_problem: str = "") -> dict:
    """Read-only view of the drawdown circuit breaker. NEVER writes.

    Why this exists (2026-08-03)
    ----------------------------
    §40 established that the breaker is a ONE-WAY LATCH and that this is a live
    defect, not a backtest artifact. `update_high_water` ratchets the peak up
    and never down — correct on its own terms, since a peak that followed
    equity downward could never fire — but combined with entries-blocked it has
    no recovery path:

        equity peaks at P -> a >=limit drawdown blocks every entry -> open
        positions exit normally and the book goes to cash -> in cash equity is
        FLAT at ~P*(1-limit) -> the peak stays P -> the drawdown stays >=limit
        -> the bot never buys again, for the rest of the run.

    In 2022-2026 that blocked 99.43% of every buy signal. Production has not
    tripped it yet (§40 measured 9.85pp of headroom), which is precisely why
    nobody has felt it.

    §41 (decay at unchanged sizing) and §44 (decay plus reduced sizing) both
    tested candidate FIXES and both were REJECTED, so `risk.drawdown_decay`
    stays absent and the latch stays. **This function does not change that and
    is not a fix.** It makes the state legible so a silent permanent kill
    switch becomes a loud one a human can clear. It moves no threshold and
    blocks nothing.

    ONE implementation on purpose: `health.status()` and `watchdog.check()`
    both read it. Two hand-rolled copies of this arithmetic would be free to
    disagree about when the rail is engaged, which is the class of divergence
    this repo numbers.

    `equity=None` means "not known" and is reported as such — an unknown
    drawdown must never read as a healthy one.

    BEFORE AND AFTER COST (owner decision 2026-10-28). `equity` is broker
    equity; `rail_equity` is broker equity less the operator cost charged so far
    — what the rail actually measures. Omitted, it is `equity` (no cost known).
    `drawdown_pct`, `headroom_pp` and `engaged` are the RAIL's view: after cost
    once the peak file carries `basis: after_cost`, gross while it is still a
    legacy gross peak (the live rail has not moved it yet, so gross against gross
    is what it is measuring). `drawdown_gross_pct` is broker equity against the
    gross peak, for display. `rail_equity=None` with `rail_problem` means the fee
    ledger could not be read: reported ENGAGED, because the live rail fails
    closed on exactly that.
    """
    out = {"known": False, "engaged": False, "equity": None, "peak_equity": None,
           "drawdown_pct": None, "limit_pct": float(limit_pct or 0),
           "headroom_pp": None, "note": "",
           "basis": None, "rail_equity": None, "peak_equity_gross": None,
           "drawdown_gross_pct": None, "rail_equity_unknown": False}
    if rail_equity is _SAME_AS_EQUITY:
        rail_equity = equity

    if not limit_pct:
        out["note"] = "rail disabled (max_drawdown_pct: 0)"
        out["known"] = True
        return out
    if equity is None:
        out["note"] = "no equity reading — drawdown unknown"
        return out

    peak = read_high_water()
    rec = _high_water_record() or {}
    out["known"] = True
    out["equity"] = float(equity)
    out["basis"] = rec.get("basis") or "gross (legacy, not yet moved)"
    after_cost = rec.get("basis") == HIGHWATER_BASIS

    # Polarity matches read_high_water's own: an unreadable peak fails CLOSED,
    # because we do not know how far below it we are. Reported as engaged so
    # the operator sees it, with a different note so it is not mistaken for a
    # real drawdown.
    if peak is _PEAK_UNKNOWN or peak == _PEAK_UNKNOWN:
        out["engaged"] = True
        out["note"] = ("equity high-water file unreadable — the rail fails "
                       "CLOSED and self-heals on the next cycle's write")
        return out

    out["peak_equity"] = float(peak)
    gross_peak = rec.get("peak_equity_gross") if after_cost else peak
    if isinstance(gross_peak, (int, float)) and gross_peak > 0:
        out["peak_equity_gross"] = float(gross_peak)
        out["drawdown_gross_pct"] = round(drawdown_pct(equity, gross_peak), 2)
    if after_cost:
        if rail_equity is None:
            # The fee ledger is unreadable, so after-cost equity is unknown. The
            # live rail fails CLOSED on exactly this, and so does the report.
            out["engaged"] = True
            # Flagged so drawdown_hint() can tell this from a real drawdown:
            # the peak reset that clears a real one is the wrong advice here.
            out["rail_equity_unknown"] = True
            out["note"] = ("operator fee ledger unreadable — rail equity "
                           "unknown, the rail fails CLOSED"
                           + (f" ({rail_problem})" if rail_problem else ""))
            return out
        out["rail_equity"] = float(rail_equity)
        equity = float(rail_equity)
    if peak <= 0:
        # Never seeded: first run, no peak to be below. Not a drawdown.
        out["drawdown_pct"] = 0.0
        out["headroom_pp"] = float(limit_pct)
        out["note"] = "high-water never seeded — no drawdown yet"
        return out

    dd = drawdown_pct(equity, peak)
    out["drawdown_pct"] = round(dd, 2)
    out["headroom_pp"] = round(limit_pct - dd, 2)
    out["engaged"] = dd >= limit_pct
    return out


HIGHWATER_RESET_HINT = (
    f"the peak only ratchets UP, so this cannot clear itself — see §40. "
    f"To reset after reviewing: rm {_HIGHWATER_FILE}")

# What to say instead when the rail is ENGAGED only because rail equity is
# unknown (2026-10-28, PR #168). That is not a drawdown: the operator fee log
# cannot be read strictly, the rail fails CLOSED, and it clears by itself at
# the first cycle that reads the log again. Printing the peak reset there would
# invite an operator to forgive a drawdown nobody reviewed, to fix a file.
RAIL_EQUITY_UNKNOWN_HINT = (
    "This is not a drawdown: the operator fee log cannot be read strictly, so "
    "the rail fails CLOSED until it can. Do NOT reset the peak for it; repair "
    "the fee log as docs/runbooks.md describes under \"rail_equity_unknown\", "
    "and the next cycle clears it.")


def drawdown_hint(state: dict) -> str:
    """The advice that goes with an ENGAGED drawdown rail (`drawdown_state`'s
    output). The peak reset for a real drawdown; the fee-log runbook when the
    rail is engaged only because rail equity is unknown. Shared by health.py
    and watchdog.py so the status line and the alert give the same advice."""
    if (state or {}).get("rail_equity_unknown"):
        return RAIL_EQUITY_UNKNOWN_HINT
    return HIGHWATER_RESET_HINT


def decay_clock(prev_trough: float | None, prev_bars: int,
                equity: float, peak: float) -> tuple[float | None, int]:
    """Advance the decay clock one bar. Pure. Returns (trough, bars_stable).

    The clock counts bars since equity last made a NEW LOW, not bars since the
    last high — and that distinction is the whole design.

    Counting from the last high looked right and is wrong: on a book that keeps
    falling, `bars_since_high` keeps growing, so the gap keeps decaying and the
    rail releases *during* the decline. Measured directly while writing the
    §41 tests: peak 100,000, equity fallen to 80,000, 40 bars after the high —
    the decayed peak lands at 87,071 and reports an 8.12% drawdown, under the
    10% cap. A 20% drawdown would have been reported as clear to trade.

    Counting from the last low inverts that. Every new low resets the clock, so
    a falling book stays blocked for as long as it keeps falling; only a book
    that has stopped falling starts earning its way back in. Recovering to a
    new high resets everything, which is the existing ratchet.
    """
    if equity >= peak:
        return (peak, 0)                       # new high: no drawdown at all
    if prev_trough is None or equity < prev_trough:
        return (equity, 0)                     # new low: the clock restarts
    return (prev_trough, prev_bars + 1)


def decayed_peak(peak: float, equity: float, bars_stable: int,
                 cfg: dict) -> float:
    """The high-water mark, decayed toward equity after a grace period. Pure.

    §40 established that the drawdown circuit breaker is a ONE-WAY LATCH:
    equity peaks at P, falls 10%, entries block, positions exit, the book goes
    to cash, cash equity is flat below P, the peak never decays, the drawdown
    stays >= 10% forever, and the bot never buys again. Across 2022-2026 that
    turned 245,213 buy signals into 29 trades, 99.43% of them stopped by this
    one rail. A circuit breaker that cannot re-close is a kill switch.

    Owner decision 2026-07-28: the peak decays toward equity. After
    `grace_bars` of STABILITY — bars since equity last made a new low, see
    `decay_clock` — the gap between peak and equity halves every
    `halflife_bars`. So the rail still fires on a real drawdown, keeps firing
    for as long as the book keeps falling, and stops firing some weeks after
    equity stabilises instead of never.

    Lives here, pure and shared, for the same reason `drawdown_pct` does: the
    simulator and the live path must not be able to disagree about the
    arithmetic, only about where the peak came from. A decay implemented on one
    side only would be divergence #11.

    Returns `peak` unchanged when disabled, inside the grace period, or when
    equity is at or above the peak — so with `enabled: false` this function is
    the identity and every prior gate result reproduces byte-identically.
    """
    dcfg = (cfg.get("risk") or {}).get("drawdown_decay") or {}
    if not dcfg.get("enabled"):
        return peak
    if peak is _PEAK_UNKNOWN or peak == _PEAK_UNKNOWN or peak <= equity:
        return peak

    grace = dcfg.get("grace_bars", 10)
    halflife = dcfg.get("halflife_bars", 20)
    if halflife <= 0 or bars_stable <= grace:
        return peak

    # Geometric decay of the GAP, not of the peak: decaying the peak itself
    # would keep shrinking it below equity on a recovering book and eventually
    # report a drawdown of zero from a peak that never existed.
    elapsed = bars_stable - grace
    return equity + (peak - equity) * (0.5 ** (elapsed / halflife))


def daily_loss_breached(account: dict, cfg: dict) -> bool:
    """True if today's P&L is below the configured loss limit.

    AFTER COST since 2026-10-28 (owner decision): today's rail equity (broker
    equity - every operator cost charged so far) against the day-start reference
    (the broker's `last_equity` - the cost charged BEFORE today, by the bot's day
    boundary; main._rail_equity puts both on the account). An account without
    them has no operator cost and reads exactly as before.

    Unknown rail equity (the fee ledger could not be read) falls back to GROSS
    here, never to "no breach": today's after-cost loss is the gross loss plus
    today's fees over a smaller base, so a gross breach is always an after-cost
    breach and this can under-fire but never over-fire. The cycle separately
    fails CLOSED on entries and alerts (main._rail_equity) — the flatten is not
    the right act for a bookkeeping file, the entry block is.
    """
    limit_pct = cfg["risk"]["daily_loss_limit_pct"]
    last = account["last_equity"]
    equity = account["equity"]
    rail_eq = rail_equity(account)
    rail_last = account.get(RAIL_LAST_EQUITY_KEY)
    if rail_eq is not None and rail_last is not None:
        equity, last = rail_eq, float(rail_last)
    if last <= 0:
        return False
    pnl_pct = (equity - last) / last * 100
    if pnl_pct <= -limit_pct:
        log.critical("Daily loss limit breached: %.2f%% (limit -%.2f%%)", pnl_pct, limit_pct)
        return True
    return False


def realized_annual_vol(bars: list[dict], period: int) -> float | None:
    """Annualized stdev of the last `period` daily log returns. None if thin."""
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < period + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - period, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(252)


def vol_scale(bars: list[dict] | None, vcfg: dict,
              strategy: str | None = None) -> float:
    """Volatility-target sizing multiplier: target_vol / realized_vol, clamped.
    1.0 whenever disabled, scoped to other strategies, or data is too thin.
    Adopted 2026-07-18 for meanrev only (frozen-snapshot gate; hurt tsmom)."""
    if not vcfg.get("enabled") or not bars:
        return 1.0
    strats = vcfg.get("strategies")
    if strats and strategy not in strats:
        return 1.0
    realized = realized_annual_vol(bars, vcfg.get("period", 20))
    if not realized or realized <= 0:
        return 1.0
    scale = vcfg.get("annual_vol", 0.20) / realized
    return min(max(scale, vcfg.get("min_scale", 0.5)), vcfg.get("max_scale", 1.5))


def _risk_sizing_active(rcfg: dict, strategy: str | None,
                        price: float, stop_price: float | None,
                        direction: str = "buy") -> bool:
    """Stop-distance sizing applies only when enabled, scoped to this
    strategy, and an actual protective stop is known on the correct side of
    price for the direction being sized: below price for a long, above price
    for a short. A stop on the wrong side (or equal to price — zero
    distance, which would divide by zero below) is not a usable stop, so
    sizing falls back to notional rather than activating on a number that
    doesn't describe a real risk distance. `direction` defaults to "buy" so
    every pre-Phase-2 caller — none of which pass it — is unaffected."""
    if not rcfg.get("enabled") or not stop_price:
        return False
    if direction == "short":
        if stop_price <= price:
            return False
    elif stop_price >= price:
        return False
    strats = rcfg.get("strategies")
    return not strats or strategy in strats


def size_order(account: dict, price: float, cfg: dict,
               bars: list[dict] | None = None,
               strategy: str | None = None,
               stop_price: float | None = None,
               direction: str = "buy") -> int:
    """Position sizing, then clamp by every cap. Returns whole-share qty.

    Two modes (per-strategy, gate-decided — see backtest_candidates.md §8):
      * notional (default): equity × risk_per_trade_pct, optionally scaled by
        the vol_target multiplier;
      * risk-based (risk_sizing.enabled + stop known): equity × risk_pct /
        stop-distance fraction, so every trade risks the same equity slice
        entry-to-stop. Replaces vol_target for that strategy — both normalize
        by realized vol and compounding them would double-count.

    `direction` ("buy" or "short", default "buy") tells the risk-based mode
    which side of price the stop sits on. A short's stop is ABOVE entry, so
    the entry-to-stop distance is (stop_price - price), not (price -
    stop_price) — the un-fixed formula went negative for a short, `dollars`
    went negative, and `qty` floored to 0 or less with no error and no log.
    The default keeps every existing (long-only) caller byte-identical.
    """
    r = cfg["risk"]
    equity = account["equity"]

    # A non-positive price would make `dollars // price` raise ZeroDivisionError
    # and take the whole cycle down. Found 2026-07-25 while testing the §28
    # floor; the bug predates it. Sizing nothing is the fail-safe answer — a
    # symbol whose price is 0 or negative is bad data, not an opportunity, and
    # the freshness/cross-check guards are what should catch it upstream.
    if price <= 0:
        return 0

    rscfg = r.get("risk_sizing") or {}
    if _risk_sizing_active(rscfg, strategy, price, stop_price, direction):
        # Magnitude, not signed subtraction: a short's stop is ABOVE price,
        # so (price - stop_price) alone would be negative here and propagate
        # a negative `dollars` into a 0-or-negative `qty` below — silently,
        # since int(dollars // price) never raises.
        stop_dist = (stop_price - price) if direction == "short" else (price - stop_price)
        stop_frac = stop_dist / price
        dollars = equity * rscfg.get("risk_pct", 0.1) / 100 / stop_frac
    else:
        dollars = equity * r["risk_per_trade_pct"] / 100      # fixed fractional
        dollars *= vol_scale(bars, r.get("vol_target") or {}, strategy)
    # §29 (2026-07-26, owner decision): max_order_value_usd is 0-disableable.
    #
    # It was $2,000 on ~$100k — a 2% ceiling on every position — and it fired
    # BEFORE every other rail, which is why portfolio heat, regime_exposure and
    # risk_sizing.risk_pct all measured as inert (§11's "1.0, 2.0 and 5.0 are
    # byte-identical", §18's "gross never nears the cap"). It also held average
    # deployment at 2.5% of capital, measured on the frozen snapshot.
    #
    # Disabling it does NOT remove per-name concentration control:
    # max_position_pct remains, and is deliberately NOT 0-disableable — a 0
    # there yields a ceiling of 0, i.e. no trades, which is the fail-safe
    # direction. Buying power still binds regardless.
    caps = [equity * r["max_position_pct"] / 100,               # concentration cap
            account["buying_power"]]
    if r.get("max_order_value_usd"):                            # 0 disables
        caps.append(r["max_order_value_usd"])                   # per-order cap
    ceiling = min(caps)
    dollars = min(dollars, ceiling)

    qty = int(dollars // price)

    # ONE-SHARE FLOOR (§28, off by default until gated). When the sizing budget
    # is smaller than a single share, this returns 0 and the name becomes
    # unbuyable — GS at $1,098 against a $999 notional budget was refused live
    # with the judge having APPROVED it at full size. No verdict and no rail
    # rejected that trade; whole-share arithmetic did.
    #
    # The floor is deliberately capped by `ceiling`, NOT by `dollars`: a single
    # share is allowed only when it still fits inside every hard rail. So it
    # can never breach the per-order cap, the concentration cap, or buying
    # power — it just stops "one share costs slightly more than the target
    # slice" from meaning "never trade this name".
    #
    # It lives HERE, in the function the backtester shares (backtest.py:705),
    # rather than in main.py — otherwise the simulator and the live bot would
    # size differently and this project would collect an eighth sim/live
    # divergence while fixing the sixth.
    #
    # The judge's authority needs no special case: main.py computes
    # `int(full_qty * review["scale"])`, so an approved entry (scale 1.0) keeps
    # its floored share while any downsize truncates 1 back to 0 exactly as
    # today. Owner decision 2026-07-25 — fix the arithmetic bug, honour every
    # downsize.
    if qty == 0 and r.get("min_one_share") and 0 < price <= ceiling:
        qty = 1
    return max(qty, 0)


def trail_stop(high_water: float, atr_value: float | None,
               cfg: dict, strategy: str | None = None) -> float | None:
    """Chandelier trail level: high-water-since-entry − trailing_atr_mult·ATR.
    None when the trail is off (mult 0/absent), scoped to other strategies
    (trailing_strategies — gate 2026-07-19 adopted it for tsmom ONLY), or ATR
    is unavailable. The caller RATCHETS — an existing stop is only ever
    replaced by a HIGHER one (backtest_candidates.md §7)."""
    b = cfg["risk"].get("brackets") or {}
    mult = b.get("trailing_atr_mult", 0)
    if not mult or not atr_value or atr_value <= 0:
        return None
    strats = b.get("trailing_strategies")
    if strats and strategy not in strats:
        return None
    return round(high_water - mult * atr_value, 2)


def rvol_blocked(bars: list, cfg: dict, strategy: str | None = None) -> bool:
    """§23: block an ENTRY whose bar lacks relative-volume confirmation.

    ONE implementation shared by live (`main.py`) and both simulators, on
    purpose. Four sim/live divergences have already cost real rework (missing
    rails §13, global counters §19b, fill timing §19a, symbol order §22); a
    filter re-implemented per call site would be the fifth.

    FAILS OPEN — a `None` rvol (short history, or a missing/zero volume
    baseline) permits the entry. A data gap must not silently halt all trading;
    the freshness and cross-check rails own that failure class, not this one.

    Threshold is per-strategy first, then global, then off:
        strategies.<name>.min_rvol  ->  risk.min_rvol  ->  0 (disabled)
    Never applied to exits: a position must always be able to leave.
    """
    threshold = None
    if strategy:
        threshold = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("min_rvol")
    if threshold is None:
        threshold = (cfg.get("risk") or {}).get("min_rvol", 0)
    if not threshold:
        return False
    period = int((cfg.get("risk") or {}).get("rvol_period", 20))
    # Local import: strategies/base.py imports nothing from risk, so this is
    # acyclic, but keeping it local keeps risk.py importable in isolation.
    from strategies.base import rvol as _rvol
    value = _rvol(bars, period)
    if value is None:
        return False                      # fail open — see docstring
    return value < float(threshold)


def extra_lookback_bars(cfg: dict) -> int:
    """Bars the RAILS need beyond what the strategies ask for, or 0.

    `strategies.max_lookback_bars` sums up `required_lookback` across strategy
    modules and nothing else, which was complete right up until a rail needed
    more history than any strategy. `contraction_blocked` needs
    `period + lookback` bars and fails OPEN below that — so under-fetching does
    not error, it silently disarms the filter on every bar.

    Returns 0 unless the contraction rail is switched on somewhere: globally,
    or by any single strategy's own override. Checking BOTH matters — a spec
    arm that sets only `strategies.tsmom.max_contraction_pctile` would
    otherwise arm a rail and starve it.
    """
    risk_cfg = cfg.get("risk") or {}
    thresholds = [risk_cfg.get("max_contraction_pctile", 0)]
    thresholds += [(params or {}).get("max_contraction_pctile", 0)
                   for params in (cfg.get("strategies") or {}).values()]
    if not any(thresholds):
        return 0
    return (int(risk_cfg.get("contraction_period", 20))
            + int(risk_cfg.get("contraction_lookback", 252)))


def contraction_blocked(bars: list, cfg: dict,
                        strategy: str | None = None) -> bool:
    """§58: block an ENTRY whose recent range is NOT contracted — the "coil".

    A PRECONDITION on entries the ensemble already takes, not a trigger. §17
    tried the trigger form (a Donchian breakout) and it was rejected with a
    profit factor of 0.874 excluding its three best trades. §21 then measured
    the trade-count levers exhausted, and §23 concluded that what remains is
    quality rather than count. This is the shape that conclusion points at, and
    it ships the way §23's own filter ships: wired, tested, and OFF.

    Structured exactly like `rvol_blocked` above, for the identical reason —
    ONE implementation called by live and by both simulators. Five sim/live
    divergences have already cost real rework; a filter re-implemented per call
    site would be the sixth.

    FAILS OPEN. A `None` percentile (short history) permits the entry. That is
    the safe polarity for a data gap, but it carries a trap of its own: a rail
    never handed enough bars blocks NOTHING while reporting as installed.
    `strategies.max_lookback_bars` is what stops that, and it is tested.

    Threshold is the percentile at or below which an entry is still allowed:
        strategies.<name>.max_contraction_pctile -> risk.max_contraction_pctile
        -> 0 (disabled)
    0 means OFF rather than "only the single quietest window in a year",
    matching `min_rvol`'s convention. The degenerate reading is not worth a
    second key. Never applied to exits: a position must always be able to leave.
    """
    threshold = None
    if strategy:
        threshold = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("max_contraction_pctile")
    if threshold is None:
        threshold = (cfg.get("risk") or {}).get("max_contraction_pctile", 0)
    if not threshold:
        return False
    risk_cfg = cfg.get("risk") or {}
    period = int(risk_cfg.get("contraction_period", 20))
    lookback = int(risk_cfg.get("contraction_lookback", 252))
    # Local import for the same reason as rvol above.
    from strategies.base import contraction_pctile as _pctile
    value = _pctile(bars, period, lookback)
    if value is None:
        return False                      # fail open — see docstring
    return value > float(threshold)


def _own_stop_mult(cfg: dict, strategy: str | None) -> float | None:
    """A strategy's own `stop_atr_mult`, or None when it does not set one.

    THE per-strategy stop-width lookup — `bracket_prices` and
    `unprotectable_entry` must answer from the same number, because the second
    exists to predict when the first will return None. Two lookups would let
    them disagree, and the disagreement is precisely an entry that passes the
    unprotectable guard and then degrades to a stopless market order.

    Added 2026-08-11 for `swing_sectors` (owner: "accept volatility" — a wider
    per-trade stop, NOT a portfolio-rail move). Every strategy that does not
    set the key keeps the global `risk.brackets` multipliers byte-identically.
    """
    if not strategy:
        return None
    own = ((cfg.get("strategies") or {}).get(strategy) or {}).get("stop_atr_mult")
    return float(own) if own else None


def unprotectable_entry(entry_price: float, atr_value: float | None,
                        cfg: dict, strategy: str | None = None) -> bool:
    """True when brackets are ON but this entry's stop would be non-positive.

    `brackets()` below returns None in that case and the caller degrades to a
    plain market order — a position with **no stop at all**. That inverts the
    safety property: it happens on the most volatile names in the universe,
    which are exactly the ones that most need a stop.

    Measured before this was written, so the cost is known rather than guessed:

        shipped 38-symbol universe    0 of  61,104 bars   (0.0000%)
        wide 500-symbol universe     25 of 803,787 bars   (0.0031%)
                                     APA, CAR, CZR, NCLH, PENN

    All five are real market data — the March 2020 crash, and a 2026 squeeze in
    CAR — at ATR/price ratios of 0.36–0.62, not corruption. A daily range half
    the size of the share price is not a swing setup.

    So this is a **provable no-op for the bot as shipped**: it cannot change one
    live decision on the current universe, and it closes the hole for any wider
    one. That measurement is why it ships without a gate.

    ENTRIES ONLY — a position must always be able to leave, the same rule
    `rvol_blocked` and `credit_blocked` follow. Fails OPEN when brackets are
    disabled or ATR is unavailable: there is then no stop to be missing, and the
    bracket path was never going to protect the trade anyway.
    """
    b = (cfg.get("risk") or {}).get("brackets") or {}
    if not b.get("enabled") or not atr_value or atr_value <= 0:
        return False
    # The WIDEST configured multiplier. If any vol regime could push the stop
    # below zero the name is unprotectable in that regime, and whether it is
    # blocked must not depend on which bucket happens to be current.
    # A strategy's own stop width REPLACES both (see _own_stop_mult): the
    # override is unconditional in bracket_prices, so the widest multiplier
    # that entry can actually receive IS the override.
    mult = (_own_stop_mult(cfg, strategy)
            or max(b.get("stop_atr_mult") or 0,
                   b.get("stop_atr_mult_high_vol") or 0))
    if mult <= 0:
        return False
    return round(entry_price - mult * atr_value, 2) <= 0


def _credit_ratios(hy: list, ig: list, ts: str, period: int):
    """(latest ratio, SMA of the last `period` ratios) at or before `ts`.

    None when there is not enough aligned history. Alignment is BY TIMESTAMP,
    not by index: one missing bar in either series would otherwise shift the two
    against each other and quietly compare different days.
    """
    ig_by_ts = {b.get("ts"): b.get("close") for b in ig}
    series = []
    for b in hy:
        bts = b.get("ts")
        if not bts or bts > ts:
            break                          # never read past the decision bar
        denom = ig_by_ts.get(bts)
        num = b.get("close")
        if not denom or denom <= 0 or not num or num <= 0:
            continue
        series.append(num / denom)
    if len(series) < period:
        return None
    window = series[-period:]
    return series[-1], sum(window) / period


def credit_blocked(credit_bars: dict | None, ts: str, cfg: dict) -> bool:
    """§31: block an ENTRY while credit risk appetite is deteriorating.

    The claim under test is that entries taken while high-yield credit weakens
    against investment grade are worse trades. The measure is the HYG:LQD close
    ratio against its own moving average — an input the traded symbol's own
    price history does not contain, which is the entire point. Every strategy
    here is otherwise a price-only rule, and `backtest_candidates.md:611`
    reports no OOS edge distinguishable from zero across all five of them.

    ONE implementation shared by live and both simulators, following
    `rvol_blocked` above. Five sim/live divergences have already cost real
    rework; a filter re-implemented per call site would be the sixth.

    FAILS OPEN. Missing bars, short history, a non-positive close, or the
    feature switched off all permit the entry. A credit-data outage must never
    silently halt trading — the freshness rails own that failure class.

    NEVER applied to exits: a position must always be able to leave, the same
    rule `rvol_blocked` follows.

    Config: `risk.credit_sma_period`; 0 or absent disables it entirely.

    `ts` bounds the window, so a backtest cannot read a ratio the live bot could
    not have seen at the same moment.
    """
    period = int((cfg.get("risk") or {}).get("credit_sma_period", 0) or 0)
    if period <= 0 or not credit_bars:
        return False
    hy = credit_bars.get("HYG") or []
    ig = credit_bars.get("LQD") or []
    if not hy or not ig:
        return False
    got = _credit_ratios(hy, ig, ts, period)
    if got is None:
        return False                       # fail open — see docstring
    latest, avg = got
    return latest < avg


def _asof_closes(bars: list, ts: str, n: int) -> list:
    """Last `n` closes with bar ts <= `ts`, oldest first. ts-bounded so a
    backtest cannot read a value the live bot could not have seen."""
    out = [b["close"] for b in bars if b.get("ts", "") <= ts]
    return out[-n:] if len(out) >= n else []


def financing_rate_pct(rate_bars: dict | None, ts: str, cfg: dict) -> float:
    """§64: the annualized borrow rate (percent) charged on negative cash.

    FAILS CLOSED, unlike every gate above — and that asymmetry is the point.
    A gate that fails open permits a trade; a financing model that fails open
    lends for FREE, which is divergence #16 (the §53 short leg was handed a
    cost-free borrow and still lost). Missing aux, short history or a
    non-positive print all fall back to `risk.margin.flat_rate_pct` — a
    deliberately punitive flat rate — never to zero.

    The aux series is ^IRX (13-week T-bill, annualized PERCENT — 4.85 means
    4.85%/yr), a market print that is never revised, so the as-of read is
    point-in-time by construction. Spread rides on top.
    """
    mcfg = (cfg.get("risk") or {}).get("margin") or {}
    spread = float(mcfg.get("spread_bps_annual", 150)) / 100.0
    flat = float(mcfg.get("flat_rate_pct", 6.0))
    series = (rate_bars or {}).get("^IRX") or []
    got = _asof_closes(series, ts, 1)
    if not got or got[0] <= -1.0:          # sanity floor, not a gate
        return flat
    return got[0] + spread


def macro_blocked(macro_bars: dict | None, ts: str, cfg: dict) -> bool:
    """§64: block an ENTRY while the macro regime series signals risk-off.

    The claim under test is that entries taken while unemployment trends up
    are worse trades — an input no traded symbol's price history contains,
    `credit_blocked`'s argument exactly. The measure: the latest as-of value
    of the configured series against its own trailing mean.

    The four properties `credit_blocked` pinned hold here too, deliberately:
    ONE implementation shared by every caller; `ts`-bounded (the aux is a
    daily as-of step series built from first-print vintages, and this reads
    only bars at or before `ts`); FAILS OPEN (missing series, short history
    or the feature off all permit the entry — freshness rails own outages);
    NEVER applied to exits.

    Config: `risk.macro_gate: {enabled, series, ma_days}`; disabled ships.
    """
    gcfg = (cfg.get("risk") or {}).get("macro_gate") or {}
    if not gcfg.get("enabled"):
        return False
    series = (macro_bars or {}).get(str(gcfg.get("series", "UNRATE"))) or []
    ma_days = int(gcfg.get("ma_days", 252) or 0)
    if ma_days <= 0:
        return False
    window = _asof_closes(series, ts, ma_days)
    if not window:
        return False                       # fail open — see docstring
    return window[-1] > sum(window) / len(window)


def rotate_scan_order(symbols: list, day=None) -> list:
    """Rotate a symbol list by date so first refusal circulates. §24.

    Contention is resolved first-come by scan position, so whoever is scanned
    first wins the last free slot and the last unit of the daily fill budget.
    Live built its scan list as `list(cfg["symbols"])`, which meant slot
    allocation was decided by **the order someone typed symbols into a YAML
    file** — SPY/QQQ/DIA/IWM sit at positions 0-3 of 38 and won essentially
    every contested slot. The live book confirmed it: all five held names came
    from config positions 0,1,2,3,7.

    Offsetting by `date.toordinal()` (not day-of-year, which discontinuously
    wraps at New Year) advances the start by exactly one symbol per calendar
    day, so coverage is even and every symbol leads equally often.

    DETERMINISTIC by design — a date, never randomness — so a re-run of any
    gate reproduces exactly. Relative order is preserved; only the starting
    point moves.

    `day` accepts None (today, UTC), a date, a datetime, or an ISO string.
    An unparseable value returns the list unchanged (fail open: a bad
    timestamp must not silently reshuffle the universe).
    """
    syms = list(symbols)
    if len(syms) < 2:
        return syms
    d = day
    if d is None:
        d = datetime.now(timezone.utc).date()
    elif isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00")).date()
        except ValueError:
            return syms
    elif isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        return syms
    offset = d.toordinal() % len(syms)
    return syms[offset:] + syms[:offset]


def scan_order(symbols: list, cfg: dict, day=None) -> list:
    """THE single entry point for scan order — live and both simulators.

    One implementation on purpose. Four sim/live divergences have already cost
    real rework (§13 missing rails, §19a fill timing, §19b global counters,
    §22 symbol order); scan order re-derived per call site would be the fifth,
    and §22 proved this exact quantity moves OOS return by 2.66pp.

    Off unless `risk.scan_rotation` is true, so behaviour is unchanged for any
    config that has not opted in.
    """
    if not (cfg.get("risk") or {}).get("scan_rotation", False):
        return list(symbols)
    return rotate_scan_order(symbols, day)


def cooldown_days_for(cfg: dict, strategy: str | None) -> int:
    """Re-entry cooldown days that apply to entries by this strategy
    (0 = none). Per-strategy scope — the 2026-07-19 gate adopted the
    cooldown for meanrev only (§9)."""
    ccfg = cfg["risk"].get("reentry_cooldown") or {}
    strats = ccfg.get("strategies")
    if strats and strategy not in strats:
        return 0
    return ccfg.get("days", 0)


def cooldown_blocked(last_exit_ts: str | None, now_ts: str,
                     cooldown_days: int) -> bool:
    """True when a NEW entry is still inside the same-ticker re-entry
    cooldown after that symbol's last exit (gate candidate, §9).
    Calendar days, matching the swing guard's semantics."""
    if cooldown_days <= 0 or not last_exit_ts:
        return False
    days = (datetime.fromisoformat(now_ts)
            - datetime.fromisoformat(last_exit_ts)).days
    return days < cooldown_days


def bracket_prices(entry_price: float, atr_value: float | None,
                   cfg: dict,
                   vol_bucket: str | None = None,
                   direction: str = "buy",
                   strategy: str | None = None) -> tuple[float, float | None] | None:
    """Deterministic stop/take-profit prices for a bracket entry.

    Long (`direction="buy"`, the default — every pre-Phase-2 caller):
    stop = entry − mult·ATR; tp = entry + take_profit_atr_mult·ATR (tp
    omitted when the multiplier is 0). Short (`direction="short"`): the
    geometry inverts — stop = entry + mult·ATR (a short loses money as price
    RISES, so its stop sits above entry), tp = entry − take_profit_atr_mult·
    ATR. `broker.bracket_market_order` (Phase 1) now refuses a short whose
    stop is not above entry, so a short bracket MUST use this geometry to be
    placeable at all. When `stop_atr_mult_high_vol` is configured and the
    market vol regime at entry is "high", that wider multiplier is used
    instead (fixed 2×ATR whipsaws in high-vol tape) — absent/0 keeps the
    single fixed multiplier. Returns None — caller degrades to a plain
    market order — when brackets are disabled, ATR is unavailable, or the
    price that can legitimately go non-positive on this side is not
    positive (see below). Computed AFTER the LLM review and the pre-trade
    rails; the LLM never sees these numbers.
    """
    b = cfg["risk"].get("brackets", {})
    if not b.get("enabled") or not atr_value or atr_value <= 0:
        return None
    mult = b["stop_atr_mult"]
    high_mult = b.get("stop_atr_mult_high_vol", 0)
    if high_mult and vol_bucket == "high":
        mult = high_mult
    # A strategy's own stop width wins UNCONDITIONALLY — over the base AND the
    # high-vol multiplier. The override exists because the strategy's thesis
    # priced its own volatility (swing_sectors: a days-to-weeks sector repair
    # must survive normal chop, so 2×ATR would stop it out of the very moves
    # it exists to hold through); letting the vol bucket re-tighten it would
    # re-impose exactly the judgment the override was written to replace.
    # `unprotectable_entry` above mirrors this rule — the two must not diverge.
    own = _own_stop_mult(cfg, strategy)
    if own:
        mult = own
    tp_mult = b.get("take_profit_atr_mult", 0)

    # The non-positive-price guard protects whichever leg can actually go
    # non-positive for this direction, not "the stop" unconditionally:
    #   - long:  stop = entry − mult·ATR shrinks toward/through 0 as ATR or
    #            mult grows; tp = entry + tp_mult·ATR is a sum of positives
    #            and can never be non-positive.
    #   - short: stop = entry + mult·ATR is a sum of positives and can never
    #            be non-positive; tp = entry − tp_mult·ATR is the one that
    #            shrinks toward/through 0.
    # Checking the wrong leg on either side would let a non-positive price
    # slip through (or reject a perfectly fine bracket on the other leg).
    if direction == "short":
        stop = round(entry_price + mult * atr_value, 2)
        tp = round(entry_price - tp_mult * atr_value, 2) if tp_mult else None
        if tp is not None and tp <= 0:
            log.warning("bracket take-profit would be non-positive (entry %.2f, "
                        "ATR %.2f) — falling back to plain market order",
                        entry_price, atr_value)
            return None
    else:
        stop = round(entry_price - mult * atr_value, 2)
        if stop <= 0:
            log.warning("bracket stop would be non-positive (entry %.2f, ATR %.2f) "
                        "— falling back to plain market order", entry_price, atr_value)
            return None
        tp = round(entry_price + tp_mult * atr_value, 2) if tp_mult else None
    return stop, tp


def bars_fresh(bars: list[dict], max_age_days: int) -> bool:
    """True when the newest bar is at most max_age_days calendar days old.

    Guards against the failure mode where a data API quietly returns a
    months-old window (it happened): trading decisions must never be
    computed from stale bars. max_age_days <= 0 disables the check.
    """
    if max_age_days <= 0:
        return True
    if not bars:
        return False
    last = datetime.fromisoformat(bars[-1]["ts"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - last
    return age.days <= max_age_days


def entry_drift_bps(signal_price: float, live_price: float) -> float:
    """Absolute drift between the price the signal saw and the live market,
    in basis points."""
    return abs(live_price - signal_price) / signal_price * 1e4


def entry_drift_ok(signal_price: float, live_price: float, cfg: dict) -> bool:
    """Order-level defense in depth behind bars_fresh (2026-07-21): even with
    fresh-looking bars, never ENTER when the live market has moved more than
    risk.max_entry_drift_bps away from the signal price — every 2026-07-16
    stale-bars fill (142-1725bps of phantom slippage) would have been blocked
    here. Entries only; exits are never blocked. <=0/absent disables."""
    cap = cfg["risk"].get("max_entry_drift_bps", 0)
    if cap <= 0 or signal_price <= 0:
        return True
    return entry_drift_bps(signal_price, live_price) <= cap


def swing_guard(entry_ts: str | None, cfg: dict):
    """Block exits on positions younger than min_holding_days (no day trading)."""
    min_days = cfg["risk"].get("min_holding_days", 0)
    if min_days <= 0 or not entry_ts:
        return
    entered = datetime.fromisoformat(entry_ts)
    age_days = (datetime.now(timezone.utc) - entered).days
    if age_days < min_days:
        raise RiskRejection(
            f"swing guard: position is {age_days}d old, minimum holding period is "
            f"{min_days}d — this bot does not day trade", rail="swing_guard")


def portfolio_heat(open_trades: dict, cfg: dict, equity: float) -> float:
    """Total open stop-risk in dollars (2026-07-21): what the whole book
    loses if EVERY open stop is hit. Per position: qty x the entry-to-stop
    MAGNITUDE from the recorded bracket stop; a position with no recorded
    stop contributes the standard per-trade risk fraction of equity
    (conservative fallback).

    The magnitude is direction-dependent: a long's stop sits below entry
    (entry - stop), a short's sits above it (stop - entry). Before this fix
    the formula was always (entry - stop) clamped at 0 — for a short that
    clamp fired on every position (entry - stop is negative for a correctly
    placed short stop), so a short's real risk contributed exactly 0.0 to
    the cap, invisibly. `rec["action"]` (as written by main.py's open_trades
    entries) selects the formula; a record with no `action` key — every
    record written before this fix — defaults to the long formula, so
    existing/legacy books are read exactly as before.

    The `max(..., 0.0)` clamp survives on BOTH sides, unchanged in spirit:
    it now catches a NONSENSICAL stop for whichever direction the record
    claims — a long with its stop above entry, or a short with its stop
    below entry — rather than doing double duty as "clamp bad longs" AND
    "zero out every short" the way the un-fixed single formula did."""
    heat = 0.0
    per_trade = equity * cfg["risk"].get("risk_per_trade_pct", 1.0) / 100
    for rec in (open_trades or {}).values():
        qty = rec.get("qty") or 0
        entry = rec.get("entry_price") or 0.0
        stop = (rec.get("order") or {}).get("stop_price")
        if stop and entry and qty:
            dist = (stop - entry) if rec.get("action") == "short" else (entry - stop)
            heat += qty * max(dist, 0.0)
        elif qty:
            heat += per_trade
    return heat


def strategy_live_stats(closed_trades: list[dict], strategy: str) -> dict:
    """Live closed-trade count and profit factor for one strategy."""
    trades = [t for t in closed_trades if t.get("strategy") == strategy]
    gross_win = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0)
    pf = (gross_win / gross_loss if gross_loss > 0
          else (float("inf") if gross_win > 0 else None))
    return {"n": len(trades), "pf": pf}


def after_cost_trades(closed_trades: list[dict], fees: dict) -> list[dict]:
    """Each closed trade with its OWN operator cost taken off its P&L. Pure.

    Owner decision 2026-10-28: outcome-based rules read after-cost results. A
    trade's cost is the fee charged on its entry fill plus its close, joined on
    the fee line's `ref` ("buy:<trade_id>", "close:<trade_id>" —
    operator_settings.trade_cost). `pnl` and `pnl_pct` are replaced (pnl_pct on
    the same entry notional the ledger used); `pnl_gross` keeps the strategy's
    own figure. The ledger record itself is never rewritten. `fees` empty is the
    identity, so a book with no operator cost reads exactly as before."""
    out = []
    for t in closed_trades:
        tid = t.get("trade_id")
        cost = round(fees.get(f"buy:{tid}", 0.0)
                     + fees.get(f"close:{tid}", 0.0), 4) if tid else 0.0
        if not cost:
            out.append(t)
            continue
        u = dict(t)
        u["pnl_gross"] = t.get("pnl")
        u["operator_cost"] = cost
        if isinstance(t.get("pnl"), (int, float)):
            u["pnl"] = round(float(t["pnl"]) - cost, 4)
        notional = abs(float(t.get("entry_price") or 0) * float(t.get("qty") or 0))
        if isinstance(t.get("pnl_pct"), (int, float)) and notional > 0:
            u["pnl_pct"] = float(t["pnl_pct"]) - cost / notional * 100
        out.append(u)
    return out


def live_kill_blocked(closed_trades: list[dict], strategy: str | None,
                      cfg: dict, *, fees: dict | None = None,
                      fees_problem: str = "") -> str | None:
    """Pre-registered live kill criteria (2026-07-21, enterprise-hardening):
    the live-side mirror of the backtest enablement gate. A strategy whose
    LIVE record over >= min_trades closed trades shows PF < pf_below stops
    ENTERING (exits always still run). Registered before any strategy is
    near the threshold, so it can't be argued with later. Returns the
    rejection reason, or None.

    AFTER COST since 2026-10-28 (owner decision). The criteria — min_trades and
    pf_below, config.yaml risk.live_kill — are UNCHANGED; only the P&L they read
    moved: each trade's own operator cost comes off it (after_cost_trades, joined
    on `ref`). `fees` is operator_settings.fees_by_ref(); None means no cost was
    supplied (reports, and callers that predate the decision). `fees_problem` set
    means the fee ledger could not be read: an enabled kill then fails CLOSED and
    refuses the entry, because an after-cost PF it cannot compute is not a pass.
    """
    kcfg = cfg["risk"].get("live_kill") or {}
    if not kcfg.get("enabled") or not strategy:
        return None
    if fees_problem:
        return (f"live kill fails CLOSED: the operator fee ledger is unreadable "
                f"({fees_problem}), so {strategy}'s after-cost record cannot be "
                f"computed — entries refused, exits unaffected")
    if fees:
        closed_trades = after_cost_trades(closed_trades, fees)
    s = strategy_live_stats(closed_trades, strategy)
    min_trades = int(kcfg.get("min_trades", 15))
    pf_below = float(kcfg.get("pf_below", 0.8))
    if s["n"] >= min_trades and s["pf"] is not None and s["pf"] < pf_below:
        basis = " after operator cost" if fees else ""
        return (f"live kill: {strategy} PF {s['pf']:.2f}{basis} over {s['n']} "
                f"live closed trades is below {pf_below} (pre-registered "
                f"criteria — entries retired, exits unaffected)")
    return None


def daily_returns(bars: list[dict]) -> list[float]:
    """Close-to-close simple returns, oldest first."""
    closes = [b["close"] for b in bars]
    return [(b - a) / a for a, b in zip(closes, closes[1:]) if a]


def dated_daily_returns(bars: list[dict]) -> dict:
    """Close-to-close returns keyed by the (later) bar's calendar date, so two
    symbols with different-length histories align by DATE, not list position.
    Tail-aligning raw return lists (as the old correlation path did) pairs
    mismatched sessions whenever bar counts differ (trading halt, recent
    listing, per-symbol stale-drop)."""
    out: dict = {}
    prev = None
    for b in bars or []:
        c = b.get("close")
        day = (b.get("ts") or "")[:10]
        if prev is not None and prev > 0 and c is not None and day:
            out[day] = (c - prev) / prev
        prev = c
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def correlated_position_count(cand_bars: list[dict], open_bars_map: dict,
                              lookback: int, threshold: float) -> int:
    """How many open positions co-move with the candidate (correlation heat,
    2026-07-21 — 'co-moving names are one bet'). Pairwise Pearson correlation
    of daily returns over the last `lookback` bars; positions with missing or
    too-short bars are skipped (fail-open per symbol)."""
    cand = dated_daily_returns(cand_bars)
    count = 0
    for bars in open_bars_map.values():
        other = dated_daily_returns(bars or [])
        shared = sorted(set(cand) & set(other))[-lookback:]
        if len(shared) < 3:
            continue  # too little overlap — fail open per position
        rho = _pearson([cand[d] for d in shared], [other[d] for d in shared])
        if rho is not None and rho >= threshold:
            count += 1
    return count


def net_exposure_pct(positions: dict, equity: float) -> float:
    """Signed exposure as a percentage of equity: longs positive, shorts
    negative. Distinct from GROSS, which sums magnitudes — a 130/30 book is
    160% gross and 100% net, and the two rails answer different questions."""
    if not equity:
        return 0.0
    return sum(p.get("market_value", 0.0)
               for p in positions.values()) / equity * 100


def sector_open_count(cfg: dict, symbol: str, positions: dict) -> int:
    """How many OTHER open positions sit in the same sector as `symbol`.

    THE single implementation, called by `pure_checks` and therefore by the
    live cycle and both simulators alike. `strategy_slots` is the cautionary
    precedent: its count is derived in two places (`risk.py` and
    `backtest.py`), and re-derived counters are the shape behind §13's missing
    rails, §19b's global counters and §22's symbol order. `scan_order`'s
    docstring states the rule outright — one entry point, because four sim/live
    divergences have already cost real rework.

    `symbol` itself is excluded: adding to a position already held is what
    `max_position_pct` governs, and counting it here would make the cap fire one
    name early on the book it is already part of. An unmapped symbol has no
    sector, so it never contributes to anyone's count.
    """
    sector = sector_of(cfg, symbol)
    if sector is None:
        return 0
    return sum(1 for s in positions
               if s != symbol and sector_of(cfg, s) == sector)


def pure_checks(action: str, symbol: str, qty: int, price: float,
                account: dict, positions: dict, cfg: dict,
                regime_label: str | None = None,
                strategy: str | None = None,
                strategy_open: int | None = None,
                peak_equity: float | None = None):
    """The side-effect-free subset of the rails (no HALT/trade-count file I/O).

    Shared with the offline backtester so simulated fills obey the exact same
    caps as live orders. Raises RiskRejection with a reason.
    """
    r = cfg["risk"]

    if qty <= 0:
        raise RiskRejection("computed quantity is zero (account too small for caps)", rail="zero_qty")

    order_value = qty * price
    if r.get("max_order_value_usd") and order_value > r["max_order_value_usd"]:
        raise RiskRejection(f"order value ${order_value:,.0f} exceeds cap ${r['max_order_value_usd']:,}", rail="order_value_cap")

    if action in ENTRY_ACTIONS:
        # §31 DRAWDOWN CIRCUIT BREAKER (2026-07-26). Entries only — exits ALWAYS
        # run, because a rail that trapped you in a losing book while it fell
        # would be the opposite of a risk control.
        #
        # This is the SOFT brake: stop adding risk. daily_loss_limit_pct is the
        # hard one — it flattens the book and engages HALT. Two different acts
        # for two different situations, deliberately not merged: a slow bleed to
        # -10% over weeks should stop new entries, not liquidate at the bottom.
        #
        # AFTER COST since 2026-10-28 (owner decision): measured on RAIL equity
        # (rail_equity: broker equity - the operator cost charged so far),
        # against a peak kept on the same basis. The simulator puts its own
        # after-cost equity under the same key, so the two cannot disagree
        # about which equity a drawdown is measured on. Unknown rail equity
        # (the fee ledger could not be read) fails CLOSED, exactly as an
        # unreadable peak does.
        dd_cap = r.get("max_drawdown_pct") or 0
        if dd_cap and peak_equity is not None:
            r_eq = rail_equity(account)
            if r_eq is None:
                raise RiskRejection(
                    "drawdown circuit breaker fails CLOSED: the operator fee "
                    "ledger is unreadable, so after-cost equity is unknown — "
                    "entries blocked, exits still run", rail="drawdown")
            dd = drawdown_pct(r_eq, peak_equity)
            if dd >= dd_cap:
                raise RiskRejection(
                    f"drawdown circuit breaker: {dd:.2f}% from peak "
                    f"${peak_equity:,.0f} (limit {dd_cap:.1f}%) — entries "
                    f"blocked, exits still run", rail="drawdown")

        # §29: 0 disables the count ceiling. Owner decision 2026-07-26 — "if
        # there's more to hold onto then hold onto it". Position COUNT is not
        # itself a risk measure: eight $250 positions and eight $25,000 ones
        # are the same number and wildly different exposure. What actually
        # bounds the book is max_portfolio_heat_pct (total stop-risk),
        # max_position_pct (per name), the correlation cap (co-moving names are
        # one bet) and buying power — all of which stayed on, and all of which
        # only START binding once the $2k order clamp is gone.
        if (r["max_open_positions"] and len(positions) >= r["max_open_positions"]
                and symbol not in positions):
            raise RiskRejection(f"max open positions reached ({r['max_open_positions']})", rail="max_open_positions")
        # Per-strategy slot allocation (§13). The global cap above is still a
        # hard ceiling over the whole book; this only ever makes a strategy's
        # own limit TIGHTER. Rationale (§12 + live evidence): a single shared
        # pool lets a slow trend strategy hold every slot for weeks and starve a
        # fast mean-reversion one, which is what stalled evidence velocity.
        # Absent config, behaviour is exactly as before.
        strat_cap = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("max_open_positions") if strategy else None
        if (strat_cap and strategy_open is not None
                and strategy_open >= strat_cap and symbol not in positions):
            raise RiskRejection(
                f"max open positions for {strategy} reached ({strat_cap})", rail="strategy_slots")
        # abs(): Alpaca reports a SHORT's market_value as negative, so raw
        # addition reads -$9,000 + $2,000 as a $7,000 position and the per-name
        # cap stops binding on exactly the side where it matters. Magnitude is
        # what concentration means.
        existing = abs(positions.get(symbol, {}).get("market_value", 0.0))
        if existing + order_value > account["equity"] * r["max_position_pct"] / 100:
            raise RiskRejection(f"would exceed {r['max_position_pct']}% concentration cap on {symbol}", rail="position_cap")

        recfg = r.get("regime_exposure") or {}
        if (recfg.get("enabled") and regime_label
                and regime_label.startswith("down")):
            # abs() for the same reason: a long and a short would otherwise net
            # toward zero and a 100%-gross book would report as 0%. GROSS is a
            # sum of magnitudes; net is a different number with its own rail.
            gross = sum(abs(p.get("market_value", 0.0))
                        for p in positions.values())
            cap = account["equity"] * recfg.get("down_max_gross_pct", 50) / 100
            if gross + order_value > cap:
                raise RiskRejection(
                    f"down-regime exposure cap: gross ${gross + order_value:,.0f} "
                    f"would exceed {recfg.get('down_max_gross_pct', 50)}% of equity", rail="regime_exposure")

        # NET exposure band (130/30). DIRECTIONAL ON PURPOSE: `max` constrains
        # buys, `min` constrains shorts. A two-sided band would be an outage —
        # §48 measured deployment at 4.72% with the drawdown rail engaged, and
        # a floor applied to buys would reject every entry, leaving a book
        # below the band permanently unable to climb back into it.
        #
        # An order that moves net TOWARD the band is never blocked.
        band = r.get("net_exposure_pct") or {}
        # `account["equity"]` guards the same way `net_exposure_pct` guards
        # its own division: a zero (or missing) equity reading must not raise
        # ZeroDivisionError out of a risk rail. In practice size_order() would
        # already have floored qty to 0 and pure_checks raises zero_qty before
        # this ever runs, so this is belt-and-suspenders, not a load-bearing
        # path.
        if band and account["equity"]:
            net = net_exposure_pct(positions, account["equity"])
            delta_pct = order_value / account["equity"] * 100
            projected = net + (delta_pct if action == "buy" else -delta_pct)
            hi, lo = band.get("max"), band.get("min")
            if action == "buy" and hi is not None and projected > hi:
                raise RiskRejection(
                    f"net exposure band: buying would take net to "
                    f"{projected:.1f}% (ceiling {hi}%)", rail="net_exposure")
            if action == "short" and lo is not None and projected < lo:
                raise RiskRejection(
                    f"net exposure band: shorting would take net to "
                    f"{projected:.1f}% (floor {lo}%)", rail="net_exposure")

    # SIGN, not presence: `symbol not in positions` passes for a symbol held
    # SHORT, and a "sell" against a short would not be a no-op — it would
    # submit a SELL that ADDS to the short. Same bug class the abs() calls
    # above exist to prevent, just checked at the sign level instead of the
    # magnitude level. market_value <= 0 catches both causes: no position at
    # all (0.0) and an existing short (negative).
    if action == "sell" and positions.get(symbol, {}).get("market_value", 0.0) <= 0:
        raise RiskRejection(f"no long position in {symbol} to sell — holding "
                            f"none or short (state desync guard)", rail="desync_sell")

    # The mirror. `symbol not in positions` passes for a symbol held LONG, and
    # a "cover" against a long would not be a no-op — it would submit a BUY
    # that ADDS to the long. market_value >= 0 catches both causes: no
    # position at all (0.0) and an existing long (positive).
    if action == "cover" and positions.get(symbol, {}).get("market_value", 0.0) >= 0:
        raise RiskRejection(f"no short position in {symbol} to cover — holding "
                            f"none or long (state desync guard)", rail="desync_cover")

    # DIRECTION CONFLICT. A long book and a short book must never hold the same
    # name in opposite directions: the two legs would pay each other's spread
    # and commissions to express no view, the net position is whatever the
    # arithmetic happens to leave, and every per-symbol number downstream
    # (P&L attribution, fill quality, the trade plan) is computed per TRADE and
    # would describe a position that does not exist in isolation.
    #
    # CORRECTION, 2026-08-05, when the xsmom short leg was actually written.
    # This comment used to claim the legs were "mechanically disjoint" because
    # `reclaim` only buys ABOVE the 200-DMA and the short leg "will never short
    # above it". THE SHORT LEG AS BUILT HAS NO 200-DMA CONDITION — it is a pure
    # cross-sectional rank plus a momentum sign — so that claim is false, and it
    # was false in the flattering direction:
    #
    #   a reclaim BUY is a name long depressed and now recovering. Twelve-month
    #   momentum is still deeply negative for exactly such a name, which is what
    #   puts it in xsmom's bottom quartile. The two legs are not merely
    #   non-disjoint, they are ATTRACTED TO THE SAME NAMES, and 23 of xsmom's 30
    #   rankable symbols are in reclaim's 127-name universe.
    #
    # SECOND CORRECTION, 2026-08-05 (§54). The paragraph that replaced the
    # "mechanically disjoint" claim above introduced a subtler false one of its
    # own. It said this rail "is the ONLY thing preventing the conflict, and
    # whichever strategy reaches the name first takes it while the other is
    # REFUSED HERE". The first half is wrong and the second half is wrong.
    #
    # The other strategy is not refused here. IT IS NEVER CONSULTED:
    #
    #   a held symbol routes to `pos["owner"]` and the branch `continue`s
    #   (main.py:1615-1652, backtest.py:1157-1181), so no second strategy is
    #   ever offered a name that is already held, in either direction; and the
    #   entry loop `break`s on the first strategy to claim a FLAT name
    #   (main.py:1742, backtest.py:1266), so two strategies cannot queue
    #   opposite orders on one name in one cycle either.
    #
    # OWNERSHIP ROUTING is what prevents a long and a short in the same name.
    # This rail is a LAST-GATE BACKSTOP for a caller that did not come through
    # the cycle — the same argument `pre_trade_checks` makes for `halt` in its
    # own comment, and the realistic trigger is a broker-side position the bot
    # did not open, where the account and the ownership map disagree.
    #
    # The practical consequence, and the reason this correction is worth its
    # length: a backtest census can NEVER count this rail, because the simulator
    # cannot reach it. §54 was designed to read
    # `census["blocked"]["direction_conflict"]` as its headline measurement and
    # had to be rewritten. A zero there means "unreachable", not "rare".
    # Asserted, with the mutation proofs, in
    # tests/test_reclaim_short_collision.py — which is what must go red if any
    # of this stops being true.
    if action in ENTRY_ACTIONS:
        mv = positions.get(symbol, {}).get("market_value", 0.0)
        if action == "short" and mv > 0:
            raise RiskRejection(
                f"cannot short {symbol} while holding it long "
                f"(${mv:,.0f}) — close the long first",
                rail="direction_conflict")
        if action == "buy" and mv < 0:
            raise RiskRejection(
                f"cannot buy {symbol} while holding it short "
                f"(${mv:,.0f}) — cover the short first",
                rail="direction_conflict")

    # SECTOR CONCENTRATION. Entries only; an exit must never be blocked.
    scfg = r.get("sector_concentration") or {}
    if action in ENTRY_ACTIONS and scfg.get("enabled"):
        cap = scfg.get("max_per_sector")
        sector = sector_of(cfg, symbol)
        # An UNMAPPED symbol is unconstrained. The core universe carries no
        # sector map, so this rail is inert for every pre-existing strategy —
        # which is why it can be added without re-gating any of them. Treating
        # "no sector" as a sector would instead lump all 38 core names together
        # under one cap and silently throttle the whole ensemble.
        if cap and sector is not None:
            n = sector_open_count(cfg, symbol, positions)
            if n >= cap:
                raise RiskRejection(
                    f"sector concentration cap: {n} open position(s) already "
                    f"in {sector} (max {cap}) — co-moving names are one bet",
                    rail="sector_concentration")


# The rails pure_checks owns, in the order it evaluates them. THE ORDER IS THE
# CONTRACT: rail_census's first True must equal the rail pure_checks raises,
# and tests/test_block_census.py asserts this tuple covers every rail= site
# inside pure_checks. Adding a rail there without adding it here fails CI —
# which is what RiskRejection's docstring has promised since 2026-08-02 and
# what nothing enforced until 2026-08-22.
PURE_RAILS = ("zero_qty", "order_value_cap", "drawdown", "max_open_positions",
              "strategy_slots", "position_cap", "regime_exposure",
              "net_exposure", "desync_sell", "desync_cover",
              "direction_conflict", "sector_concentration")

# Rails that live OUTSIDE pure_checks and are deliberately NOT censused, each
# for a reason that is about inputs, never about importance:
#
#   halt, daily_cap     read FILES (the HALT file, the trade-count file). A
#                       read-only census must never stat the kill switch.
#   heat, correlation   in pre_trade_checks, which does that same file I/O.
#   swing_guard         called from elsewhere entirely.
#   live_kill           needs the CLOSED-TRADE HISTORY. Censusing it would
#                       make a pure function read the ledger.
#   entry_drift         needs a LIVE QUOTE — a network call, and the one rail
#                       here that is fail-OPEN (a quote outage is not a bad
#                       price; see the README's amber path).
#   downsize_zero_qty   happens AFTER the judge shrinks an order, so it cannot
#                       exist at census time — the census runs on the signal's
#                       qty, not the judged one.
#
# Named rather than merely absent so a new rail added outside cannot silently
# look covered — test_block_census.py asserts this partition is exhaustive
# across ALL of src/, not just this file. That widening (2026-08-23) is what
# found `entry_drift` and `downsize_zero_qty`: both had been live-path rails
# for months, registered nowhere, and invisible to every partition check.
NON_PURE_RAILS = ("halt", "daily_cap", "heat", "correlation", "swing_guard",
                  "live_kill", "entry_drift", "downsize_zero_qty")


def rail_census(action: str, symbol: str, qty: int, price: float,
                account: dict, positions: dict, cfg: dict,
                regime_label: str | None = None,
                strategy: str | None = None,
                strategy_open: int | None = None,
                peak_equity: float | None = None) -> dict:
    """Which of pure_checks' rails WOULD refuse this order — all of them.

    pure_checks raises on the FIRST failure, so every rail after it is never
    evaluated and the ledger records one key. That answers "what stopped this
    trade" and can never answer "what else would have" — the audit's Phase 4
    question, and the reason §48's "the drawdown rail blocked 94.58% of
    signals" is a statement about which rail fired first, not how many would.

    Returns {rail: True | False | None}:
        True  — would refuse
        False — evaluated and cleared
        None  — DOES NOT APPLY to this action, or is disabled by config.

    None rather than False for inapplicable rails, deliberately: False reads as
    "checked and cleared" and would overstate how much checking happened. A
    drawdown rail is not "cleared" on a sell; it was never asked.

    READ-ONLY AND SIDE-EFFECT-FREE, exactly as pure_checks is. It decides
    NOTHING — pure_checks remains the sole authority and this must never be
    substituted for it. A boolean that changed whether a rail raises would be
    a way to disable a rail; see statepaths.py on why the HALT path is not
    configurable.

    Deliberately a PARALLEL implementation rather than a refactor of
    pure_checks onto a shared registry. That refactor is 200 lines of
    incident-derived logic pinned by five test files, and doing it before
    test_rail_census_agrees_with_pure_checks exists and has survived a
    mutation is how a rail inverts silently.
    """
    r = cfg["risk"]
    entry = action in ENTRY_ACTIONS
    out: dict = {k: None for k in PURE_RAILS}

    out["zero_qty"] = qty <= 0

    order_value = qty * price
    cap_usd = r.get("max_order_value_usd")
    out["order_value_cap"] = (order_value > cap_usd) if cap_usd else None

    if entry:
        dd_cap = r.get("max_drawdown_pct") or 0
        if dd_cap and peak_equity is not None:
            r_eq = rail_equity(account)
            out["drawdown"] = (r_eq is None
                               or drawdown_pct(r_eq, peak_equity) >= dd_cap)

        max_open = r["max_open_positions"]
        out["max_open_positions"] = (
            bool(max_open) and len(positions) >= max_open
            and symbol not in positions) if max_open else None

        strat_cap = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("max_open_positions") if strategy else None
        if strat_cap and strategy_open is not None:
            out["strategy_slots"] = (strategy_open >= strat_cap
                                     and symbol not in positions)

        existing = abs(positions.get(symbol, {}).get("market_value", 0.0))
        out["position_cap"] = (existing + order_value
                               > account["equity"] * r["max_position_pct"] / 100)

        recfg = r.get("regime_exposure") or {}
        if (recfg.get("enabled") and regime_label
                and regime_label.startswith("down")):
            gross = sum(abs(p.get("market_value", 0.0))
                        for p in positions.values())
            cap = account["equity"] * recfg.get("down_max_gross_pct", 50) / 100
            out["regime_exposure"] = gross + order_value > cap

        band = r.get("net_exposure_pct") or {}
        if band and account["equity"]:
            net = net_exposure_pct(positions, account["equity"])
            delta_pct = order_value / account["equity"] * 100
            projected = net + (delta_pct if action == "buy" else -delta_pct)
            hi, lo = band.get("max"), band.get("min")
            if action == "buy" and hi is not None:
                out["net_exposure"] = projected > hi
            elif action == "short" and lo is not None:
                out["net_exposure"] = projected < lo

        mv = positions.get(symbol, {}).get("market_value", 0.0)
        out["direction_conflict"] = ((action == "short" and mv > 0)
                                     or (action == "buy" and mv < 0))

        scfg = r.get("sector_concentration") or {}
        if scfg.get("enabled"):
            cap = scfg.get("max_per_sector")
            sector = sector_of(cfg, symbol)
            if cap and sector is not None:
                out["sector_concentration"] = (
                    sector_open_count(cfg, symbol, positions) >= cap)

    if action == "sell":
        out["desync_sell"] = positions.get(symbol, {}).get("market_value", 0.0) <= 0
    if action == "cover":
        out["desync_cover"] = positions.get(symbol, {}).get("market_value", 0.0) >= 0

    return out


def first_refusing_rail(census: dict) -> str | None:
    """The rail pure_checks would raise, read off a census. None if it clears.

    Evaluation order is PURE_RAILS, not dict order, so this stays correct if
    the census dict is ever rebuilt or reordered.
    """
    for rail in PURE_RAILS:
        if census.get(rail) is True:
            return rail
    return None


def pre_trade_checks(action: str, symbol: str, qty: int, price: float,
                     account: dict, positions: dict, cfg: dict,
                     entry_ts: str | None = None,
                     regime_label: str | None = None,
                     bars_map: dict | None = None,
                     open_trades: dict | None = None,
                     candidate_stop: float | None = None,
                     strategy: str | None = None):
    """Last-stage gate every order must pass. Raises RiskRejection with a reason."""
    # ENTRIES ONLY (2026-08-02). This check was previously unreachable: under a
    # HALT the cycle returned in _bootstrap_cycle before any signal existed, so
    # nothing ever arrived here to be refused. `halt_mode() == "exits"` makes it
    # live, and an unguarded version would refuse the very sells that mode was
    # added to allow — a rail blocking an exit is a rail enlarging risk (§31 for
    # the drawdown breaker, #69 for the judge, #72 for the daily cap; fourth
    # rail, same rule).
    #
    # Still raised for buys even though the cycle also blocks entries upstream.
    # That redundancy is deliberate: it is the last gate before an order, and it
    # holds even for a caller that never went through _run_cycle.
    if action in ENTRY_ACTIONS and check_halt():
        raise RiskRejection("HALT file present — trading disabled", rail="halt")
    # §29: 0 disables. This is no longer a risk rail — it is a RUNAWAY GUARD.
    # Set well above observed demand (~15 buy signals/day live) so it never
    # refuses a real opportunity, but still bounds an API loop, a bad feed, or
    # a signal bug that would otherwise place orders until buying power ran out.
    #
    # ENTRIES ONLY (2026-08-02). Until now this had no action guard, and
    # `record_trade()` counts sells as well as buys, so a day that reached the
    # cap refused the next EXIT and left the position open past the signal that
    # said to close it. Worse, `_trades_today()` fails CLOSED on an unreadable
    # counter — right for entries, and catastrophic for exits, where one corrupt
    # JSON file would have blocked every close until a human repaired it.
    #
    # Every hazard in the rationale above is entry-side: a loop, a bad feed and
    # a signal bug all place ORDERS until buying power runs out. None of them is
    # a reason to refuse a sell. A control that traps a position through its own
    # limit has enlarged risk, which is the shape PR #69 refused for the judge
    # and §31 refused for the drawdown breaker. Same reasoning, third rail.
    _cap_day = cfg["risk"].get("max_trades_per_day") or 0
    if action in ENTRY_ACTIONS and _cap_day and _trades_today() >= _cap_day:
        raise RiskRejection(f"max trades per day reached ({_cap_day})", rail="daily_cap")

    # Count THIS strategy's currently-open positions for its slot allocation.
    strategy_open = None
    if strategy and open_trades is not None:
        strategy_open = sum(1 for rec in open_trades.values()
                            if rec.get("strategy") == strategy)
    # §31: ratchet the peak BEFORE checking against it, so a new high never
    # reads as a drawdown, and so a corrupt file self-heals within one cycle.
    # Runs for sells too — the peak must keep tracking even while entries are
    # blocked, otherwise the breaker could never clear itself.
    #
    # On RAIL equity (after the operator cost) since 2026-10-28, with the cost
    # charged so far passed along so a legacy gross peak is moved first, never
    # ratcheted on the wrong basis. Unknown rail equity writes nothing: the
    # stored peak is READ and pure_checks fails the entry closed.
    peak_equity = None
    if cfg["risk"].get("max_drawdown_pct"):
        r_eq = rail_equity(account)
        if r_eq is None:
            peak_equity = read_high_water()
        else:
            peak_equity = update_high_water(
                r_eq, cost_charged=account.get(COST_CHARGED_KEY),
                gross_equity=account.get("equity"))

    pure_checks(action, symbol, qty, price, account, positions, cfg,
                regime_label=regime_label, strategy=strategy,
                strategy_open=strategy_open, peak_equity=peak_equity)

    # Portfolio heat cap (2026-07-21): total open stop-risk plus this entry's
    # risk must stay under max_portfolio_heat_pct of equity. Entries only.
    #
    # `new_risk` is the CANDIDATE order's own contribution, on top of
    # portfolio_heat's tally of what is already open. Same class of bug
    # portfolio_heat had (see its docstring): `price - candidate_stop` is
    # only ever a magnitude when the stop sits below price. A short's
    # candidate stop sits ABOVE price, so the un-fixed formula went negative,
    # `max(..., 0.0)` clamped it to $0.00, and the very order the cap is
    # deciding on contributed nothing to the decision. Direction-select the
    # same way portfolio_heat does, so the two agree: `action` (this
    # function's own parameter) selects (candidate_stop - price) for a short
    # vs (price - candidate_stop) for a long, and the `max(..., 0.0)` clamp
    # survives on both sides to catch a nonsensical stop for whichever
    # direction `action` claims.
    heat_cap_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
    if action in ENTRY_ACTIONS and heat_cap_pct > 0 and open_trades is not None:
        heat = portfolio_heat(open_trades, cfg, account["equity"])
        if candidate_stop:
            dist = ((candidate_stop - price) if action == "short"
                    else (price - candidate_stop))
            new_risk = qty * max(dist, 0.0)
        else:
            new_risk = (account["equity"]
                        * cfg["risk"].get("risk_per_trade_pct", 1.0) / 100)
        cap = account["equity"] * heat_cap_pct / 100
        if heat + new_risk > cap:
            raise RiskRejection(
                f"portfolio heat cap: open stop-risk ${heat:,.0f} + this "
                f"trade's ${new_risk:,.0f} would exceed "
                f"{heat_cap_pct}% of equity (${cap:,.0f})", rail="heat")

    # Correlation heat cap (entries only; needs the cycle's bars). Fail-open
    # when bars are unavailable — the per-symbol cap above still applies.
    ccfg = cfg["risk"].get("correlation_cap") or {}
    if (action in ENTRY_ACTIONS and ccfg.get("enabled") and bars_map
            and bars_map.get(symbol)):
        open_bars = {s: bars_map.get(s) for s in positions if s != symbol}
        n = correlated_position_count(bars_map[symbol], open_bars,
                                      int(ccfg.get("lookback", 60)),
                                      float(ccfg.get("threshold", 0.85)))
        if n >= int(ccfg.get("max_correlated", 2)):
            raise RiskRejection(
                f"correlation cap: {n} open positions already move with "
                f"{symbol} (corr>={ccfg.get('threshold', 0.85)}) — "
                f"co-moving names are one bet", rail="correlation")

    # EXIT_ACTIONS, not just "sell": a "cover" closes a short the same way a
    # "sell" closes a long, and gating on "sell" alone let a cover bypass the
    # minimum holding period entirely — the swing guard's whole purpose,
    # structurally impossible day-trading, would not have applied to the short
    # side at all.
    if action in EXIT_ACTIONS:
        swing_guard(entry_ts, cfg)
