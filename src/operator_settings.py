"""Operator trading settings: a cost model per market and a trade frequency.

Set by Peter (or the Reformatic operator) on the console's /trading page. The console
host writes them to a JSON file in this bot's state directory (`memory/`, see
`statepaths.operator_dir`) every few minutes
(console/scripts/trading_settings_sync.py); this module is the only reader. The bot
reads the file at the start of every cycle (`main._bootstrap_cycle`), so a change
takes effect at the next trade and never rewrites past ones.

    {"schema": 2, "version": 4, "frequency": "medium",
     "updated_at": "2026-10-28T14:00:00Z",
     "costs": {"equities": {"model": "per_share", "flat_per_order": 1.0,
                            "per_share": 0.005, "min_per_order": 1.0,
                            "max_pct_of_value": 1.0},
               "options": {...}, "fx": {...}, "crypto": {...}},
     "fee_per_trade": 1.0, "fee_on_crypto": false, "fee_on_fx": false}

THE COST MODELS (settings schema 2, 2026-10-27) live in `cost_models.py`, a verbatim
copy of the console's reformatic/trading/costs.py so the formulas never diverge.
This bot trades equities, so it charges the `equities` model: per_share (US$0.005 a
share, at least US$1.00 an order, at most 1% of the order's value), `flat` or `off`.
A schema-1 file (only `fee_per_trade`, `fee_on_crypto`, `fee_on_fx`) still loads:
`cost_models.from_settings` turns it into flat models that charge exactly what
schema 1 charged, and a schema-2 file whose `costs` fail to validate falls back to
those flat fields with `problem` set. The flat fields stay on OperatorSettings as
the schema-1 view (health output); charging goes through the cost models only.

THE COST IS SIMULATED. Paper brokers charge nothing, so the bot does not change any
order, its cash, or the broker equity it sizes on (invariant #4: equity is always
read fresh from the broker). It appends one line per charged order to
`operator_fees.jsonl` next to the settings file; the console's exporter subtracts the
running total from the equity it shows and puts each fee on the matching transaction
(`ref` is that transaction's key in the exporter: "buy:<trade_id>" for a filled entry,
"close:<trade_id>" for a close). The entry key is "buy:" whichever way the trade
points: since 2026-10-28 a short's opening sell pays under it too (with `side`
"short"), so a short's two orders join on `ref` exactly as a long's do. Each line also
says how the fee was worked out (`settings_schema`, `model`, `gross`, `venue_fee`,
`basis`). It is not a fill cost:
it never goes into the slippage numbers or sizing, and the outcome record's P&L stays
the strategy's gross result (the console nets the fee itself).

Amended 2026-10-28 (owner decision): "Yes: make gates, backtests and the
drawdown/retire rails use after-cost results first (repete4's override included).
Entry cost filters later, bot by bot." The paragraph above used to end "it never
goes into the backtester, the slippage numbers, sizing, the rails or the outcome
P&L". The rails now read RAIL EQUITY = broker equity - the cost charged so far
(`read_strict` below; risk.rail_equity), the outcome-based rules (live kill, decay
monitor) subtract each trade's own fees joined on `ref` (`fees_by_ref`), and the
backtester charges the same cost models from a pinned block (`backtest.
operator_costs`, off by default). Sizing and the recorded broker equity do not
change.

TWO READERS, on purpose. `total()` and `charged_refs()` are LENIENT: they skip a
line they cannot read, which is right for display and for the once-per-ref guard.
The rails must never do that — a skipped line is a cost treated as 0, which flatters
every rail that reads it. `read_strict()` (and `rail_totals()` / `fees_by_ref()` on
top of it) raises FeeLedgerError on an unreadable file, a torn line, or a line in
another currency; the rails then fail CLOSED. A missing file is not an error: it
means nothing has been charged yet.

THE FREQUENCY changes a few entry knobs for the cycle (`apply_frequency`). "medium" is
the bot exactly as configured in config.yaml — the config object comes back untouched.

A missing or unreadable file means "no operator settings yet": every market `off`
(no cost), medium frequency. That is the behaviour before this module existed.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cost_models

SETTINGS_FILE = "operator_settings.json"
FEES_FILE = "operator_fees.jsonl"
FREQUENCIES = ("low", "medium", "high")
MARKETS = cost_models.MARKETS
# Schema 1's bound on fee_per_trade (cost_models.from_settings enforces it).
FEE_MAX = 1000.0

# This bot trades US equities in USD on Alpaca paper.
MARKET = "equities"
CURRENCY = "USD"

# Where the settings in effect for this cycle ride along inside the cycle's cfg dict
# (as a plain dict, so cfg stays JSON-serialisable). Set by main._bootstrap_cycle.
CFG_KEY = "_operator_settings"

# What each frequency changes. "medium" is absent on purpose: it changes nothing.
# max_trades_per_day is a ceiling (min with the configured one; 0 = uncapped counts
# as no ceiling), the other two are set outright.
LOW_MAX_TRADES_PER_DAY = 2
FREQUENCY_MAP = {
    "low": {"max_trades_per_day_cap": LOW_MAX_TRADES_PER_DAY,
            "meanrev.rsi_buy_below": 5,
            "reentry_cooldown.days": 10},
    "high": {"meanrev.rsi_buy_below": 15,
             "reentry_cooldown.days": 2},
}


def no_costs() -> dict:
    """Every market `off`: what the bot charges with no settings file. The values
    are the research's, so the dict is a valid cost-models block."""
    return {m: dict(cost_models.RECOMMENDED[m], model="off") for m in MARKETS}


@dataclass(frozen=True)
class OperatorSettings:
    version: int = 0  # 0 = no file: nothing set yet
    # The schema-1 view of the costs (cost_models.v1_equivalent), kept for health
    # output and older readers. NOT used to charge: charge() reads `costs`.
    fee_per_trade: float = 0.0
    fee_on_crypto: bool = False
    fee_on_fx: bool = False
    frequency: str = "medium"
    updated_at: str = ""
    problem: str = ""  # why the file was ignored, for health output; "" when fine
    # How the costs were read: 2 = the file's `costs` block, 1 = the flat schema-1
    # fields (a schema-1 file, or a schema-2 file whose `costs` were invalid),
    # 0 = no file / file ignored.
    schema: int = 0
    # cost_models' normalised block, every market present. Plain dicts so the
    # settings ride in the cycle's cfg as JSON (to_cfg/from_cfg).
    costs: dict = field(default_factory=no_costs, hash=False)

    def fee_for(self, market: str) -> float:
        """The SCHEMA-1 view: the flat cost one order paid in this market under
        schema 1. Kept for older callers and tests; charge() does not use it."""
        if market not in MARKETS:
            raise ValueError(f"unknown market {market!r}")
        if market == "crypto" and not self.fee_on_crypto:
            return 0.0
        if market == "fx" and not self.fee_on_fx:
            return 0.0
        return self.fee_per_trade

    def describe(self, market: str | None = None) -> str:
        """This bot's market line, e.g. "US$0.005 a share, at least US$1.00 an
        order, at most 1% of its value", or "no cost"."""
        return cost_models.describe(self.costs, market or MARKET)

    def summary(self) -> str:
        """One line for logs and the ledger."""
        s = (f"v{self.version} schema={self.schema} "
             f"{MARKET} cost=({self.describe()}) "
             f"frequency={self.frequency}")
        return s + (f" | problem: {self.problem}" if self.problem else "")


def load(state_dir: Path | str) -> OperatorSettings:
    """The settings in `state_dir`, or the defaults with `problem` set when the file is
    missing or not valid. Never raises: a bad file must not stop a trading cycle."""
    path = Path(state_dir) / SETTINGS_FILE
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return OperatorSettings(problem="no operator settings file yet")
    except (OSError, ValueError) as e:
        return OperatorSettings(problem=f"operator settings unreadable: {e}")
    try:
        freq = str(raw["frequency"])
        version = int(raw["version"])
        if freq not in FREQUENCIES:
            raise ValueError(f"frequency {freq!r}")
        # The costs: schema 2's `costs` block, or the flat schema-1 fields (also
        # the fallback when `costs` is invalid; `problem` then says so). Raises
        # only when the flat fields are bad too: the whole file is ignored then.
        costs, problem = cost_models.from_settings(raw)
        schema = 2 if raw.get("schema") == cost_models.SCHEMA and not problem else 1
        v1 = cost_models.v1_equivalent(costs)
        return OperatorSettings(
            version=version,
            fee_per_trade=v1["fee_per_trade"],
            fee_on_crypto=v1["fee_on_crypto"],
            fee_on_fx=v1["fee_on_fx"],
            frequency=freq,
            updated_at=str(raw.get("updated_at") or ""),
            problem=problem,
            schema=schema,
            costs=costs,
        )
    except (KeyError, TypeError, ValueError) as e:
        return OperatorSettings(problem=f"operator settings invalid: {e}")


def to_cfg(cfg: dict, settings: OperatorSettings) -> dict:
    """Attach the settings in effect to the cycle's cfg (mutates and returns cfg)."""
    cfg[CFG_KEY] = asdict(settings)
    return cfg


def from_cfg(cfg: dict | None) -> OperatorSettings | None:
    """The settings this cycle loaded, or None when the cfg never had any attached
    (a test fixture, a script that did not bootstrap a cycle): no charge then."""
    try:
        raw = (cfg or {}).get(CFG_KEY)
        return OperatorSettings(**raw) if raw else None
    except (AttributeError, TypeError):
        return None


def apply_frequency(cfg: dict, settings: OperatorSettings) -> dict:
    """The cycle's config under the operator's trade frequency.

    medium (and anything unexpected) returns `cfg` ITSELF, untouched: medium is the
    bot exactly as config.yaml describes it. low/high return an adjusted deep copy;
    the caller's dict is never mutated. A block that is missing from the config is
    left missing — inventing an unscoped `reentry_cooldown` would apply it to every
    strategy, which is not what either level means.

        low:  risk.max_trades_per_day = min(current, 2)   (0/absent = uncapped -> 2)
              strategies.meanrev.rsi_buy_below = 5
              risk.reentry_cooldown.days = 10
        high: strategies.meanrev.rsi_buy_below = 15
              risk.reentry_cooldown.days = 2
              (max_trades_per_day unchanged)
    """
    plan = FREQUENCY_MAP.get(settings.frequency)
    if not plan:
        return cfg
    out = copy.deepcopy(cfg)
    risk_cfg = out.get("risk")
    if "max_trades_per_day_cap" in plan and isinstance(risk_cfg, dict):
        cap = plan["max_trades_per_day_cap"]
        cur = risk_cfg.get("max_trades_per_day") or 0
        risk_cfg["max_trades_per_day"] = min(cur, cap) if cur else cap
    meanrev = (out.get("strategies") or {}).get("meanrev")
    if isinstance(meanrev, dict):
        meanrev["rsi_buy_below"] = plan["meanrev.rsi_buy_below"]
    cooldown = risk_cfg.get("reentry_cooldown") if isinstance(risk_cfg, dict) else None
    if isinstance(cooldown, dict):
        cooldown["days"] = plan["reentry_cooldown.days"]
    return out


def charged_refs(state_dir: Path | str) -> set[str]:
    """Every `ref` already charged, so a fill seen twice (reconcile, retries) pays once."""
    out = set()
    path = Path(state_dir) / FEES_FILE
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.add(json.loads(line)["ref"])
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return out


def charge(
    state_dir: Path | str,
    settings: OperatorSettings,
    *,
    ref: str,
    market: str,
    symbol: str,
    side: str,
    currency: str = "USD",
    ts: datetime | None = None,
    shares: float | None = None,
    contracts: float | None = None,
    units: float | None = None,
    notional: float | None = None,
    pip: float | None = None,
    quote_rate: float | None = None,
    venue_fee: float = 0.0,
) -> float:
    """Charge one order under the market's cost model. Appends a line to
    operator_fees.jsonl and returns the fee, or returns 0.0 and writes nothing when
    the order costs nothing or `ref` was already charged. `ref` must be the
    exporter's transaction key for this order.

    The order's real figures (`shares`, `notional`, ...) go to
    cost_models.order_cost, which raises ValueError when the model needs one that
    was not given; `venue_fee` is what the venue already charged on this fill (0 on
    commission-free paper), so the charge only tops it up to the model."""
    if market not in MARKETS:
        raise ValueError(f"unknown market {market!r}")
    gross, basis = cost_models.order_cost(
        settings.costs, market, shares=shares, contracts=contracts, units=units,
        notional=notional, pip=pip, quote_rate=quote_rate)
    fee = cost_models.charge_amount(gross, venue_fee)
    if fee <= 0 or ref in charged_refs(state_dir):
        return 0.0
    line = {
        "ts": (ts or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "ref": ref,
        "symbol": symbol,
        "side": side,
        "market": market,
        "fee": fee,
        "currency": currency,
        "settings_version": settings.version,
        "settings_schema": settings.schema,
        "model": settings.costs[market]["model"],
        "gross": gross,
        "venue_fee": round(max(0.0, float(venue_fee or 0.0)), 4),
        "basis": basis,
    }
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(state_dir) / FEES_FILE, "a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    return fee


def total(state_dir: Path | str) -> float:
    """Every cost charged so far. LENIENT, for display: a line it cannot read is
    skipped. Never use it for a rail; the rails use read_strict()."""
    path = Path(state_dir) / FEES_FILE
    s = 0.0
    try:
        with open(path) as f:
            for line in f:
                try:
                    s += float(json.loads(line)["fee"])
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return round(s, 2)


# ---------------------------------------------------------------- the rails' reader
#
# Owner decision 2026-10-28: the drawdown, daily-loss and live-kill rails and the
# decay monitor read results AFTER this cost. They read the fee log through the
# strict reader below and never through total(): a line total() skips is a cost the
# rail would treat as 0, which is the one direction a rail must never be wrong in.


class FeeLedgerError(ValueError):
    """operator_fees.jsonl cannot be trusted for a rail: unreadable, a torn line, or
    a line in another currency. The rail that reads it fails CLOSED."""


def read_strict(state_dir: Path | str, currency: str = CURRENCY) -> list[dict]:
    """Every fee line, checked. A missing file is [] (nothing charged yet).

    Raises FeeLedgerError on an unreadable file, a line that is not a JSON object
    with a string `ref`, a finite non-negative number `fee` and an ISO `ts`, or a
    line whose `currency` is not this bot's account currency. Blank lines (a
    trailing newline) are not lines. Never skips a line it cannot read."""
    path = Path(state_dir) / FEES_FILE
    try:
        with open(path) as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise FeeLedgerError(f"{path} unreadable: {e}") from None
    out = []
    for n, line in enumerate(raw_lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
            ref, fee, ts = rec["ref"], rec["fee"], rec["ts"]
            if not isinstance(ref, str) or not ref:
                raise ValueError("ref is not a string")
            if isinstance(fee, bool) or not isinstance(fee, (int, float)):
                raise ValueError("fee is not a number")
            if not math.isfinite(fee) or fee < 0:
                raise ValueError(f"fee {fee} out of range")
            datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError) as e:
            raise FeeLedgerError(f"{path} line {n} is torn or invalid ({e})") from None
        if rec.get("currency") != currency:
            raise FeeLedgerError(
                f"{path} line {n} is in {rec.get('currency')!r}, not this bot's "
                f"{currency}: a rail cannot net it off {currency} equity")
        out.append(rec)
    return out


def _ts(rec: dict) -> datetime:
    t = datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def rail_totals(state_dir: Path | str, day_start: datetime | None = None,
                currency: str = CURRENCY) -> tuple[float, float]:
    """(cost charged so far, cost charged BEFORE `day_start`), from ONE strict read.

    The second figure is what the daily-loss rail takes off its day-start reference:
    a fee whose `ts` (the fill time) is before the bot's day boundary was already in
    the day-start equity's economics. With no `day_start` the two are equal.
    Raises FeeLedgerError (see read_strict)."""
    lines = read_strict(state_dir, currency)
    whole = sum(float(r["fee"]) for r in lines)
    if day_start is None:
        before = whole
    else:
        before = sum(float(r["fee"]) for r in lines if _ts(r) < day_start)
    return round(whole, 4), round(before, 4)


def fees_by_ref(state_dir: Path | str, currency: str = CURRENCY) -> dict[str, float]:
    """ref -> fee charged, for joining a trade to its own cost ("buy:<trade_id>",
    "close:<trade_id>"). Strict: raises FeeLedgerError (see read_strict). A ref seen
    twice is summed, never overwritten, so a duplicate can only make a rail more
    conservative."""
    out: dict[str, float] = {}
    for r in read_strict(state_dir, currency):
        out[r["ref"]] = round(out.get(r["ref"], 0.0) + float(r["fee"]), 4)
    return out


def trade_cost(fees: dict[str, float], trade_id: str | None) -> float:
    """The operator cost one round trip paid: its entry fill plus its close."""
    if not trade_id:
        return 0.0
    return round(fees.get(f"buy:{trade_id}", 0.0)
                 + fees.get(f"close:{trade_id}", 0.0), 4)
