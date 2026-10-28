"""swing_scan — the 30-minute trigger loop, offline.

What must stay true:

  * the zone comes from `swing_sectors.assess` on completed bars — the scan
    adds ONE comparison (live quote vs zone), never an indicator;
  * fail CLOSED on a closed/unknown market — a queued market order fills at
    the next open at a price no guard ever saw;
  * `enabled: false` is a dry run: candidates are ledgered, nothing is placed;
  * at most ONE entry per pass, deepest laggard first;
  * the idempotency key is the SAME string the 15:45 cycle derives, so the
    broker itself arbitrates scan-vs-cycle double entry;
  * the rails see the same shapes the cycle passes (strategy name, the FULL
    bars map, the candidate stop);
  * this module opens longs and does nothing else — no exit path exists.
"""
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

import swing_scan                                             # noqa: E402
from test_swing_sectors import (CFG as SHIPPED, bars_from_closes,   # noqa: E402
                                decline, deep_stabilized, shallow)


def scan_cfg(enabled=True):
    import copy
    cfg = copy.deepcopy(SHIPPED)
    cfg["strategies"]["swing_sectors"]["enabled"] = enabled
    return cfg


def etf_bars(candidate="XLE"):
    """A rankable 11-fund cross-section where only `candidate` is armed."""
    bars = {candidate: deep_stabilized()}
    for i, sym in enumerate(s for s in SHIPPED["sector_etfs"]
                            if s != candidate):
        bars[sym] = shallow(100.0 + i)
    bars["SPY"] = shallow(500.0)
    return bars


def zone_of(bars, cfg):
    from strategies import swing_sectors, prepare_one
    xs = prepare_one(cfg, "swing_sectors",
                     {s: b for s, b in bars.items() if s != "SPY"})
    return swing_sectors.assess("XLE", bars["XLE"],
                                cfg["strategies"]["swing_sectors"], xs)


# ------------------------------------------------------------ find_candidates

def test_the_armed_fund_is_a_candidate():
    cands = swing_scan.find_candidates(scan_cfg(), etf_bars(), positions={})
    assert [c["symbol"] for c in cands] == ["XLE"]
    z = zone_of(etf_bars(), scan_cfg())
    assert cands[0]["zone_low"] == pytest.approx(z["zone_low"])
    assert cands[0]["zone_high"] == pytest.approx(z["zone_high"])


def test_a_held_fund_is_never_a_candidate():
    """One position per symbol, whoever owns it — the scan must not add."""
    cands = swing_scan.find_candidates(scan_cfg(), etf_bars(),
                                       positions={"XLE": {"qty": 10}})
    assert cands == []


def test_candidates_rank_deepest_first():
    bars = etf_bars()
    # A second, deeper stabilized laggard: same shape, deeper trough.
    bars["XLB"] = bars_from_closes([150.0] * 60 + decline(150.0, 80.0, 160)
                                   + decline(80.0, 82.0, 60))
    cands = swing_scan.find_candidates(scan_cfg(), bars, positions={})
    assert [c["symbol"] for c in cands] == ["XLB", "XLE"]
    assert cands[0]["drawdown"] > cands[1]["drawdown"]


def test_no_armed_fund_no_candidates():
    bars = {sym: shallow(100.0 + i)
            for i, sym in enumerate(SHIPPED["sector_etfs"])}
    assert swing_scan.find_candidates(scan_cfg(), bars, positions={}) == []


# ---------------------------------------------------------------- run_scan

class FakeBroker:
    def __init__(self, live_prices, market_is_open=True, clock_raises=False):
        self.live = live_prices
        self.is_open = market_is_open
        self.clock_raises = clock_raises
        self.orders = []

    def market_open(self):
        if self.clock_raises:
            raise RuntimeError("clock endpoint down")
        return self.is_open

    def latest_price(self, symbol):
        return self.live[symbol]

    def bracket_market_order(self, symbol, qty, stop, tp=None, *,
                             client_order_id=None):
        self.orders.append({"symbol": symbol, "qty": qty, "stop_price": stop,
                            "take_profit_price": tp, "coid": client_order_id,
                            "kind": "bracket"})
        return dict(self.orders[-1], id=f"o{len(self.orders)}")

    def market_order(self, symbol, qty, side, client_order_id=None):
        self.orders.append({"symbol": symbol, "qty": qty, "side": side,
                            "coid": client_order_id, "kind": "market"})
        return dict(self.orders[-1], id=f"o{len(self.orders)}")


class FakeLedger:
    def __init__(self):
        self.events, self.decisions = [], []

    def log_event(self, event, detail=""):
        self.events.append((event, detail))

    def log_decision(self, symbol, action, reason, indicators, review,
                     executed, **kw):
        self.decisions.append({"symbol": symbol, "action": action,
                               "executed": executed, **kw})
        return f"t{len(self.decisions)}"

    def open_buys(self):
        return {}

    def closed_trades(self):
        return []


class FakeJudgments:
    def __init__(self):
        self.rows = []

    def log_judgment(self, *a, **kw):
        self.rows.append((a, kw))
        return "jg-test"


class FakeMemory:
    judgments = None

    def __init__(self):
        self.judgments = FakeJudgments()

    def context_for_llm(self, **kw):
        return "CONTEXT"


def wire(monkeypatch, cfg, bars, broker, ledger=None, memory=None,
         positions=None, verdict="approve"):
    """Patch the cycle seams; return (ledger, memory, calls) recorders."""
    ledger = ledger or FakeLedger()
    memory = memory or FakeMemory()
    account = {"equity": 100_000.0, "buying_power": 200_000.0, "cash": 100_000.0}
    calls = {"rails": [], "recaps": [], "recorded": 0}
    monkeypatch.setattr(swing_scan.cycle, "_bootstrap_cycle",
                        lambda: (cfg, ledger, memory, broker, account,
                                 positions or {}, False))
    monkeypatch.setattr(
        swing_scan.cycle, "_fetch_and_validate_bars",
        lambda b, c, led, syms, completed_bars_only: (bars, None, None,
                                                      None, "sideways"))
    monkeypatch.setattr(
        swing_scan.llm, "review_signal",
        lambda sig, ctx, c: {"verdict": verdict, "scale": 1.0,
                             "reasoning": "ok", "degraded": False})
    monkeypatch.setattr(swing_scan.llm, "write_x_post", lambda *a, **k: None)
    monkeypatch.setattr(swing_scan.x_poster, "post_recap",
                        lambda *a, **k: calls["recaps"].append(a))
    monkeypatch.setattr(swing_scan.cycle, "journal_and_link",
                        lambda *a, **k: None)
    monkeypatch.setattr(swing_scan.risk, "record_trade",
                        lambda: calls.__setitem__("recorded",
                                                  calls["recorded"] + 1))

    def fake_rails(action, symbol, qty, price, account_, positions_, c, **kw):
        calls["rails"].append({"action": action, "symbol": symbol,
                               "qty": qty, **kw})
    monkeypatch.setattr(swing_scan.risk, "pre_trade_checks", fake_rails)
    return ledger, memory, calls


def in_zone_price(bars, cfg):
    z = zone_of(bars, cfg)
    return (z["zone_low"] + z["zone_high"]) / 2


def test_a_closed_market_skips_the_pass_before_any_fetch(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({}, market_is_open=False)
    ledger, _, calls = wire(monkeypatch, cfg, bars, broker)
    monkeypatch.setattr(
        swing_scan.cycle, "_fetch_and_validate_bars",
        lambda *a, **k: pytest.fail("fetched bars on a closed market"))
    assert swing_scan.run_scan() == 0
    assert broker.orders == [] and calls["rails"] == []


def test_an_unreachable_clock_fails_CLOSED(monkeypatch):
    """A queued market order fills at the next open at a price no guard saw —
    so an unknown market state must mean skip, not proceed."""
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({}, clock_raises=True)
    wire(monkeypatch, cfg, bars, broker)
    assert swing_scan.run_scan() == 0
    assert broker.orders == []


def test_disabled_is_a_dry_run_that_ledgers_the_candidate(monkeypatch):
    cfg, bars = scan_cfg(enabled=False), etf_bars()
    live = in_zone_price(bars, cfg)
    broker = FakeBroker({"XLE": live})
    ledger, _, calls = wire(monkeypatch, cfg, bars, broker)
    assert swing_scan.run_scan() == 0
    assert broker.orders == [], "enabled: false must never place an order"
    assert calls["rails"] == []
    kinds = [e for e, _ in ledger.events]
    assert "swing_scan_candidate" in kinds, (
        "the dry-run candidate is the evidence the owner reads alongside §62 "
        "— if it is not ledgered the shipped state records nothing")


def test_out_of_zone_is_silent(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    z = zone_of(bars, cfg)
    broker = FakeBroker({"XLE": z["zone_high"] * 1.05})
    ledger, _, _ = wire(monkeypatch, cfg, bars, broker)
    assert swing_scan.run_scan() == 0
    assert broker.orders == [] and ledger.events == [], \
        "a skip is a success — the ledger must not hear about it"


def test_an_in_zone_trigger_places_one_bracketed_entry(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    live = in_zone_price(bars, cfg)
    broker = FakeBroker({"XLE": live})
    ledger, memory, calls = wire(monkeypatch, cfg, bars, broker)
    assert swing_scan.run_scan() == 0
    assert len(broker.orders) == 1
    o = broker.orders[0]
    assert o["kind"] == "bracket" and o["symbol"] == "XLE"
    # The wide per-strategy stop reached the order: 3.5×ATR below the live
    # trigger price, not the global 2.0×.
    from strategies import atr
    expected = round(live - 3.5 * atr(bars["XLE"], 14), 2)
    assert o["stop_price"] == pytest.approx(expected)
    # The cycle's own idempotency key, byte-identical.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert o["coid"] == f"ta-XLE-buy-{today}"
    # Executed decision + judgment + recap all recorded.
    assert any(d["executed"] for d in ledger.decisions)
    assert calls["recorded"] == 1
    assert memory.judgments.rows and calls["recaps"]
    # The rails saw the cycle's shapes.
    rail = calls["rails"][0]
    assert rail["strategy"] == "swing_sectors"
    assert rail["bars_map"] is bars, "rails must see the FULL fetch, not a slice"
    assert rail["candidate_stop"] == o["stop_price"]


def test_at_most_one_entry_per_pass(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    bars["XLB"] = bars_from_closes([150.0] * 60 + decline(150.0, 80.0, 160)
                                   + decline(80.0, 82.0, 60))
    zb = zone_of(bars, cfg)                       # XLE zone
    from strategies import swing_sectors as ss, prepare_one
    xs = prepare_one(cfg, "swing_sectors",
                     {s: b for s, b in bars.items() if s != "SPY"})
    za = ss.assess("XLB", bars["XLB"], cfg["strategies"]["swing_sectors"], xs)
    assert za is not None, "fixture: both funds must actually be armed"
    broker = FakeBroker({"XLB": (za["zone_low"] + za["zone_high"]) / 2,
                         "XLE": (zb["zone_low"] + zb["zone_high"]) / 2})
    wire(monkeypatch, cfg, bars, broker)
    swing_scan.run_scan()
    assert len(broker.orders) == 1, "one entry per pass, then stop"
    assert broker.orders[0]["symbol"] == "XLB", "deepest laggard first"


def test_a_veto_places_nothing_and_is_ledgered(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({"XLE": in_zone_price(bars, cfg)})
    ledger, memory, _ = wire(monkeypatch, cfg, bars, broker, verdict="veto")
    swing_scan.run_scan()
    assert broker.orders == []
    assert any(d["detail"] == "LLM veto" for d in ledger.decisions
               if "detail" in d)
    assert any(a[3] == "veto" for a, _ in memory.judgments.rows)


def test_entries_blocked_stops_the_pass(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({"XLE": in_zone_price(bars, cfg)})
    ledger, _, calls = wire(monkeypatch, cfg, bars, broker)
    monkeypatch.setattr(
        swing_scan.cycle, "_fetch_and_validate_bars",
        lambda *a, **k: (bars, "vendors disagree on XLK", "datacheck",
                         None, "sideways"))
    swing_scan.run_scan()
    assert broker.orders == [] and calls["rails"] == []
    assert ("swing_scan_entries_blocked",
            "datacheck: vendors disagree on XLK") in ledger.events


def test_a_halted_bot_scans_nothing(monkeypatch):
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({"XLE": in_zone_price(bars, cfg)})
    ledger, memory, _ = wire(monkeypatch, cfg, bars, broker)
    monkeypatch.setattr(swing_scan.cycle, "_bootstrap_cycle",
                        lambda: (cfg, ledger, memory, broker,
                                 {"equity": 1.0}, {}, True))   # halted=True
    assert swing_scan.run_scan() == 0
    assert broker.orders == []


def test_an_unreadable_fee_ledger_fails_the_scan_closed(monkeypatch):
    """2026-10-28 (owner decision: the rails read after-cost results). The
    bootstrap could not read the operator fee log, so rail equity is unknown:
    no rail can clear an entry, and this module only enters. Same fixture as
    the in-zone entry above, which DOES place an order — so the stop here is
    the ledger, not the fixture."""
    cfg, bars = scan_cfg(), etf_bars()
    broker = FakeBroker({"XLE": in_zone_price(bars, cfg)})
    ledger, memory, calls = wire(monkeypatch, cfg, bars, broker)
    account = {"equity": 100_000.0, "buying_power": 200_000.0,
               "cash": 100_000.0, "rail_equity": None,
               "rail_problem": "operator_fees.jsonl line 3 is torn"}
    monkeypatch.setattr(swing_scan.cycle, "_bootstrap_cycle",
                        lambda: (cfg, ledger, memory, broker, account, {},
                                 False))
    assert swing_scan.run_scan() == 0
    assert broker.orders == [] and calls["rails"] == []
    assert ("swing_scan_entries_blocked",
            "rail_equity_unknown: operator_fees.jsonl line 3 is torn"
            ) in ledger.events


# --------------------------------------------------------- module discipline

def test_the_scan_has_no_exit_path():
    """This module opens longs; exits belong to the daily cycle's ownership
    rule, and broker-side brackets protect in between. A second exit path
    would be a second implementation of that rule — pin its absence at the
    source level, the same standing as opportunity_scan's no-order-verbs
    test."""
    src = open("src/swing_scan.py").read()
    for verb in ('"sell"', "'sell'", '"cover"', "'cover'", '"short"',
                 "'short'", "cancel_open_orders", "flatten"):
        assert verb not in src, f"exit/short vocabulary in swing_scan: {verb}"


def test_the_scan_never_touches_the_forming_bar():
    """completed_bars_only=True is the whole §19a settlement — the fetch must
    drop the forming bar before any strategy code sees it."""
    src = open("src/swing_scan.py").read()
    assert "completed_bars_only=True" in src
    assert "drop_forming_bar" not in src, \
        "the scan must go through the cycle's fetch, not re-implement it"
