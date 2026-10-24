"""Operator trading settings: a flat cost per trade and a trade frequency.

Set by Peter (or the Reformatic operator) on the console's /trading page. The console
host writes them to a JSON file in this bot's state directory (`memory/`, see
`statepaths.operator_dir`) every few minutes
(console/scripts/trading_settings_sync.py); this module is the only reader. The bot
reads the file at the start of every cycle (`main._bootstrap_cycle`), so a change
takes effect at the next trade and never rewrites past ones.

    {"version": 3, "fee_per_trade": 9.0, "fee_on_crypto": false, "fee_on_fx": false,
     "frequency": "medium", "updated_at": "2026-10-24T14:00:00Z"}

THE COST IS SIMULATED. Paper brokers charge nothing, so the bot does not change any
order, its cash, or the broker equity its risk rails use (invariant #4: equity is
always read fresh from the broker). It appends one line per charged order to
`operator_fees.jsonl` next to the settings file; the console's exporter subtracts the
running total from the equity it shows and puts each fee on the matching transaction
(`ref` is that transaction's key in the exporter: "buy:<trade_id>" for a filled entry,
"close:<trade_id>" for a close). It is not a fill cost: it never goes into the
backtester, the slippage numbers or the outcome P&L.

THE FREQUENCY changes a few entry knobs for the cycle (`apply_frequency`). "medium" is
the bot exactly as configured in config.yaml — the config object comes back untouched.

A missing or unreadable file means "no operator settings yet": no cost, medium
frequency. That is the behaviour before this module existed.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

SETTINGS_FILE = "operator_settings.json"
FEES_FILE = "operator_fees.jsonl"
FREQUENCIES = ("low", "medium", "high")
# Stocks and options always pay the cost; crypto and FX only when the operator says so.
MARKETS = ("equities", "options", "crypto", "fx")
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


@dataclass(frozen=True)
class OperatorSettings:
    version: int = 0  # 0 = no file: nothing set yet
    fee_per_trade: float = 0.0
    fee_on_crypto: bool = False
    fee_on_fx: bool = False
    frequency: str = "medium"
    updated_at: str = ""
    problem: str = ""  # why the file was ignored, for health output; "" when fine

    def fee_for(self, market: str) -> float:
        """The cost one order pays in this market under these settings."""
        if market not in MARKETS:
            raise ValueError(f"unknown market {market!r}")
        if market == "crypto" and not self.fee_on_crypto:
            return 0.0
        if market == "fx" and not self.fee_on_fx:
            return 0.0
        return self.fee_per_trade

    def summary(self) -> str:
        """One line for logs and the ledger."""
        s = (f"v{self.version} fee=${self.fee_per_trade:.2f}/trade "
             f"(crypto={'on' if self.fee_on_crypto else 'off'}, "
             f"fx={'on' if self.fee_on_fx else 'off'}) "
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
        fee = float(raw["fee_per_trade"])
        freq = str(raw["frequency"])
        version = int(raw["version"])
        if not math.isfinite(fee) or not 0 <= fee <= FEE_MAX:
            raise ValueError(f"fee_per_trade {fee} out of range")
        if freq not in FREQUENCIES:
            raise ValueError(f"frequency {freq!r}")
        return OperatorSettings(
            version=version,
            fee_per_trade=round(fee, 2),
            fee_on_crypto=raw.get("fee_on_crypto") is True,
            fee_on_fx=raw.get("fee_on_fx") is True,
            frequency=freq,
            updated_at=str(raw.get("updated_at") or ""),
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
) -> float:
    """Charge one order. Appends a line to operator_fees.jsonl and returns the fee, or
    returns 0.0 and writes nothing when the market pays no cost or `ref` was already
    charged. `ref` must be the exporter's transaction key for this order."""
    fee = settings.fee_for(market)
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
    }
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(state_dir) / FEES_FILE, "a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    return fee


def total(state_dir: Path | str) -> float:
    """Every cost charged so far."""
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
