"""Offline backtest harness — replays historical daily bars through the SAME
deterministic code the live bot uses (`strategy.generate_signal`, `strategy.atr`,
`risk.size_order`, `risk.pure_checks`) with a fee/slippage model.

Isolation guarantees:
  * never imports Ledger/Memory/llm/x_poster — the live audit trail, learnings
    and X posting are untouchable from here;
  * the only file it writes is the trials log (DEFAULT_TRIALS_PATH below,
    append-only — every parameter variant tried is recorded, per the
    False-Strategy-Theorem discipline in GUIDE.md);
  * `broker` is imported lazily inside fetch_bars() only when no --bars-file is
    given, and only for READ-ONLY market data.

Anti-look-ahead: signals are computed on the close of bar i and filled at the
OPEN of bar i+1, with slippage charged against you on every fill. Bracket legs
are checked intrabar pessimistically (stop before take-profit when both touch).

Usage:
    python src/backtest.py --bars-file data/bars.json --fast 5 10 --slow 20 30
    python src/backtest.py --symbols SPY --start 2020-01-01 --end 2026-01-01
"""
import argparse
import csv
import gzip
import itertools
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import strategies
from strategies.base import ENTRY_ACTIONS, EXIT_ACTIONS, side_of_qty
import strategy
import risk
import cost_models
import regime as regime_mod
import earnings as earnings_mod
import intraday
import judge_model


# ---------------------------------------------------------------- data models

@dataclass
class SimTrade:
    symbol: str
    entry_ts: str
    entry_price: float
    qty: int
    exit_ts: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    fees: float = 0.0
    # Owner decision 2026-10-28: the operator's simulated cost on this round
    # trip's two orders (cost_models, `backtest.operator_costs`). Kept apart
    # from `fees` because it never touches the sim's cash — live sizes on
    # broker equity, which never includes it — but it comes off the P&L, and
    # so off profit factor and every significance comparison. 0.0 when off.
    operator_cost: float = 0.0

    @property
    def pnl(self) -> float:
        return ((self.exit_price - self.entry_price) * self.qty - self.fees
                - self.operator_cost)


@dataclass
class SimAccount:
    cash: float
    positions: dict = field(default_factory=dict)
    # symbol -> {"qty", "avg_entry", "entry_ts", "stop", "tp", "trade": SimTrade}

    def equity(self, prices: dict) -> float:
        held = sum(p["qty"] * prices.get(s, p["avg_entry"])
                   for s, p in self.positions.items())
        return self.cash + held

    # §64: 1.0 = cash account, and the 1.0 path below must stay byte-identical
    # to the pre-margin code — every recorded verdict was scored through it.
    margin_multiplier: float = 1.0

    def account_dict(self, prices: dict) -> dict:
        """Shape risk.size_order / risk.pure_checks expect."""
        eq = self.equity(prices)
        if self.margin_multiplier > 1.0:
            gross = sum(abs(p["qty"]) * prices.get(s, p["avg_entry"])
                        for s, p in self.positions.items())
            bp = max(eq * self.margin_multiplier - gross, 0.0)
        else:
            bp = self.cash
        return {"equity": eq, "cash": self.cash, "last_equity": eq,
                "buying_power": bp}

    def positions_dict(self, prices: dict) -> dict:
        return {s: {"qty": p["qty"],
                    "market_value": p["qty"] * prices.get(s, p["avg_entry"])}
                for s, p in self.positions.items()}


@dataclass
class Result:
    params: dict
    start: str
    end: str
    n_trades: int
    n_guard_skipped_exits: int
    total_return_pct: float
    buy_hold_return_pct: float
    buy_hold_max_drawdown_pct: float
    avg_deployment_pct: float   # mean % of equity actually invested (rails cap it)
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    n_heat_blocked: int = 0     # entries stopped by the portfolio-heat cap
    n_corr_blocked: int = 0     # entries stopped by the correlation cap
    n_fill_fallback: int = 0    # §25: fills that had no intraday bar and
                                # reverted to the daily open (never silent)
    census: dict = field(default_factory=dict)   # §40: signal -> outcome census
    exits: dict = field(default_factory=dict)    # §45: trade -> exit-reason census
    deployment_zero_pct: float = 0.0   # § 40: share of bars fully in cash
    deployment_median_pct: float = 0.0
    deployment_max_pct: float = 0.0
    # PHASE 3: gross and net, because `avg_deployment_pct` above is NET and a
    # 130/30 book therefore reports 100% — the same number a book that is 100%
    # long and flat reports. Net is still the right input to
    # `beats_exposure_matched` (the book carries ~100% of market beta either
    # way), so it keeps its meaning and these are added ALONGSIDE it rather
    # than replacing it: every recorded verdict reads the old field.
    #
    # `net_exposure_min_pct` and `gross_exposure_max_pct` are the two ends that
    # answer "did the net-exposure band bind" and "did the book ever reach the
    # 130% the ratio was premised on" — averages hide both.
    gross_exposure_avg_pct: float = 0.0
    gross_exposure_max_pct: float = 0.0
    net_exposure_avg_pct: float = 0.0
    net_exposure_min_pct: float = 0.0
    n_short_trades: int = 0
    n_short_symbols: int = 0
    # §50: a SURVIVORSHIP-FREE benchmark, alongside the poisoned one above.
    # `buy_hold_return_pct` averages the whole snapshot universe, and §48
    # measured what that costs: the wide snapshots are built from TODAY's index
    # membership, so 2000-2006's equal-weight buy-and-hold returns +138.74%
    # while SPY over the identical bars returns +8.68% — a +130pp gap that is
    # pure survivorship. An index ETF's price already reflects constituent
    # changes (names that failed left the index and took their losses with
    # them), so a single-instrument benchmark carries none of it.
    #
    # None means "could not be computed" and must NEVER read as zero — a
    # missing benchmark has to fail its clause, not quietly clear a bar of 0.0.
    benchmark_symbol: str = ""
    benchmark_return_pct: float | None = None
    benchmark_max_drawdown_pct: float | None = None
    # §64: cumulative dollars paid to borrow (margin financing). Zero on the
    # 1.0-multiplier path and in simulate(), which cannot lever. A levered
    # result quoting returns without this number is quoting someone else's
    # strategy — divergence #16 is the standing warning.
    financing_paid_usd: float = 0.0
    # Owner decision 2026-10-28: "Yes: make gates, backtests and the
    # drawdown/retire rails use after-cost results first (repete4's override
    # included). Entry cost filters later, bot by bot." The operator's
    # simulated cost model this run charged (`backtest.operator_costs`, a
    # cost_models block pinned in the gate spec) and the total it charged.
    # None / 0.0 when off, and then summary() leaves BOTH out, so every
    # cost-free result serialises byte-for-byte as it did before the field
    # existed: absent means no operator cost.
    operator_costs: dict | None = None
    operator_cost_paid_usd: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    @property
    def symbols_traded(self) -> set:
        """Distinct symbols this run actually opened a position in.

        DERIVED, not stored, on purpose. §27's capacity clause turns on this
        number, and a stored field written at construction time is one more
        thing that can silently disagree with `trades` — which is the exact
        class of bug §13, §20 and §24 all turned out to be. Computed from the
        trade list, it cannot drift.
        """
        return {t.symbol for t in self.trades}

    @property
    def n_symbols_traded(self) -> int:
        return len(self.symbols_traded)

    # ---- the four numbers a Deflated Sharpe needs, DERIVED from the curve ----
    #
    # Added 2026-08-22 because the audit could not compute one. Bailey &
    # López de Prado's DSR deflates an observed Sharpe by the number of trials
    # AND by the skew and kurtosis of the return series — and summary() had
    # always popped `equity_curve`, the only thing those could be read from.
    # So the 225 arm summaries already in research/verdicts.jsonl carry none
    # of them, and never can: DSR is computable for every run from here on
    # and NOT recoverable for §1–§72. That limit is stated, not papered over.
    #
    # Derived like symbols_traded, not stored: a field written at construction
    # is one more thing that can silently disagree with the curve. None on an
    # empty or one-point curve, never 0.0 — a zero Sharpe is a claim; a missing
    # one is an absence, and this repo's standing rule is that the two must
    # never be confused (benchmark_return_pct above says the same).

    @property
    def _bar_returns(self) -> list:
        c = self.equity_curve
        if len(c) < 2:
            return []
        out = []
        for a, b in zip(c, c[1:]):
            if a and a > 0:
                out.append(b / a - 1.0)
        return out

    @property
    def n_obs(self) -> int | None:
        """Bars in the return series — the T that DSR's track-record term uses."""
        r = self._bar_returns
        return len(r) if r else None

    @property
    def sharpe(self) -> float | None:
        """Per-BAR Sharpe, unannualised. Annualisation is the reader's, and
        stating the raw number avoids baking a trading-days assumption into a
        log that will be read across intraday and daily runs."""
        r = self._bar_returns
        if len(r) < 2:
            return None
        mu = sum(r) / len(r)
        var = sum((x - mu) ** 2 for x in r) / (len(r) - 1)
        if var <= 0:
            return None
        return round(mu / var ** 0.5, 6)

    @property
    def return_skew(self) -> float | None:
        r = self._bar_returns
        if len(r) < 3:
            return None
        mu = sum(r) / len(r)
        m2 = sum((x - mu) ** 2 for x in r) / len(r)
        if m2 <= 0:
            return None
        m3 = sum((x - mu) ** 3 for x in r) / len(r)
        return round(m3 / m2 ** 1.5, 6)

    @property
    def return_kurtosis(self) -> float | None:
        """Raw (non-excess) kurtosis — DSR's formula wants γ4 with 3 for a
        normal, and the excess convention is the silent off-by-three."""
        r = self._bar_returns
        if len(r) < 4:
            return None
        mu = sum(r) / len(r)
        m2 = sum((x - mu) ** 2 for x in r) / len(r)
        if m2 <= 0:
            return None
        m4 = sum((x - mu) ** 4 for x in r) / len(r)
        return round(m4 / m2 ** 2, 6)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("equity_curve")
        d.pop("trades")
        if self.operator_costs is None:
            # Absent means no operator cost — and keeps every cost-free summary
            # (trial rows, verdict arms) byte-identical to its pre-2026-10-28 shape.
            d.pop("operator_costs")
            d.pop("operator_cost_paid_usd")
        else:
            d["operator_costs_sha256"] = operator_costs_sha256(self.operator_costs)
        # asdict() serialises FIELDS only, never properties — without this line
        # every gate report silently omits the reachability number.
        d["n_symbols_traded"] = self.n_symbols_traded
        # Same for these four: the curve is popped one line up, so these are
        # the only trace of it that reaches the trial log or the verdict row.
        d["n_obs"] = self.n_obs
        d["sharpe"] = self.sharpe
        d["return_skew"] = self.return_skew
        d["return_kurtosis"] = self.return_kurtosis
        return d


# ------------------------------------------------------------------- metrics

def max_drawdown(curve: list) -> float:
    """Largest peak-to-trough decline, as a positive percentage."""
    peak, worst = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


def profit_factor(trades: list) -> float:
    """Gross wins / gross losses. inf when there are wins but no losses."""
    wins = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


# ------------------------------------------------- the operator's cost (2026-10-28)
#
# Owner decision 2026-10-28, quoted exactly: "Yes: make gates, backtests and the
# drawdown/retire rails use after-cost results first (repete4's override
# included). Entry cost filters later, bot by bot."
#
# Live charges every filled order the console's cost model (operator_settings.py,
# cost_models.py) and the rails now read equity after it. The simulator charges
# the SAME formula on every simulated fill, from a block PINNED in the run's
# config — `backtest.operator_costs`, set by a gate spec's top-level
# `operator_costs:` (run_gate.apply_operator_costs). It never reads the console's
# operator_settings.json: a backtest whose cost moved when someone changed a
# setting on a web page would not be reproducible.
#
# OFF BY DEFAULT. config.yaml carries no block and is not in any spec's hash, so
# an absent block must mean no cost: every frozen spec, verdict and golden file
# reproduces byte for byte.
#
# Where the sim also charges a venue fee (`fee_per_trade_usd`, 0.0 here), the
# operator cost is the top-up `charge_amount(model, venue_fee)`, so the total per
# order equals the model, exactly as live tops up a venue's own fee.
#
# Sizing is untouched: live sizes on broker equity, which never includes the
# operator cost, so the sim keeps it OUT of cash in its own accumulator. It comes
# off each trade's P&L (SimTrade.operator_cost), the equity curve (and so maxDD,
# Sharpe and total return), the sim's own drawdown rail, and the benchmarks.

OPERATOR_MARKET = "equities"


def operator_costs_block(cfg: dict) -> dict | None:
    """The run's pinned cost-models block, validated, or None (= no cost).

    Raises ValueError on a block that is present and malformed: a typo'd cost
    model must stop the run, never quietly charge nothing."""
    raw = (cfg.get("backtest") or {}).get("operator_costs")
    if raw is None or raw is False:
        return None
    block = cost_models.validate(raw)
    return None if block[OPERATOR_MARKET]["model"] == "off" else block


def operator_costs_sha256(block: dict | None) -> str | None:
    """sha256 of the block as charged — what a verdict row is stamped with."""
    if block is None:
        return None
    import hashlib
    return hashlib.sha256(json.dumps(block, sort_keys=True, separators=(
        ",", ":")).encode("utf-8")).hexdigest()


def operator_charge(costs: dict | None, qty: float, price: float,
                    venue_fee: float = 0.0) -> float:
    """The operator cost of ONE simulated order: the equities model on its
    shares and notional, less what the venue fee already charged."""
    if costs is None:
        return 0.0
    shares = abs(float(qty))
    gross, _ = cost_models.order_cost(costs, OPERATOR_MARKET, shares=shares,
                                      notional=shares * float(price))
    return cost_models.charge_amount(gross, venue_fee)


def buy_and_hold_drawdown(sym_bars: dict) -> float:
    """Max drawdown of an equal-weighted buy-and-hold of the same universe."""
    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    if not all_ts:
        return 0.0
    first_close: dict = {}
    last_rel: dict = {}
    curve = []
    by_ts = {sym: {b["ts"]: b["close"] for b in bars}
             for sym, bars in sym_bars.items()}
    for ts in all_ts:
        for sym in sym_bars:
            c = by_ts[sym].get(ts)
            if c is not None:
                first_close.setdefault(sym, c)
                last_rel[sym] = c / first_close[sym]
        if last_rel:
            curve.append(sum(last_rel.values()) / len(last_rel))
    return max_drawdown(curve)


def buy_and_hold_return(bars: list, slippage_bps: float, fee: float,
                        cash: float = 100_000.0,
                        operator_costs: dict | None = None) -> float:
    """Buy at the second bar's open (same information timing as the strategy's
    first possible fill), sell at the last close. Slippage + fees applied.

    `operator_costs` (2026-10-28): the benchmark's two orders pay the same
    operator cost model the strategy's do, so the race stays like-for-like.
    None (the default) is the pre-2026-10-28 arithmetic, unchanged."""
    if len(bars) < 2:
        return 0.0
    buy = bars[1]["open"] * (1 + slippage_bps / 1e4)
    sell = bars[-1]["close"] * (1 - slippage_bps / 1e4)
    qty = int((cash - fee) // buy)
    if qty <= 0:
        return 0.0
    ret = ((sell - buy) * qty - 2 * fee) / cash * 100
    if operator_costs is not None:
        ret -= (operator_charge(operator_costs, qty, buy, fee)
                + operator_charge(operator_costs, qty, sell, fee)) / cash * 100
    return ret


BENCHMARK_SYMBOL = "SPY"


def benchmark_stats(sym_bars: dict, symbol: str, slippage_bps: float,
                    fee: float, cash: float,
                    operator_costs: dict | None = None) -> tuple:
    """(return_pct, max_drawdown_pct) for ONE instrument — the §50 benchmark.

    Why a single instrument. `buy_hold_return_pct` averages the whole snapshot
    universe, and §48 measured that those universes are built from today's
    index membership: every name in them survived to 2026 by construction. The
    result is a "market" that returned +138.74% over 2000-2006 while SPY over
    the identical bars returned +8.68%. An index ETF's price already reflects
    constituent changes, so it carries none of that inflation.

    Charged the SAME slippage and fees as the universe benchmark, and entered
    at the same bar, so the two are like-for-like in everything except which
    instruments they hold. A benchmark scored frictionlessly against a strategy
    that pays costs would be a bar nobody could clear.

    Returns (None, None) when the symbol is absent or too short. Callers must
    treat that as "cannot be measured" — `run_gate.evaluate` FAILS the clause,
    on the §33 RUN 1 precedent that a silently skipped check reads as a passed
    one.
    """
    bars = sym_bars.get(symbol)
    if not bars or len(bars) < 2:
        return None, None
    return (round(buy_and_hold_return(bars, slippage_bps, fee, cash,
                                      operator_costs), 3),
            round(buy_and_hold_drawdown({symbol: bars}), 3))


# --------------------------------------------------------------- bar loading

def load_bars_file(path: str) -> dict:
    """JSON ({symbol: [bar,...]} or a bare list for one symbol named FILE) or
    CSV (columns: symbol?,ts,open,high,low,close,volume) -> broker.bars shape.

    `.json.gz` is accepted so the committed gate snapshots stay ~1.8 MB rather
    than ~7.8 MB (see scripts/build_snapshot.py)."""
    if path.endswith((".json", ".json.gz")):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"FILE": data}
        return data
    out: dict = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol", "FILE")
            out.setdefault(sym, []).append({
                "ts": row["ts"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]), "volume": float(row.get("volume", 0))})
    return out


def fetch_bars(symbols: list, start: str, end: str) -> dict:
    """READ-ONLY market data via Alpaca (lazy import; needs .env keys)."""
    from dotenv import load_dotenv
    load_dotenv()
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"])
    # adjustment=ALL — see divergence #12 (src/broker.py:85). Omitting it gives
    # RAW bars, and every frozen snapshot is split/dividend adjusted. This path
    # feeds src/revalidate.py and the --symbols CLI, so raw bars here would
    # revalidate live decisions against a series no gate ever scored.
    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
        adjustment=Adjustment.ALL))
    return {sym: [{"ts": b.timestamp.isoformat(), "open": b.open, "high": b.high,
                   "low": b.low, "close": b.close, "volume": b.volume}
                  for b in rows]
            for sym, rows in resp.data.items()}


# ---------------------------------------------------------------- simulation

def _cal_days(entry_ts: str, now_ts: str) -> int:
    return (datetime.fromisoformat(now_ts) - datetime.fromisoformat(entry_ts)).days


BRACKET_KEYS = ("stop_atr_mult", "take_profit_atr_mult",
                "stop_atr_mult_high_vol", "trailing_atr_mult")


_RCFG_DEFAULT = {"sma_period": 50, "vol_period": 20,
                 "vol_low": 0.15, "vol_high": 0.25}


def regime_label_series(spy_bars: list[dict], rcfg: dict) -> dict:
    """ts -> full regime label (e.g. "down/low") from SPY history up to each
    bar (no look-ahead). Used by the down-regime exposure-cap candidate."""
    out = {}
    for i in range(len(spy_bars)):
        r = regime_mod.compute_regime(spy_bars[:i + 1], rcfg)
        if r:
            out[spy_bars[i]["ts"]] = r["label"]
    return out


def measured_slippage_bps(records: list[dict], min_fills: int = 10,
                          sane_bps: float = 100.0) -> float | None:
    """Median measured slippage from fill_quality ledger records, for
    calibrating the backtest cost model to reality. Fills with |bps| beyond
    sane_bps are artifacts (e.g. the stale-signal-price era) and excluded;
    returns None until min_fills clean measurements exist."""
    slips = sorted(r["slippage_bps"] for r in records
                   if r.get("type") == "fill_quality"
                   and abs(r.get("slippage_bps", 0)) <= sane_bps)
    if len(slips) < min_fills:
        return None
    mid = len(slips) // 2
    med = slips[mid] if len(slips) % 2 else (slips[mid - 1] + slips[mid]) / 2
    return round(max(med, 0.0), 2)  # never model negative cost


def vol_bucket_series(spy_bars: list[dict], rcfg: dict) -> dict:
    """ts -> "low"|"mid"|"high" from SPY history up to each bar (no
    look-ahead). Empty dict when SPY isn't in the tested universe — the
    vol-conditioned stop then fails open to the base multiplier."""
    out = {}
    for i in range(len(spy_bars)):
        r = regime_mod.compute_regime(spy_bars[:i + 1], rcfg)
        if r:
            out[spy_bars[i]["ts"]] = r["vol"]
    return out


def simulate(sym_bars: dict, cfg: dict, params: dict | None = None,
             start_cash: float = 100_000.0,
             strategy_name: str = "ma_crossover",
             earnings: dict | None = None) -> Result:
    """Replay bars through the live strategy/risk code for ONE named strategy.
    `params` overlays that strategy's settings (plus bracket multipliers)
    onto a COPY of cfg — the real config is never mutated.
    `earnings` maps symbol -> sorted ISO earnings dates; only consulted when
    the earnings_blackout_days param/config is set (mirrors the live filter).
    """
    params = params or {}
    cfg = json.loads(json.dumps(cfg))  # deep copy — never mutate the caller's config
    smap = cfg.setdefault("strategies", {})
    sparams = smap.setdefault(strategy_name, {"enabled": True})
    if strategy_name == "ma_crossover":  # legacy defaults from strategy: section
        sparams.setdefault("fast_period", cfg["strategy"].get("fast_period", 10))
        sparams.setdefault("slow_period", cfg["strategy"].get("slow_period", 30))
    for k, v in params.items():
        if k not in BRACKET_KEYS and k != "strategy":
            sparams[k] = v
    bcfg = cfg["risk"].setdefault("brackets", {})
    if "stop_atr_mult" in params:
        bcfg.update(enabled=params["stop_atr_mult"] > 0,
                    stop_atr_mult=params["stop_atr_mult"])
        bcfg.setdefault("atr_period", 14)
    if "take_profit_atr_mult" in params:
        bcfg["take_profit_atr_mult"] = params["take_profit_atr_mult"]
    if "stop_atr_mult_high_vol" in params:
        bcfg["stop_atr_mult_high_vol"] = params["stop_atr_mult_high_vol"]
    if "trailing_atr_mult" in params:
        bcfg["trailing_atr_mult"] = params["trailing_atr_mult"]
    t_strats = bcfg.get("trailing_strategies")
    trail_on = (bcfg.get("trailing_atr_mult", 0) > 0
                and (not t_strats or strategy_name in t_strats))
    cooldown_days = risk.cooldown_days_for(cfg, strategy_name)

    # THIS SIMULATOR IS LONG-ONLY AND SAYS SO RATHER THAN GUESSING.
    #
    # `simulate_ensemble` models signed positions since Phase 3; this function
    # does not — its account carries an unsigned qty, its bracket legs test one
    # side of the bar, and its slippage always moves against a buyer. Handed a
    # strategy with a short leg it would score the long half of the book and
    # return a perfectly plausible number for a strategy that does not exist.
    #
    # That is the exact failure `docs/divergences.md` exists to count, so it
    # raises instead. Gates use `simulate_ensemble` (README: "the one gates
    # use"), so nothing frozen depends on this path being able to short; the
    # callers are the `--strategy` CLI and `walk_forward`.
    if (sparams.get("short_bottom_fraction") or 0) > 0:
        raise ValueError(
            f"simulate() cannot score {strategy_name}: it is configured to "
            f"short (short_bottom_fraction="
            f"{sparams['short_bottom_fraction']}) and this simulator is "
            f"long-only — unsigned positions, one-sided bracket legs, "
            f"buyer's slippage. Use simulate_ensemble(), which models signed "
            f"positions, or set short_bottom_fraction: 0 for this run.")

    smod = strategies.REGISTRY[strategy_name]
    needs_xs = smod.NEEDS_CROSS_SECTION

    bt = cfg.get("backtest", {})
    slip = bt.get("slippage_bps", 5)
    fee = bt.get("fee_per_trade_usd", 0.0)
    min_days = cfg["risk"].get("min_holding_days", 0)
    # 2026-10-28: the operator's cost, pinned in cfg, None = off. Its own
    # accumulator, never cash: sizing stays on the broker-equivalent basis.
    op_costs = operator_costs_block(cfg)
    op_paid = 0.0

    # Per-date vol bucket from SPY (only needed for the vol-conditioned stop;
    # empty => bracket_prices falls back to the base multiplier).
    rcfg = cfg.get("learning", {}).get("regime", _RCFG_DEFAULT)
    vol_series = (vol_bucket_series(sym_bars.get("SPY", []), rcfg)
                  if bcfg.get("stop_atr_mult_high_vol") else {})
    regime_labels = (regime_label_series(sym_bars.get("SPY", []), rcfg)
                     if (cfg["risk"].get("regime_exposure") or {}).get("enabled")
                     else {})

    acct = SimAccount(cash=start_cash)
    closed: list = []
    curve: list = []
    deployment: list = []
    guard_skips = 0
    n_heat_blocked = 0      # §13: rails the sim previously ignored
    n_corr_blocked = 0
    sim_peak = 0.0          # §31: running equity high-water for the drawdown rail
    sim_trough, sim_stable = None, 0   # §41: decay clock (bars since a new low)
    fills_by_date: dict = {}
    last_exit: dict = {}  # symbol -> exit ts (re-entry cooldown, §9)
    pending: list = []  # (symbol, action) to fill at that symbol's next open

    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    idx = {sym: {b["ts"]: i for i, b in enumerate(bars)}
           for sym, bars in sym_bars.items()}
    last_close: dict = {}

    def _exit(sym, price, ts, reason):
        nonlocal op_paid
        pos = acct.positions.pop(sym)
        fill = price * (1 - slip / 1e4)
        acct.cash += pos["qty"] * fill - fee
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, fill, reason
        t.fees += fee
        if op_costs is not None:
            _op = operator_charge(op_costs, pos["qty"], fill, fee)
            t.operator_cost += _op
            op_paid += _op
        last_exit[sym] = ts
        closed.append(t)

    def _rail_eq() -> float:
        """Equity the sim's drawdown rail measures: after the operator cost,
        as live's rail_equity is (risk.rail_equity). Identity when off."""
        return acct.equity(last_close) - op_paid

    def _rail_account() -> dict:
        d = acct.account_dict(last_close)
        if op_costs is not None:
            d[risk.RAIL_EQUITY_KEY] = d["equity"] - op_paid
        return d

    # §22: caller order is honoured here (a test pins it). §24 rotates that
    # order per DAY when enabled — same shared helper live uses.
    base_order = list(sym_bars)

    for ts in all_ts:
        today = {sym: sym_bars[sym][idx[sym][ts]]
                 for sym in risk.scan_order(base_order, cfg, ts)
                 if ts in idx[sym]}
        for sym, bar in today.items():
            last_close[sym] = bar["close"]

        # §41 / divergence #10: ratchet the peak EVERY bar, not only on bars
        # where a buy was attempted. A high-water mark sampled only when the
        # book happens to transact is not a high-water mark. See the identical
        # hoist in simulate_ensemble().
        #
        # Clock BEFORE ratchet, in both paths: decay_clock compares equity to
        # the peak as it stood at the START of the bar. Ratcheting first would
        # make every new high look like "equity >= peak" one bar early and
        # reset the clock a bar sooner than live does.
        sim_trough, sim_stable = risk.decay_clock(
            sim_trough, sim_stable, _rail_eq(), sim_peak)
        sim_peak = max(sim_peak, _rail_eq())

        # 1. Fill orders queued on the previous bar, at this bar's open.
        still_pending = []
        for sym, action in pending:
            if sym not in today:
                still_pending.append((sym, action))
                continue
            bar = today[sym]
            date = ts[:10]
            if action == "buy":
                # §29: 0 disables, exactly as pre_trade_checks does live. If
                # only one of the two honoured 0 this would be divergence #9.
                _cap_day = cfg["risk"].get("max_trades_per_day") or 0
                if _cap_day and fills_by_date.get(date, 0) >= _cap_day:
                    continue  # rate limit across symbols, mirroring the live bot
                fill = bar["open"] * (1 + slip / 1e4)
                i = idx[sym][ts]
                hist = sym_bars[sym][:i + 1]
                # Bracket prices BEFORE sizing (mirrors the live pipeline) so
                # stop-distance sizing (§8) sees the same stop it will place.
                prices = risk.bracket_prices(
                    fill, strategy.atr(hist, bcfg.get("atr_period", 14)), cfg,
                    vol_bucket=vol_series.get(ts),
                    strategy=strategy_name)
                stop, tp = prices if prices else (None, None)
                qty = risk.size_order(acct.account_dict(last_close), fill, cfg,
                                      bars=hist,
                                      strategy=strategy_name,
                                      stop_price=stop)
                # §29 / divergence #8: live cuts 53.5% of buys before they are
                # placed. Off by default, so every gate run before 2026-07-26
                # reproduces byte-identically.
                qty = judge_model.apply(qty, sym, ts, cfg)
                try:
                    # Single-strategy sim: every open position belongs to the
                    # strategy under test, so its slot allocation (§13) is
                    # exactly len(positions).
                    # §31: the sim keeps its OWN running peak — live reads a
                    # file, the sim cannot. The ARITHMETIC is shared
                    # (risk.drawdown_pct), so the two cannot disagree about
                    # what a drawdown is, only about where the peak came from.
                    # §41: the ratchet itself moved to the top of the bar loop
                    # (divergence #10) — it used to sit HERE, inside the buy
                    # branch, so a bar that only sold never updated the peak.
                    risk.pure_checks("buy", sym, qty, fill,
                                     _rail_account(),
                                     acct.positions_dict(last_close), cfg,
                                     regime_label=regime_labels.get(ts),
                                     strategy=strategy_name,
                                     strategy_open=len(acct.positions),
                                     peak_equity=risk.decayed_peak(
                                         sim_peak, _rail_eq(),
                                         sim_stable, cfg))
                    # The heat + correlation caps live in pre_trade_checks,
                    # which the sim cannot call (it does HALT/trade-count file
                    # I/O). They were therefore NEVER simulated: every gate
                    # through §12 was measured with fewer rails than live runs
                    # with, so the sim overstated achievable trade count. Apply
                    # them here so sim and live agree.
                    _sim_open_trades = {
                        s: {"qty": p["qty"], "entry_price": p["avg_entry"],
                            "order": {"stop_price": p.get("stop")}}
                        for s, p in acct.positions.items()}
                    heat_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
                    if heat_pct:
                        eq = acct.equity(last_close)
                        heat = risk.portfolio_heat(_sim_open_trades, cfg, eq)
                        new_risk = (qty * max(fill - stop, 0.0) if stop
                                    else eq * cfg["risk"].get(
                                        "risk_per_trade_pct", 1.0) / 100)
                        if heat + new_risk > eq * heat_pct / 100:
                            n_heat_blocked += 1
                            continue
                    ccfg = cfg["risk"].get("correlation_cap") or {}
                    if ccfg.get("enabled") and acct.positions:
                        open_bars = {s: sym_bars[s][:idx[s][ts] + 1]
                                     for s in acct.positions
                                     if s != sym and ts in idx.get(s, {})}
                        n_corr = risk.correlated_position_count(
                            hist, open_bars, int(ccfg.get("lookback", 60)),
                            float(ccfg.get("threshold", 0.85)))
                        if n_corr >= int(ccfg.get("max_correlated", 2)):
                            n_corr_blocked += 1
                            continue
                except risk.RiskRejection:
                    continue
                acct.cash -= qty * fill + fee
                _op = operator_charge(op_costs, qty, fill, fee)
                op_paid += _op
                acct.positions[sym] = {
                    "qty": qty, "avg_entry": fill, "entry_ts": ts,
                    "stop": stop, "tp": tp, "hw": fill,
                    "trade": SimTrade(sym, ts, fill, qty, fees=fee,
                                      operator_cost=_op)}
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
            elif sym in acct.positions:
                _exit(sym, bar["open"], ts, "strategy_sell")
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
        pending = still_pending

        # 2. Bracket legs, intrabar, pessimistic: stop wins when both touch.
        # The chandelier trail (§7) ratchets AFTER the exit check, from data
        # through today — so today's high can never raise a stop that then
        # fires on today's own low (no intrabar look-ahead; matches the live
        # once-per-cycle update timing).
        for sym in list(acct.positions):
            if sym not in today:
                continue
            bar, pos = today[sym], acct.positions[sym]
            if pos["stop"] is not None and bar["low"] <= pos["stop"]:
                _exit(sym, pos["stop"], ts, "stop_loss")
                continue
            if pos["tp"] is not None and bar["high"] >= pos["tp"]:
                _exit(sym, pos["tp"], ts, "take_profit")
                continue
            if trail_on:
                pos["hw"] = max(pos.get("hw", pos["avg_entry"]), bar["high"])
                hist = sym_bars[sym][:idx[sym][ts] + 1]
                t_stop = risk.trail_stop(
                    pos["hw"], strategy.atr(hist, bcfg.get("atr_period", 14)),
                    cfg, strategy=strategy_name)
                if t_stop is not None and (pos["stop"] is None
                                           or t_stop > pos["stop"]):
                    pos["stop"] = t_stop

        # 3. Signals on data up to and including this bar's close.
        xs_ctx = None
        if needs_xs:
            hist_by_sym = {s: sym_bars[s][:idx[s][ts] + 1]
                           for s in sym_bars if ts in idx[s]}
            # Through the shared entry point, not smod.prepare directly: the
            # universe/held scoping and the cfg argument must be identical to
            # the live cycle's, or this simulator silently scores a different
            # strategy than the one that will trade.
            # `universe=` is the symbols THIS RUN is simulating, not
            # cfg["symbols"]: `--symbols` overrides config, and scoping a
            # cross-sectional strategy by a universe it was not given produces
            # an empty ranking and zero trades — output indistinguishable from
            # "the strategy found nothing".
            xs_ctx = strategies.prepare_one(cfg, strategy_name, hist_by_sym,
                                            held=set(acct.positions),
                                            universe=set(hist_by_sym))
        # NO `in_universe` FILTER HERE, deliberately — see divergence #17 in the
        # ensemble loop below. `--symbols A B C` means "score this strategy on
        # A, B, C"; the run's symbol list IS its universe, which is exactly why
        # `prepare_one` is given `universe=set(hist_by_sym)` three lines up.
        # Applying cfg's universe on top would delete the flag's whole purpose
        # and report zero trades, output indistinguishable from "found nothing".
        entry_cap = sparams.get("max_entries_per_cycle", 0)
        blackout_days = sparams.get("earnings_blackout_days", 0)
        buys_queued_today = 0
        for sym, bar in today.items():
            i = idx[sym][ts]
            hist = sym_bars[sym][:i + 1]
            holding = sym in acct.positions
            entry_ts = acct.positions[sym]["entry_ts"] if holding else None
            # LONG-ONLY BY CONSTRUCTION, and passed EXPLICITLY for that reason.
            # This simulator's `acct.positions` carries an unsigned qty and its
            # exit branch tests `sig.action == "sell"`, so nothing it holds can
            # be a short. Passing "long" when holding states that fact instead
            # of leaving the strategy to infer it from a None the live path
            # never sends — a strategy that reads a different side here than in
            # main.py is a simulator/live divergence, which is precisely the
            # class docs/divergences.md exists to count.
            #
            # PHASE 3 REPLACES THIS with the position's real side, at the same
            # time as it makes `acct.positions` signed. Until then a hardcoded
            # "long" is the truth about this simulator, not a placeholder.
            sig = strategies.generate(strategy_name, sym, hist, cfg, holding,
                                      cross_section=xs_ctx, entry_ts=entry_ts,
                                      position_side="long" if holding else None)
            if sig.action == "buy" and not holding:
                # re-entry cooldown (§9) — same calendar-day rule as main.py
                if cooldown_days and risk.cooldown_blocked(
                        last_exit.get(sym), ts, cooldown_days):
                    continue
                # earnings blackout — same rule as the live filter in main.py
                if blackout_days and earnings and earnings_mod.next_within(
                        earnings.get(sym, []), ts, blackout_days):
                    continue
                # §23 relative-volume confirmation (entries only; fails open)
                if risk.rvol_blocked(hist, cfg, strategy_name):
                    continue
                # §58 contraction precondition (entries only; fails open)
                if risk.contraction_blocked(hist, cfg, strategy_name):
                    continue
                # Unprotectable entry (2026-07-27): an ATR-derived stop at or
                # below zero means brackets() returns None and the position runs
                # with NO stop. Same helper as live and the ensemble.
                if risk.unprotectable_entry(
                        hist[-1]["close"], strategies.atr(hist, 14), cfg,
                        strategy=strategy_name):
                    continue
                # per-cycle entry cap, first-come in symbol order — the same
                # semantics as the live loop in main.py
                if entry_cap and buys_queued_today >= entry_cap:
                    continue
                pending.append((sym, "buy"))
                buys_queued_today += 1
            elif sig.action == "sell" and holding:
                # sim swing guard — same calendar-day semantics as risk.swing_guard
                if min_days and _cal_days(acct.positions[sym]["entry_ts"], ts) < min_days:
                    guard_skips += 1
                    continue
                pending.append((sym, "sell"))

        eq = acct.equity(last_close)
        # After the operator cost (op_paid is 0.0 when off: the identity).
        curve.append(round(eq - op_paid, 2))
        deployment.append((eq - acct.cash) / eq if eq > 0 else 0.0)

    # Liquidate leftovers at the final close so the metrics see every trade.
    final_ts = all_ts[-1] if all_ts else ""
    for sym in list(acct.positions):
        _exit(sym, last_close[sym], final_ts, "end_of_data")

    total_ret = (acct.cash - op_paid - start_cash) / start_cash * 100
    wins = [t for t in closed if t.pnl > 0]
    bh = (sum(buy_and_hold_return(b, slip, fee, start_cash / len(sym_bars),
                                  op_costs)
              for b in sym_bars.values()) / len(sym_bars)) if sym_bars else 0.0
    bm_ret, bm_dd = benchmark_stats(sym_bars, BENCHMARK_SYMBOL, slip, fee,
                                    start_cash, op_costs)

    return Result(
        params={**params, "strategy": strategy_name},
        start=all_ts[0] if all_ts else "", end=final_ts,
        n_trades=len(closed), n_guard_skipped_exits=guard_skips,
        n_heat_blocked=n_heat_blocked, n_corr_blocked=n_corr_blocked,
        total_return_pct=round(total_ret, 3),
        buy_hold_return_pct=round(bh, 3),
        buy_hold_max_drawdown_pct=round(buy_and_hold_drawdown(sym_bars), 3),
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_return_pct=bm_ret, benchmark_max_drawdown_pct=bm_dd,
        avg_deployment_pct=round(100 * sum(deployment) / len(deployment), 2)
        if deployment else 0.0,
        win_rate=round(len(wins) / len(closed), 3) if closed else 0.0,
        profit_factor=round(profit_factor(closed), 3),
        max_drawdown_pct=round(max_drawdown(curve), 3),
        operator_costs=op_costs,
        operator_cost_paid_usd=round(op_paid, 2),
        equity_curve=curve, trades=closed)


# ------------------------------------------------------- ensemble simulation

def simulate_ensemble(sym_bars: dict, cfg: dict, start_cash: float = 100_000.0,
                      earnings: dict | None = None,
                      strategy_overrides: dict | None = None,
                      hour_index: dict | None = None,
                      fill_hour: int | None = None,
                      credit: dict | None = None,
                      rate_bars: dict | None = None,
                      macro: dict | None = None) -> Result:
    """Replay bars through ALL enabled strategies at once, sharing the rails.

    Why this exists (2026-07-23)
    ----------------------------
    `simulate()` above replays ONE strategy in isolation. The live bot runs the
    whole ensemble against a SINGLE set of rails — one cash balance, one global
    `max_open_positions` ceiling, one `max_trades_per_day` counter, one
    portfolio-heat budget, one correlation cap. So every gate verdict in
    `knowledge/backtest_candidates.md` describes a bot that does not exist.

    That is not a hypothetical. §19b tried to gate `max_trades_per_day` and got
    four BYTE-IDENTICAL arms, because `risk._trades_today()` counts the whole
    ensemble while the simulator was watching one strategy that never came near
    the cap. It is the third sim/live divergence found in two days, after the
    missing heat/correlation caps (§13) and the fill-timing gap (§19a).

    Relationship to simulate()
    --------------------------
    This is a SIBLING, not a replacement. `simulate()` stays byte-stable so the
    §14–§19 numbers remain re-derivable. The correctness argument is that with
    exactly one strategy enabled, this function must reproduce `simulate()`
    trade-for-trade — `tests/test_ensemble_sim.py` asserts precisely that, for
    each strategy. Ensemble behaviour is then single-strategy behaviour plus
    contention, and nothing else.

    Shared vs per-strategy
    ----------------------
    shared:        cash/equity, global max_open_positions, the per-day trade
                   counter, portfolio heat, the correlation cap
    per-strategy:  params, re-entry cooldown (§9), the chandelier trailing gate
                   (§7, tsmom only), and the §13 slot allocation

    `strategy_overrides` maps strategy name -> params to overlay (and may set
    `enabled`), so a gate can vary one strategy's settings — or turn one off —
    without touching the live config.
    """
    cfg = json.loads(json.dumps(cfg))    # never mutate the caller's config
    smap = cfg.setdefault("strategies", {})
    for name, over in (strategy_overrides or {}).items():
        smap.setdefault(name, {"enabled": True}).update(over)
    # Legacy ma_crossover defaults live in the `strategy:` section.
    if "ma_crossover" in smap:
        smap["ma_crossover"].setdefault(
            "fast_period", cfg["strategy"].get("fast_period", 10))
        smap["ma_crossover"].setdefault(
            "slow_period", cfg["strategy"].get("slow_period", 30))

    # Entry order = live priority order. Contention on a symbol is resolved
    # first-come in this order, exactly as main.py's loop does.
    active = strategies.enabled(cfg)
    if not active:
        raise ValueError("no enabled strategies — nothing to simulate")

    bcfg = cfg["risk"].setdefault("brackets", {})
    t_strats = bcfg.get("trailing_strategies")
    trail_mult_on = bcfg.get("trailing_atr_mult", 0) > 0

    def _trails(owner: str) -> bool:
        """Per-POSITION now, not per-run: the chandelier is gated to tsmom, so
        a shared run has to ask who owns the position."""
        return trail_mult_on and (not t_strats or owner in t_strats)

    cooldowns = {n: risk.cooldown_days_for(cfg, n) for n, _ in active}

    bt = cfg.get("backtest", {})
    slip = bt.get("slippage_bps", 5)
    fee = bt.get("fee_per_trade_usd", 0.0)
    min_days = cfg["risk"].get("min_holding_days", 0)
    # 2026-10-28: the operator's cost, pinned in cfg (a gate spec's
    # `operator_costs:`), None = off. Its own accumulator, never cash, so
    # sizing stays on the broker-equivalent basis live sizes on.
    op_costs = operator_costs_block(cfg)
    op_paid = 0.0

    rcfg = cfg.get("learning", {}).get("regime", _RCFG_DEFAULT)
    vol_series = (vol_bucket_series(sym_bars.get("SPY", []), rcfg)
                  if bcfg.get("stop_atr_mult_high_vol") else {})
    regime_labels = (regime_label_series(sym_bars.get("SPY", []), rcfg)
                     if (cfg["risk"].get("regime_exposure") or {}).get("enabled")
                     else {})

    # §64: the multiplier reaches SimAccount at construction, not per call —
    # buying power is a property of the account, and threading cfg into
    # account_dict would put a config read on the hottest path in the file.
    _margin_mult = float(((cfg.get("risk") or {}).get("margin") or {})
                         .get("multiplier", 1.0))
    # §64: gem's T-bill threshold reads the rate aux through prepare()'s cfg
    # argument — the one channel the strategy contract already carries.
    # Shallow copy so the caller's dict is never mutated; live main.py never
    # sets the key, so rate-needing strategies are inert live by construction.
    if rate_bars is not None:
        cfg = {**cfg, "_rate_bars": rate_bars}
    acct = SimAccount(cash=start_cash, margin_multiplier=_margin_mult)
    financing_paid = 0.0
    closed: list = []
    curve: list = []
    deployment: list = []
    gross: list = []          # sum(|market_value|) / equity
    net: list = []            # signed exposure / equity
    guard_skips = 0
    n_heat_blocked = 0
    n_corr_blocked = 0
    # §40 BLOCK CENSUS. `signals` is the denominator that did not exist before:
    # every buy a strategy actually emitted. Each one then either executes or
    # lands in exactly one `blocked` bucket, and the two must sum back to
    # `signals` — asserted at the end of the run, not merely tested.
    #
    # Without a denominator, "blocked 40 times" cannot be read against anything.
    # Without per-rail buckets the reason was discarded at the one moment it was
    # known: every RiskRejection funnelled into a single counter. Across four
    # periods this bot sits ~91% in cash and nobody could say why.
    #
    # Only simulate_ensemble is instrumented — it is what every gate runs.
    census = {"signals": 0, "executed": 0, "blocked": {}}

    def _blocked(reason: str):
        census["blocked"][reason] = census["blocked"].get(reason, 0) + 1

    sim_peak = 0.0          # §31: running equity high-water for the drawdown rail
    sim_trough, sim_stable = None, 0   # §41: decay clock (bars since a new low)
    fills_by_date: dict = {}
    last_exit: dict = {}          # (strategy, symbol) -> exit ts; cooldown is
                                  # per-strategy, matching risk.cooldown_days_for
    pending: list = []            # (symbol, action, owner)
    by_strategy: dict = {n: {"trades": 0, "pnl": 0.0, "blocked": 0}
                         for n, _ in active}

    # SYMBOL ORDER IS LOAD-BEARING (found 2026-07-23, the hard way). Contention
    # is resolved first-come in symbol order, so whoever is scanned first gets
    # first refusal on the shared slots and the daily fill budget. Live builds
    # its scan list as `list(cfg["symbols"])` + extras (main.py), i.e. CONFIG
    # ORDER — while a snapshot loaded from disk arrives alphabetically sorted.
    # Running the same config in the two orders moved OOS return by 2.66pp.
    # Mirror live: config order first, then anything else the caller passed.
    scan_order = [s for s in cfg.get("symbols", []) if s in sym_bars]
    scan_order += [s for s in sym_bars if s not in set(scan_order)]

    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    idx = {sym: {b["ts"]: i for i, b in enumerate(bars)}
           for sym, bars in sym_bars.items()}
    last_close: dict = {}

    # §25 intraday fill timing. `fill_hour` is None for every pre-§25 caller,
    # so this path is inert and results stay byte-identical (a test pins that).
    # When set, fills land at that ET hour's open instead of the daily open —
    # and a missing hourly bar falls back to the daily open and is COUNTED,
    # never silently substituted, so thin coverage cannot masquerade as a result.
    n_fill_fallback = 0

    def _fill_open(sym, bar, ts):
        nonlocal n_fill_fallback
        if fill_hour is None or hour_index is None:
            return bar["open"]
        px = intraday.fill_price(hour_index, sym, ts[:10], fill_hour)
        if px is None:
            n_fill_fallback += 1
            return bar["open"]
        return px

    def _rail_eq() -> float:
        """Equity the sim's drawdown rail measures: after the operator cost,
        as live's rail_equity is (risk.rail_equity). Identity when off."""
        return acct.equity(last_close) - op_paid

    def _rail_account() -> dict:
        d = acct.account_dict(last_close)
        if op_costs is not None:
            d[risk.RAIL_EQUITY_KEY] = d["equity"] - op_paid
        return d

    def _exit(sym, price, ts, reason):
        nonlocal op_paid
        pos = acct.positions.pop(sym)
        # SLIPPAGE FOLLOWS THE ORDER, NOT THE LIFECYCLE. Closing a LONG sells,
        # and a seller is hurt by a LOWER fill. Closing a SHORT buys, and a
        # buyer is hurt by a HIGHER one. `(1 - slip)` unconditionally would pay
        # every cover a discount — a trading cost recorded as a saving, on the
        # one side of the book where costs compound with holding period. Same
        # table as `record_fill_quality`'s sign in main.py: what decides is
        # whether the order BUYS or SELLS, never whether it opens or closes.
        fill = price * (1 + slip / 1e4 if pos["qty"] < 0 else 1 - slip / 1e4)
        # `qty` is SIGNED, so one expression covers both sides: closing a long
        # returns cash (+qty × fill), closing a short spends it (−qty × fill).
        acct.cash += pos["qty"] * fill - fee
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, fill, reason
        t.fees += fee
        if op_costs is not None:
            # The closing order — a sell or a cover — pays the model on its own
            # shares and fill, as live's close:<trade_id> line does.
            _op = operator_charge(op_costs, pos["qty"], fill, fee)
            t.operator_cost += _op
            op_paid += _op
        owner = pos["owner"]
        last_exit[(owner, sym)] = ts
        stats = by_strategy.setdefault(owner, {"trades": 0, "pnl": 0.0,
                                               "blocked": 0})
        stats["trades"] += 1
        stats["pnl"] += t.pnl
        closed.append(t)

    for ts in all_ts:
        # §24: rotate that config order per DAY, via the same shared helper
        # live calls, so first refusal circulates instead of being fixed by
        # position in config.yaml.
        today = {sym: sym_bars[sym][idx[sym][ts]]
                 for sym in risk.scan_order(scan_order, cfg, ts)
                 if ts in idx[sym]}
        for sym, bar in today.items():
            last_close[sym] = bar["close"]

        # §41: the decay clock, advanced once per bar in lockstep with the
        # ratchet. `decay_clock` is pure and shared with the live path so the
        # two cannot disagree about when the breaker re-closes; with
        # risk.drawdown_decay disabled, decayed_peak() below is the identity
        # and every §1-§40 result reproduces byte-identically.
        sim_trough, sim_stable = risk.decay_clock(
            sim_trough, sim_stable, _rail_eq(), sim_peak)

        # §41 / DIVERGENCE #10. This ratchet used to live inside the buy branch
        # below, so the peak advanced only on bars where an entry was attempted.
        # Live ratchets inside pre_trade_checks, which runs for sells too
        # (src/risk.py:828-834, and the comment there says exactly why: "the
        # peak must keep tracking even while entries are blocked"). Live
        # therefore sampled the peak on strictly more occasions than the
        # simulator, which means the §40 latch bit HARDER in production than in
        # any backtest that measured it.
        #
        # Fixed by making both correct rather than by making one match the
        # other's artifact: a high-water mark sampled only when the book happens
        # to transact is not a high-water mark. Every bar, unconditionally.
        #
        # On RAIL equity since 2026-10-28 (after the operator cost; the
        # identity when it is off), the basis live's peak is kept on.
        sim_peak = max(sim_peak, _rail_eq())

        # 1. Fill orders queued on the previous bar, at this bar's open.
        still_pending = []
        for sym, action, owner in pending:
            if sym not in today:
                still_pending.append((sym, action, owner))
                continue
            bar = today[sym]
            date = ts[:10]
            if action in ENTRY_ACTIONS:
                is_short = action == "short"
                # SHARED rate limit — the whole point of this function. One
                # strategy exhausting the daily cap blocks the others.
                # §29: 0 disables, exactly as pre_trade_checks does live. If
                # only one of the two honoured 0 this would be divergence #9.
                _cap_day = cfg["risk"].get("max_trades_per_day") or 0
                if _cap_day and fills_by_date.get(date, 0) >= _cap_day:
                    by_strategy[owner]["blocked"] += 1
                    _blocked("daily_cap")
                    continue
                if sym in acct.positions:
                    # Counted, not silent: this was the one drop the old
                    # counters ignored entirely, so a book full of contended
                    # names looked identical to no signals at all.
                    _blocked("already_held")
                    continue          # another strategy already claimed it
                # A BUY PAYS UP, A SHORT SELLS DOWN. Opening a short is a SELL,
                # so slippage moves the fill against it in the other direction.
                # See _exit for the full table; the rule is the order's side.
                fill = _fill_open(sym, bar, ts) * (
                    1 - slip / 1e4 if is_short else 1 + slip / 1e4)
                i = idx[sym][ts]
                hist = sym_bars[sym][:i + 1]
                prices = risk.bracket_prices(
                    fill, strategy.atr(hist, bcfg.get("atr_period", 14)), cfg,
                    direction=action,
                    vol_bucket=vol_series.get(ts),
                    strategy=owner)
                stop, tp = prices if prices else (None, None)
                # QTY STAYS UNSIGNED all the way through sizing, the judge and
                # the heat cap, and only the STORED POSITION carries a sign.
                # That mirrors live exactly: risk.size_order and
                # judge_model.apply see magnitudes, and broker.positions()
                # reports a short's qty negative. Signing earlier would make
                # judge_model.apply's `if qty <= 0: return qty` guard skip every
                # short — the judge unable to downsize the one side of the book
                # where the loss is unbounded, which is invariant #2 inverted.
                qty = risk.size_order(acct.account_dict(last_close), fill, cfg,
                                      bars=hist, strategy=owner,
                                      stop_price=stop, direction=action)
                # §29 / divergence #8 — see the single-strategy path above.
                qty = judge_model.apply(qty, sym, ts, cfg)
                try:
                    # §13 slot allocation counts only THIS strategy's positions;
                    # the global ceiling inside pure_checks sees all of them.
                    strategy_open = sum(1 for p in acct.positions.values()
                                        if p["owner"] == owner)
                    # §41: ratchet hoisted to the top of the bar loop —
                    # divergence #10. See the comment there.
                    # The REAL action, not the literal "buy". Every rail inside
                    # pure_checks keys off ENTRY_ACTIONS or off the action
                    # itself — direction_conflict, the net-exposure band and the
                    # heat term all read it — so passing "buy" for a short would
                    # send the whole rail set the wrong direction while every
                    # one of them reported that it had run.
                    risk.pure_checks(action, sym, qty, fill,
                                     _rail_account(),
                                     acct.positions_dict(last_close), cfg,
                                     regime_label=regime_labels.get(ts),
                                     strategy=owner,
                                     strategy_open=strategy_open,
                                     peak_equity=risk.decayed_peak(
                                         sim_peak, _rail_eq(),
                                         sim_stable, cfg))
                    # "action" is what makes risk.portfolio_heat select a
                    # short's (stop − entry) instead of a long's (entry − stop).
                    # Without it every short in the book contributes EXACTLY
                    # ZERO heat — the Phase 2-A defect, reproduced here because
                    # this dict is built by hand rather than read from a ledger.
                    _sim_open_trades = {
                        s: {"qty": abs(p["qty"]), "entry_price": p["avg_entry"],
                            "action": p.get("action", "buy"),
                            "order": {"stop_price": p.get("stop")}}
                        for s, p in acct.positions.items()}
                    heat_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
                    if heat_pct:
                        eq = acct.equity(last_close)
                        heat = risk.portfolio_heat(_sim_open_trades, cfg, eq)
                        # The CANDIDATE's own contribution, direction-selected
                        # the same way pre_trade_checks does since Phase 2 Fix 1.
                        # A short's stop sits ABOVE the fill, so `fill - stop`
                        # is negative and max(..., 0.0) zeroed it: the cap went
                        # blind to the very order it was deciding on.
                        #
                        # The `if stop` guard has to wrap the SUBTRACTION, not
                        # just choose between two finished numbers. An earlier
                        # draft hoisted the distance out of the conditional and
                        # crashed on `fill - None` for every run with brackets
                        # disabled — a config the gate specs legitimately use.
                        if stop:
                            _dist = (stop - fill) if is_short else (fill - stop)
                            new_risk = qty * max(_dist, 0.0)
                        else:
                            new_risk = eq * cfg["risk"].get(
                                "risk_per_trade_pct", 1.0) / 100
                        if heat + new_risk > eq * heat_pct / 100:
                            n_heat_blocked += 1
                            by_strategy[owner]["blocked"] += 1
                            _blocked("heat")
                            continue
                    ccfg = cfg["risk"].get("correlation_cap") or {}
                    if ccfg.get("enabled") and acct.positions:
                        open_bars = {s: sym_bars[s][:idx[s][ts] + 1]
                                     for s in acct.positions
                                     if s != sym and ts in idx.get(s, {})}
                        n_corr = risk.correlated_position_count(
                            hist, open_bars, int(ccfg.get("lookback", 60)),
                            float(ccfg.get("threshold", 0.85)))
                        if n_corr >= int(ccfg.get("max_correlated", 2)):
                            n_corr_blocked += 1
                            by_strategy[owner]["blocked"] += 1
                            _blocked("correlation")
                            continue
                except risk.RiskRejection as e:
                    # §40: `e.rail` names which rail fired. Until 2026-07-29 this
                    # handler discarded it, collapsing regime, position cap,
                    # drawdown, cash and zero-quantity into one number. The
                    # reason existed at the moment of the block and was dropped.
                    by_strategy[owner]["blocked"] += 1
                    _blocked(getattr(e, "rail", "unattributed"))
                    continue
                census["executed"] += 1
                # SIGNED from here on, matching what broker.positions() reports
                # for a real short. Every downstream reader — equity(),
                # positions_dict() and therefore every rail, and SimTrade.pnl —
                # then works on both sides with no second formula.
                signed_qty = -qty if is_short else qty
                # Opening a short SELLS: cash goes UP by the proceeds. One
                # expression for both, because the sign already says which.
                acct.cash -= signed_qty * fill + fee
                # The opening order — a buy OR a short — pays the operator's
                # model (2026-10-28). Live charges only `buy` entries today;
                # that gap is the live side's, recorded in the PR follow-ups.
                _op = operator_charge(op_costs, signed_qty, fill, fee)
                op_paid += _op
                acct.positions[sym] = {
                    "qty": signed_qty, "avg_entry": fill, "entry_ts": ts,
                    "stop": stop, "tp": tp, "hw": fill, "owner": owner,
                    "action": action,
                    "trade": SimTrade(sym, ts, fill, signed_qty, fees=fee,
                                      operator_cost=_op)}
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
            elif sym in acct.positions:
                _exit(sym, _fill_open(sym, bar, ts), ts, "strategy_sell")
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
        pending = still_pending

        # 2. Bracket legs, intrabar, pessimistic: stop wins when both touch.
        for sym in list(acct.positions):
            if sym not in today:
                continue
            bar, pos = today[sym], acct.positions[sym]
            short = pos["qty"] < 0
            # WHICH SIDE OF THE BAR TOUCHES WHICH LEG. A long stops out when the
            # LOW falls to a stop below entry and targets when the HIGH rises to
            # a tp above it; a short is the mirror — stop ABOVE entry (reached
            # by the high), target BELOW (reached by the low). Reading a short
            # with the long tests is not merely wrong, it fires on bar one every
            # time: the first bar's low is already beneath a stop placed above
            # entry. Same defect class as counterfactual.py's, fixed in #92.
            #
            # PESSIMISM SURVIVES THE INVERSION: the stop is still checked before
            # the take-profit, so a bar spanning both legs is read against the
            # position whichever way it points. That is the backtest.simulate
            # step-2 parity every other exit path in this repo promises.
            stop_hit = (pos["stop"] is not None
                        and (bar["high"] >= pos["stop"] if short
                             else bar["low"] <= pos["stop"]))
            if stop_hit:
                _exit(sym, pos["stop"], ts, "stop_loss")
                continue
            tp_hit = (pos["tp"] is not None
                      and (bar["low"] <= pos["tp"] if short
                           else bar["high"] >= pos["tp"]))
            if tp_hit:
                _exit(sym, pos["tp"], ts, "take_profit")
                continue
            if _trails(pos["owner"]):
                # REFUSED, NOT SKIPPED. A short's chandelier ratchets DOWN from
                # a LOW-water mark, which is a different function from the one
                # `risk.trail_stop` implements — it takes a high-water mark and
                # the caller only ever raises the stop. Silently not trailing a
                # short would leave `trailing_atr_mult` looking applied while
                # doing nothing, which is the dead-guard shape this repo has
                # paid for twice (`short_bottom_fraction`, the preflight
                # account check).
                #
                # Unreachable today: `trailing_strategies: [tsmom]` and tsmom
                # has no short leg. It is a guard for the config that changes
                # that, not for a case that exists.
                if short:
                    raise ValueError(
                        f"{sym}: trailing stops are not implemented for shorts "
                        f"(owner {pos['owner']} is in trailing_strategies). A "
                        f"short's trail ratchets DOWN from a low-water mark; "
                        f"risk.trail_stop only ratchets up from a high-water "
                        f"one. Remove the owner from trailing_strategies or "
                        f"implement the short side before scoring this config.")
                pos["hw"] = max(pos.get("hw", pos["avg_entry"]), bar["high"])
                hist = sym_bars[sym][:idx[sym][ts] + 1]
                t_stop = risk.trail_stop(
                    pos["hw"], strategy.atr(hist, bcfg.get("atr_period", 14)),
                    cfg, strategy=pos["owner"])
                if t_stop is not None and (pos["stop"] is None
                                           or t_stop > pos["stop"]):
                    pos["stop"] = t_stop

        # 3. Signals, per strategy, in live priority order.
        xs_ctx: dict = {}
        for name, sparams in active:
            smod = strategies.REGISTRY[name]
            if smod.NEEDS_CROSS_SECTION:
                hist_by_sym = {s: sym_bars[s][:idx[s][ts] + 1]
                               for s in sym_bars if ts in idx[s]}
                # Shared entry point — see the single-strategy loop above and
                # strategies.prepare_one's docstring. `held` is this account's
                # open book, mirroring main.py passing its own open trades.
                xs_ctx[name] = strategies.prepare_one(
                    cfg, name, hist_by_sym, held=set(acct.positions))

        # ONE pass over symbols, mirroring the live cycle's structure exactly
        # (main.py "Phase 3: per-symbol ensemble loop with position ownership"):
        # a held symbol is judged ONLY by its owner; a flat symbol is offered to
        # each enabled strategy in priority order and the first buy claims it.
        #
        # The ordering is load-bearing, not cosmetic. `fills_by_date` counts
        # exits as well as entries, so interleaving buys and sells in symbol
        # order — rather than draining all exits first — decides which orders
        # fit under the shared daily cap. An earlier draft split the two passes
        # and silently changed the results.
        buys_queued = {name: 0 for name, _ in active}
        for sym, bar in today.items():
            i = idx[sym][ts]
            hist = sym_bars[sym][:i + 1]

            if sym in acct.positions:
                pos = acct.positions[sym]
                owner = pos["owner"]
                if owner not in strategies.REGISTRY:
                    continue          # orphaned owner: bracket legs still apply
                # The position's REAL side, off the signed qty — the same
                # derivation main.py uses, through the same helper, so the two
                # cannot answer differently. Phase 2 shipped a hardcoded "long"
                # here with a note that Phase 3 would replace it; this is that.
                sig = strategies.generate(
                    owner, sym, hist, cfg, True,
                    cross_section=xs_ctx.get(owner),
                    entry_ts=pos["entry_ts"],
                    position_side=side_of_qty(pos["qty"]))
                # EXIT_ACTIONS, not `== "sell"`: a short is closed by a "cover",
                # and on the old condition that cover fell through and was
                # silently discarded — the position's own exit signal ignored,
                # every bar, forever. Exactly the bug main.py had at the
                # equivalent site before Phase 2-C.
                if sig.action in EXIT_ACTIONS:
                    if min_days and _cal_days(pos["entry_ts"], ts) < min_days:
                        guard_skips += 1
                        continue
                    pending.append((sym, sig.action, owner))
                continue

            for name, sparams in active:
                # DIVERGENCE #17 — PER-STRATEGY UNIVERSE, entries only.
                #
                # `main.py:1685` has filtered entries by `in_universe` since
                # PR #90 introduced per-strategy universes. This loop did not,
                # so every strategy could enter every symbol in the snapshot:
                # on a 500-name wide snapshot the incumbent traded 500 names
                # here and 38 live.
                #
                # It went unnoticed because the only two universe-scoped
                # strategies, `xsmom` and `reclaim`, are BOTH cross-sectional,
                # and `prepare_one` scopes their ranking — so `generate` answers
                # "insufficient history for ranking" for a name outside their
                # universe and the filter appeared to be working. That is a
                # coincidence of those two being cross-sectional, not a filter:
                # a per-symbol strategy with a `universe:` key would have traded
                # the whole snapshot here, silently.
                #
                # BEFORE generate(), mirroring live, which skips the strategy
                # outright rather than asking it and discarding the answer.
                #
                # Entries only, and `in_universe`'s own docstring says why: exits
                # route to the OWNING strategy regardless of universe, or a
                # position whose symbol left a universe has no strategy willing
                # to close it. The owner-only exit branch above is untouched.
                #
                # Live's news-nomination bypass has no counterpart here: this
                # simulator has no news at all, which is divergence #14.
                if not strategies.in_universe(cfg, name, sym):
                    continue
                sig = strategies.generate(name, sym, hist, cfg, False,
                                          cross_section=xs_ctx.get(name),
                                          entry_ts=None)
                # ENTRY_ACTIONS, not `== "buy"`: a "short" is an entry, and
                # dropping it here would have made every short-leg arm score as
                # long-only while reporting that it ran.
                if sig.action not in ENTRY_ACTIONS:
                    continue
                census["signals"] += 1      # §40 denominator
                cd = cooldowns.get(name, 0)
                if cd and risk.cooldown_blocked(last_exit.get((name, sym)),
                                                ts, cd):
                    _blocked("cooldown")
                    continue
                blackout_days = sparams.get("earnings_blackout_days", 0)
                if blackout_days and earnings and earnings_mod.next_within(
                        earnings.get(sym, []), ts, blackout_days):
                    _blocked("earnings_blackout")
                    continue
                # §23 relative-volume confirmation (entries only; fails open).
                # `continue` not `break`: this symbol failed volume confirmation
                # for THIS strategy, but a lower-priority strategy may still
                # claim it — only an accepted buy consumes the symbol.
                if risk.rvol_blocked(hist, cfg, name):
                    _blocked("rvol")
                    continue
                # §58 contraction precondition (entries only; fails open).
                # `continue` not `break`, for rvol's reason exactly: the
                # threshold is per-strategy, so a lower-priority strategy with
                # a different setting may still claim this symbol today.
                if risk.contraction_blocked(hist, cfg, name):
                    _blocked("contraction")
                    continue
                # §31 cross-asset credit gate (entries only; fails open).
                # `break`, not `continue`: unlike rvol this is a MARKET-WIDE
                # condition with no per-strategy component, so no other
                # strategy could claim this symbol today either. Continuing
                # would re-evaluate an identical boolean for every remaining
                # strategy and reach the same answer.
                if risk.credit_blocked(credit, ts, cfg):
                    _blocked("credit")
                    break
                # §64 macro regime gate (entries only; fails open). `break`
                # for the credit gate's reason exactly: market-wide, no
                # per-strategy component, same answer for every strategy.
                if risk.macro_blocked(macro, ts, cfg):
                    _blocked("macro")
                    break
                # Unprotectable entry (2026-07-27). `continue`, not `break`:
                # this is a PER-SYMBOL property, so another strategy scanning a
                # different symbol is unaffected — unlike the market-wide credit
                # gate above.
                if risk.unprotectable_entry(
                        hist[-1]["close"], strategies.atr(hist, 14), cfg,
                        strategy=name):
                    _blocked("unprotectable_entry")
                    continue
                entry_cap = sparams.get("max_entries_per_cycle", 0)
                if entry_cap and buys_queued[name] >= entry_cap:
                    _blocked("entry_cap")
                    continue
                # sig.action, not "buy": the fill branch reads this tuple to
                # decide direction, and a "short" queued as a "buy" would be
                # filled as a long — the signal inverted between the bar that
                # produced it and the bar that executed it.
                pending.append((sym, sig.action, name))
                buys_queued[name] += 1
                break                 # first strategy to claim the symbol wins

        # §64 margin financing, charged BEFORE the equity mark so the curve
        # (and every drawdown read off it) carries the cost on the day it
        # accrued. Guarded on the multiplier because on the 1.0 path cash
        # cannot go negative through longs, and the guard keeps that path
        # byte-identical. Shorts' proceeds-borrow stays uncharged — that is
        # divergence #16, recorded, and out of scope here.
        if _margin_mult > 1.0 and acct.cash < 0:
            _rate = risk.financing_rate_pct(rate_bars, ts, cfg)
            _charge = abs(acct.cash) * _rate / 100.0 / 252.0
            acct.cash -= _charge
            financing_paid += _charge
        eq = acct.equity(last_close)
        # After the operator cost (op_paid is 0.0 when off: the identity), so
        # maxDD, Sharpe and the return all read after cost. Deployment and
        # exposure below stay on the broker basis: they measure the book, not
        # its P&L.
        curve.append(round(eq - op_paid, 2))
        # `avg_deployment_pct` KEEPS ITS MEANING. `(equity - cash) / equity` is
        # NET exposure, and every recorded verdict plus the `deployment_at_least`
        # and `beats_exposure_matched` rules read it. Redefining it would
        # silently reinterpret the whole archive, so gross is added ALONGSIDE
        # rather than instead.
        deployment.append((eq - acct.cash) / eq if eq > 0 else 0.0)
        # GROSS vs NET is the distinction a 130/30 book turns on, and nothing
        # here had to notice it before: shorting RAISES cash, so a book that is
        # 130% long and 30% short reports 100% deployment — indistinguishable
        # from one that is 100% long and flat. Gross sums MAGNITUDES and reads
        # 160% for the first and 100% for the second.
        _pos = acct.positions_dict(last_close)
        gross.append(sum(abs(p["market_value"]) for p in _pos.values()) / eq
                     if eq > 0 else 0.0)
        # Through risk.net_exposure_pct — the SAME function the live
        # net-exposure rail calls — so the simulator and the rail cannot
        # disagree about what net means.
        net.append(risk.net_exposure_pct(_pos, eq) / 100 if eq > 0 else 0.0)

    final_ts = all_ts[-1] if all_ts else ""
    for sym in list(acct.positions):
        _exit(sym, last_close[sym], final_ts, "end_of_data")

    # §40: the census must ACCOUNT for every signal. A missing one means a drop
    # path exists that nobody is counting — which is precisely the state this
    # census was built to end, so it fails loudly rather than balancing quietly.
    # A buy queued on the last bar, or one whose symbol stopped printing bars,
    # never reaches the fill path. It is a real signal that produced no trade
    # and no block, and it is the drop path the balance check caught on its
    # very first run — which is the whole argument for asserting the sum rather
    # than trusting the buckets.
    #
    # ENTRY_ACTIONS, not `== "buy"`. This drain kept counting only buys after
    # the queue learned to carry shorts, so a leftover SHORT was a counted
    # signal that reached no bucket — and the balance check below caught it on
    # the first run that produced one (138 signals, 136 accounted for). It stays
    # an ACTION test rather than counting every leftover, because `pending` also
    # holds exits, and an unfilled exit is not an entry that failed to happen.
    for _sym, _action, _owner in pending:
        if _action in ENTRY_ACTIONS:
            _blocked("expired_unfilled")

    _seen = census["executed"] + sum(census["blocked"].values())
    if _seen != census["signals"]:
        # Never balance quietly. An unexplained gap means a drop path exists
        # that nobody counts, which is the exact state this census ends.
        census["blocked"]["unaccounted"] = census["signals"] - _seen
        print(f"  !! block census does not balance: {census['signals']} "
              f"signals, {_seen} accounted for", file=sys.stderr)

    # §45 EXIT CENSUS — the mirror of §40's entry census, same must-sum rule.
    #
    # §40 asked how capital gets IN. Nothing had ever asked how it gets OUT,
    # even though every closed trade has carried an `exit_reason` since the
    # simulator was written; it was aggregated nowhere. The live book turns over
    # at 0.27 closes/day, which puts Invariant 10's >=30 closed trades ~98 days
    # out — later than the >=60-day clock — so the close rate, not the calendar,
    # is what gates revenue, and it had never been measured.
    #
    # `end_of_data` is the bucket that matters. It is a FORCED close at run end,
    # not a decision, and live has no equivalent — nothing force-closes a real
    # position. Every trade in that bucket is one the strategies never chose to
    # sell, so a backtest's closed-trade count is inflated by run truncation to
    # exactly that extent.
    #
    # Observation only: this reads `closed` after the run and cannot change it.
    # The frozen gates must reproduce byte-identically, s43d included.
    exits: dict = {"closed": len(closed), "by_reason": {},
                   "holding_days": {}, "guard_skipped_exits": guard_skips}
    _holds = []
    for t in closed:
        exits["by_reason"][t.exit_reason or "unset"] = \
            exits["by_reason"].get(t.exit_reason or "unset", 0) + 1
        d = _cal_days(t.entry_ts, t.exit_ts) if t.exit_ts else None
        if d is not None:
            _holds.append(d)
    if _holds:
        _holds.sort()
        _h = len(_holds)
        exits["holding_days"] = {
            "min": _holds[0], "max": _holds[-1],
            "median": _holds[_h // 2],
            "mean": round(sum(_holds) / _h, 1),
            "p90": _holds[min(_h - 1, int(_h * 0.9))],
        }
    _acc = sum(exits["by_reason"].values())
    if _acc != len(closed):
        # Same rule as §40: an exit reason that goes unrecorded means a close
        # path nobody counts. Fail loudly rather than balance quietly.
        exits["by_reason"]["unaccounted"] = len(closed) - _acc
        print(f"  !! exit census does not balance: {len(closed)} closed, "
              f"{_acc} accounted for", file=sys.stderr)

    _dep = sorted(deployment)
    _n = len(_dep)

    total_ret = (acct.cash - op_paid - start_cash) / start_cash * 100
    wins = [t for t in closed if t.pnl > 0]
    bh = (sum(buy_and_hold_return(b, slip, fee, start_cash / len(sym_bars),
                                  op_costs)
              for b in sym_bars.values()) / len(sym_bars)) if sym_bars else 0.0
    _bm_ret, _bm_dd = benchmark_stats(sym_bars, BENCHMARK_SYMBOL, slip, fee,
                                      start_cash, op_costs)

    return Result(
        params={"strategy": "ENSEMBLE",
                "members": [n for n, _ in active],
                "by_strategy": {k: {"trades": v["trades"],
                                    "pnl": round(v["pnl"], 2),
                                    "blocked": v["blocked"]}
                                for k, v in by_strategy.items()}},
        start=all_ts[0] if all_ts else "", end=final_ts,
        n_trades=len(closed), n_guard_skipped_exits=guard_skips,
        n_heat_blocked=n_heat_blocked, n_corr_blocked=n_corr_blocked,
        census=census,
        exits=exits,
        deployment_zero_pct=round(
            100 * sum(1 for d in _dep if d < 1e-9) / _n, 2) if _n else 0.0,
        deployment_median_pct=round(100 * _dep[_n // 2], 2) if _n else 0.0,
        deployment_max_pct=round(100 * _dep[-1], 2) if _n else 0.0,
        gross_exposure_avg_pct=round(100 * sum(gross) / len(gross), 2)
        if gross else 0.0,
        gross_exposure_max_pct=round(100 * max(gross), 2) if gross else 0.0,
        net_exposure_avg_pct=round(100 * sum(net) / len(net), 2) if net else 0.0,
        net_exposure_min_pct=round(100 * min(net), 2) if net else 0.0,
        n_short_trades=sum(1 for t in closed if t.qty < 0),
        n_short_symbols=len({t.symbol for t in closed if t.qty < 0}),
        n_fill_fallback=n_fill_fallback,
        financing_paid_usd=round(financing_paid, 2),
        operator_costs=op_costs,
        operator_cost_paid_usd=round(op_paid, 2),
        total_return_pct=round(total_ret, 3),
        buy_hold_return_pct=round(bh, 3),
        buy_hold_max_drawdown_pct=round(buy_and_hold_drawdown(sym_bars), 3),
        # THE PATH EVERY GATE ACTUALLY TAKES. The single-strategy Result above
        # is constructed separately, so a field added to only one of these two
        # sites scores None everywhere it matters while looking correct in a
        # unit test — there is a test asserting BOTH paths populate it.
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_return_pct=_bm_ret, benchmark_max_drawdown_pct=_bm_dd,
        avg_deployment_pct=round(100 * sum(deployment) / len(deployment), 2)
        if deployment else 0.0,
        win_rate=round(len(wins) / len(closed), 3) if closed else 0.0,
        profit_factor=round(profit_factor(closed), 3),
        max_drawdown_pct=round(max_drawdown(curve), 3),
        equity_curve=curve, trades=closed)


# -------------------------------------------------------------- walk-forward

def load_config(path: str = "config.yaml") -> dict:
    """The live config, for callers that drive simulate() directly (e.g.
    scripts/gate_compare.py). simulate() deep-copies before touching it."""
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


# THE trials log path, owned here and nowhere else.
#
# Until 2026-08-22 this literal existed in fifteen places — two here, one in
# revalidate.py, seven gate_*.py scripts, conftest, a test docstring, CLAUDE.md
# and twice in config.yaml — which is the exact bug src/statepaths.py was
# written to end ("Three constants can drift where one cannot"). Moving the
# file without collapsing them would have re-created it with fifteen.
#
# research/, not memory/. Two reasons, and the second is the decisive one:
#   * research/registrations.jsonl and research/verdicts.jsonl live there and
#     are tracked; the trial log is the third leg of the same record.
#   * memory/ is gitignored, so a test that reads it is SKIPPED on CI and in
#     every worktree — "the check that cannot fail", as test_doc_counts.py
#     already argued for the live trade count. A trial count nobody can
#     re-derive is not accounting.
DEFAULT_TRIALS_PATH = "research/trials.jsonl"


def append_trial(path: str, record: dict):
    """Append one trial. Mutates `record` (adds logged_at) — pass a fresh dict.

    Raw open(), not store.open_store(): the sqlite backend is for the live
    bot's streams, and a research log that nothing on the trading path reads
    does not need to follow it there.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    record["logged_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def walk_forward(sym_bars: dict, cfg: dict, grid: list, split: float,
                 trials_path: str, start_cash: float = 100_000.0,
                 strategy_name: str = "ma_crossover",
                 earnings: dict | None = None) -> dict:
    """In-sample grid search, out-of-sample validation of the single winner.
    EVERY variant tried is appended to the trials log — the honest audit trail
    against the False Strategy Theorem (the more variants you try, the better
    the best one looks by luck alone)."""
    is_bars = {s: b[:int(len(b) * split)] for s, b in sym_bars.items()}
    oos_bars = {s: b[int(len(b) * split):] for s, b in sym_bars.items()}

    results = []
    for params in grid:
        r = simulate(is_bars, cfg, params, start_cash, strategy_name,
                     earnings=earnings)
        results.append(r)
        append_trial(trials_path, {"phase": "in_sample", **r.summary()})

    best = max(results, key=lambda r: (r.total_return_pct, r.profit_factor))
    best_params = {k: v for k, v in best.params.items() if k != "strategy"}
    oos = simulate(oos_bars, cfg, best_params, start_cash, strategy_name,
                   earnings=earnings)
    append_trial(trials_path, {"phase": "oos", **oos.summary()})

    return {"n_variants": len(grid), "best_params": best.params,
            "is_result": best.summary(), "oos_result": oos.summary()}


def enablement_gate(oos: dict) -> tuple[bool, list[str]]:
    """May a strategy be enabled live? The OOS result must clear every bar.
    Params must be the in-sample winner, never re-picked after seeing OOS
    (the trials log makes violations visible)."""
    reasons = []
    if oos["total_return_pct"] <= 0:
        reasons.append(f"OOS return {oos['total_return_pct']:+.2f}% not positive")
    if oos["n_trades"] < 15:
        reasons.append(f"only {oos['n_trades']} OOS trades (need >=15)")
    if oos["profit_factor"] < 1.3:
        reasons.append(f"OOS profit factor {oos['profit_factor']} < 1.3")
    bh = oos["buy_hold_return_pct"]
    beats_bh = oos["total_return_pct"] >= bh
    # risk-adjusted escape hatch: most of the return at half the drawdown
    risk_adjusted = (oos["total_return_pct"] >= 0.7 * bh
                     and oos["max_drawdown_pct"]
                     <= 0.5 * oos["buy_hold_max_drawdown_pct"])
    # exposure-matched benchmark: the risk rails cap deployment at a few
    # percent of equity, so total-equity return can't fairly race a 100%%-
    # deployed B&H. Compare against B&H scaled to the SAME average exposure.
    deploy_frac = oos.get("avg_deployment_pct", 100.0) / 100.0
    exposure_bench = bh * deploy_frac
    exposure_matched = (deploy_frac < 0.5
                        and oos["total_return_pct"] >= exposure_bench)
    if not (beats_bh or risk_adjusted or exposure_matched):
        reasons.append(f"OOS return {oos['total_return_pct']:+.2f}% beats neither "
                       f"B&H {bh:+.2f}%, the risk-adjusted bar "
                       f"(dd {oos['max_drawdown_pct']:.1f}% vs B&H dd "
                       f"{oos['buy_hold_max_drawdown_pct']:.1f}%), nor the "
                       f"exposure-matched benchmark {exposure_bench:+.2f}% "
                       f"(at {oos.get('avg_deployment_pct', 100):.1f}% avg deployment)")
    return (not reasons), reasons


def build_grid(fasts, slows, stop_mults, tp_mults) -> list:
    grid = [{"fast_period": f, "slow_period": s,
             "stop_atr_mult": sm, "take_profit_atr_mult": tm}
            for f, s, sm, tm in itertools.product(fasts, slows, stop_mults, tp_mults)
            if f < s]
    if not grid:
        raise SystemExit("empty parameter grid (need fast < slow)")
    return grid


def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def build_generic_grid(param_args: list, stop_mults, tp_mults) -> list:
    """Cross product of --param NAME v1 v2 ... lists plus bracket multipliers."""
    axes = {}
    for entry in param_args:
        name, *values = entry
        if not values:
            raise SystemExit(f"--param {name} needs at least one value")
        axes[name] = [_coerce(v) for v in values]
    axes["stop_atr_mult"] = list(stop_mults)
    axes["take_profit_atr_mult"] = list(tp_mults)
    names = list(axes)
    return [dict(zip(names, combo))
            for combo in itertools.product(*axes.values())]


# ----------------------------------------------------------------------- CLI

def main():
    cfg = load_config()
    bt = cfg.get("backtest", {})

    p = argparse.ArgumentParser(description="Offline backtest (never touches the live ledger)")
    p.add_argument("--strategy", default="ma_crossover",
                   choices=sorted(strategies.REGISTRY),
                   help="which strategy to walk-forward")
    p.add_argument("--param", nargs="+", action="append", default=[],
                   metavar=("NAME", "VALUES"),
                   help="grid values for a strategy param, e.g. "
                        "--param momentum_bars 63 126 252 (repeatable)")
    p.add_argument("--symbols", nargs="+", default=cfg["symbols"])
    p.add_argument("--bars-file", help="JSON/CSV bars; skips all network access")
    p.add_argument("--earnings-file",
                   help="JSON {symbol: [iso dates]} — frozen earnings "
                        "calendar for earnings_blackout_days variants "
                        "(omit to fetch via yfinance, cached)")
    p.add_argument("--start"), p.add_argument("--end")
    p.add_argument("--fast", nargs="+", type=int,
                   default=[cfg["strategy"]["fast_period"]])
    p.add_argument("--slow", nargs="+", type=int,
                   default=[cfg["strategy"]["slow_period"]])
    p.add_argument("--stop-mult", nargs="+", type=float,
                   default=[cfg["risk"].get("brackets", {}).get("stop_atr_mult", 0)])
    p.add_argument("--tp-mult", nargs="+", type=float,
                   default=[cfg["risk"].get("brackets", {}).get("take_profit_atr_mult", 0)])
    p.add_argument("--split", type=float, default=bt.get("walk_forward_split", 0.7))
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--slippage-bps", type=float, default=None,
                   help="cost model bps; default = median measured fill "
                        "slippage from the ledger when >=10 clean fills "
                        "exist, else config backtest.slippage_bps")
    p.add_argument("--vol-target", action="store_true",
                   help="CANDIDATE: enable risk.vol_target sizing for this run")
    p.add_argument("--regime-cap", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: enable down-regime gross exposure cap at PCT")
    p.add_argument("--trail-mult", type=float, default=None, metavar="MULT",
                   help="CANDIDATE §7: chandelier trailing stop at MULT x ATR")
    p.add_argument("--risk-sizing", type=float, default=None, metavar="PCT",
                   help="CANDIDATE §8: stop-distance sizing at PCT equity "
                        "risk per trade (replaces vol_target for the run)")
    p.add_argument("--cooldown-days", type=int, default=None, metavar="N",
                   help="CANDIDATE §9: same-ticker re-entry cooldown, N days")
    # Deployment / capacity levers (§12-§13). These live in cfg["risk"] and had
    # no flags, so the ONLY way to test them was editing the live config.yaml —
    # exactly the setup that produced the 07-16 drift accident. Flags keep
    # exploration off the live config.
    p.add_argument("--risk-per-trade", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: risk.risk_per_trade_pct — the NOTIONAL "
                        "sizing lever (binds tsmom/ma_crossover)")
    p.add_argument("--max-order-value", type=float, default=None, metavar="USD",
                   help="CANDIDATE: risk.max_order_value_usd — the per-order "
                        "cap (binds meanrev's risk-sizing path)")
    p.add_argument("--max-position-pct", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: risk.max_position_pct concentration cap")
    p.add_argument("--max-positions", type=int, default=None, metavar="N",
                   help="CANDIDATE: risk.max_open_positions — slot count "
                        "(the live evidence-velocity bottleneck)")
    p.add_argument("--max-trades-day", type=int, default=None, metavar="N",
                   help="CANDIDATE: risk.max_trades_per_day rate limit")
    p.add_argument("--judge-model", action="store_true",
                   help="CANDIDATE §29: simulate the LLM judge's downsizing. "
                        "OFF by default so pre-2026-07-26 gates reproduce; "
                        "every number in backtest_candidates.md before that "
                        "date was produced WITHOUT it (divergence #8)")
    p.add_argument("--judge-salt", default=None, metavar="S",
                   help="redraw the simulated judge's verdicts. For testing "
                        "that a result survives a different draw — NOT for "
                        "shopping until one looks good")
    p.add_argument("--strategy-max-positions", type=int, default=None,
                   metavar="N",
                   help="CANDIDATE §13: per-strategy slot allocation for the "
                        "strategy under test (global cap still applies)")
    p.add_argument("--fee", type=float, default=bt.get("fee_per_trade_usd", 0.0))
    p.add_argument("--trials-path",
                   default=bt.get("trials_path", DEFAULT_TRIALS_PATH))
    args = p.parse_args()

    cfg.setdefault("backtest", {})
    if args.slippage_bps is not None:
        slip_resolved, slip_src = args.slippage_bps, "explicit flag"
    else:
        measured = None
        try:
            from ledger import Ledger
            measured = measured_slippage_bps(
                Ledger(cfg["memory"]["ledger_path"]).all_records())
        except Exception:  # noqa: BLE001 — calibration is best-effort
            pass
        slip_resolved = (measured if measured is not None
                         else bt.get("slippage_bps", 5))
        slip_src = ("measured from ledger fills" if measured is not None
                    else "config default (need >=10 clean measured fills)")
    print(f"(cost model: slippage {slip_resolved} bps — {slip_src})")
    cfg["backtest"]["slippage_bps"] = slip_resolved
    cfg["backtest"]["fee_per_trade_usd"] = args.fee
    if args.vol_target:
        cfg["risk"].setdefault("vol_target", {}).update(
            enabled=True, strategies=None)  # flag = test THIS strategy
        print(f"(CANDIDATE on: vol_target {cfg['risk']['vol_target']})")
    if args.regime_cap is not None:
        cfg["risk"]["regime_exposure"] = {"enabled": True,
                                          "down_max_gross_pct": args.regime_cap}
        print(f"(CANDIDATE on: regime_exposure cap {args.regime_cap}%)")
    if args.trail_mult is not None:
        cfg["risk"].setdefault("brackets", {})["trailing_atr_mult"] = args.trail_mult
        print(f"(CANDIDATE on: chandelier trail {args.trail_mult}x ATR)")
    if args.risk_sizing is not None:
        # Pre-registered (§8): risk-based sizing REPLACES vol_target for the
        # run — both normalize by realized vol; stacking them double-counts.
        cfg["risk"]["risk_sizing"] = {"enabled": True,
                                      "risk_pct": args.risk_sizing,
                                      "strategies": None}
        cfg["risk"]["vol_target"] = {"enabled": False}
        print(f"(CANDIDATE on: risk-based sizing {args.risk_sizing}%/trade, "
              "vol_target off for this run)")
    if args.cooldown_days is not None:
        cfg["risk"]["reentry_cooldown"] = {"days": args.cooldown_days,
                                           "strategies": None}
        print(f"(CANDIDATE on: re-entry cooldown {args.cooldown_days}d)")
    for flag, key, label in (
            (args.risk_per_trade, "risk_per_trade_pct", "notional risk/trade %"),
            (args.max_order_value, "max_order_value_usd", "per-order cap $"),
            (args.max_position_pct, "max_position_pct", "concentration cap %"),
            (args.max_positions, "max_open_positions", "position slots"),
            (args.max_trades_day, "max_trades_per_day", "trades/day")):
        if flag is not None:
            cfg["risk"][key] = flag
            print(f"(CANDIDATE on: {label} = {flag})")
    if args.judge_model:
        cfg.setdefault("backtest", {}).setdefault("judge_model", {})[
            "enabled"] = True
        cal = judge_model.load_calibration()
        print(f"(CANDIDATE §29 on: simulated judge — cuts "
              f"{cal['downsize_rate']:.1%} of buys, mean scale "
              f"{cal['mean_scale']:.3f}, n={cal['n_judged_buys']} live)")
    if args.judge_salt:
        cfg.setdefault("backtest", {}).setdefault("judge_model", {})[
            "salt"] = args.judge_salt
        print(f"(judge draw re-salted: {args.judge_salt})")
    if args.strategy_max_positions is not None:
        cfg.setdefault("strategies", {}).setdefault(args.strategy, {})[
            "max_open_positions"] = args.strategy_max_positions
        # The per-strategy allocation can only TIGHTEN; lift the global ceiling
        # so it is the allocation under test rather than the old global 5.
        cfg["risk"]["max_open_positions"] = max(
            cfg["risk"]["max_open_positions"], args.strategy_max_positions)
        print(f"(CANDIDATE §13 on: {args.strategy} slot allocation = "
              f"{args.strategy_max_positions})")

    if args.bars_file:
        sym_bars = load_bars_file(args.bars_file)
    elif args.start and args.end:
        sym_bars = fetch_bars(args.symbols, args.start, args.end)
    else:
        p.error("need --bars-file, or --start and --end")

    if args.strategy == "ma_crossover" and not args.param:
        grid = build_grid(args.fast, args.slow, args.stop_mult, args.tp_mult)
    else:
        grid = build_generic_grid(args.param, args.stop_mult, args.tp_mult)

    # Earnings calendar: only needed when the grid varies the blackout.
    earnings_data = None
    if any(p.get("earnings_blackout_days") for p in grid):
        if args.earnings_file:
            with open(args.earnings_file) as f:
                earnings_data = json.load(f)
        else:
            import earnings as earnings_mod_cli
            earnings_data = {s: earnings_mod_cli.get_dates(s)
                             for s in sym_bars}
            frozen = "memory/earnings_snapshot.json"
            with open(frozen, "w") as f:
                json.dump(earnings_data, f)
            print(f"(earnings calendar fetched + frozen to {frozen} — pass "
                  f"--earnings-file {frozen} to reproduce)\n")

    print(f"Walk-forward [{args.strategy}]: {len(grid)} variant(s), split "
          f"{args.split:.0%} IS / {1 - args.split:.0%} OOS, "
          f"{len(sym_bars)} symbol(s)\n")
    out = walk_forward(sym_bars, cfg, grid, args.split, args.trials_path,
                       args.cash, strategy_name=args.strategy,
                       earnings=earnings_data)

    is_r, oos_r = out["is_result"], out["oos_result"]
    print(f"Best in-sample params: {out['best_params']}")
    print(f"  IS : return {is_r['total_return_pct']:+.2f}%  vs B&H "
          f"{is_r['buy_hold_return_pct']:+.2f}%  | trades {is_r['n_trades']}  "
          f"win {is_r['win_rate']:.0%}  PF {is_r['profit_factor']}  "
          f"maxDD {is_r['max_drawdown_pct']:.1f}%")
    print(f"  OOS: return {oos_r['total_return_pct']:+.2f}%  vs B&H "
          f"{oos_r['buy_hold_return_pct']:+.2f}%  | trades {oos_r['n_trades']}  "
          f"win {oos_r['win_rate']:.0%}  PF {oos_r['profit_factor']}  "
          f"maxDD {oos_r['max_drawdown_pct']:.1f}%")
    print(f"\nThe OOS line is the honest one. All {out['n_variants']} variant(s) "
          f"logged to {args.trials_path}.")
    ok, reasons = enablement_gate(oos_r)
    if ok:
        print(f"ENABLEMENT GATE: PASS — {args.strategy} may be enabled in "
              "config.yaml with the best-IS params above.")
    else:
        print(f"ENABLEMENT GATE: FAIL — {args.strategy} stays disabled:")
        for r in reasons:
            print(f"  - {r}")


if __name__ == "__main__":
    main()
