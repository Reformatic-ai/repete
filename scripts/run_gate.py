#!/usr/bin/env python3
"""Execute a frozen pre-registration and record the verdict.
`python scripts/run_gate.py <spec-id> [--workers N]`

Replaces the bespoke `scripts/gate_*.py` pattern — seven runners, 1,464 lines,
all doing the same five things around `backtest.simulate_ensemble`,
`backtest.enablement_gate` and `significance.compare`. Writing those by hand is
what capped how many hypotheses this project could test; the compute never was.

Three refusals, and they are the point
--------------------------------------
1. **Not registered.** No row in research/registrations.jsonl → there is no
   frozen pass mark, so any result would be unfalsifiable.
2. **Altered since registration.** The hash moved → the claim changed after it
   was frozen. The runner names the exact fields that differ, because
   "clauses.2.pp: 1.0 -> 3.0" is the finding, while "spec was altered" only
   starts a search.
3. **Snapshot drift.** The data's sha256 is not the one the registration named
   → this would be a different experiment wearing the same id.

Every existing gate script already refuses on (3). (1) and (2) are new, and they
are what makes pre-registration mechanical rather than a habit backed by a
commit. §33 RUN 1 is the standing reminder: it printed VALIDATED and was an
artifact of a runner detail nobody had frozen.

Determinism
-----------
`--workers` fans arms out across processes — the one place the Bizon's 64 cores
pay off, since arms are embarrassingly parallel. Results are collected back into
spec order, never completion order, and `significance.compare` carries a fixed
seed, so **the verdict cannot depend on how many workers ran it**. That is a
test (`test_run_gate_reproduces.py`), not a hope.

A verdict is a recommendation, never an action: nothing here enables a strategy
or widens a rail (CLAUDE.md invariant 2). The owner decides.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                       # noqa: E402
import gatespec as gs                                       # noqa: E402
import judge_model as jm                                    # noqa: E402
import significance as sig                                  # noqa: E402

SPEC_DIR = "research/specs"
VERDICTS = "research/verdicts.jsonl"


# ---- refusals --------------------------------------------------------------

def check_frozen(spec: dict, registrations_path: str) -> dict:
    reg = gs.registrations(registrations_path).get(spec["id"])
    if reg is None:
        raise SystemExit(
            f"REFUSING to run {spec['id']}: it is not registered.\n"
            f"There is no frozen pass mark, so a result here could not be "
            f"wrong.\n\n  python scripts/register_gate.py {spec['id']}")

    digest = gs.canonical_sha256(spec)
    if digest != reg["spec_sha256"]:
        changed = gs.diff_fields(reg["spec"], spec) or ["(structure differs)"]
        raise SystemExit(
            f"REFUSING to run {spec['id']}: the spec changed after it was "
            f"registered.\n"
            f"  registered {reg['spec_sha256'][:16]}…  at {reg['registered_at'][:19]}\n"
            f"  now        {digest[:16]}…\n\nChanged:\n  "
            + "\n  ".join(changed)
            + "\n\nEither restore the registered spec, or register a new id.")
    return reg


def verify_data(spec: dict) -> dict:
    """sha256-check the primary snapshot and every aux file. Returns paths."""
    paths = {"snapshot": spec["snapshot"]}
    paths.update({k: v for k, v in (spec.get("aux") or {}).items()})
    for label, entry in paths.items():
        got = gs.sha256_file(entry["path"])
        if got != entry["sha256"]:
            raise SystemExit(
                f"SNAPSHOT DRIFT on {label} ({entry['path']}):\n"
                f"  registered {entry['sha256'][:16]}…\n"
                f"  actual     {got[:16]}…\n"
                f"Refusing to score {spec['id']} on data the registration did "
                f"not name.")
        print(f"  verified {os.path.basename(entry['path'])}  "
              f"sha256 {got[:16]}…")
    return paths


# ---- running ---------------------------------------------------------------

def judge_setting(spec: dict) -> bool | None:
    """True / False as declared, or None for a spec that predates the field.

    None is NOT False. §35-§41 were scored judge-less by accident, and the
    difference between "chose not to model the judge" and "did not know the
    question existed" is the whole finding — flattening them here would erase
    it. register_gate.py refuses any NEW spec without the field.
    """
    return spec.get("judge_model")


def apply_judge(cfg: dict, on: bool) -> dict:
    """Set the simulated judge for this run, overriding config.yaml.

    config.yaml ships `enabled: false` on purpose, so gates predating W2-1
    reproduce. The SPEC decides, not the ambient config — which is what makes
    the setting part of the frozen registration rather than of whatever the
    machine happened to have on disk that day.
    """
    cfg.setdefault("backtest", {}).setdefault("judge_model", {})["enabled"] = on
    return cfg


def operator_costs_setting(spec: dict):
    """The spec's `operator_costs`: a cost-models block, False, or None.

    None = the spec predates the field (2026-10-28) and runs with no operator
    cost, which is how it was frozen and the only way it reproduces. False =
    deliberately gross. A block = charged on every simulated fill. Same three
    states as judge_setting, for the same reason: "chose not to" and "did not
    know to" are different findings."""
    return spec.get("operator_costs")


def apply_operator_costs(cfg: dict, setting) -> dict:
    """Pin the run's operator cost from the SPEC, overriding config.yaml.

    config.yaml is not in the spec hash, so an `operator_costs` block there
    must never reach a frozen spec: anything but a declared block REMOVES the
    key. Owner decision 2026-10-28 — "Yes: make gates, backtests and the
    drawdown/retire rails use after-cost results first (repete4's override
    included). Entry cost filters later, bot by bot." — is honoured by NEW
    specs declaring a block (register_gate.py refuses one that declares
    nothing), never by quietly re-scoring an old one."""
    bt_cfg = cfg.setdefault("backtest", {})
    if isinstance(setting, dict):
        bt_cfg["operator_costs"] = setting
    else:
        bt_cfg.pop("operator_costs", None)
    return cfg


def run_arm(args) -> tuple:
    """(name, summary, trade pnls, seconds, judge stats, trade keys).

    Top-level to pickle. The KEYS are §55: `significance.compare_paired` needs
    to know which trades the two arms have in common, and a bare list of P&L
    floats cannot say. They ride back from the worker alongside the P&Ls
    because the trades themselves do not survive the process boundary.

    The judge counters are read HERE and returned, not read by the parent after
    the pool closes: arms fan out over `fork`, so each child mutates its own
    copy of the module global and the parent's would stay at zero. Returning
    them is what makes the parent's refusal check see real numbers.
    """
    spec, arm, base_cfg, sym_bars, aux, cash = args
    t0 = time.monotonic()
    cfg = gs.apply_overlay(base_cfg, spec, arm)
    jm.reset_stats()
    result = bt.simulate_ensemble(sym_bars, cfg, cash, **aux)
    return (arm["name"], result.summary(), sig.trade_pnls(result),
            time.monotonic() - t0, jm.stats(), sig.trade_keys(result))


def run_arms(spec, base_cfg, sym_bars, aux, cash, workers: int) -> dict:
    jobs = [(spec, arm, base_cfg, sym_bars, aux, cash) for arm in spec["arms"]]
    if workers <= 1:
        done = [run_arm(j) for j in jobs]
    else:
        import multiprocessing as mp
        try:
            ctx = mp.get_context("fork")     # bars stay shared, not pickled
        except ValueError:                    # pragma: no cover - non-fork host
            print("  (fork unavailable — running serially)")
            done = [run_arm(j) for j in jobs]
            ctx = None
        if ctx is not None:
            with ctx.Pool(min(workers, len(jobs))) as pool:
                done = pool.map(run_arm, jobs)
    return in_spec_order(done, spec["arms"])


# Field positions inside an arm record, as `in_spec_order` builds it. Named
# because positional unpacking of these tuples has now broken the runner once.
SUMMARY, PNLS, SECS, JUDGE_STATS, KEYS = range(5)


def in_spec_order(done: list, arms: list) -> dict:
    """Re-key results by arm name in SPEC order, never completion order.

    `multiprocessing.Pool.map` already preserves input order, so today this is
    defence in depth rather than a live fix — which is exactly why it is a
    separate function with its own test. The first version of that test drove
    `run_arms` end to end and passed whether or not the reordering existed,
    because the pool was doing the work. A guard nothing can falsify is not a
    guard; feeding this shuffled input is what actually exercises it.

    It matters because every clause is expressed relative to `baseline`. If the
    mapping ever slipped, the pass mark would invert silently rather than
    error, which is the worst available failure.
    """
    by_name = {name: (summary, pnls, secs, jstats, keys)
               for name, summary, pnls, secs, jstats, keys in done}
    missing = [a["name"] for a in arms if a["name"] not in by_name]
    if missing:
        raise SystemExit(f"arms produced no result: {missing}")
    return {arm["name"]: by_name[arm["name"]] for arm in arms}


# ---- the pass mark ---------------------------------------------------------

def default_candidate(spec: dict) -> str:
    """Which arm the clauses are applied to when the spec names none.

    Two arms or more: the second, because the first is `baseline` and exists to
    be beaten. Exactly one arm: that arm — a single-arm spec (§39) puts the
    BASELINE ITSELF on trial against benchmarks from its own run.

    Extracted and tested because the inline version was `spec["arms"][1]`, which
    crashed §39 with IndexError AFTER a 388-second run had computed every
    number. Validation had been relaxed to permit one arm; this line was not
    updated, and no test caught it because `evaluate()` is always called with an
    explicit candidate. The gap was between the tested function and its caller.
    """
    if spec.get("candidate"):
        return spec["candidate"]
    arms = spec["arms"]
    return arms[1]["name"] if len(arms) > 1 else arms[0]["name"]


def judge_stats_by_arm(arms: dict) -> dict:
    """{arm name: judge counters}.

    Extracted so it can be TESTED. The inline version destructured the arm
    record positionally, §55 added a sixth element to `run_arm`'s return, and
    the mismatch blew up after every arm had finished computing — the §39
    failure mode exactly. Nothing in the suite touched it because the tests
    drive `evaluate()`, which never sees these records.
    """
    return {name: rec[JUDGE_STATS] for name, rec in arms.items()}


def evaluate(spec: dict, arms: dict, candidate: str, resamples: int) -> dict:
    base_summary = arms["baseline"][SUMMARY]
    cand_summary, cand_pnls = arms[candidate][SUMMARY], arms[candidate][PNLS]
    base_pnls = arms["baseline"][PNLS]

    comp = None
    paired = None          # §55, computed at most once like `comp`
    outcomes = []
    for clause in spec["clauses"]:
        rule = clause["rule"]
        if rule == "enablement_gate":
            ok, reasons = bt.enablement_gate(cand_summary)
            detail = "; ".join(reasons) if reasons else ""
        elif rule == "pf_gt_baseline":
            ok = cand_summary["profit_factor"] > base_summary["profit_factor"]
            detail = (f"{cand_summary['profit_factor']:.3f} vs "
                      f"{base_summary['profit_factor']:.3f}")
        elif rule == "maxdd_within":
            limit = base_summary["max_drawdown_pct"] + clause["pp"]
            ok = cand_summary["max_drawdown_pct"] <= limit
            detail = f"{cand_summary['max_drawdown_pct']:.2f}% vs {limit:.2f}%"
        elif rule == "min_trades":
            ok = cand_summary["n_trades"] >= clause["n"]
            detail = f"{cand_summary['n_trades']} vs {clause['n']}"
        elif rule == "beats_buy_hold":
            bh = cand_summary["buy_hold_return_pct"]
            ok = cand_summary["total_return_pct"] >= bh
            detail = f"{cand_summary['total_return_pct']:+.2f}% vs B&H {bh:+.2f}%"
        elif rule == "beats_exposure_matched":
            # backtest.enablement_gate's own formula, reproduced rather than
            # reinvented: buy-and-hold scaled to the average exposure the arm
            # actually carried. A bot sitting 90% in cash cannot fairly race a
            # 100%-deployed index, and this is the fair bar for it.
            bh = cand_summary["buy_hold_return_pct"]
            deploy = cand_summary.get("avg_deployment_pct", 100.0) / 100.0
            bar = bh * deploy
            ok = cand_summary["total_return_pct"] >= bar
            detail = (f"{cand_summary['total_return_pct']:+.2f}% vs {bar:+.2f}% "
                      f"(B&H {bh:+.2f}% x {deploy:.1%} deployment)")
        elif rule == "beats_benchmark_symbol":
            # §50. THE SURVIVORSHIP-FREE BAR. Every other benchmark in this
            # function derives from the snapshot's own universe, and §48
            # measured those at up to +130pp of survivorship inflation
            # (2000-2006: equal-weight +138.74%, SPY +8.68% over the same
            # bars). This races ONE named instrument whose price already
            # absorbs constituent changes.
            sym = clause["symbol"]
            bm = cand_summary.get("benchmark_return_pct")
            if bm is None or cand_summary.get("benchmark_symbol") != sym:
                # FAIL, never skip. §33 RUN 1 manufactured a VALIDATED verdict
                # out of checks that quietly did not run; "could not be
                # measured" must never read as "passed".
                have = cand_summary.get("benchmark_symbol") or "none"
                ok = False
                detail = (f"benchmark {sym} unavailable (the run computed "
                          f"{have!r}) — cannot score")
            else:
                ok = cand_summary["total_return_pct"] >= bm
                dd = cand_summary.get("benchmark_max_drawdown_pct")
                # maxDD is REPORTED, never gated: matched drawdown was §44's
                # question and was rejected there. It is shown so a
                # return-only pass cannot be misread as a deployable one.
                detail = (f"{cand_summary['total_return_pct']:+.2f}% vs {sym} "
                          f"{bm:+.2f}%")
                if dd is not None:
                    detail += (f" | maxDD {cand_summary['max_drawdown_pct']:.2f}%"
                               f" vs {sym} {dd:.2f}% (reported, not gated)")
        elif rule == "beats_benchmark_risk_adjusted":
            # §51. A DIFFERENT QUESTION FROM THE RULE ABOVE — not "did it make
            # more money" but "did it make more money PER UNIT OF DRAWDOWN".
            # §50 found the incumbent losing to SPY on absolute return in both
            # periods where the benchmark is clean, while drawing down less
            # than SPY in all four. So these two rules genuinely disagree, and
            # a PASS here is NOT the claim §50 rejected.
            #
            # Risk-MATCHED, not a bare ratio: scale the strategy's return by
            # the drawdown gap and ask whether it then clears the benchmark.
            # Same arithmetic, but it keeps the leverage assumption visible.
            sym = clause["symbol"]
            bm = cand_summary.get("benchmark_return_pct")
            bdd = cand_summary.get("benchmark_max_drawdown_pct")
            sdd = cand_summary.get("max_drawdown_pct")
            if bm is None or bdd is None or cand_summary.get(
                    "benchmark_symbol") != sym:
                have = cand_summary.get("benchmark_symbol") or "none"
                ok = False
                detail = (f"benchmark {sym} unavailable (the run computed "
                          f"{have!r}) — cannot score")
            elif not bdd:
                # Nothing to match against. Reported rather than treated as an
                # infinitely easy bar.
                ok = False
                detail = f"{sym} drawdown is 0 — no risk to match, cannot score"
            elif not sdd:
                # The scale factor is undefined. A multi-year arm with zero
                # drawdown is a bug or a bot that never traded, not an infinite
                # edge — and (a)/(b) would normally have caught it first.
                ok = False
                detail = ("strategy drawdown is 0 — scale factor undefined, "
                          "cannot score")
            else:
                matched = cand_summary["total_return_pct"] * (bdd / sdd)
                ok = matched >= bm
                detail = (f"{cand_summary['total_return_pct']:+.2f}% at "
                          f"{sdd:.2f}% maxDD -> {matched:+.2f}% levered to "
                          f"{sym}'s {bdd:.2f}% maxDD, vs {sym} {bm:+.2f}%")
        elif rule == "deployment_at_least":
            # §41. Without this clause the latch fix could clear every risk
            # clause by changing nothing at all — a bot that still never
            # invests trivially fails to make drawdown worse. This is the
            # clause that makes "the breaker actually re-closed" a machine-
            # checked pass condition rather than a claim in the write-up.
            dep = cand_summary.get("avg_deployment_pct", 0.0)
            ok = dep >= clause["pct"]
            detail = f"{dep:.2f}% deployed vs {clause['pct']:.1f}% required"
        elif rule in ("significantly_better", "not_worse"):
            if not base_pnls or not cand_pnls:
                # An arm with no closed trades cannot be compared. Recording
                # this as FAIL rather than skipping it keeps "we could not
                # measure" from reading as "it did not pass" — §33 RUN 1's
                # two silent zero-trade arms manufactured a VALIDATED verdict.
                ok, detail = False, "an arm produced no closed trades"
            else:
                comp = comp or sig.compare(
                    base_pnls, cand_pnls,
                    n_comparisons=spec.get("bonferroni_k", 1),
                    resamples=resamples)
                ok = comp.significant if rule == "significantly_better" \
                    else comp.not_worse
                detail = comp.describe()
        elif rule in ("significantly_better_paired", "not_worse_paired"):
            # §55. Same two questions as the rules above, asked with common
            # random numbers so the variance the two arms SHARE cancels instead
            # of being counted twice. Measured against a known-zero effect:
            # nominal 0.0500 coverage, and 0.645 power where the independent
            # estimator reaches 0.025.
            base_keys = arms["baseline"][KEYS]
            cand_keys = arms[candidate][KEYS]
            if not base_pnls or not cand_pnls:
                ok, detail = False, "an arm produced no closed trades"
            elif not base_keys or not cand_keys:
                # Cannot pair without identities. FAIL rather than silently
                # falling back to `compare()`: a spec that registered the
                # paired rule and got scored by the independent one would be a
                # different claim than the one frozen.
                ok = False
                detail = ("no trade keys available — cannot pair (the arm "
                          "predates §55 or the runner dropped them)")
            else:
                paired = paired or sig.compare_paired(
                    base_keys, base_pnls, cand_keys, cand_pnls,
                    n_comparisons=spec.get("bonferroni_k", 1),
                    resamples=resamples)
                ok = paired.significant \
                    if rule == "significantly_better_paired" else paired.not_worse
                detail = paired.describe()
                # The nested form when it applies, REPORTED and not gated: it
                # is the sharper reading, but gating on whichever of two
                # instruments looks better is selection. The clause that was
                # registered is the one that decides.
                nested = sig.disjoint_report(
                    base_keys, base_pnls, cand_keys, cand_pnls,
                    n_comparisons=spec.get("bonferroni_k", 1),
                    resamples=resamples)
                if nested is not None:
                    detail += (f" | KEPT vs REMOVED (reported, not gated): "
                               f"{nested.describe()}")
        elif rule == "no_interior_optimum":
            # §55, and it is §23's own conclusion executed instead of recalled.
            # The only rule that reads EVERY arm rather than the candidate: the
            # question is about the SHAPE of the response across a grid, which
            # no single arm can answer.
            metric, order = clause["metric"], clause["order"]
            better = clause.get("better", "high")
            absent = [n for n in order
                      if n not in arms or arms[n][SUMMARY].get(metric) is None]
            if absent:
                # FAIL, never skip — §33 RUN 1 manufactured a VALIDATED verdict
                # out of checks that quietly did not run.
                ok = False
                detail = f"cannot score {metric}: arms {absent} produced no value"
            else:
                vals = [float(arms[n][SUMMARY][metric]) for n in order]
                best = max(vals) if better == "high" else min(vals)
                idx = vals.index(best)
                interior = 0 < idx < len(vals) - 1
                ok = not interior
                shape = " -> ".join(f"{v:.3f}" for v in vals)
                word = "peak" if better == "high" else "trough"
                where = "INTERIOR" if interior else (
                    "flat" if len(set(vals)) == 1 else "at an end")
                detail = (f"{metric} across {order}: {shape}; {word} at index "
                          f"{idx} ({order[idx]}), {where}")
                if interior:
                    detail += (" — a lone interior optimum is the signature of "
                               "a fitted parameter, not a mechanism (§23)")
        else:                                  # pragma: no cover - validated
            raise SystemExit(f"unknown clause rule {rule!r}")
        outcomes.append({"id": clause["id"], "rule": rule,
                         "pass": bool(ok), "detail": detail})

    return {"clauses": outcomes, "passed": all(c["pass"] for c in outcomes),
            "comparison": comp.describe() if comp else None}


# ---- output ----------------------------------------------------------------

def fmt(name: str, s: dict, secs: float) -> str:
    return (f"  {name:<24} ret {s['total_return_pct']:+7.2f}%  "
            f"PF {s['profit_factor']:5.3f}  maxDD {s['max_drawdown_pct']:5.2f}%  "
            f"trades {s['n_trades']:5d}  symbols {s['n_symbols_traded']:3d}  "
            f"deploy {s['avg_deployment_pct']:6.2f}%  ({secs:.0f}s)")


def fmt_exposure(name: str, s: dict) -> str | None:
    """GROSS beside NET, and only for an arm that actually shorted.

    `deploy` above is NET, so a 130/30 arm and a flat-100%-long arm print the
    SAME number. Without this line a reader comparing the two would conclude
    the short leg changed nothing about the book's size, which is the opposite
    of what happened.

    Suppressed for a long-only arm on purpose: there gross IS net, and a second
    identical column is noise that trains people to skim the row."""
    if not s.get("n_short_trades"):
        return None
    return (f"  {'':<24} gross {s.get('gross_exposure_avg_pct', 0.0):6.2f}% "
            f"(max {s.get('gross_exposure_max_pct', 0.0):6.2f}%)  "
            f"net {s.get('net_exposure_avg_pct', 0.0):+6.2f}% "
            f"(min {s.get('net_exposure_min_pct', 0.0):+6.2f}%)  "
            f"shorts {s['n_short_trades']:4d} in {s.get('n_short_symbols', 0):3d} names")


def fmt_rails(name: str, s: dict) -> str | None:
    """WHICH RAIL REFUSED WHAT. `simulate_ensemble` has collected this per rail
    since §40 and `run_gate` never printed it, so "does the new rail bind?" had
    no answer in the gate report even though the data was in the Result.

    §48's headline finding — the drawdown rail blocking 94.58% of signals — was
    reached by reading a census by hand. Printing it makes that the default
    rather than an investigation."""
    census = s.get("census") or {}
    blocked = census.get("blocked") or {}
    if not blocked:
        return None
    top = sorted(blocked.items(), key=lambda kv: -kv[1])
    total = sum(blocked.values())
    shown = "  ".join(f"{k} {v}" for k, v in top[:6])
    return (f"  {'':<24} signals {census.get('signals', 0):6d}  "
            f"blocked {total:6d}  |  {shown}")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec_id")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--registrations", default=gs.REGISTRATIONS)
    p.add_argument("--verdicts", default=VERDICTS)
    p.add_argument("--trials", default=bt.DEFAULT_TRIALS_PATH,
                   help="append-only trial log; every arm of every run "
                        "is written here, refused or not")
    p.add_argument("--spec-dir", default=SPEC_DIR)
    p.add_argument("--dry-run", action="store_true",
                   help="check the freeze and the data, then stop")
    args = p.parse_args()

    spec = gs.load(os.path.join(args.spec_dir, f"{args.spec_id}.yaml"))
    reg = check_frozen(spec, args.registrations)

    judge = judge_setting(spec)
    op_setting = operator_costs_setting(spec)
    op_block = bt.operator_costs_block({"backtest": {
        "operator_costs": op_setting if isinstance(op_setting, dict) else None}})
    print(f"{spec['id']} — {spec['title']}")
    print(f"claim {spec['claim']} | K={spec.get('bonferroni_k', 1)} | "
          f"frozen {reg['registered_at'][:19]} | "
          f"sha {reg['spec_sha256'][:16]}… | "
          f"judge {'ON' if judge else 'OFF' if judge is False else 'UNDECLARED'}")
    print("operator cost "
          + (f"ON — {bt.cost_models.describe(op_block, bt.OPERATOR_MARKET)} "
             f"(block sha {bt.operator_costs_sha256(op_block)[:16]}…)"
             if op_block is not None
             else "OFF (declared gross)" if op_setting is False
             or isinstance(op_setting, dict)
             else "UNDECLARED (predates 2026-10-28: gross, as frozen)")
          + "\n")
    if judge is None:
        print("=" * 72)
        print("THIS SPEC PREDATES THE `judge_model` FIELD (W2-1, 2026-07-29).")
        print("It runs WITHOUT the simulated judge, which is how it was frozen")
        print("and the only way it reproduces. Live, the judge cuts 58% of buys")
        print("and vetoes 2.4% — so this run measures a bot that does not exist")
        print("in production. That is divergence #8, and it is why §35-§41 must")
        print("be read as judge-less measurements.")
        print("=" * 72 + "\n")
    print(f"prior: {spec['prior'].strip()}\n")
    verify_data(spec)
    if args.dry_run:
        print("\n--dry-run: freeze and data verified, nothing scored.")
        return 0

    base_cfg = apply_operator_costs(
        apply_judge(bt.load_config(), bool(judge)), op_setting)
    if judge:
        # Fail here, not 300 seconds into the run. A missing or too-small
        # calibration raises CalibrationError by design rather than silently
        # sizing at 1.0 — but only on first use, which without this line is
        # deep inside a worker process where the traceback is worth less.
        cal = jm.load_calibration()
        print(f"  judge calibration n={cal['n_judged_buys']} "
              f"({cal['observed_from']}..{cal['observed_to']})  "
              f"veto {cal['veto_rate']:.1%}  "
              f"downsize {cal['downsize_rate']:.1%}  "
              f"mean scale {cal['mean_scale']:.3f}")
    sym_bars = bt.load_bars_file(spec["snapshot"]["path"])
    aux = {k: bt.load_bars_file(v["path"])
           for k, v in (spec.get("aux") or {}).items()}

    split = spec.get("split")
    if split:
        # Per-symbol fractional cut, matching gate_budget.py and
        # gate_wide_universe.py exactly. Per-symbol rather than a global date
        # because symbols have different history lengths; a global cut would
        # silently give short-history names no OOS at all.
        cut = {s: int(len(b) * split["fraction"]) for s, b in sym_bars.items()}
        sym_bars = {s: b[cut[s]:] for s, b in sym_bars.items()}
        print(f"\n  split {split['fraction']:.0%} | scoring OOS only")

    print(f"\n  {len(sym_bars)} symbols | {len(spec['arms'])} arms | "
          f"{args.workers} worker(s)\n", flush=True)

    t0 = time.monotonic()
    arms = run_arms(spec, base_cfg, sym_bars, aux,
                    spec.get("cash", 100_000.0), args.workers)
    wall = time.monotonic() - t0
    for name, rec in arms.items():
        summary, secs = rec[SUMMARY], rec[SECS]
        print(fmt(name, summary, secs), flush=True)
        for extra in (fmt_exposure(name, summary), fmt_rails(name, summary)):
            if extra:
                print(extra, flush=True)

    # LOG EVERY ARM, HERE, BEFORE ANY REFUSAL BELOW CAN DISCARD THE RUN.
    #
    # This runner never called append_trial until 2026-08-22. It is the runner
    # behind 95 of 102 registered specs, so the trial log stopped on
    # 2026-07-27 while the project kept running gates — and the audit could
    # not compute a Deflated Sharpe because nobody could say how many things
    # had been tried. Declared K was 16; the arm count in verdicts.jsonl alone
    # was 225.
    #
    # Placement is the whole point:
    #   * HERE in main(), once, in the parent — not in run_arm, which executes
    #     in forked workers and would interleave appends. A trial log that
    #     depends on --workers would hand this runner's determinism guarantee
    #     (tests/test_run_gate_reproduces.py) a counterexample.
    #   * BEFORE the judge-consistency refusal below, which SystemExits and
    #     writes no verdict. Those arms were computed and the trial WAS SPENT.
    #     A log that records only runs surviving every refusal is precisely
    #     the dishonest accounting this exists to end.
    #   * AFTER the --dry-run return above, which computes nothing.
    for name, rec in arms.items():
        bt.append_trial(args.trials, {
            "phase": "oos" if spec.get("split") else "single",
            "arm": name,
            "section": spec["id"],
            "spec_sha256": reg["spec_sha256"],
            "claim": spec["claim"],
            "bonferroni_k": spec.get("bonferroni_k", 1),
            "judge_model": bool(judge),
            "snapshot_sha256": spec["snapshot"]["sha256"],
            # 2026-10-28. Absent for a spec that predates the field, so its
            # trial rows keep their old shape; the arm summary below carries
            # the total paid whenever a cost was charged.
            **({"operator_costs_sha256": bt.operator_costs_sha256(op_block)}
               if "operator_costs" in spec else {}),
            "workers": args.workers,
            **rec[SUMMARY],
        })

    # Index, not positional unpack. §55 added a sixth element to `run_arm`'s
    # return — the trade keys `compare_paired` needs — and updated three of the
    # four places that read it. This one still destructured four, and it blew
    # up AFTER every arm had finished computing: exactly the §39 failure that
    # took a 388-second run to surface. Indexing does not silently depend on
    # the arity, so the next field to be added cannot reopen this.
    judged = judge_stats_by_arm(arms)
    if judge:
        print()
        for name, js in judged.items():
            print(f"  judge on {name:<22} sized {js['sized']:6d}  "
                  f"cut {js['cut']:6d}  vetoed {js['vetoed']:5d}  "
                  f"truncated to 0 {js['zeroed']:5d}")
        # THE CHECK THAT MAKES THE FLAG MEAN SOMETHING.
        #
        # `judge_model: true` is a claim the run makes about itself. Without
        # this, a broken wiring, an empty histogram or a future refactor that
        # stops calling judge_model.apply() would produce a verdict labelled
        # judge-on and measured judge-off — which is precisely the failure that
        # went unnoticed from §35 to §41, wearing a different hat.
        #
        # Refusing BEFORE the verdict is written is the point: a rejected run
        # costs the compute again, while a wrong row in verdicts.jsonl is a
        # false record that later work is built on.
        total = sum(js["sized"] for js in judged.values())
        acted = sum(js["cut"] + js["vetoed"] for js in judged.values())
        if total == 0 or acted == 0:
            raise SystemExit(
                f"\nREFUSING to record {spec['id']}: the spec declares "
                f"`judge_model: true`,\nbut the model never acted — "
                f"{total} entries sized, {acted} cut or vetoed.\n\n"
                f"A verdict labelled judge-on and measured judge-off is worse "
                f"than no\nverdict: it looks like it closed divergence #8. "
                f"Nothing has been written.\n\n"
                f"Check that backtest.simulate_ensemble still calls "
                f"judge_model.apply(), and\nthat "
                f"knowledge/judge_calibration.json has a non-degenerate "
                f"histogram.")

    bh = arms["baseline"][SUMMARY].get("buy_hold_return_pct")
    if bh is not None:
        print(f"\n  buy-and-hold on this universe: {bh:+.2f}%")

    family = spec.get("family")
    if family:
        # §34's prescription, verbatim: "require the whole family to move. If an
        # effect is real it should lift every arm in the family, not one. A
        # result that appears in one arm and not its neighbours is what
        # selection noise looks like."
        #
        # So every member faces the full pass mark and ONE failure sinks the
        # claim. Nothing is chosen, which is the point — no selector can be
        # credited or blamed for the verdict, and §34 established this repo has
        # no selector worth trusting.
        per_arm = {name: evaluate(spec, arms, name, args.resamples)
                   for name in family}
        verdict = {"family": family, "per_arm": per_arm,
                   "passed": all(v["passed"] for v in per_arm.values()),
                   "clauses": [], "comparison": None}

        print(f"\n-- PRE-REGISTERED PASS MARK "
              f"(family of {len(family)}; ALL must clear) --")
        for name in family:
            v = per_arm[name]
            print(f"\n  {name}  ->  {'CLEARS' if v['passed'] else 'FAILS'}")
            for c in v["clauses"]:
                print(f"    [{'PASS' if c['pass'] else 'FAIL'}] ({c['id']}) "
                      f"{c['rule']}"
                      + (f"  {c['detail']}" if c["detail"] else ""))
        cleared = [n for n in family if per_arm[n]["passed"]]
        print(f"\n  {len(cleared)}/{len(family)} members clear"
              + (f": {', '.join(cleared)}" if cleared else ""))
    else:
        candidate = default_candidate(spec)
        verdict = evaluate(spec, arms, candidate, args.resamples)
        print(f"\n-- PRE-REGISTERED PASS MARK ({candidate}) --")
        for c in verdict["clauses"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] ({c['id']}) "
                  f"{c['rule']}"
                  + (f"  {c['detail']}" if c["detail"] else ""))

    if spec["claim"] == "DIAGNOSTIC":
        # Say it at the point of the result, not only in the header. Someone
        # reading a scrollback sees the verdict line; the banner has to be
        # adjacent to it or it may as well not exist.
        print(f"\n{'=' * 68}\nDIAGNOSTIC — this decides NOTHING. It is not a "
              f"claim and cannot\nenable, reject or support anything. See the "
              f"spec's `prior` for why.\n{'=' * 68}")

    print(f"\nVERDICT: {'PASS' if verdict['passed'] else 'REJECTED'}  "
          f"({wall:.0f}s wall)")
    if not verdict["passed"]:
        print("  Nothing is enabled. The incumbent stands.")
    else:
        print("  PROVISIONAL PASS — a recommendation, not an action. Enabling "
              "is the owner's\n  call and every registered constraint still "
              "applies.")

    record = {
        "id": spec["id"], "ran_at": datetime.now(timezone.utc).isoformat(),
        "spec_sha256": reg["spec_sha256"],
        "candidate": None if family else candidate,
        "family": family,
        "per_arm": {n: v["clauses"] for n, v in verdict["per_arm"].items()}
                   if family else None,
        "passed": verdict["passed"], "clauses": verdict["clauses"],
        "comparison": verdict["comparison"], "wall_seconds": round(wall, 1),
        "workers": args.workers,
        # Recorded so verdicts.jsonl can be read years from now without having
        # to reconstruct which config was on disk. `null` means the spec
        # predates the field — §35-§41 — and is NOT the same as `false`.
        "judge_model": judge,
        "judge_stats": judged if judge else None,
        # Owner decision 2026-10-28: which operator cost the run charged — the
        # block itself (null = the spec predates the field, false = declared
        # gross) and its hash — and, per arm, what it paid
        # (arms.<name>.operator_cost_paid_usd).
        "operator_costs": op_setting,
        "operator_costs_sha256": bt.operator_costs_sha256(op_block),
        "operator_cost_paid_usd": {
            name: rec[SUMMARY].get("operator_cost_paid_usd", 0.0)
            for name, rec in arms.items()},
        "arms": {name: rec[SUMMARY] for name, rec in arms.items()},
    }
    os.makedirs(os.path.dirname(args.verdicts) or ".", exist_ok=True)
    with open(args.verdicts, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"\nrecorded -> {args.verdicts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
