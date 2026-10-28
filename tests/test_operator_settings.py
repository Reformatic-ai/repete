"""Operator settings from the Reformatic console: a simulated cost model per market
and a trade frequency (src/operator_settings.py, src/cost_models.py and their call
sites in main.py/health.py).

The promises pinned here:
  * the cost is charged ONCE per filled order, under the console exporter's
    transaction key ("buy:<trade_id>" / "close:<trade_id>"), and never for an
    entry that did not fill; it changes no order, P&L or rail.
    Amended 2026-10-28 (owner decision): "Yes: make gates, backtests and the
    drawdown/retire rails use after-cost results first (repete4's override
    included). Entry cost filters later, bot by bot." It still changes no
    order, sizing or broker equity, and the OUTCOME RECORD's P&L stays the
    strategy's gross result (pinned below, deliberately). The RAILS now read
    it: rail equity = broker equity - the cost charged, and the outcome-based
    rules take each trade's own cost off its P&L by joining on `ref`. Those
    are pinned in tests/test_after_cost_rails.py.
  * each charge site passes the order's real filled shares and fill price, so a
    schema-2 per_share model charges the reference amounts; a schema-1 file
    still charges exactly its flat fee.
  * frequency "medium" is the bot exactly as configured: the config comes back
    as the SAME object, untouched.
"""
import copy
import json
import os
from datetime import datetime, timezone

import pytest
import yaml

import cost_models
import health
import main
import operator_settings as ops
import statepaths
from conftest import make_bars
from ledger import Ledger
from memory import Memory
from test_main_cycle import BUY_CLOSES, FakeCycleBroker

REPO = os.path.join(os.path.dirname(__file__), "..")


def _write_settings(d, **over):
    raw = {"version": 3, "fee_per_trade": 9.0, "fee_on_crypto": False,
           "fee_on_fx": False, "frequency": "medium",
           "updated_at": "2026-10-24T14:00:00Z"}
    raw.update(over)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ops.SETTINGS_FILE), "w") as f:
        json.dump(raw, f)
    return raw


def _fees(d):
    path = os.path.join(d, ops.FEES_FILE)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def opdir():
    """The directory conftest already isolated for this test."""
    return os.environ["REPETE_OPERATOR_DIR"]


@pytest.fixture
def shipped_cfg():
    with open(os.path.join(REPO, "config.yaml")) as f:
        return yaml.safe_load(f)


def _with_settings(cfg, opdir, **over):
    _write_settings(opdir, **over)
    return ops.to_cfg(cfg, ops.load(opdir))


# The schema-2 file the console's sync writes (cost-models spec, 2026-10-27).
V2_COSTS = {
    "equities": {"model": "per_share", "flat_per_order": 1.0, "per_share": 0.005,
                 "min_per_order": 1.0, "max_pct_of_value": 1.0},
    "options": {"model": "per_contract", "flat_per_order": 1.4,
                "per_contract": 0.65, "fees_per_contract": 0.025,
                "min_per_order": 1.0},
    "fx": {"model": "per_10k_units", "flat_per_order": 1.0, "per_10k_units": 1.0,
           "spread_pips": 0.2},
    "crypto": {"model": "percent", "flat_per_order": 1.0, "pct_of_value": 0.8},
}


def _write_v2(d, costs=None, **over):
    return _write_settings(d, **{
        "schema": 2, "version": 4, "frequency": "medium",
        "updated_at": "2026-10-28T14:00:00Z",
        "costs": copy.deepcopy(V2_COSTS if costs is None else costs),
        "fee_per_trade": 1.0, "fee_on_crypto": False, "fee_on_fx": False,
        **over})


def _with_v2(cfg, opdir, **over):
    _write_v2(opdir, **over)
    return ops.to_cfg(cfg, ops.load(opdir))


def _load_v2(opdir):
    _write_v2(opdir)
    return ops.load(opdir)


def _v1(fee, crypto=False, fx=False, version=3):
    """Settings as a schema-1 file loads them: flat models from the flat fields."""
    return ops.OperatorSettings(
        version=version, schema=1, fee_per_trade=fee, fee_on_crypto=crypto,
        fee_on_fx=fx, costs=cost_models.from_v1(fee, crypto, fx))


# shares, fill price, fee: the research's per_share reference numbers.
EQUITY_REFERENCE = [(100, 50.0, 1.00), (1000, 50.0, 5.00), (5, 8.0, 0.40)]


# ---------------------------------------------------------------- load()

def test_missing_file_is_no_cost_medium_with_a_reason(tmp_path):
    s = ops.load(tmp_path)
    assert (s.version, s.fee_per_trade, s.frequency) == (0, 0.0, "medium")
    assert s.problem == "no operator settings file yet"


def test_unreadable_file_is_defaults_with_a_reason(tmp_path):
    (tmp_path / ops.SETTINGS_FILE).write_text("{not json")
    s = ops.load(tmp_path)
    assert s.fee_per_trade == 0.0 and s.frequency == "medium"
    assert s.problem.startswith("operator settings unreadable")


@pytest.mark.parametrize("over", [
    {"frequency": "turbo"},
    {"fee_per_trade": -1},
    {"fee_per_trade": ops.FEE_MAX + 1},
    {"fee_per_trade": float("nan")},
    {"fee_per_trade": "nine"},
    {"version": None},
])
def test_invalid_values_are_ignored_whole(tmp_path, over):
    _write_settings(tmp_path, **over)
    s = ops.load(tmp_path)
    assert s.fee_per_trade == 0.0 and s.frequency == "medium" and s.version == 0
    assert s.problem.startswith("operator settings invalid")


def test_missing_key_is_ignored_whole(tmp_path):
    raw = _write_settings(tmp_path)
    del raw["frequency"]
    (tmp_path / ops.SETTINGS_FILE).write_text(json.dumps(raw))
    assert ops.load(tmp_path).problem.startswith("operator settings invalid")


def test_valid_file_loads(tmp_path):
    _write_settings(tmp_path, fee_per_trade=4.999, fee_on_crypto=True,
                    fee_on_fx="yes", frequency="low")
    s = ops.load(tmp_path)
    assert s.version == 3 and s.fee_per_trade == 5.0 and s.frequency == "low"
    assert s.fee_on_crypto is True
    assert s.fee_on_fx is False          # only a JSON true turns it on
    assert s.updated_at == "2026-10-24T14:00:00Z" and s.problem == ""


def test_fee_for_markets():
    s = ops.OperatorSettings(version=1, fee_per_trade=9.0)
    assert s.fee_for("equities") == 9.0 and s.fee_for("options") == 9.0
    assert s.fee_for("crypto") == 0.0 and s.fee_for("fx") == 0.0
    on = ops.OperatorSettings(version=1, fee_per_trade=9.0, fee_on_crypto=True,
                              fee_on_fx=True)
    assert on.fee_for("crypto") == 9.0 and on.fee_for("fx") == 9.0
    with pytest.raises(ValueError):
        s.fee_for("bonds")


def test_summary_names_the_problem():
    assert "problem: x" in ops.OperatorSettings(problem="x").summary()
    assert "problem" not in ops.OperatorSettings(version=2).summary()


def test_cfg_round_trip_and_absent():
    s = ops.OperatorSettings(version=4, fee_per_trade=9.0, frequency="high")
    cfg = ops.to_cfg({}, s)
    json.dumps(cfg)                       # cfg stays JSON-serialisable
    assert ops.from_cfg(cfg) == s
    assert ops.from_cfg({}) is None and ops.from_cfg(None) is None
    assert ops.from_cfg({ops.CFG_KEY: {"bogus": 1}}) is None
    assert ops.from_cfg("not a dict") is None


# ---------------------------------------------------------------- charge()

def test_charge_writes_the_contract_line_once(tmp_path):
    s = _v1(9.0)
    ts = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
    assert ops.charge(tmp_path, s, ref="buy:t1", market="equities", symbol="SPY",
                      side="buy", ts=ts) == 9.0
    assert ops.charge(tmp_path, s, ref="buy:t1", market="equities", symbol="SPY",
                      side="buy") == 0.0     # idempotent on ref
    lines = _fees(tmp_path)
    assert lines == [{"ts": "2026-10-24T15:00:00+00:00", "ref": "buy:t1",
                      "symbol": "SPY", "side": "buy", "market": "equities",
                      "fee": 9.0, "currency": "USD", "settings_version": 3,
                      "settings_schema": 1, "model": "flat", "gross": 9.0,
                      "venue_fee": 0.0, "basis": {"model": "flat"}}]
    assert ops.total(tmp_path) == 9.0


def test_no_line_when_market_is_off_or_fee_is_zero(tmp_path):
    s = _v1(9.0)
    assert ops.charge(tmp_path, s, ref="c1", market="crypto", symbol="BTC",
                      side="buy") == 0.0
    assert ops.charge(tmp_path, ops.OperatorSettings(), ref="e1",
                      market="equities", symbol="SPY", side="buy") == 0.0
    assert _fees(tmp_path) == [] and ops.total(tmp_path) == 0.0


def test_charge_creates_the_dir_and_reads_skip_junk(tmp_path):
    d = tmp_path / "new"
    s = _v1(2.5, version=1)
    ops.charge(d, s, ref="a", market="equities", symbol="X", side="buy")
    with open(d / ops.FEES_FILE, "a") as f:
        f.write("garbage\n{}\n" + json.dumps({"ref": "b", "fee": "x"}) + "\n")
    ops.charge(d, s, ref="c", market="equities", symbol="X", side="sell")
    assert ops.charged_refs(d) == {"a", "b", "c"}
    assert ops.total(d) == 5.0
    assert ops.total(tmp_path / "nowhere") == 0.0
    assert ops.charged_refs(tmp_path / "nowhere") == set()


# ---------------------------------------------------------- apply_frequency()

def test_medium_returns_the_config_itself_untouched(shipped_cfg):
    before = copy.deepcopy(shipped_cfg)
    out = ops.apply_frequency(shipped_cfg, ops.OperatorSettings(frequency="medium"))
    assert out is shipped_cfg and out == before


def test_no_settings_file_is_medium(shipped_cfg, tmp_path):
    out = ops.apply_frequency(shipped_cfg, ops.load(tmp_path))
    assert out is shipped_cfg


def _diff(a, b, path=""):
    """Every leaf path where two configs differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = set()
        for k in set(a) | set(b):
            out |= _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return set() if a == b else {path}


def test_low_changes_exactly_three_keys(shipped_cfg):
    before = copy.deepcopy(shipped_cfg)
    out = ops.apply_frequency(shipped_cfg, ops.OperatorSettings(frequency="low"))
    assert shipped_cfg == before          # caller's dict never mutated
    assert _diff(before, out) == {"risk.max_trades_per_day",
                                  "strategies.meanrev.rsi_buy_below",
                                  "risk.reentry_cooldown.days"}
    assert out["risk"]["max_trades_per_day"] == min(
        before["risk"]["max_trades_per_day"], 2)
    assert out["strategies"]["meanrev"]["rsi_buy_below"] == 5
    assert out["risk"]["reentry_cooldown"]["days"] == 10


def test_high_changes_exactly_two_keys(shipped_cfg):
    before = copy.deepcopy(shipped_cfg)
    out = ops.apply_frequency(shipped_cfg, ops.OperatorSettings(frequency="high"))
    assert shipped_cfg == before
    assert _diff(before, out) == {"strategies.meanrev.rsi_buy_below",
                                  "risk.reentry_cooldown.days"}
    assert out["strategies"]["meanrev"]["rsi_buy_below"] == 15
    assert out["risk"]["reentry_cooldown"]["days"] == 2
    assert out["risk"]["max_trades_per_day"] == before["risk"]["max_trades_per_day"]


@pytest.mark.parametrize("cur,want", [(0, 2), (None, 2), (1, 1), (15, 2)])
def test_low_trade_cap_is_a_ceiling(cur, want):
    cfg = {"risk": {"max_trades_per_day": cur}}
    out = ops.apply_frequency(cfg, ops.OperatorSettings(frequency="low"))
    assert out["risk"]["max_trades_per_day"] == want


def test_missing_blocks_are_not_invented():
    cfg = {"risk": {"max_trades_per_day": 5}, "strategies": {"tsmom": {}}}
    for level in ("low", "high"):
        out = ops.apply_frequency(cfg, ops.OperatorSettings(frequency=level))
        assert "reentry_cooldown" not in out["risk"]
        assert "meanrev" not in out["strategies"]
    assert ops.apply_frequency({}, ops.OperatorSettings(frequency="low")) == {}


def test_shipped_config_still_passes_preflight_at_every_level(shipped_cfg):
    import preflight
    base = preflight.run(shipped_cfg)
    for level in ops.FREQUENCIES:
        out = ops.apply_frequency(shipped_cfg, ops.OperatorSettings(frequency=level))
        assert preflight.run(out) == base


# ---------------------------------------------------------------- statepaths

def test_operator_dir_resolution(monkeypatch):
    monkeypatch.delenv("REPETE_OPERATOR_DIR")
    assert statepaths.operator_dir(None) == "memory"
    assert statepaths.operator_dir({"memory": None}) == "memory"
    assert statepaths.operator_dir({"memory": {"operator_dir": "x"}}) == "x"
    monkeypatch.setenv("REPETE_OPERATOR_DIR", "/env")
    assert statepaths.operator_dir({"memory": {"operator_dir": "x"}}) == "/env"


# ------------------------------------------------- charge site: fill quality

class FillBroker:
    def __init__(self, orders):
        self._orders = orders

    def get_order(self, order_id):
        return self._orders[order_id]


def _buy(led, order_id="o-1", symbol="SPY", action="buy"):
    return led.log_decision(symbol, action, "test", {}, None, executed=True,
                            order={"id": order_id}, entry_price=100.0, qty=10)


def test_filled_buy_is_charged_once_under_its_exporter_key(tmp_path, opdir):
    cfg = _with_settings({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = _buy(led)
    broker = FillBroker({"o-1": {"filled_avg_price": 100.1,
                                 "filled_at": "2026-10-24T13:30:01Z"}})
    main.record_fill_quality(broker, led, cfg=cfg)
    main.record_fill_quality(broker, led, cfg=cfg)
    fees = _fees(opdir)
    assert [(f["ref"], f["side"], f["market"], f["fee"]) for f in fees] == [
        (f"buy:{tid}", "buy", "equities", 9.0)]
    assert fees[0]["ts"] == "2026-10-24T13:30:01+00:00"
    assert fees[0]["settings_version"] == 3


def test_naive_fill_time_is_read_as_utc(tmp_path, opdir):
    cfg = _with_settings({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led)
    main.record_fill_quality(
        FillBroker({"o-1": {"filled_avg_price": 100.0,
                            "filled_at": "2026-10-24T13:30:01"}}), led, cfg=cfg)
    assert _fees(opdir)[0]["ts"] == "2026-10-24T13:30:01+00:00"


def test_unfilled_buy_is_not_charged(tmp_path, opdir):
    cfg = _with_settings({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led)
    main.record_fill_quality(FillBroker({"o-1": {"filled_avg_price": None}}),
                             led, cfg=cfg)
    assert _fees(opdir) == []


def test_sell_fill_is_not_charged_here(tmp_path, opdir):
    """The close pays in handle_close under close:<buy trade id>; the sell
    decision's own fill must not pay a second time."""
    cfg = _with_settings({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led, order_id="o-2", action="sell")
    main.record_fill_quality(FillBroker({"o-2": {"filled_avg_price": 99.0}}),
                             led, cfg=cfg)
    assert _fees(opdir) == []


def test_written_off_entry_that_fills_late_is_not_charged(tmp_path, opdir):
    """The console shows no row for an entry_unfilled trade; if it fills late
    its position is adopted under a NEW id and pays there."""
    cfg = _with_settings({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = _buy(led)
    led.close_trade(tid, 100.0, 0.0, 0.0, exit_reason="entry_unfilled")
    main.record_fill_quality(FillBroker({"o-1": {"filled_avg_price": 100.0}}),
                             led, cfg=cfg)
    assert any(r["type"] == "fill_quality" for r in led.all_records())
    assert _fees(opdir) == []


def test_no_settings_in_cfg_charges_nothing(tmp_path, opdir):
    _write_settings(opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led)
    main.record_fill_quality(FillBroker({"o-1": {"filled_avg_price": 100.0}}), led)
    assert _fees(opdir) == []


def test_zero_fee_charges_nothing(tmp_path, opdir):
    cfg = _with_settings({}, opdir, fee_per_trade=0)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led)
    main.record_fill_quality(FillBroker({"o-1": {"filled_avg_price": 100.0}}),
                             led, cfg=cfg)
    assert _fees(opdir) == []


def test_a_failing_charge_never_breaks_the_caller(tmp_path, opdir, monkeypatch):
    cfg = _with_settings({}, opdir)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ops, "charge", boom)
    assert main._charge_operator_fee(cfg, "buy:x", "SPY", "buy") == 0.0
    assert main._charge_operator_fee(cfg, "buy:y", "SPY", "buy",
                                     ts="not a time") == 0.0


# ------------------------------------------- charge site: closes and adoption

@pytest.fixture
def env(tmp_path, cfg, opdir):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    ledger = Ledger(cfg["memory"]["ledger_path"])
    return ledger, Memory(cfg, ledger), _with_settings(cfg, opdir)


def test_close_is_charged_once_under_close_key(env, opdir):
    ledger, memory, cfg = env
    tid = _buy(ledger)
    rec = ledger.open_buys()[tid]
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)   # replay: no 2nd fee
    assert [(f["ref"], f["side"], f["symbol"]) for f in _fees(opdir)] == [
        (f"close:{tid}", "sell", "SPY")]
    # The outcome RECORD's P&L is the strategy's gross result, untouched by the
    # simulated cost — still true after 2026-10-28, deliberately: the console
    # nets the cost itself, so writing it in here would count it twice. What
    # changed is how the outcome-based rules READ it: this trade's own cost,
    # joined on its ref, comes off (risk.after_cost_trades).
    out = [r for r in ledger.all_records() if r["type"] == "outcome"][0]
    assert out["pnl"] == pytest.approx(100.0)
    import risk
    t = risk.after_cost_trades(ledger.closed_trades(), ops.fees_by_ref(opdir))[0]
    assert t["pnl"] == pytest.approx(100.0 - 9.0)
    assert t["pnl_gross"] == pytest.approx(100.0)


def test_short_close_is_a_cover(env, opdir):
    ledger, memory, cfg = env
    tid = _buy(ledger, action="short")
    main.handle_close(tid, ledger.open_buys()[tid], 90.0, ledger, memory, cfg)
    assert [(f["ref"], f["side"]) for f in _fees(opdir)] == [(f"close:{tid}", "cover")]


def test_reconciled_stop_out_is_charged_and_unfilled_entry_is_not(env, opdir):
    ledger, memory, cfg = env
    stopped = _buy(ledger, order_id="e-1", symbol="SPY")
    unfilled = _buy(ledger, order_id="e-2", symbol="QQQ")

    class _StopOutBroker:
        def get_order(self, oid):
            return {"e-1": {"status": "OrderStatus.FILLED", "filled_qty": 10,
                            "filled_avg_price": 100.0},
                    "e-2": {"status": "OrderStatus.CANCELED", "filled_qty": 0,
                            "filled_avg_price": None}}[oid]

        def closed_orders(self, symbol, limit=20):
            return [{"side": "OrderSide.SELL", "type": "OrderType.STOP",
                     "filled_avg_price": 95.0}]

        def bars(self, *a, **k):
            return []

    main.reconcile_closed_positions(_StopOutBroker(), ledger, memory, cfg, positions={})
    assert [f["ref"] for f in _fees(opdir)] == [f"close:{stopped}"]
    assert f"close:{unfilled}" not in ops.charged_refs(opdir)


def test_adopted_long_pays_its_entry_and_short_does_not(env, opdir):
    ledger, _, cfg = env
    main.adopt_untracked_positions(
        broker=None, ledger=ledger, cfg=cfg,
        positions={"SPY": {"qty": 10, "avg_entry": 450.0, "market_value": 4500.0},
                   "QQQ": {"qty": -5, "avg_entry": 400.0, "market_value": -2000.0}})
    opens = {r["symbol"]: tid for tid, r in ledger.open_buys().items()}
    assert [(f["ref"], f["symbol"]) for f in _fees(opdir)] == [
        (f"buy:{opens['SPY']}", "SPY")]


# ----------------------------------------- end to end: refs == exporter keys

def _exporter_keys(ledger_path):
    """The console exporter's repete_transactions keys, restated: executed
    buy decisions not written off as entry_unfilled, and every outcome but
    entry_unfilled."""
    recs = Ledger(ledger_path).all_records()
    decisions = {r["trade_id"]: r for r in recs
                 if r["type"] == "decision" and r.get("executed")}
    outcomes = [r for r in recs if r["type"] == "outcome"]
    unfilled = {o["trade_id"] for o in outcomes
                if o.get("exit_reason") == "entry_unfilled"}
    keys = {f"buy:{t}" for t, d in decisions.items()
            if d["action"] == "buy" and t not in unfilled}
    keys |= {f"close:{o['trade_id']}" for o in outcomes
             if o.get("exit_reason") != "entry_unfilled"
             and o["trade_id"] in decisions}
    return keys


@pytest.fixture
def cycle_env(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    def _install(broker):
        monkeypatch.setattr(main, "Broker", lambda c: broker)
        return broker
    return cfg, _install


FILLED_ENTRY = {"id": "entry-1", "status": "OrderStatus.FILLED",
                "filled_avg_price": 20.0, "filled_qty": 50, "legs": [],
                "filled_at": "2026-10-23T19:45:02+00:00"}


def test_a_buy_then_stop_out_charges_exactly_the_exporter_rows(cycle_env, opdir):
    cfg, install = cycle_env
    _write_settings(opdir)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    assert _fees(opdir) == []           # placed, not yet known to have filled

    install(FakeCycleBroker(make_bars(BUY_CLOSES + [20], start_day=2),
                            positions={}, orders={
        "entry-1": FILLED_ENTRY,
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 12.67,
                     "filled_qty": 50, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                   "type": "OrderType.LIMIT", "filled_avg_price": None,
                   "filled_qty": 0, "legs": []}}))
    main.run_cycle()

    refs = [f["ref"] for f in _fees(opdir)]
    assert len(refs) == len(set(refs)) == 2
    assert set(refs) == _exporter_keys(cfg["memory"]["ledger_path"])
    assert ops.total(opdir) == 18.0


def test_an_entry_that_never_fills_charges_nothing(cycle_env, opdir):
    cfg, install = cycle_env
    _write_settings(opdir)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    install(FakeCycleBroker(make_bars(BUY_CLOSES + [20], start_day=2),
                            positions={}, orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.CANCELED",
                    "filled_avg_price": None, "filled_qty": 0, "legs": []}}))
    main.run_cycle()
    assert _fees(opdir) == []
    assert _exporter_keys(cfg["memory"]["ledger_path"]) == set()


def test_cycle_applies_frequency_and_logs_it_once(cycle_env, opdir):
    cfg, install = cycle_env
    _write_settings(opdir, frequency="low", version=7)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    started = main._bootstrap_cycle()
    run_cfg = started[0]
    assert run_cfg["risk"]["max_trades_per_day"] == min(
        cfg["risk"]["max_trades_per_day"], 2)
    assert ops.from_cfg(run_cfg).version == 7

    main.run_cycle()
    events = [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
              if r.get("event") == "operator_settings"]
    assert len(events) == 1
    assert "v7" in events[0]["detail"] and "frequency=low" in events[0]["detail"]


def test_cycle_without_a_settings_file_is_unchanged_and_says_why(cycle_env):
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    run_cfg = main._bootstrap_cycle()[0]
    assert {k: v for k, v in run_cfg.items() if k != ops.CFG_KEY} == cfg
    main.run_cycle()
    ev = [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
          if r.get("event") == "operator_settings"]
    assert "no operator settings file yet" in ev[0]["detail"]


def test_settings_event_never_raises(tmp_path):
    class Bad:
        def log_event(self, *a):
            raise OSError("ledger gone")
    main._log_operator_settings(Bad(), {})          # swallowed
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._log_operator_settings(led, {})
    assert "not loaded" in led.all_records()[0]["detail"]


# ---------------------------------------------------------------- health

def test_health_reports_settings_without_degrading(cfg, opdir, capsys,
                                                   monkeypatch):
    s = health.status(cfg)
    assert s["operator_settings"]["problem"] == "no operator settings file yet"
    assert not any("operator" in p for p in s["problems"])

    _write_settings(opdir, frequency="high")
    ops.charge(opdir, ops.load(opdir), ref="buy:t", market="equities",
               symbol="SPY", side="buy")
    s = health.status(cfg)
    assert s["operator_settings"] == {
        "version": 3, "schema": 1, "market": "equities",
        "cost": "US$9.00 an order, flat", "fee_per_trade": 9.0,
        "fee_on_crypto": False, "fee_on_fx": False, "frequency": "high",
        "updated_at": "2026-10-24T14:00:00Z", "fees_charged_total": 9.0,
        "problem": None,
        # 2026-10-28: the rails' STRICT read of the same log, None = clean.
        "fee_ledger_problem": None}

    monkeypatch.setattr(health, "status", lambda: {
        **s, "operator_settings": {**s["operator_settings"],
                                   "problem": "operator settings invalid: x"}})
    monkeypatch.setattr("sys.argv", ["health.py"])
    health.main()
    out = capsys.readouterr().out
    assert ("operator=v3 schema=1 cost=(US$9.00 an order, flat) high "
            "(charged $9.00)") in out
    assert "(operator settings: operator settings invalid: x)" in out


def test_health_survives_a_broken_settings_reader(cfg, monkeypatch):
    def boom(_):
        raise RuntimeError("bad")
    monkeypatch.setattr(ops, "load", boom)
    s = health.status(cfg)
    assert s["operator_settings"]["problem"].startswith(
        "operator settings check failed")
    assert not any("operator" in p for p in s["problems"])


# ================================================ cost models (settings schema 2)

def test_cost_models_is_the_console_module_plus_one_header_line():
    """cost_models.py is copied VERBATIM from the console's
    reformatic/trading/costs.py: one provenance line on top, nothing edited."""
    src = os.path.join(REPO, "src", "cost_models.py")
    with open(src) as f:
        first, second = f.readline(), f.readline()
    assert first.startswith("# Copied from reformatic-console "
                            "reformatic/trading/costs.py")
    assert second.startswith('"""What a trade costs, market by market')


@pytest.mark.parametrize("market,inputs,venue_fee,want", [
    ("equities", {"shares": 100, "notional": 5000.0}, 0, 1.00),
    ("equities", {"shares": 1000, "notional": 50000.0}, 0, 5.00),
    ("equities", {"shares": 5, "notional": 40.0}, 0, 0.40),    # 1% cap
    ("options", {"contracts": 2}, 0, 1.35),      # 1-lot two-leg spread
    ("options", {"contracts": 1}, 0, 1.025),     # one contract, one leg
    ("fx", {"units": 10_000, "pip": 0.0001, "quote_rate": 1.39}, 0, 1.278),
    ("crypto", {"notional": 1000.0}, 2.60, 5.40),
])
def test_reference_numbers(market, inputs, venue_fee, want):
    """The research's reference numbers, through the copied module."""
    gross, _ = cost_models.order_cost(cost_models.validate(V2_COSTS), market,
                                      **inputs)
    assert cost_models.charge_amount(gross, venue_fee) == pytest.approx(want)


def test_schema2_file_loads(tmp_path):
    _write_v2(tmp_path, frequency="low")
    s = ops.load(tmp_path)
    assert (s.version, s.schema, s.frequency, s.problem) == (4, 2, "low", "")
    assert s.updated_at == "2026-10-28T14:00:00Z"
    assert s.costs == cost_models.validate(V2_COSTS)
    # The schema-1 view is the per_share model's per-order minimum.
    assert (s.fee_per_trade, s.fee_on_crypto, s.fee_on_fx) == (1.0, False, False)
    assert s.describe() == ("US$0.005 a share, at least US$1.00 an order, "
                            "at most 1% of its value")
    assert s.summary() == ("v4 schema=2 equities cost=(US$0.005 a share, at least "
                           "US$1.00 an order, at most 1% of its value) "
                           "frequency=low")


def test_schema2_file_does_not_need_valid_flat_fields(tmp_path):
    """Valid `costs` are read as written; the schema-1 fields are only a
    fallback, so a bad one there does not throw the file away."""
    _write_v2(tmp_path, fee_per_trade="nine")
    s = ops.load(tmp_path)
    assert (s.schema, s.problem) == (2, "")
    assert s.costs["equities"]["model"] == "per_share"


def test_schema1_file_loads_and_charges_the_same_flat_amount(tmp_path):
    _write_settings(tmp_path)                    # today's file: no schema, $9
    s = ops.load(tmp_path)
    assert (s.version, s.schema, s.problem) == (3, 1, "")
    assert s.costs["equities"] == dict(cost_models.RECOMMENDED["equities"],
                                       model="flat", flat_per_order=9.0)
    assert s.costs["crypto"]["model"] == s.costs["fx"]["model"] == "off"
    assert s.describe() == "US$9.00 an order, flat"
    assert "v3 schema=1" in s.summary() and "US$9.00 an order, flat" in s.summary()
    d = tmp_path / "fees"
    # The flat fee whatever the order's size, and with no figures at all.
    for ref, inputs in [("a", {"shares": 100, "notional": 5000.0}),
                        ("b", {"shares": 5, "notional": 40.0}),
                        ("c", {})]:
        assert ops.charge(d, s, ref=ref, market="equities", symbol="SPY",
                          side="buy", **inputs) == 9.0
    assert ops.total(d) == 27.0


@pytest.mark.parametrize("bad", [
    "not an object",
    {k: v for k, v in V2_COSTS.items() if k != "options"},        # market missing
    {**V2_COSTS, "equities": {**V2_COSTS["equities"], "model": "per_contract"}},
    {**V2_COSTS, "equities": {**V2_COSTS["equities"], "per_share": -0.005}},
    {**V2_COSTS, "equities": {**V2_COSTS["equities"], "per_share": 2.0}},  # > 1
    {**V2_COSTS, "crypto": {"model": "percent", "flat_per_order": 1.0}},   # key missing
])
def test_invalid_costs_block_falls_back_to_schema1_with_problem(tmp_path, bad):
    _write_v2(tmp_path, costs=bad, fee_per_trade=9.0, frequency="high")
    s = ops.load(tmp_path)
    assert s.schema == 1
    assert s.problem.startswith("costs invalid (")
    assert s.problem.endswith("using the flat schema-1 cost")
    assert s.costs == cost_models.from_v1(9.0, False, False)
    # Version and frequency still come from the file.
    assert (s.version, s.frequency) == (4, "high")
    assert ops.charge(tmp_path, s, ref="x", market="equities", symbol="SPY",
                      side="buy", shares=100, notional=5000.0) == 9.0
    assert "problem: costs invalid" in s.summary()


def test_invalid_costs_and_bad_flat_fields_ignore_the_file(tmp_path):
    _write_v2(tmp_path, costs={"equities": {}}, fee_per_trade="nine")
    s = ops.load(tmp_path)
    assert s.problem.startswith("operator settings invalid")
    assert (s.version, s.schema, s.frequency) == (0, 0, "medium")
    assert {c["model"] for c in s.costs.values()} == {"off"}


def test_no_file_default_is_every_market_off(tmp_path):
    for s in (ops.OperatorSettings(), ops.load(tmp_path)):
        assert {m: c["model"] for m, c in s.costs.items()} == {
            m: "off" for m in cost_models.MARKETS}
        cost_models.validate(s.costs)                # a well-formed block
        assert s.schema == 0 and s.describe() == "no cost"
        assert ops.charge(tmp_path, s, ref="r", market="equities", symbol="SPY",
                          side="buy", shares=100, notional=5000.0) == 0.0
    assert _fees(tmp_path) == []


def test_costs_survive_the_cfg_round_trip(tmp_path):
    _write_v2(tmp_path)
    s = ops.load(tmp_path)
    cfg = json.loads(json.dumps(ops.to_cfg({}, s)))   # cfg stays JSON-clean
    back = ops.from_cfg(cfg)
    assert back == s and back.costs == s.costs and back.schema == 2


@pytest.mark.parametrize("shares,price,want", EQUITY_REFERENCE)
def test_schema2_charge_writes_the_spec_line(tmp_path, shares, price, want):
    _write_v2(tmp_path)
    s = ops.load(tmp_path)
    ts = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    d = tmp_path / "fees"
    assert ops.charge(d, s, ref="buy:t1", market="equities", symbol="SPY",
                      side="buy", ts=ts, shares=shares,
                      notional=shares * price) == pytest.approx(want)
    basis = {"model": "per_share", "shares": float(shares),
             "notional": shares * price}
    if want < 1.0:
        basis["capped"] = True
    assert _fees(d) == [{
        "ts": "2026-10-28T15:00:00+00:00", "ref": "buy:t1", "symbol": "SPY",
        "side": "buy", "market": "equities", "fee": want, "currency": "USD",
        "settings_version": 4, "settings_schema": 2, "model": "per_share",
        "gross": want, "venue_fee": 0.0, "basis": basis}]


def test_schema2_duplicate_ref_charges_once(tmp_path):
    _write_v2(tmp_path)
    s = ops.load(tmp_path)
    d = tmp_path / "fees"
    for _ in range(3):
        ops.charge(d, s, ref="close:t1", market="equities", symbol="SPY",
                   side="sell", shares=1000, notional=50_000.0)
    assert [f["ref"] for f in _fees(d)] == ["close:t1"]
    assert ops.total(d) == 5.0


def test_a_missing_input_is_logged_not_invented(tmp_path, opdir, caplog):
    """per_share needs the share count; the never-raise wrapper logs the gap
    and writes nothing. A flat schema-1 model needs no figures."""
    cfg = _with_v2({}, opdir)
    assert main._charge_operator_fee(cfg, "buy:x", "SPY", "buy") == 0.0
    assert "per_share needs shares" in caplog.text
    assert _fees(opdir) == []
    cfg = _with_settings({}, opdir)              # schema 1, flat $9
    assert main._charge_operator_fee(cfg, "buy:x", "SPY", "buy") == 9.0


# ----------------------------------- schema 2 at each of the three charge sites

@pytest.mark.parametrize("shares,price,want", EQUITY_REFERENCE)
def test_entry_fill_site_charges_the_broker_fill(tmp_path, opdir, shares,
                                                 price, want):
    """The decision said 10 @ $100; the broker filled `shares` @ `price`. The
    charge uses the broker's figures, not the decision's."""
    cfg = _with_v2({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = _buy(led)
    main.record_fill_quality(
        FillBroker({"o-1": {"filled_avg_price": price, "filled_qty": shares,
                            "filled_at": "2026-10-28T13:30:01Z"}}), led, cfg=cfg)
    [f] = _fees(opdir)
    assert (f["ref"], f["fee"], f["model"]) == (f"buy:{tid}", want, "per_share")
    assert (f["basis"]["shares"], f["basis"]["notional"]) == (shares,
                                                              shares * price)


def test_entry_fill_without_filled_qty_falls_back_to_the_decision_qty(
        tmp_path, opdir):
    cfg = _with_v2({}, opdir)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _buy(led)                                    # qty 10
    main.record_fill_quality(FillBroker({"o-1": {"filled_avg_price": 8.0}}),
                             led, cfg=cfg)
    [f] = _fees(opdir)
    assert f["basis"] == {"model": "per_share", "shares": 10.0,
                          "notional": 80.0, "capped": True}
    assert f["fee"] == 0.8


@pytest.mark.parametrize("shares,price,want", EQUITY_REFERENCE)
def test_adopted_position_site_charges_the_broker_position(env, opdir, shares,
                                                           price, want):
    ledger, _, cfg = env
    cfg = ops.to_cfg(cfg, _load_v2(opdir))
    main.adopt_untracked_positions(
        broker=None, ledger=ledger, cfg=cfg,
        positions={"SPY": {"qty": shares, "avg_entry": price,
                           "market_value": shares * price * 1.1}})
    [f] = _fees(opdir)
    assert f["ref"].startswith("buy:") and f["fee"] == pytest.approx(want)
    assert (f["basis"]["shares"], f["basis"]["notional"]) == (shares,
                                                              shares * price)


@pytest.mark.parametrize("action,side", [("buy", "sell"), ("short", "cover")])
@pytest.mark.parametrize("shares,price,want", EQUITY_REFERENCE)
def test_close_site_charges_the_exit_qty_and_exit_price(env, opdir, shares,
                                                        price, want, action,
                                                        side):
    ledger, memory, cfg = env
    cfg = ops.to_cfg(cfg, _load_v2(opdir))
    tid = ledger.log_decision("SPY", action, "test", {}, None, executed=True,
                              order={"id": "o-1"}, entry_price=60.0, qty=shares)
    rec = ledger.open_buys()[tid]
    main.handle_close(tid, rec, price, ledger, memory, cfg)
    main.handle_close(tid, rec, price, ledger, memory, cfg)   # replay: once
    [f] = _fees(opdir)
    assert (f["ref"], f["side"], f["fee"]) == (f"close:{tid}", side,
                                               pytest.approx(want))
    assert (f["basis"]["shares"], f["basis"]["notional"]) == (shares,
                                                              shares * price)
    # The outcome RECORD's P&L is the strategy's gross result, untouched by
    # the cost (still, after 2026-10-28 — see the close test above); the rules
    # read it after cost through the ref join.
    out = [r for r in ledger.all_records() if r["type"] == "outcome"][0]
    sign = -1 if action == "short" else 1
    assert out["pnl"] == pytest.approx(sign * (price - 60.0) * shares)
    import risk
    t = risk.after_cost_trades(ledger.closed_trades(), ops.fees_by_ref(opdir))[0]
    assert t["pnl"] == pytest.approx(sign * (price - 60.0) * shares - want)


def test_schema2_buy_then_stop_out_charges_the_exporter_rows(cycle_env, opdir):
    """End to end under per_share: a 50-share entry filled @ $20 ($1,000) and
    stopped out @ $12.67 ($633.50) each pay the US$1.00 minimum."""
    cfg, install = cycle_env
    _write_v2(opdir)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    assert _fees(opdir) == []
    install(FakeCycleBroker(make_bars(BUY_CLOSES + [20], start_day=2),
                            positions={}, orders={
        "entry-1": FILLED_ENTRY,
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 12.67,
                     "filled_qty": 50, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                   "type": "OrderType.LIMIT", "filled_avg_price": None,
                   "filled_qty": 0, "legs": []}}))
    main.run_cycle()
    fees = {f["ref"].split(":")[0]: f for f in _fees(opdir)}
    assert {f["ref"] for f in fees.values()} == _exporter_keys(
        cfg["memory"]["ledger_path"])
    assert fees["buy"]["basis"] == {"model": "per_share", "shares": 50.0,
                                    "notional": 1000.0}
    assert fees["close"]["basis"] == {"model": "per_share", "shares": 50.0,
                                      "notional": 633.5}
    assert ops.total(opdir) == 2.0


def test_schema2_cycle_logs_the_market_line(cycle_env, opdir):
    cfg, install = cycle_env
    _write_v2(opdir)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    [ev] = [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
            if r.get("event") == "operator_settings"]
    assert ev["detail"].startswith("v4 schema=2 equities cost=(US$0.005 a share")


def test_health_reports_the_schema2_model(cfg, opdir, capsys, monkeypatch):
    _write_v2(opdir)
    s = health.status(cfg)
    op = s["operator_settings"]
    assert (op["version"], op["schema"], op["market"]) == (4, 2, "equities")
    assert op["cost"] == ("US$0.005 a share, at least US$1.00 an order, "
                          "at most 1% of its value")
    assert op["fee_per_trade"] == 1.0 and op["problem"] is None
    monkeypatch.setattr(health, "status", lambda: s)
    monkeypatch.setattr("sys.argv", ["health.py"])
    health.main()
    assert ("operator=v4 schema=2 cost=(US$0.005 a share, at least US$1.00 an "
            "order, at most 1% of its value) medium (charged $0.00)"
            ) in capsys.readouterr().out
