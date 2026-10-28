"""Daily decay check: has the live edge fallen to random-entry levels?

ALERT-ONLY BY DESIGN (2026-08-01 owner decision). This module does NOT call
`risk.engage_halt` and does not touch config. It reads, compares, prints and
optionally alerts — nothing else. Two reasons:

  1. The statistic has no track record yet. A false positive should cost an
     email, not a stopped book. Auto-HALT is a later upgrade, once the monitor
     has fired enough times to be trusted.
  2. It makes invariant #2 ("anything the model produces may only SHRINK risk")
     hold by construction rather than by argument: a component that can only
     emit text cannot enlarge risk.

Reads through `store.read_only_reader`, the same zero-write path the publisher
and /healthz use, so the monitor structurally cannot corrupt the ledger it is
inspecting.

Exit codes
----------
  0  no alert  (BEATS_RANDOM, INDISTINGUISHABLE, or INSUFFICIENT_DATA)
  1  alert     (WORSE_THAN_RANDOM)
  2  could not run (bad config / no bars) — distinct from "ran and found
     nothing", because a monitor that failed to run must never read as a pass.
"""
import argparse
import json
import sys

import randombaseline as rb


DEFAULT_CONFIG = "config.yaml"


def _load_cfg(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_records(cfg: dict) -> list[dict]:
    import store as store_mod
    ledger_path = cfg.get("memory", {}).get("ledger_path",
                                            "memory/ledger.jsonl")
    return store_mod.read_only_reader(cfg, ledger_path).read_all()


def fetch_bars(cfg: dict, symbols: list[str], lookback: int) -> dict:
    """Daily bars per symbol from the broker. Network — kept out of
    randombaseline so the engine stays pure and offline-testable."""
    from broker import Broker
    b = Broker(cfg)
    timeframe = cfg.get("strategy", {}).get("timeframe", "1Day")
    out = {}
    for s in symbols:
        try:
            out[s] = b.bars(s, timeframe, lookback)
        except Exception as e:                      # noqa: BLE001
            print(f"  warn: no bars for {s}: {e}", file=sys.stderr)
    return out


def run(cfg: dict, *, strategy: str | None = None, min_trades: int,
        samples: int, lookback: int, offline_bars: dict | None = None
        ) -> tuple[rb.Assessment, dict]:
    records = load_records(cfg)
    trades = rb.closed_trades(records)
    if strategy:
        trades = [t for t in trades if t.get("strategy") == strategy]

    symbols = sorted({t["symbol"] for t in trades})
    if offline_bars is not None:
        bars = offline_bars
    elif len(trades) < min_trades or not symbols:
        # Short-circuit: the verdict is already INSUFFICIENT_DATA and no
        # baseline can change it. Fetching bars here would spend a network
        # round-trip to learn nothing — and worse, a fetch failure would be
        # reported as "could not run" when the honest answer is already known
        # offline. Thin data must degrade to a clean verdict, not an error.
        bars = {}
    else:
        bars = fetch_bars(cfg, symbols, lookback)

    slippage = float(cfg.get("backtest", {}).get("slippage_bps", 0) or 0)
    fees, costs = operator_costs(cfg)
    a = rb.check(records, bars, min_trades=min_trades, n_samples=samples,
                 slippage_bps=slippage, strategy=strategy, fees=fees,
                 costs=costs)
    meta = {"symbols": symbols, "n_closed_trades": len(trades),
            "slippage_bps": slippage, "strategy": strategy or "all",
            "basis": "after_operator_cost",
            "operator_cost_paid": round(sum(fees.values()), 2),
            "cost_model": (costs or {}).get("equities", {}).get("model",
                                                                "off")}
    return a, meta


def operator_costs(cfg: dict) -> tuple[dict, dict]:
    """(ref -> fee charged, the cost models in force) for the after-cost check.

    Owner decision 2026-10-28: the decay monitor reads each trade's return LESS
    its own operator cost, and charges the random null the same model. The fee
    log is read STRICTLY: an unreadable file, a torn line or a line in another
    currency raises operator_settings.FeeLedgerError, and main() turns that into
    exit 2 — "could not run" — because a monitor that silently scored the trades
    as if they had paid nothing would read as a pass it never earned."""
    import operator_settings
    import statepaths
    op_dir = statepaths.operator_dir(cfg)
    return (operator_settings.fees_by_ref(op_dir),
            operator_settings.load(op_dir).costs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--strategy", default=None,
                    help="restrict to one strategy (default: all)")
    ap.add_argument("--min-trades", type=int, default=rb.DEFAULT_MIN_TRADES)
    ap.add_argument("--samples", type=int, default=rb.DEFAULT_SAMPLES)
    ap.add_argument("--lookback", type=int, default=400,
                    help="daily bars to fetch per symbol")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--alert", action="store_true",
                    help="send an alert when the verdict is WORSE_THAN_RANDOM")
    args = ap.parse_args(argv)

    # launchd runs a bare environment: no shell profile, no .env unless the
    # entrypoint loads it explicitly (§7). Omitting this was one of the two
    # faults that made every alert silently fail to reach a phone.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:                             # tests run without it
        pass

    try:
        cfg = _load_cfg(args.config)
    except Exception as e:                          # noqa: BLE001
        print(f"decaycheck: cannot read {args.config}: {e}", file=sys.stderr)
        return 2

    try:
        a, meta = run(cfg, strategy=args.strategy, min_trades=args.min_trades,
                      samples=args.samples, lookback=args.lookback)
    except Exception as e:                          # noqa: BLE001
        print(f"decaycheck: run failed: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({**meta, "verdict": a.verdict, "alert": a.alert,
                          "n_trades": a.n_trades, "min_trades": a.min_trades,
                          "actual_mean_pct": a.actual_mean_pct,
                          "null_mean_pct": a.null_mean_pct,
                          "percentile": a.percentile,
                          "n_samples": a.n_samples, "seed": a.seed,
                          "detail": a.detail}, indent=2))
    else:
        print(f"decaycheck [{meta['strategy']}] {a.describe()}")

    if a.alert and args.alert:
        try:
            import alerting
            alerting.send("Repete: strategy may have decayed", a.describe())
        except Exception as e:                      # noqa: BLE001
            print(f"  warn: alert delivery failed: {e}", file=sys.stderr)

    return 1 if a.alert else 0


if __name__ == "__main__":
    sys.exit(main())
