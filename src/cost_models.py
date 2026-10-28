# Copied from reformatic-console reformatic/trading/costs.py; keep the two in step.
"""What a trade costs, market by market (trading settings schema 2).

Peter's first setting (migration 0032) was one flat cost per trade, $9 on every buy
and every sell. The research of 2026-10-27 found that wrong both ways: about nine
times too high for stocks and option spreads, and too low for crypto above about
$1,125 an order. So each market now has its own model, priced the way the broker
the research picked for that market charges:

  equities  per_share      IBKR Fixed: US$0.005 a share, at least US$1.00 an order,
                           at most 1% of the order's value.
  options   per_contract   IBKR: US$0.65 a contract plus US$0.025 OCC fees, at least
                           US$1.00 an order (the minimum is on the commission only),
                           so a 1-lot two-leg spread order is US$1.35. A spread that
                           expires worthless sends no order and pays nothing.
  fx        per_10k_units  OANDA Canada core: CAD 1 per 10,000 units a side, plus
                           about 0.2 pip of spread.
  crypto    percent        Kraken base tier, taker: 0.80% of the order's value a side.

Every market can instead be `flat` (one amount per order, the old way) or `off`.
Each market keeps all its values whatever model it is on, so switching back and
forth loses nothing.

A bot charges the model's cost LESS what its own venue already charged on that fill
(`charge = max(0, cost - venue_fee)`): Repete1's simulator already takes Kraken's
old 0.26% taker fee from its cash, so the operator cost tops it up to the model
rather than paying twice. The other four bots trade on commission-free paper, so
their venue fee is 0.

This module is the one place the models are written down. It uses the standard
library only, so `scripts/trading_settings_sync.py` loads it by path, and each bot
keeps a copy of it (named in that bot's operator-settings module).
"""

from __future__ import annotations

import math

SCHEMA = 2
MARKETS = ("equities", "options", "fx", "crypto")

# The model a market uses when it is priced like its broker.
NATURAL = {
    "equities": "per_share",
    "options": "per_contract",
    "fx": "per_10k_units",
    "crypto": "percent",
}
MODELS = {m: ("off", "flat", NATURAL[m]) for m in MARKETS}

# Every value a market carries, whatever its model, with its upper bound (all are
# >= 0). `max_pct_of_value` 0 means no cap.
LIMITS = {
    "equities": {
        "flat_per_order": 1000.0,
        "per_share": 1.0,
        "min_per_order": 1000.0,
        "max_pct_of_value": 100.0,
    },
    "options": {
        "flat_per_order": 1000.0,
        "per_contract": 100.0,
        "fees_per_contract": 100.0,
        "min_per_order": 1000.0,
    },
    "fx": {"flat_per_order": 1000.0, "per_10k_units": 1000.0, "spread_pips": 100.0},
    "crypto": {"flat_per_order": 1000.0, "pct_of_value": 10.0},
}

# The research's values (2026-10-27). The flat amounts are what a flat order would
# cost at that broker for a typical order of these bots.
RECOMMENDED = {
    "equities": {
        "model": "per_share",
        "flat_per_order": 1.0,
        "per_share": 0.005,
        "min_per_order": 1.0,
        "max_pct_of_value": 1.0,
    },
    "options": {
        "model": "per_contract",
        "flat_per_order": 1.4,
        "per_contract": 0.65,
        "fees_per_contract": 0.025,
        "min_per_order": 1.0,
    },
    "fx": {
        "model": "per_10k_units",
        "flat_per_order": 1.0,
        "per_10k_units": 1.0,
        "spread_pips": 0.2,
    },
    "crypto": {"model": "percent", "flat_per_order": 1.0, "pct_of_value": 0.8},
}

# How the amounts read on the page. Stocks and options are priced in US dollars (the
# accounts that trade them are USD); FX in Canadian dollars (Repete2's account is
# CAD). A bot charges in its own account currency.
UNIT = {"equities": "US$", "options": "US$", "fx": "CAD ", "crypto": "$"}
LABELS = {"equities": "Stocks", "options": "Options", "fx": "FX", "crypto": "Crypto"}
BOTS = {
    "equities": "Repete, Repete3",
    "options": "Repete4",
    "fx": "Repete2",
    "crypto": "Repete1",
}


def _number(market: str, key: str, value) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{market}.{key} must be a number")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{market}.{key} must be a number") from None
    if not math.isfinite(x) or not 0 <= x <= LIMITS[market][key]:
        raise ValueError(f"{market}.{key} {x} is outside 0..{LIMITS[market][key]:g}")
    return round(x, 6)


def validate(raw) -> dict:
    """The cost models, checked and normalised, or ValueError naming the bad value.
    Every market must be there with a known model and every one of its values;
    anything else in `raw` is dropped."""
    if not isinstance(raw, dict):
        raise ValueError("costs must be an object")
    out = {}
    for market in MARKETS:
        m = raw.get(market)
        if not isinstance(m, dict):
            raise ValueError(f"costs.{market} is missing")
        model = m.get("model")
        if model not in MODELS[market]:
            raise ValueError(f"{market}.model {model!r} is not one of {', '.join(MODELS[market])}")
        out[market] = {"model": model}
        for key in LIMITS[market]:
            if key not in m:
                raise ValueError(f"{market}.{key} is missing")
            out[market][key] = _number(market, key, m[key])
    return out


def from_v1(fee_per_trade: float, fee_on_crypto: bool, fee_on_fx: bool) -> dict:
    """Schema-1 settings as cost models that charge exactly what they charged: the
    flat fee on stocks and options, and on crypto and FX only when switched on. The
    per-market values are the research's, ready for when a market is switched to
    its broker's model."""
    fee = round(float(fee_per_trade), 6)
    out = {m: dict(RECOMMENDED[m], flat_per_order=fee) for m in MARKETS}
    for m in ("equities", "options"):
        out[m]["model"] = "flat"
    out["crypto"]["model"] = "flat" if fee_on_crypto else "off"
    out["fx"]["model"] = "flat" if fee_on_fx else "off"
    return out


def v1_equivalent(costs: dict) -> dict:
    """The schema-1 fields for a bot that cannot read cost models yet. Schema 1 has
    one amount for every market, so it takes the stock order's: the flat amount, or
    the per-order minimum under per_share, or 0 when stocks are off. Crypto and FX
    pay it only when they are on `flat`: a flat amount cannot stand in for a
    percentage or a per-unit price, so under those models a schema-1 bot charges
    nothing there, as it did before."""
    eq = costs["equities"]
    fee = {"off": 0.0, "flat": eq["flat_per_order"], "per_share": eq["min_per_order"]}[eq["model"]]
    return {
        "fee_per_trade": round(fee, 2),
        "fee_on_crypto": costs["crypto"]["model"] == "flat",
        "fee_on_fx": costs["fx"]["model"] == "flat",
    }


def from_settings(raw: dict) -> tuple[dict, str]:
    """(cost models, problem) from a settings file as the sync writes it. Schema 2
    carries `costs`; a schema-1 file (or a schema-2 file whose `costs` fail to
    validate) is read from its flat fields. `problem` is "" when the costs were read
    as written. Raises ValueError (or KeyError/TypeError) only when the flat fields
    are bad too; the caller then charges nothing, as with no file at all."""
    problem = ""
    if raw.get("schema") == SCHEMA:
        try:
            return validate(raw.get("costs")), ""
        except ValueError as e:
            problem = f"costs invalid ({e}); using the flat schema-1 cost"
    fee = float(raw["fee_per_trade"])
    if not math.isfinite(fee) or not 0 <= fee <= 1000:
        raise ValueError(f"fee_per_trade {fee} out of range")
    return from_v1(fee, raw.get("fee_on_crypto") is True, raw.get("fee_on_fx") is True), problem


def _need(model: str, **given) -> None:
    missing = [k for k, v in given.items() if v is None]
    if missing:
        raise ValueError(f"{model} needs {', '.join(missing)}")


def order_cost(
    costs: dict,
    market: str,
    *,
    shares: float | None = None,
    contracts: float | None = None,
    units: float | None = None,
    notional: float | None = None,
    pip: float | None = None,
    quote_rate: float | None = None,
) -> tuple[float, dict]:
    """(cost, basis) of ONE order in `market`, in the account's currency, before any
    venue fee is taken off. `basis` says how it was worked out, for the fee log.

      shares     equities per_share: shares in the order (sign ignored)
      notional   the order's value (per_share's 1% cap; percent's base)
      contracts  options per_contract: contracts across ALL legs of the order
                 (a 1-lot two-leg spread is 2)
      units      fx per_10k_units: units of the base currency
      pip        fx: the pair's pip size (0.0001; 0.01 for JPY pairs)
      quote_rate fx: account currency per one unit of the QUOTE currency (1 for
                 USD_CAD in a CAD account). Without it, or without `pip`, the spread
                 part is left out and `basis` says so.

    Raises ValueError when the model needs an input that was not given."""
    c = costs[market]
    model = c["model"]
    basis: dict = {"model": model}
    if model == "off":
        return 0.0, basis
    if model == "flat":
        return round(c["flat_per_order"], 4), basis
    if model == "per_share":
        _need(model, shares=shares)
        n = abs(float(shares))
        fee = max(n * c["per_share"], c["min_per_order"])
        basis["shares"] = n
        if c["max_pct_of_value"] > 0 and notional is not None:
            cap = c["max_pct_of_value"] / 100 * abs(float(notional))
            basis["notional"] = round(abs(float(notional)), 4)
            if cap < fee:
                fee, basis["capped"] = cap, True
        return round(fee, 4), basis
    if model == "per_contract":
        _need(model, contracts=contracts)
        n = abs(float(contracts))
        fee = max(n * c["per_contract"], c["min_per_order"]) + n * c["fees_per_contract"]
        basis["contracts"] = n
        return round(fee, 4), basis
    if model == "per_10k_units":
        _need(model, units=units)
        u = abs(float(units))
        fee = u / 10_000 * c["per_10k_units"]
        basis["units"] = u
        if c["spread_pips"] > 0:
            if pip is None or quote_rate is None:
                basis["spread_skipped"] = "no pip size or quote rate"
            else:
                spread = c["spread_pips"] * float(pip) * u * float(quote_rate)
                fee += spread
                basis["spread"] = round(spread, 4)
        return round(fee, 4), basis
    if model == "percent":
        _need(model, notional=notional)
        v = abs(float(notional))
        basis["notional"] = round(v, 4)
        return round(c["pct_of_value"] / 100 * v, 4), basis
    raise ValueError(f"unknown model {model!r}")


def charge_amount(gross: float, venue_fee: float | None) -> float:
    """What the operator cost adds on top of what the venue already charged."""
    return round(max(0.0, gross - max(0.0, float(venue_fee or 0.0))), 4)


def _money(unit: str, x: float) -> str:
    s = f"{x:,.3f}" if round(x, 2) != round(x, 3) else f"{x:,.2f}"
    return f"{unit}{s}"


def describe(costs: dict, market: str) -> str:
    """One line for the page and the bots' logs."""
    c, u = costs[market], UNIT[market]
    model = c["model"]
    if model == "off":
        return "no cost"
    if model == "flat":
        return f"{_money(u, c['flat_per_order'])} an order, flat"
    if model == "per_share":
        s = f"{_money(u, c['per_share'])} a share, at least {_money(u, c['min_per_order'])}"
        s += " an order"
        if c["max_pct_of_value"] > 0:
            s += f", at most {c['max_pct_of_value']:g}% of its value"
        return s
    if model == "per_contract":
        s = f"{_money(u, c['per_contract'])} a contract"
        if c["fees_per_contract"] > 0:
            s += f" + {_money(u, c['fees_per_contract'])} fees"
        return s + f", at least {_money(u, c['min_per_order'])} an order"
    if model == "per_10k_units":
        s = f"{_money(u, c['per_10k_units'])} per 10,000 units a side"
        if c["spread_pips"] > 0:
            s += f" + {c['spread_pips']:g} pip"
        return s
    return f"{c['pct_of_value']:g}% of the order's value a side"
