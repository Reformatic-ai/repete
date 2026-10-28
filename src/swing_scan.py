"""Intraday opportunity scan — swing_sectors' 30-minute trigger loop.

    python src/swing_scan.py        (launchd: every 30 min, 09:35-15:35 ET)

Owner direction, 2026-08-11: the bot should act when it sees opportunity, not
at a fixed time. This module is how that happens WITHOUT re-opening §19a:

  * Every condition and every level comes from `swing_sectors.assess()` on
    COMPLETED daily bars — the same function, the same numbers, that the
    15:45 cycle and the §62 gate see. Nothing here computes an indicator
    from a forming bar. What this module adds is one comparison: is the
    LIVE quote inside a zone that was fully determined by completed bars?
    That is the same standing as a broker-side bracket stop, which also
    waits on live price all day.
  * `opportunity_scan.py` (alerts-only) is unchanged and its no-order-verbs
    test still binds. THIS module may trade — exactly one strategy, exactly
    one direction (a long swing entry), through the same pipeline the cycle
    uses: judge review, hard rails, brackets, ledger, journal.

WHAT A PASS DOES. Bootstrap exactly as a cycle does (`main._bootstrap_cycle`:
preflight fail-safe, HALT semantics, broker state), fetch completed bars
through the same rails (`main._fetch_and_validate_bars`: staleness, universe
floor, vendor cross-check, regime), rank the sector cross-section, and ask
`assess()` for armed symbols. A live quote inside an armed zone is a
candidate. With `swing_sectors.enabled: false` the pass LOGS the candidate
and places nothing — the dry-run mode the strategy ships in. Enabled, the
candidate goes through `_enter()`, which mirrors main.py's `_process_signal`
entry path step for step (the order of guards is load-bearing; see each
comment there for why).

AT MOST ONE ENTRY PER PASS, deepest laggard first. The rails would allow
more, but each entry re-prices the account, and 30 minutes later the next
pass judges the next candidate against the book as it actually is — not
against a cycle-start snapshot three entries stale.

WHY A PASS EXITS QUIETLY. A skip is a success. Most passes find nothing, and
a scan that wrote a ledger event every 30 minutes would bury the record it
exists to keep. The ledger hears about candidates, entries, and failures —
never about "nothing happened".

WHAT THIS MODULE NEVER DOES: exits. Broker-side bracket stops protect
continuously; strategy exits (thesis complete / base failed / time stop) run
in the daily cycle via the ownership rule, where every exit for every
strategy runs. A second exit path would be a second implementation of the
ownership rule.
"""
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import main as cycle
import risk
import strategies
import trade_plan
import x_poster
from strategies import swing_sectors
from strategies.base import Signal

log = logging.getLogger("swing_scan")

STRATEGY = swing_sectors.NAME


def find_candidates(cfg: dict, all_bars: dict, positions: dict) -> list[dict]:
    """Armed symbols whose ZONE is fully determined by completed bars.

    Pure and broker-free on purpose — the live-quote comparison happens in
    `run_scan`, so this half is testable offline and reusable by anything
    that wants to know what the scan WOULD watch. Order: deepest drawdown
    first, matching the cross-section's own ranking.
    """
    held = set(positions)
    xs = strategies.prepare_one(cfg, STRATEGY, all_bars, held=held)
    if not xs:
        return []
    params = strategies.strategy_params(cfg, STRATEGY)
    out = []
    for sym in xs.get("laggards", ()):
        if sym in held:
            continue                      # the ensemble holds one position per
                                          # symbol; entries never add to one
        bars = all_bars.get(sym)
        if not bars:
            continue
        zone = swing_sectors.assess(sym, bars, params, xs)
        if zone:
            out.append({"symbol": sym, "bars": bars, **zone})
    return out


def _enter(cand: dict, live: float, cfg, ledger, memory, broker, account,
           positions, open_trades, all_bars, market_regime,
           regime_label) -> str:
    """One live-triggered swing entry through the cycle's own pipeline.

    A deliberate MIRROR of main._process_signal's ENTRY path, reduced to the
    single case this module is allowed to produce (a long entry, never an
    exit, never a short). Kept in the same order for the same reasons:
    judge before rails would let a veto hide a rail rejection from the
    calibration record; brackets before sizing so stop-distance sizing sees
    the stop it will place; drift guard last, nearest the order. If you
    change _process_signal, check this function — test_swing_scan pins the
    shared invariants, but only the ones it knows to look for.
    """
    symbol, bars = cand["symbol"], cand["bars"]
    price = live                          # the trigger price IS the signal price
    sig = Signal(
        symbol, "buy",
        f"unloved sector {cand['drawdown']:.1f}% off its 52-week high, "
        f"stabilized above a rising base — live ${live:.2f} inside the "
        f"precomputed entry zone [{cand['zone_low']:.2f}, "
        f"{cand['zone_high']:.2f}] (intraday scan)",
        {"drawdown_pct": round(cand["drawdown"], 2),
         "zone_low": round(cand["zone_low"], 2),
         "zone_high": round(cand["zone_high"], 2),
         "live": round(live, 2), "trigger": "swing_scan"},
        STRATEGY)

    review = llm.review_signal(
        sig, memory.context_for_llm(symbol=symbol, regime=market_regime,
                                    strategy=STRATEGY, signal=sig,
                                    positions=positions, account=account), cfg)
    if review.get("degraded"):
        ledger.log_event(
            "degradation",
            f"llm_judge[{review.get('degraded_reason', 'unknown')}]: review "
            f"unavailable for {symbol}, proceeding rule-based "
            f"({review['degraded']})")
    if review.get("unavailable_block"):
        why = (f"judge unavailable ({review.get('degraded_reason', 'unknown')})"
               f" and llm.on_unavailable=block")
        tid = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                  review, executed=False, detail=why,
                                  regime=regime_label, strategy=STRATEGY)
        memory.judgments.log_judgment(
            tid, symbol, "buy", "degraded_block", 1.0, price, regime_label,
            kind="degraded", executed=False, reasoning=why, strategy=STRATEGY)
        log.warning("%s: BUY blocked — %s", symbol, why)
        return "blocked"
    if review["verdict"] == "veto":
        tid = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                  review, executed=False, detail="LLM veto",
                                  regime=regime_label, strategy=STRATEGY)
        memory.judgments.log_judgment(
            tid, symbol, "buy", "veto", review.get("scale", 1.0), price,
            regime_label, kind="llm", executed=False,
            reasoning=review.get("reasoning", ""), strategy=STRATEGY,
            cited_lessons=review.get("cited_lessons"),
            confidence=review.get("confidence"))
        log.info("%s: buy VETOED — %s", symbol, review["reasoning"])
        return "blocked"

    bcfg = cfg["risk"].get("brackets", {})
    brackets = risk.bracket_prices(
        price, strategies.atr(bars, bcfg.get("atr_period", 14)), cfg,
        vol_bucket=(market_regime or {}).get("vol"), strategy=STRATEGY)
    full_qty = risk.size_order(account, price, cfg, bars=bars,
                               strategy=STRATEGY,
                               stop_price=brackets[0] if brackets else None)
    qty = int(full_qty * review["scale"])
    if qty <= 0:
        why = (f"position sizing yields 0 shares at ${price:,.2f}"
               if full_qty <= 0 else
               f"LLM downsize x{review['scale']} truncated a {full_qty}-share "
               f"order to 0 (whole shares only) — skipped, not resized up")
        tid = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                  review, executed=False,
                                  detail=f"risk rejection: {why}",
                                  rail="downsize_zero_qty",
                                  regime=regime_label, strategy=STRATEGY)
        memory.judgments.log_judgment(
            tid, symbol, "buy", "rails_reject", 1.0, price, regime_label,
            kind="rails", executed=False, reasoning=why, strategy=STRATEGY)
        return "blocked"

    try:
        # After cost (2026-10-28), joined on each trade's own fee lines.
        _fees, _fees_problem = cycle._fees_for_rules(cfg)
        kill = risk.live_kill_blocked(ledger.closed_trades(), STRATEGY, cfg,
                                      fees=_fees, fees_problem=_fees_problem)
        if kill:
            raise risk.RiskRejection(kill, rail="live_kill")
        # `bars_map=all_bars` — the FULL fetch, which run_scan sized to the
        # universe PLUS every held symbol, exactly as the cycle does. The
        # correlation cap compares the candidate against what the book holds;
        # hand it only the candidate's own bars and it fails open pair by
        # pair, a rail silently disarmed for every scan entry.
        risk.pre_trade_checks("buy", symbol, qty, price, account, positions,
                              cfg, entry_ts=None, regime_label=regime_label,
                              bars_map=all_bars,
                              open_trades=open_trades,
                              candidate_stop=brackets[0] if brackets else None,
                              strategy=STRATEGY)
    except risk.RiskRejection as e:
        tid = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                  review, executed=False,
                                  detail=f"risk rejection: {e}",
                                  rail=getattr(e, "rail", "unattributed"),
                                  regime=regime_label, strategy=STRATEGY)
        memory.judgments.log_judgment(
            tid, symbol, "buy", "rails_reject", 1.0, price, regime_label,
            kind="rails", executed=False, reasoning=str(e), strategy=STRATEGY)
        log.warning("%s: buy REJECTED by risk rails — %s", symbol, e)
        return "blocked"

    # Drift guard, re-armed: the judge round-trip takes real seconds, and the
    # price that triggered the zone is not necessarily the price still there.
    try:
        fresh = broker.latest_price(symbol)
    except Exception as e:  # noqa: BLE001 — quote outage != bad price
        log.warning("%s: drift check skipped, quote unavailable (%s)", symbol, e)
        ledger.log_event("degradation", f"drift_guard: quote unavailable for "
                                        f"{symbol}, guard skipped ({e})")
        fresh = None
    if fresh is not None and not risk.entry_drift_ok(price, fresh, cfg):
        msg = (f"entry drift {risk.entry_drift_bps(price, fresh):.0f}bps > "
               f"{cfg['risk']['max_entry_drift_bps']}bps cap "
               f"(trigger ${price:.2f} vs live ${fresh:.2f})")
        tid = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                  review, executed=False,
                                  detail=f"risk rejection: {msg}",
                                  rail="entry_drift",
                                  regime=regime_label, strategy=STRATEGY)
        memory.judgments.log_judgment(
            tid, symbol, "buy", "rails_reject", 1.0, price, regime_label,
            kind="rails", executed=False, reasoning=msg, strategy=STRATEGY)
        return "blocked"

    # Same idempotency key the 15:45 cycle would derive for this symbol and
    # day — so scan and cycle cannot double-enter one name between them, with
    # the broker as the arbiter rather than any state file of ours.
    coid = (f"ta-{symbol}-buy-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d')}")
    try:
        if brackets:
            order = broker.bracket_market_order(symbol, qty, *brackets,
                                                client_order_id=coid)
        else:
            order = broker.market_order(symbol, qty, "buy",
                                        client_order_id=coid)
    except Exception as e:  # noqa: BLE001 — no naked stop-sized entry
        ledger.log_decision(symbol, "buy", sig.reason, sig.indicators, review,
                            executed=False, detail=f"order error: {e}",
                            regime=regime_label, strategy=STRATEGY)
        log.error("%s: order failed — %s", symbol, e)
        return "blocked"

    risk.record_trade()
    plan = None
    try:
        plan = trade_plan.build(sig, cfg, price, qty, order, regime_label,
                                review)
    except Exception as e:  # noqa: BLE001 — narration never blocks a trade
        log.warning("trade_plan build failed for %s: %s", symbol, e)
    trade_id = ledger.log_decision(symbol, "buy", sig.reason, sig.indicators,
                                   review, executed=True, order=order,
                                   entry_price=price, qty=qty,
                                   detail="swing_scan", regime=regime_label,
                                   strategy=STRATEGY, trade_plan=plan)
    # kind is conditional for the same reason as main.py's executed judgment:
    # a fallback approval is not a judgement, and counting it as one is how
    # every recorded approval in this bot's history came to be a fallback.
    memory.judgments.log_judgment(
        trade_id, symbol, "buy", review["verdict"], review.get("scale", 1.0),
        price, regime_label,
        kind=("degraded" if llm.is_fallback_review(review) else "llm"),
        executed=True,
        reasoning=review.get("reasoning", ""),
        stop_price=order.get("stop_price"),
        tp_price=order.get("take_profit_price"), strategy=STRATEGY,
        cited_lessons=review.get("cited_lessons"),
        confidence=review.get("confidence"))
    log.info("%s: EXECUTED BUY x%d @ ~$%.2f (trade %s, %s via scan)",
             symbol, qty, price, trade_id, STRATEGY)

    recap = {"symbol": symbol, "action": "buy", "qty": qty,
             "entry_price": price, "strategy_reason": sig.reason,
             "strategy": STRATEGY, "llm_reasoning": review["reasoning"]}
    link = cycle.journal_and_link(
        {"trade_id": trade_id, "symbol": symbol, "action": "buy", "qty": qty,
         "entry_price": price, "strategy": STRATEGY,
         "strategy_reason": sig.reason, "indicators": sig.indicators,
         "llm_review": review, "order": order, "regime": regime_label}, cfg)
    x_poster.post_recap(recap, cfg, llm.write_x_post(recap, cfg), link=link)
    return "executed"


def run_scan() -> int:
    boot = cycle._bootstrap_cycle()
    if boot is None:
        return 0                          # preflight failure or HALT freeze —
                                          # both already ledgered by bootstrap
    cfg, ledger, memory, broker, account, positions, halted = boot
    if halted:
        return 0                          # HALT exits-mode: this module only
                                          # opens; there is nothing for it to do
    params = strategies.strategy_params(cfg, STRATEGY)
    if params is None:
        return 0
    if account.get(risk.RAIL_PROBLEM_KEY):
        # 2026-10-28: the operator fee log could not be read strictly, so rail
        # equity is unknown and every after-cost rail fails CLOSED. This module
        # only opens, so the pass is over — said once, as the cycle does.
        log.critical("rail equity unknown — scan pass over: %s",
                     account[risk.RAIL_PROBLEM_KEY])
        ledger.log_event("swing_scan_entries_blocked",
                         f"rail_equity_unknown: "
                         f"{account[risk.RAIL_PROBLEM_KEY]}"[:300])
        return 0

    try:
        if not broker.market_open():
            log.info("market closed — scan pass skipped")
            return 0
    except Exception as e:  # noqa: BLE001 — FAIL CLOSED: skip, don't queue
        log.warning("market clock unavailable (%s) — scan pass skipped", e)
        return 0

    scan_symbols = sorted(strategies.universe_for(cfg, STRATEGY))
    if "SPY" not in scan_symbols:
        scan_symbols.append("SPY")        # the freshness/regime anchor
    # Held symbols ride along exactly as in the cycle's scan list: the
    # correlation cap needs THEIR bars to judge a candidate against the book.
    scan_symbols += [s for s in positions if s not in scan_symbols]
    fetched = cycle._fetch_and_validate_bars(broker, cfg, ledger, scan_symbols,
                                             completed_bars_only=True)
    if fetched is None:
        return 0                          # SPY stale — already ledgered
    all_bars, blocked_reason, blocked_rail, market_regime, regime_label = fetched
    if blocked_reason:
        # The same deterministic stop the cycle honours (vendor divergence /
        # universe floor). One event, not one per candidate: no judge ever
        # saw these, so there is no per-signal decision to record.
        log.warning("entries blocked (%s) — scan pass over: %s",
                    blocked_rail, blocked_reason)
        ledger.log_event("swing_scan_entries_blocked",
                         f"{blocked_rail}: {blocked_reason[:300]}")
        return 0

    candidates = find_candidates(cfg, all_bars, positions)
    in_zone = []
    for cand in candidates:
        try:
            live = broker.latest_price(cand["symbol"])
        except Exception as e:  # noqa: BLE001
            log.warning("%s: no live quote (%s) — candidate skipped",
                        cand["symbol"], e)
            continue
        if cand["zone_low"] <= live <= cand["zone_high"]:
            in_zone.append((cand, live))
        else:
            log.info("%s: armed, live $%.2f outside zone [%.2f, %.2f]",
                     cand["symbol"], live, cand["zone_low"], cand["zone_high"])

    if not in_zone:
        return 0                          # a skip is a success

    if not params.get("enabled"):
        # Dry-run visibility: the state the strategy SHIPS in. Loud in the
        # ledger precisely because it is rare and is the evidence the owner
        # reads alongside §62 before flipping enabled.
        for cand, live in in_zone:
            msg = (f"{cand['symbol']}: would enter — live ${live:.2f} in zone "
                   f"[{cand['zone_low']:.2f}, {cand['zone_high']:.2f}], "
                   f"drawdown {cand['drawdown']:.1f}%")
            log.info("DRY (enabled: false): %s", msg)
            ledger.log_event("swing_scan_candidate", msg)
        return 0

    open_trades = ledger.open_buys()
    for cand, live in in_zone:            # deepest laggard first, then stop:
        outcome = _enter(cand, live, cfg, ledger, memory, broker, account,
                         positions, open_trades, all_bars, market_regime,
                         regime_label)
        if outcome == "executed":
            break                         # ONE entry per pass — see module doc
    return 0


if __name__ == "__main__":
    cycle.configure_logging()
    sys.exit(run_scan())
