"""Has the edge decayed? Compare realised trades against random entries.

Why this exists
---------------
The daily-loss kill switch (`risk.engage_halt`) answers "am I losing money too
fast". It does NOT answer "did my entry signal stop carrying information".
Those are different failure modes: a strategy that has been arbitraged away
bleeds slowly and never trips a loss limit. Nothing in this repo measured that
until now.

The null being tested
---------------------
*Would entering on random dates have done as well?*

Random entries are drawn on the SAME symbols, over the SAME bar series, with a
holding-period distribution RESAMPLED FROM THE ACTUAL TRADES, and the same
slippage model. Matching exposure is what makes the comparison honest — an
unmatched null holds a different amount of market risk and will read as "edge"
in any up market.

The verdict is a percentile: where does the strategy's mean return per trade sit
inside the distribution of means that random timing would have produced?

Pre-registered thresholds (frozen before first run, see
knowledge/backtest_candidates.md §RANDOM-ENTRY DECAY MONITOR)
------------------------------------------------------------
  percentile < 5.0     -> WORSE_THAN_RANDOM          (alert)
  5.0 <= pct < 95.0    -> INDISTINGUISHABLE_FROM_RANDOM
  percentile >= 95.0   -> BEATS_RANDOM
  n_trades < min_trades-> INSUFFICIENT_DATA          (never a pass)

INSUFFICIENT_DATA is a distinct state on purpose. A monitor that returns
"healthy" on an empty book is the "empty is not the same as absent" trap, and
this repo has been bitten by it before.

Known asymmetry, stated rather than hidden
------------------------------------------
Real trades carry stops and take-profits; these null trades do not — they enter,
hold, and exit at the close. Resampling the REALISED holding periods absorbs
much of this (a trade stopped out on day 2 contributes a 2-bar holding), but not
all of it: an unprotected null takes the full loss where a real trade cut it.
That biases the null DOWNWARD, which makes the strategy look better, which makes
this monitor **lenient** — it will under-fire rather than over-fire. Erring
toward under-firing is the right direction for an alert whose false positives
cost attention, but it must not be mistaken for a conservative test of edge.

No network, no I/O, deterministic seed — callers pass bars in. Same shape as
`scorecard.py` and `significance.py`.

AFTER THE OPERATOR COST (owner decision 2026-10-28)
---------------------------------------------------
"Yes: make gates, backtests and the drawdown/retire rails use after-cost results
first." The realised side reads each trade's return LESS its own operator cost
(`fees`, joined on the fee line's ref: "buy:<trade_id>" + "close:<trade_id>",
over the trade's entry notional). The null side is charged the SAME cost model
(`costs`, a cost_models block) on both of its legs, on a notional resampled from
the realised trades, so the two sides pay alike — charging only the realised
side would make random timing look better by exactly the cost. The thresholds
above are unchanged. With no fees and the model off (or `costs` None) both
sides read and draw exactly as before: the notional is drawn only when a cost
is on, so the seeded stream does not move.
"""
from dataclasses import dataclass
import random

# Frozen defaults. Changing these changes what counts as decay, so they live
# here as named constants rather than as magic numbers at the call sites.
DEFAULT_MIN_TRADES = 20
DEFAULT_SAMPLES = 2000
DEFAULT_SEED = 20260801
ALERT_BELOW_PERCENTILE = 5.0
STRONG_ABOVE_PERCENTILE = 95.0

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
WORSE_THAN_RANDOM = "WORSE_THAN_RANDOM"
INDISTINGUISHABLE = "INDISTINGUISHABLE_FROM_RANDOM"
BEATS_RANDOM = "BEATS_RANDOM"


@dataclass
class Assessment:
    """Result of one decay check. `alert` is the only field callers must act on."""
    verdict: str
    n_trades: int
    min_trades: int
    actual_mean_pct: float | None = None
    null_mean_pct: float | None = None
    percentile: float | None = None
    n_samples: int = 0
    seed: int = DEFAULT_SEED
    detail: str = ""

    @property
    def alert(self) -> bool:
        """Only a measured shortfall alerts. Thin data must never alert —
        otherwise the monitor cries wolf every day the book is quiet, and gets
        muted, which is the same as not having it."""
        return self.verdict == WORSE_THAN_RANDOM

    def describe(self) -> str:
        if self.verdict == INSUFFICIENT_DATA:
            return (f"{INSUFFICIENT_DATA}: {self.n_trades} closed trade(s), "
                    f"need {self.min_trades}. No decay claim either way.")
        return (f"{self.verdict}: strategy {self.actual_mean_pct:+.3f}%/trade "
                f"(n={self.n_trades}) sits at the {self.percentile:.1f}th "
                f"percentile of {self.n_samples} random-entry samples "
                f"(random mean {self.null_mean_pct:+.3f}%/trade, seed {self.seed})")


def holding_bars(bars: list[dict], entry_ts: str, exit_ts: str) -> int | None:
    """Trading-bar distance between two timestamps, counted on THIS series.

    Calendar days would overstate holding on weekends and holidays; the null
    must hold the same number of *bars* the real trade did, not the same number
    of dates.
    """
    entry_d, exit_d = entry_ts[:10], exit_ts[:10]
    i = j = None
    for k, b in enumerate(bars):
        d = str(b.get("ts", ""))[:10]
        if i is None and d >= entry_d:
            i = k
        if d <= exit_d:
            j = k
    if i is None or j is None or j <= i:
        return None
    return j - i


def _cost_on(costs: dict | None) -> bool:
    return bool(costs) and (costs.get("equities") or {}).get("model",
                                                             "off") != "off"


def _leg_cost_pct(costs: dict, notional: float, entry: float,
                  exit_: float) -> float:
    """Both legs' operator cost as % of the entry notional. Shares are the
    notional at the entry fill; each leg is one order under the equities model
    (per-share with its minimum and its 1% cap), as live charges it."""
    import cost_models
    shares = notional / entry
    cin, _ = cost_models.order_cost(costs, "equities", shares=shares,
                                    notional=shares * entry)
    cout, _ = cost_models.order_cost(costs, "equities", shares=shares,
                                     notional=shares * exit_)
    return (cin + cout) / notional * 100.0


def random_entry_pct(bars: list[dict], holding: int, rng: random.Random,
                     slippage_bps: float = 0.0, costs: dict | None = None,
                     notional: float | None = None) -> float | None:
    """One random entry: decide on bar i, fill at the OPEN of bar i+1, exit at
    the CLOSE of bar i+holding. Slippage is charged against you on both legs.

    Entering at the next open rather than the decision bar's close is the same
    no-look-ahead rule the backtester uses; a null that could act on the close
    it decided from would be easier to beat than reality.
    """
    # need i+1 (entry bar) and i+holding (exit bar) to exist
    last_i = len(bars) - 1 - holding
    if holding < 1 or last_i < 0:
        return None
    i = rng.randint(0, last_i)
    entry = float(bars[i + 1]["open"]) * (1 + slippage_bps / 1e4)
    exit_ = float(bars[i + holding]["close"]) * (1 - slippage_bps / 1e4)
    if entry <= 0:
        return None
    pct = (exit_ - entry) / entry * 100.0
    if _cost_on(costs) and notional and notional > 0:
        pct -= _leg_cost_pct(costs, notional, entry, exit_)
    return pct


def null_distribution(bars_by_symbol: dict, symbols: list[str],
                      holdings: list[int], n_trades: int,
                      n_samples: int = DEFAULT_SAMPLES,
                      seed: int = DEFAULT_SEED,
                      slippage_bps: float = 0.0,
                      costs: dict | None = None,
                      notionals: list[float] | None = None) -> list[float]:
    """Distribution of MEAN return per trade under random entry timing.

    Each sample builds a whole synthetic track record of `n_trades` trades —
    symbols resampled from `symbols`, holding periods resampled from
    `holdings` — and records its mean. Comparing means (not individual trades)
    is the right unit: the question is whether the strategy's *record* beats a
    random record of the same size and shape.
    """
    usable = [s for s in symbols if len(bars_by_symbol.get(s) or []) > 2]
    if not usable or not holdings or n_trades < 1:
        return []
    charge = _cost_on(costs) and bool(notionals)
    rng = random.Random(seed)
    out = []
    for _ in range(n_samples):
        pcts = []
        for _ in range(n_trades):
            for _attempt in range(8):     # a few retries when a draw doesn't fit
                sym = usable[rng.randrange(len(usable))]
                hold = holdings[rng.randrange(len(holdings))]
                # Drawn ONLY when a cost is charged, so a cost-free run keeps
                # the exact seeded stream it always had.
                size = (notionals[rng.randrange(len(notionals))]
                        if charge else None)
                p = random_entry_pct(bars_by_symbol[sym], hold, rng,
                                     slippage_bps, costs=costs if charge
                                     else None, notional=size)
                if p is not None:
                    pcts.append(p)
                    break
        if pcts:
            out.append(sum(pcts) / len(pcts))
    return out


def percentile_of(value: float, distribution: list[float]) -> float | None:
    """Share of the distribution at or below `value`, as a percentage."""
    if not distribution:
        return None
    below = sum(1 for d in distribution if d <= value)
    return below / len(distribution) * 100.0


def assess(actual_pcts: list[float], null_means: list[float], *,
           min_trades: int = DEFAULT_MIN_TRADES,
           seed: int = DEFAULT_SEED) -> Assessment:
    """Apply the frozen thresholds. Pure decision logic, no sampling."""
    n = len(actual_pcts)
    if n < min_trades:
        return Assessment(verdict=INSUFFICIENT_DATA, n_trades=n,
                          min_trades=min_trades, seed=seed,
                          detail="below minimum closed-trade count")
    if not null_means:
        return Assessment(verdict=INSUFFICIENT_DATA, n_trades=n,
                          min_trades=min_trades, seed=seed,
                          detail="no usable bars to build a random baseline")

    actual_mean = sum(actual_pcts) / n
    pct = percentile_of(actual_mean, null_means)
    if pct < ALERT_BELOW_PERCENTILE:
        verdict = WORSE_THAN_RANDOM
    elif pct >= STRONG_ABOVE_PERCENTILE:
        verdict = BEATS_RANDOM
    else:
        verdict = INDISTINGUISHABLE
    return Assessment(verdict=verdict, n_trades=n, min_trades=min_trades,
                      actual_mean_pct=actual_mean,
                      null_mean_pct=sum(null_means) / len(null_means),
                      percentile=pct, n_samples=len(null_means), seed=seed)


def closed_trades(records: list[dict]) -> list[dict]:
    """Join `outcome` records back to their `decision` by trade_id.

    Returns dicts carrying symbol, entry_ts, exit_ts and pnl_pct — everything
    the check needs and nothing else. Records missing any of those are dropped
    rather than defaulted, so a malformed row cannot quietly become a 0% trade.
    """
    decisions = {r.get("trade_id"): r for r in records
                 if r.get("type") == "decision"}
    out = []
    for r in records:
        if r.get("type") != "outcome" or r.get("pnl_pct") is None:
            continue
        d = decisions.get(r.get("trade_id"))
        if not d or not d.get("symbol"):
            continue
        entry_ts = d.get("entry_ts") or d.get("ts")
        exit_ts = r.get("ts")
        if not entry_ts or not exit_ts:
            continue
        out.append({"symbol": d["symbol"], "entry_ts": entry_ts,
                    "exit_ts": exit_ts, "pnl_pct": float(r["pnl_pct"]),
                    "strategy": d.get("strategy"),
                    # 2026-10-28: what joins a trade to its own operator cost.
                    "trade_id": r.get("trade_id"),
                    "notional": abs(float(d.get("entry_price") or 0)
                                    * float(d.get("qty") or 0))})
    return out


def after_cost(trades: list[dict], fees: dict | None) -> list[dict]:
    """Each trade's pnl_pct LESS its own operator cost (buy + close fee over its
    entry notional). Empty/None `fees` is the identity. A trade with a cost but
    no notional cannot be converted to a percentage and raises, rather than
    being scored as if it had paid nothing."""
    if not fees:
        return trades
    out = []
    for t in trades:
        tid = t.get("trade_id")
        cost = (fees.get(f"buy:{tid}", 0.0) + fees.get(f"close:{tid}", 0.0)
                if tid else 0.0)
        if not cost:
            out.append(t)
            continue
        if not t.get("notional"):
            raise ValueError(f"trade {tid} paid ${cost:.2f} of operator cost "
                             f"but has no entry notional to express it on")
        out.append({**t, "pnl_pct": t["pnl_pct"] - cost / t["notional"] * 100.0,
                    "operator_cost": cost})
    return out


def check(records: list[dict], bars_by_symbol: dict, *,
          min_trades: int = DEFAULT_MIN_TRADES,
          n_samples: int = DEFAULT_SAMPLES,
          seed: int = DEFAULT_SEED,
          slippage_bps: float = 0.0,
          strategy: str | None = None,
          fees: dict | None = None,
          costs: dict | None = None) -> Assessment:
    """End-to-end decay check over a ledger slice. The one call sites use.

    `fees` (ref -> fee, operator_settings.fees_by_ref) and `costs` (the cost
    model in force) put both sides on the after-cost basis — see the module
    docstring. Omitted, the check is exactly the pre-2026-10-28 one."""
    trades = after_cost(closed_trades(records), fees)
    if strategy:
        trades = [t for t in trades if t.get("strategy") == strategy]
    if len(trades) < min_trades:
        return Assessment(verdict=INSUFFICIENT_DATA, n_trades=len(trades),
                          min_trades=min_trades, seed=seed,
                          detail="below minimum closed-trade count")

    holdings = []
    for t in trades:
        h = holding_bars(bars_by_symbol.get(t["symbol"]) or [],
                         t["entry_ts"], t["exit_ts"])
        if h:
            holdings.append(h)
    symbols = sorted({t["symbol"] for t in trades})
    null = null_distribution(bars_by_symbol, symbols, holdings, len(trades),
                             n_samples=n_samples, seed=seed,
                             slippage_bps=slippage_bps, costs=costs,
                             notionals=[t["notional"] for t in trades
                                        if t.get("notional")])
    return assess([t["pnl_pct"] for t in trades], null,
                  min_trades=min_trades, seed=seed)
