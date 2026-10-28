"""Skeptical performance review — `python src/review.py`.

Reads the ledger and learnings and prints an honest report card against the
go-live gate (GUIDE.md §9): 2-3 months of paper history, >=30 closed trades,
and beating SPY buy-and-hold over the same window. Built to talk you OUT of
going live until the evidence is there.

Pure file reads; the SPY benchmark uses the read-only data API and degrades
gracefully when keys are absent.
"""
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger import Ledger
from lessons import LessonStore
from judgments import (JudgmentStore, calibration_metrics, calibration_line,
                       confidence_calibration_lines)
from strategies.base import ENTRY_ACTIONS

GATE_MIN_DAYS = 60          # "2-3 months" — use the low end as the floor
GATE_MIN_CLOSED = 30
STALE_LESSON_DAYS = 21


def per_strategy_breakdown(closed: list[dict]) -> dict:
    """Pure: closed trades grouped by owning strategy (legacy -> ma_crossover)."""
    groups: dict = {}
    for t in closed:
        groups.setdefault(t.get("strategy") or "ma_crossover", []).append(t)
    out = {}
    for name, trades in groups.items():
        wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
        out[name] = {
            "n_closed": len(trades),
            "win_rate": sum(1 for t in trades if t["result"] == "win") / len(trades),
            "profit_factor": (wins / losses if losses > 0
                              else (float("inf") if wins > 0 else None)),
            "realized_pnl": round(sum(t["pnl"] for t in trades), 2),
        }
    return out


def lesson_book_summary(states: dict) -> dict:
    """Pure: counts by status + oldest active hypothesis."""
    counts = {st: 0 for st in ("candidate", "active", "refuted", "retired")}
    oldest_active = None
    for s in states.values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
        if s["status"] == "active" and (oldest_active is None
                                        or s["created_ts"] < oldest_active["created_ts"]):
            oldest_active = s
    return {"counts": counts, "oldest_active": oldest_active}


def build_report(records: list, learnings_lines: list, now: datetime) -> dict:
    """Pure aggregation over ledger records — unit-testable, no I/O."""
    decisions = [r for r in records if r["type"] == "decision"]
    executed = [r for r in decisions if r.get("executed")]
    outcomes = {r["trade_id"]: r for r in records if r["type"] == "outcome"}
    by_id = {r["trade_id"]: r for r in decisions if r.get("trade_id")}
    closed = [{**by_id[tid], **o} for tid, o in outcomes.items() if tid in by_id]

    wins = [t for t in closed if t["result"] == "win"]
    gross_win = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in closed if t["pnl"] < 0)

    first_ts = min((r["ts"] for r in records), default=None)
    days = ((now - datetime.fromisoformat(first_ts)).days if first_ts else 0)

    vetoes = [r for r in decisions
              if (r.get("llm_review") or {}).get("verdict") == "veto"]
    rejections = [r for r in decisions
                  if not r.get("executed") and "risk rejection" in (r.get("detail") or "")]
    exit_reasons: dict = {}
    for t in closed:
        key = t.get("exit_reason") or "unknown"
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    last_lesson_ts = None
    for line in reversed(learnings_lines):
        if line.startswith("- **"):
            last_lesson_ts = line[4:14]  # the YYYY-MM-DD stamp
            break

    # 2026-07-16 fills are excluded: that day's signals priced off stale bars
    # (the incident that prompted the freshness guard), so their 142-1725bps
    # "slippage" is data contamination, not execution cost. Read-side filter
    # only — the ledger stays append-only.
    fq = [r for r in records if r["type"] == "fill_quality"]
    excluded = sum(1 for r in fq if r["ts"].startswith("2026-07-16"))
    slips = sorted(r["slippage_bps"] for r in fq
                   if not r["ts"].startswith("2026-07-16"))
    slippage = None
    if slips or excluded:
        mid = len(slips) // 2
        median = ((slips[mid] if len(slips) % 2
                   else (slips[mid - 1] + slips[mid]) / 2) if slips else None)
        slippage = {"n_fills": len(slips),
                    "n_excluded_stale": excluded,
                    "median_bps": round(median, 2) if slips else None,
                    "mean_bps": (round(sum(slips) / len(slips), 2)
                                 if slips else None)}

    # Fail-loud degradations (2026-07-21): fail-open events (skipped guards,
    # missing context) are ledgered so silence stays distinguishable from
    # "checked and fine" — count them by source here.
    degradations: dict = {}
    for r in records:
        if r.get("type") == "event" and r.get("event") == "degradation":
            src = (r.get("detail") or "unknown").split(":", 1)[0]
            degradations[src] = degradations.get(src, 0) + 1

    return {
        "slippage": slippage,
        "degradations": degradations,
        "history_days": days,
        "n_decisions": len(decisions),
        "n_executed": len(executed),
        "n_closed": len(closed),
        # Executed ENTRIES minus closed — `executed` also holds exits, and an
        # exit is not an opening. Counting them inflated the open book by
        # one per closed trade: the live ledger read "6 open positions" against
        # a 5-row table and a broker reporting 5. Found 2026-07-25, when
        # per-position marks made the page contradict itself. This is the same
        # definition `Ledger.open_buys()` has always used — including its Phase
        # 2 move to ENTRY_ACTIONS, made here in lockstep because
        # tests/test_review.py pins `n_open == len(led.open_buys())` and
        # because a count that drifted from the ledger's own is exactly the
        # self-contradicting page this line was written to fix.
        "n_open": (len([r for r in executed
                        if r.get("action") in ENTRY_ACTIONS])
                   - len(closed)) if executed else 0,
        "win_rate": len(wins) / len(closed) if closed else None,
        "profit_factor": (gross_win / gross_loss if gross_loss > 0
                          else (float("inf") if gross_win > 0 else None)),
        "realized_pnl": sum(t["pnl"] for t in closed),
        "n_vetoes": len(vetoes),
        "n_risk_rejections": len(rejections),
        "exit_reasons": exit_reasons,
        "last_lesson_date": last_lesson_ts,
    }


def spy_benchmark_bars(cfg: dict, days: int) -> list[dict]:
    """SPY daily bars for the monthly benchmark column. [] when unavailable.

    Extracted from report() 2026-08-21 so the dashboard can reuse it. The
    dashboard's S&P column had been rendering "n/a" for months, not because
    the data was missing but because two of the four render() callers passed
    no bars: the 15:45 cycle populated the column and the 16:20 review
    re-rendered the same file without it. Last writer wins, and the evening
    review always ran last.

    Returns a LIST rather than the scalar spy_benchmark_pct() gives, because
    scorecard.monthly_scorecard needs per-bar data to split by month.

    Never raises: a benchmark is a reporting nicety and must not be able to
    take down a trading cycle's cosmetic render.
    """
    if days < 1:
        return []
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env"))
        from broker import Broker as _B
        return _B(cfg).bars("SPY", "1Day", max(days, 2))
    except (Exception, SystemExit):  # noqa: BLE001 — offline still renders
        return []


def spy_benchmark_pct(days: int) -> float | None:
    """SPY buy-and-hold % over the review window. None when unavailable."""
    if days < 1:
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env"))
        from broker import Broker
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        bars = Broker(cfg).bars("SPY", "1Day", max(days, 2))
        if len(bars) < 2:
            return None
        return (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100
    except Exception:  # noqa: BLE001 — benchmark is optional, never crash the report
        return None


def equal_weight_pct(bars_by_symbol: dict) -> float | None:
    """Equal-weight buy-and-hold %% across a universe (pure). The honest
    second baseline: the bot picks FROM these symbols, so beating SPY alone
    can just mean the universe beat SPY."""
    rets = [(bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100
            for bars in bars_by_symbol.values() if len(bars) >= 2]
    return round(sum(rets) / len(rets), 2) if rets else None


def universe_benchmark_pct(days: int, cfg: dict) -> float | None:
    """Equal-weight B&H %% of the configured universe over the window."""
    if days < 1:
        return None
    try:
        from broker import Broker
        broker = Broker(cfg)
        bars_by_symbol = {}
        for sym in cfg["symbols"]:
            try:
                bars_by_symbol[sym] = broker.bars(sym, "1Day", max(days, 2))
            except Exception:  # noqa: BLE001
                continue
        return equal_weight_pct(bars_by_symbol)
    except Exception:  # noqa: BLE001 — benchmark is optional
        return None


def heat_report(positions: dict, stops: list[dict], atr_by_symbol: dict,
                equity: float, gap_atr_fraction: float = 0.5) -> dict:
    """Pure gap-adjusted portfolio heat: what firing every protective stop
    would cost, plus a modeled gap-through (stops do NOT fill at the stop —
    gap_atr_fraction x ATR further against you). Positions without a resting
    stop are flagged uncovered rather than guessed at. Reporting only — no
    cap binds at current sizing (same finding as candidates.md §6)."""
    best_stop: dict = {}
    for s in stops:
        if s["symbol"] not in best_stop or s["stop_price"] > best_stop[s["symbol"]]:
            best_stop[s["symbol"]] = s["stop_price"]
    risk_usd = gap_usd = 0.0
    uncovered = []
    for sym, p in positions.items():
        qty = p.get("qty", 0)
        price = (p["market_value"] / qty) if qty and p.get("market_value") \
            else p.get("avg_entry", 0)
        stop = best_stop.get(sym)
        if stop is None:
            uncovered.append(sym)
            continue
        risk_usd += max(price - stop, 0.0) * qty
        atr = atr_by_symbol.get(sym)
        if atr:
            gap_usd += gap_atr_fraction * atr * qty
    total = risk_usd + gap_usd
    return {"committed_risk_usd": round(risk_usd, 2),
            "gap_add_usd": round(gap_usd, 2),
            "gap_adjusted_usd": round(total, 2),
            "pct_of_equity": round(total / equity * 100, 2) if equity else None,
            "uncovered": uncovered}


def model_version_breakdown(closed: list[dict], current: str | None) -> dict:
    """Pure: closed trades per decision-surface fingerprint. A record that
    spans versions is a blend of rulebooks — the go-live gate should read
    per-version numbers, not the blend."""
    by_ver: dict = {}
    for t in closed:
        v = t.get("model_version") or "untagged"
        b = by_ver.setdefault(v, {"n": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] = round(b["pnl"] + t.get("pnl", 0.0), 2)
    return {"current": current, "versions": by_ver,
            "mixed": len(by_ver) > 1}


def _fmt_gate(ok: bool | None, label: str) -> str:
    mark = "?" if ok is None else ("PASS" if ok else "FAIL")
    return f"  [{mark:>4}] {label}"


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    ledger = Ledger(cfg["memory"]["ledger_path"])
    try:
        with open(cfg["memory"]["learnings_path"]) as f:
            learnings = f.readlines()
    except FileNotFoundError:
        learnings = []

    now = datetime.now(timezone.utc)
    r = build_report(ledger.all_records(), learnings, now)

    print("=" * 62)
    print("PAPER TRADING REVIEW — skeptical by design")
    print("=" * 62)
    print(f"History: {r['history_days']} days | decisions: {r['n_decisions']} "
          f"| executed: {r['n_executed']} | closed: {r['n_closed']} "
          f"| open: {r['n_open']}")
    wr = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "n/a"
    pf = ("n/a" if r["profit_factor"] is None
          else "inf (no losses yet — too early to mean anything)"
          if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}")
    print(f"Win rate: {wr} | profit factor: {pf} "
          f"| realized P&L: ${r['realized_pnl']:,.2f}")
    print(f"LLM vetoes: {r['n_vetoes']} | risk-rail rejections: {r['n_risk_rejections']}")
    if r["slippage"]:
        s = r["slippage"]
        note = (f" [{s['n_excluded_stale']} contaminated 2026-07-16 "
                f"stale-bars fills excluded]"
                if s.get("n_excluded_stale") else "")
        if s["n_fills"]:
            print(f"Measured slippage ({s['n_fills']} fills): median "
                  f"{s['median_bps']:+.1f} bps | mean {s['mean_bps']:+.1f} bps "
                  f"(positive = fills worse than signal price){note}")
        else:
            print(f"Measured slippage: no clean fills yet{note}")
    if r["exit_reasons"]:
        print("Exits:", ", ".join(f"{k}={v}" for k, v in sorted(r["exit_reasons"].items())))

    # Monthly scorecard vs S&P (2026-07-21): the owner's benchmark goal,
    # measured month by month. SPY bars fetched once; offline => "n/a".
    import scorecard
    records = ledger.all_records()
    spy_bars = spy_benchmark_bars(cfg, max(r["history_days"] + 5, 10))
    card = scorecard.monthly_scorecard(
        records, spy_bars, scorecard.realized_pnl_by_month(records))
    print()
    for line in scorecard.format_lines(card):
        print(line)

    # Live kill criteria status (pre-registered) — visible BEFORE it binds.
    kcfg = cfg["risk"].get("live_kill") or {}
    if kcfg.get("enabled"):
        import risk as risk_mod
        closed_all = ledger.closed_trades()
        strategies_seen = sorted({t.get("strategy") for t in closed_all
                                  if t.get("strategy")}
                                 | set((cfg.get("strategies") or {}).keys()))
        print(f"\nLIVE KILL CRITERIA (pre-registered: PF < "
              f"{kcfg.get('pf_below', 0.8)} over >= "
              f"{kcfg.get('min_trades', 15)} live closed trades "
              f"retires a strategy's entries):")
        # After the operator cost, exactly as the rail reads it (2026-10-28):
        # each trade's own cost off its P&L. Strict read; an unreadable fee
        # log is shown as the fail-closed state the rail is in.
        fees, fees_problem = None, ""
        try:
            import operator_settings
            import statepaths
            fees = operator_settings.fees_by_ref(statepaths.operator_dir(cfg))
        except Exception as e:  # noqa: BLE001 — FeeLedgerError; a report never dies
            fees_problem = str(e)
            print(f"  (operator fee ledger unreadable — the live kill fails "
                  f"CLOSED: {fees_problem})")
        print("  (PF after each trade's own operator cost)")
        stats_trades = (risk_mod.after_cost_trades(closed_all, fees)
                        if fees else closed_all)
        for strat in strategies_seen:
            s = risk_mod.strategy_live_stats(stats_trades, strat)
            killed = risk_mod.live_kill_blocked(closed_all, strat, cfg,
                                                fees=fees,
                                                fees_problem=fees_problem)
            pf_txt = ("n/a" if s["pf"] is None
                      else "inf" if s["pf"] == float("inf")
                      else f"{s['pf']:.2f}")
            status = ("KILLED — entries retired" if killed
                      else "armed" if s["n"] >= kcfg.get("min_trades", 15)
                      else f"n too small ({s['n']}/{kcfg.get('min_trades', 15)})")
            print(f"  {strat}: n={s['n']}, PF {pf_txt} [{status}]")

    spy = spy_benchmark_pct(r["history_days"])
    bot_pct = r["realized_pnl"] / 100_000 * 100  # vs the $100k paper account

    print("\nGO-LIVE GATE (GUIDE.md §9) — all three must pass:")
    print(_fmt_gate(r["history_days"] >= GATE_MIN_DAYS,
                    f"{GATE_MIN_DAYS}+ days of paper history "
                    f"(have {r['history_days']})"))
    print(_fmt_gate(r["n_closed"] >= GATE_MIN_CLOSED,
                    f"{GATE_MIN_CLOSED}+ closed trades (have {r['n_closed']})"))
    if spy is None:
        print(_fmt_gate(None, "beats SPY buy-and-hold (benchmark unavailable)"))
    else:
        print(_fmt_gate(bot_pct > spy,
                        f"beats SPY buy-and-hold ({bot_pct:+.2f}% vs SPY {spy:+.2f}%)"))
    ew = universe_benchmark_pct(r["history_days"], cfg)
    if ew is not None:
        print(f"  [info] equal-weight universe B&H over same window: {ew:+.2f}% "
              f"(bot {bot_pct:+.2f}%) — the harder honest baseline")

    # --- Model-version segmentation (decision-surface fingerprint) ---
    import modelver
    mv = model_version_breakdown(ledger.closed_trades(),
                                 modelver.current_version())
    if mv["versions"]:
        parts = ", ".join(f"{v[:12]} n={b['n']} P&L ${b['pnl']:,.2f}"
                          for v, b in sorted(mv["versions"].items()))
        print(f"\nMODEL VERSIONS (current {mv['current']}): {parts}")
        if mv["mixed"]:
            print("  [note] record spans multiple rulebook versions — judge "
                  "the gate on per-version numbers, not the blend")

    # --- Post-exit runner tracking (what happened AFTER each close) ---
    import postexit
    pe_states = postexit.PostExitStore(
        postexit._cfg(cfg)["path"]).replay()
    pes = postexit.summary(pe_states)
    if pe_states:
        v = ", ".join(f"{k}={n}" for k, n in sorted(pes["verdicts"].items()))
        print(f"\nPOST-EXIT ({pes['n_resolved']} resolved, "
              f"{pes['n_tracking']} tracking): {v or 'no verdicts yet'}")
        if pes["avg_winner_max_extension_pct"] is not None:
            print(f"  Winners kept running on average "
                  f"{pes['avg_winner_max_extension_pct']:+.1f}% past our exit "
                  f"(evidence for future exit-rule gate candidates)")

    # --- Gap-adjusted portfolio heat (reporting only; live broker state) ---
    try:
        from broker import Broker
        import strategy as strategy_mod
        b = Broker(cfg)
        positions = b.positions()
        if positions:
            stops = b.open_stop_orders()
            atr_p = cfg["risk"].get("brackets", {}).get("atr_period", 14)
            atrs = {}
            for sym in positions:
                try:
                    atrs[sym] = strategy_mod.atr(b.bars(sym, "1Day", atr_p * 3),
                                                 atr_p)
                except Exception:  # noqa: BLE001
                    pass
            equity = b.account()["equity"]
            gap_frac = cfg.get("reporting", {}).get("heat_gap_atr_fraction", 0.5)
            h = heat_report(positions, stops, atrs, equity, gap_frac)
            print(f"\nPORTFOLIO HEAT (if every stop fires, gap-adjusted "
                  f"{gap_frac}xATR through): ${h['gap_adjusted_usd']:,.2f} "
                  f"= {h['pct_of_equity']:.2f}% of equity "
                  f"(stops ${h['committed_risk_usd']:,.2f} + gap "
                  f"${h['gap_add_usd']:,.2f})")
            if h["uncovered"]:
                print(f"  [warn] positions with NO resting stop: "
                      f"{', '.join(sorted(h['uncovered']))}")
    except Exception:  # noqa: BLE001 — heat is optional (offline runs)
        pass

    # --- Per-strategy breakdown ---
    per_strat = per_strategy_breakdown(ledger.closed_trades())
    if per_strat:
        print("\nPER-STRATEGY (closed trades):")
        for name, s in sorted(per_strat.items()):
            pf = ("inf" if s["profit_factor"] == float("inf")
                  else "n/a" if s["profit_factor"] is None
                  else f"{s['profit_factor']:.2f}")
            print(f"  {name:<14} n={s['n_closed']:<3} win {s['win_rate']:.0%}  "
                  f"PF {pf}  P&L ${s['realized_pnl']:,.2f}")

    # --- Learning loop ---
    lcfg = cfg.get("learning", {})
    states = LessonStore(lcfg.get("lessons_path", "memory/lessons.jsonl")).replay()
    book = lesson_book_summary(states)
    c = book["counts"]
    print(f"\nHYPOTHESIS BOOK: {c['active']} active | {c['candidate']} candidate "
          f"| {c['refuted']} refuted | {c['retired']} retired")
    if book["oldest_active"]:
        oa = book["oldest_active"]
        print(f"  Oldest active ({oa['created_ts'][:10]}, "
              f"n={len(oa['supports'])}/{len(oa['contradicts'])}): "
              f"{oa['hypothesis'][:100]}")
    elif not states:
        print("  (empty — hypotheses generate from closed trades, LLM key required)")

    jstore = JudgmentStore(lcfg.get("judgments_path", "memory/judgments.jsonl"))
    print(calibration_line(calibration_metrics(jstore.replay())))
    n_pending = len(jstore.unresolved())
    if n_pending:
        print(f"  ({n_pending} judgments awaiting resolution — embargo/horizon)")

    for line in confidence_calibration_lines(jstore.replay()):
        print(line)

    if r["degradations"]:
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(r["degradations"].items()))
        print(f"Degradations (fail-open events — guards skipped, context "
              f"missing): {sum(r['degradations'].values())} ({parts})")
    else:
        print("Degradations: 0 (no guard ran fail-open this window)")

    if r["last_lesson_date"]:
        age = (now - datetime.fromisoformat(r["last_lesson_date"] + "T00:00:00+00:00")).days
        stale = " — STALE, regime may have moved" if age > STALE_LESSON_DAYS else ""
        print(f"Last lesson: {r['last_lesson_date']} ({age}d ago){stale}")

    if r["n_closed"] < GATE_MIN_CLOSED:
        print(f"\nVerdict: not enough evidence to conclude ANYTHING yet "
              f"({r['n_closed']}/{GATE_MIN_CLOSED} closed trades). Keep paper trading.")
    elif spy is not None and bot_pct <= spy:
        print("\nVerdict: the strategy is NOT beating buy-and-hold. "
              "Do not go live; improve the strategy instead.")
    else:
        print("\nVerdict: gate metrics look satisfied — re-run the backtest and "
              "review the trials log before even considering the live switch.")

    print("\nAudit pack (Phase C): python src/evidence.py --out evidence/")


if __name__ == "__main__":
    main()
