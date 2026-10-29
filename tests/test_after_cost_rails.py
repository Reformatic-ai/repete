"""The live rails read AFTER-COST results (owner decision 2026-10-28).

Connor, in his own words:

    "Yes: make gates, backtests and the drawdown/retire rails use after-cost
    results first (repete4's override included). Entry cost filters later, bot
    by bot."

Until this, the operator's simulated cost (operator_settings.py) only lowered
the equity the console SHOWED. Every rail measured gross. What is pinned here:

  * RAIL EQUITY = broker equity - the cost charged so far, read STRICTLY from
    memory/operator_fees.jsonl. A missing file is 0. An unreadable file, a torn
    line or a line in another currency is NEVER 0: the rails fail CLOSED.
  * each changed rail at its boundary on the after-cost basis — one side
    passes, the other trips, and gross alone would have passed both;
  * the equity peak moves ONCE from its gross value to peak - cost, stamped,
    and a rerun is a no-op;
  * the cycle charges the fills it confirms BEFORE the rails read equity, and
    refreshes after reconcile, so the rails never see a cost one run late;
  * the outcome-based rules (live kill, decay monitor) take each trade's OWN
    cost off its P&L, joined on the fee line's ref — thresholds untouched.
"""
import json
import os
from datetime import datetime, timezone

import pytest
import yaml

import decaycheck
import health
import main
import operator_settings as ops
import randombaseline as rb
import risk
import watchdog
from conftest import make_bars
from ledger import Ledger
from test_main_cycle import BUY_CLOSES, FakeCycleBroker


@pytest.fixture
def opdir():
    return os.environ["REPETE_OPERATOR_DIR"]


def _line(ref, fee, ts="2026-10-28T15:00:00+00:00", currency="USD", **over):
    rec = {"ts": ts, "ref": ref, "symbol": "SPY", "side": "buy",
           "market": "equities", "fee": fee, "currency": currency,
           "settings_version": 4, "settings_schema": 2, "model": "per_share",
           "gross": fee, "venue_fee": 0.0, "basis": {"model": "per_share"}}
    rec.update(over)
    return json.dumps(rec, sort_keys=True)


def _ledger(d, *lines):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ops.FEES_FILE), "w") as f:
        for ln in lines:
            f.write(ln + "\n")


TORN = '{"ts": "2026-10-28T15:00:00+00:00", "ref": "buy:t9", "fee": 1.0'


# ------------------------------------------------------- the strict reader

def test_a_missing_ledger_is_zero_not_an_error(tmp_path):
    assert ops.read_strict(tmp_path) == []
    assert ops.rail_totals(tmp_path) == (0.0, 0.0)
    assert ops.fees_by_ref(tmp_path) == {}


def test_a_clean_ledger_sums_and_maps_by_ref(tmp_path):
    _ledger(tmp_path, _line("buy:t1", 1.0), _line("close:t1", 1.25),
            _line("buy:t2", 5.0))
    assert ops.rail_totals(tmp_path) == (7.25, 7.25)
    assert ops.fees_by_ref(tmp_path) == {"buy:t1": 1.0, "close:t1": 1.25,
                                         "buy:t2": 5.0}
    assert ops.trade_cost(ops.fees_by_ref(tmp_path), "t1") == 2.25


def test_a_duplicate_ref_is_summed_never_overwritten(tmp_path):
    """The writer is idempotent on ref; if a duplicate ever lands anyway, the
    rail must read MORE cost, not less."""
    _ledger(tmp_path, _line("buy:t1", 1.0), _line("buy:t1", 1.0))
    assert ops.fees_by_ref(tmp_path) == {"buy:t1": 2.0}


@pytest.mark.parametrize("bad", [
    TORN,                                                    # torn tail
    "not json at all",
    json.dumps(["a", "list"]),                               # not an object
    json.dumps({"ts": "2026-10-28T15:00:00+00:00", "fee": 1.0,
                "currency": "USD"}),                         # no ref
    _line("buy:t1", "1.0"),                                  # fee a string
    _line("buy:t1", -1.0),                                   # negative fee
    _line("buy:t1", True),                                   # bool is not a fee
    _line("buy:t1", 1.0, ts="yesterday"),                    # bad ts
])
def test_a_torn_or_invalid_line_raises_rather_than_reading_as_zero(tmp_path,
                                                                     bad):
    _ledger(tmp_path, _line("buy:t1", 1.0), bad)
    with pytest.raises(ops.FeeLedgerError):
        ops.read_strict(tmp_path)
    with pytest.raises(ops.FeeLedgerError):
        ops.rail_totals(tmp_path)
    with pytest.raises(ops.FeeLedgerError):
        ops.fees_by_ref(tmp_path)


def test_a_line_in_another_currency_raises(tmp_path):
    _ledger(tmp_path, _line("buy:t1", 1.0), _line("buy:t2", 1.0,
                                                  currency="CAD"))
    with pytest.raises(ops.FeeLedgerError, match="CAD"):
        ops.rail_totals(tmp_path)


def test_an_unreadable_ledger_raises(tmp_path):
    os.makedirs(tmp_path / ops.FEES_FILE)        # a directory: open() fails
    with pytest.raises(ops.FeeLedgerError, match="unreadable"):
        ops.read_strict(tmp_path)


def test_the_lenient_display_reader_is_unchanged(tmp_path):
    """Its pair: total() still skips what it cannot read — right for a
    number on a page, which is exactly why no rail may use it."""
    _ledger(tmp_path, _line("buy:t1", 1.0), TORN)
    assert ops.total(tmp_path) == 1.0


def test_cost_before_the_day_boundary_is_split_out(tmp_path):
    _ledger(tmp_path,
            _line("buy:t1", 1.0, ts="2026-10-27T19:45:00+00:00"),   # yesterday
            _line("close:t1", 2.0, ts="2026-10-28T14:00:00+00:00"))  # today
    day = main._day_start(datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc))
    assert ops.rail_totals(tmp_path, day) == (3.0, 1.0)


def test_the_day_boundary_is_new_york_midnight():
    d = main._day_start(datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc))
    # 03:00 UTC is still 23:00 ET on the 27th.
    assert (d.year, d.month, d.day, d.hour) == (2026, 10, 27, 0)


# ----------------------------------------------------------- rail equity

def _account(equity=100_000.0, last=100_000.0):
    return {"equity": equity, "last_equity": last, "cash": equity,
            "buying_power": equity}


def test_rail_equity_is_broker_equity_less_the_cost_charged(opdir):
    _ledger(opdir, _line("buy:t1", 1.0, ts="2026-10-27T19:45:00+00:00"),
            _line("close:t1", 2.0, ts="2026-10-28T14:00:00+00:00"))
    acct = _account(99_000.0, 100_000.0)
    assert main._rail_equity({}, acct, datetime(2026, 10, 28, 15, 0,
                                                tzinfo=timezone.utc)) == ""
    assert acct["equity"] == 99_000.0                  # broker figure untouched
    assert acct[risk.RAIL_EQUITY_KEY] == 98_997.0
    assert acct[risk.RAIL_LAST_EQUITY_KEY] == 99_999.0
    assert acct[risk.COST_CHARGED_KEY] == 3.0
    assert risk.rail_equity(acct) == 98_997.0


def test_a_torn_ledger_leaves_rail_equity_unknown_never_zero_cost(opdir):
    _ledger(opdir, _line("buy:t1", 1.0), TORN)
    acct = _account()
    problem = main._rail_equity({}, acct)
    assert "torn" in problem
    assert acct[risk.RAIL_EQUITY_KEY] is None
    assert risk.rail_equity(acct) is None


def test_an_account_without_the_key_is_broker_equity():
    """The simulator, tests and pre-decision callers: no cost, no change."""
    assert risk.rail_equity(_account(123.0)) == 123.0


# ------------------------------------------------- drawdown, at the boundary

def _dd_cfg(dd=10.0):
    return {"risk": {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
                     "max_order_value_usd": 0, "max_open_positions": 0,
                     "max_trades_per_day": 15, "max_drawdown_pct": dd}}


def _railed(equity, cost):
    a = _account(equity)
    a[risk.RAIL_EQUITY_KEY] = equity - cost
    return a


def test_drawdown_boundary_on_the_after_cost_basis():
    """Peak 100,000, limit 10%. Broker equity 90,050 is 9.95% down GROSS —
    clear either way. After $40 of cost (rail 90,010: 9.99%) it still
    passes; after $60 (rail 89,990: 10.01%) it trips. Gross alone would have
    let both through."""
    risk.pure_checks("buy", "AAA", 1, 100.0, _railed(90_050, 40), {},
                     _dd_cfg(), peak_equity=100_000)
    with pytest.raises(risk.RiskRejection, match="drawdown circuit breaker") as e:
        risk.pure_checks("buy", "AAA", 1, 100.0, _railed(90_050, 60), {},
                         _dd_cfg(), peak_equity=100_000)
    assert e.value.rail == "drawdown"
    risk.pure_checks("buy", "AAA", 1, 100.0, _account(90_050), {}, _dd_cfg(),
                     peak_equity=100_000)               # gross: passes


def test_unknown_rail_equity_fails_the_drawdown_rail_closed():
    acct = _account(150_000)
    acct[risk.RAIL_EQUITY_KEY] = None
    with pytest.raises(risk.RiskRejection, match="fails CLOSED") as e:
        risk.pure_checks("buy", "AAA", 1, 100.0, acct, {}, _dd_cfg(),
                         peak_equity=100_000)
    assert e.value.rail == "drawdown"
    # Exits are never refused by the drawdown rail, known or not.
    risk.pure_checks("sell", "AAA", 1, 100.0, acct,
                     {"AAA": {"qty": 1, "market_value": 100.0}}, _dd_cfg(),
                     peak_equity=100_000)


def test_the_census_agrees_with_the_rail_on_the_after_cost_basis():
    assert risk.rail_census("buy", "AAA", 1, 100.0, _railed(90_050, 60), {},
                            _dd_cfg(), peak_equity=100_000)["drawdown"] is True
    assert risk.rail_census("buy", "AAA", 1, 100.0, _railed(90_050, 40), {},
                            _dd_cfg(), peak_equity=100_000)["drawdown"] is False


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_pre_trade_checks_ratchets_and_measures_on_rail_equity(in_tmp):
    risk.update_high_water(100_000)
    acct = _railed(90_050, 60)
    acct[risk.COST_CHARGED_KEY] = 60.0
    with pytest.raises(risk.RiskRejection, match="drawdown"):
        risk.pre_trade_checks("buy", "AAA", 1, 100.0, acct, {}, _dd_cfg())
    assert risk.read_high_water() == 100_000


def test_pre_trade_checks_never_writes_the_peak_when_rail_equity_is_unknown(
        in_tmp):
    risk.update_high_water(100_000)
    acct = _account(250_000)                 # a gross new high...
    acct[risk.RAIL_EQUITY_KEY] = None        # ...on a ledger nobody can read
    with pytest.raises(risk.RiskRejection, match="fails CLOSED"):
        risk.pre_trade_checks("buy", "AAA", 1, 100.0, acct, {}, _dd_cfg())
    assert risk.read_high_water() == 100_000


# ------------------------------------------------- the one-time peak move

HWM = os.path.join("memory", ".equity_highwater.json")


def _legacy_peak(value):
    os.makedirs("memory", exist_ok=True)
    with open(HWM, "w") as f:
        json.dump({"peak_equity": value,
                   "updated": "2026-10-27T20:00:00+00:00"}, f)


def _peak_file():
    with open(HWM) as f:
        return json.load(f)


def test_a_legacy_gross_peak_moves_once_by_the_cost_charged(in_tmp):
    _legacy_peak(100_000.0)
    peak = risk.update_high_water(95_000.0, cost_charged=37.5,
                                  gross_equity=95_037.5)
    assert peak == 100_000.0 - 37.5
    rec = _peak_file()
    assert rec["basis"] == "after_cost"
    assert rec["moved_from_gross"] == 100_000.0
    assert rec["moved_by_cost"] == 37.5
    assert rec["peak_equity_gross"] == 100_000.0


def test_the_move_is_idempotent_a_rerun_does_not_move_it_again(in_tmp):
    _legacy_peak(100_000.0)
    risk.update_high_water(95_000.0, cost_charged=37.5)
    first = _peak_file()
    # More cost has been charged since; a stamped peak must NOT move again.
    again = risk.update_high_water(95_000.0, cost_charged=80.0)
    assert again == 100_000.0 - 37.5
    rec = _peak_file()
    assert (rec["basis"], rec["moved_by_cost"], rec["moved_at"]) == (
        first["basis"], first["moved_by_cost"], first["moved_at"])


def test_after_the_move_it_ratchets_on_rail_equity(in_tmp):
    _legacy_peak(100_000.0)
    risk.update_high_water(95_000.0, cost_charged=50.0)
    assert risk.update_high_water(101_000.0) == 101_000.0
    assert risk.update_high_water(99_000.0) == 101_000.0       # never down
    assert _peak_file()["basis"] == "after_cost"


def test_a_caller_that_does_not_know_the_cost_cannot_skip_the_move(in_tmp):
    """Without `cost_charged` a gross peak is ratcheted but NOT stamped, so the
    first caller that does know the cost still moves it."""
    _legacy_peak(100_000.0)
    risk.update_high_water(90_000.0)
    assert "basis" not in _peak_file()
    assert risk.update_high_water(90_000.0, cost_charged=10.0) == 99_990.0


def test_a_fresh_peak_is_seeded_on_the_new_basis(in_tmp):
    risk.update_high_water(100_000.0, cost_charged=25.0)
    rec = _peak_file()
    assert (rec["peak_equity"], rec["basis"]) == (100_000.0, "after_cost")
    assert "moved_by_cost" not in rec          # nothing gross to move


# ------------------------------------------------- daily loss, at the boundary

def _dl_cfg(limit=2.0):
    return {"risk": {"daily_loss_limit_pct": limit}}


def _dl_acct(equity, last, cost_today, cost_before=0.0):
    a = _account(equity, last)
    a[risk.RAIL_EQUITY_KEY] = equity - cost_before - cost_today
    a[risk.RAIL_LAST_EQUITY_KEY] = last - cost_before
    return a


def test_daily_loss_boundary_on_the_after_cost_basis():
    """Limit 2% from a 100,000 day start. Broker equity 98,050 is -1.95% —
    clear gross. $40 of cost today (98,010: -1.99%) passes; $60 (97,990:
    -2.01%) trips."""
    assert not risk.daily_loss_breached(_dl_acct(98_050, 100_000, 40), _dl_cfg())
    assert risk.daily_loss_breached(_dl_acct(98_050, 100_000, 60), _dl_cfg())
    assert not risk.daily_loss_breached(_account(98_050, 100_000), _dl_cfg())


def test_cost_charged_before_today_moves_the_reference_not_todays_loss():
    """$5,000 charged before today comes off BOTH sides: today's loss is the
    same $1,960 it would be gross, measured on a 95,000 base (-2.06%)."""
    a = _dl_acct(98_040, 100_000, 0, cost_before=5_000)
    assert a[risk.RAIL_EQUITY_KEY] == 93_040
    assert a[risk.RAIL_LAST_EQUITY_KEY] == 95_000
    assert risk.daily_loss_breached(a, _dl_cfg())                 # -2.06%
    assert not risk.daily_loss_breached(_account(98_040, 100_000),
                                        _dl_cfg())                # -1.96%


def test_unknown_rail_equity_falls_back_to_gross_never_to_no_breach():
    """A gross breach is always an after-cost breach, so falling back to gross
    can under-fire but never over-fire; the entry block covers the rest."""
    a = _account(97_000, 100_000)
    a[risk.RAIL_EQUITY_KEY] = None
    a[risk.RAIL_LAST_EQUITY_KEY] = None
    assert risk.daily_loss_breached(a, _dl_cfg())
    b = _account(99_000, 100_000)
    b[risk.RAIL_EQUITY_KEY] = None
    assert not risk.daily_loss_breached(b, _dl_cfg())


# ------------------------------------------------- live kill, after cost

KILL = {"risk": {"live_kill": {"enabled": True, "min_trades": 15,
                               "pf_below": 0.8}}}


def _book(n_win=5, win=100.0, n_loss=10, loss=-60.0):
    """15 trades. Gross PF = 500 / 600 = 0.833: clear of 0.8."""
    out = []
    for i in range(n_win + n_loss):
        pnl = win if i < n_win else loss
        out.append({"trade_id": f"t{i}", "strategy": "meanrev", "pnl": pnl,
                    "pnl_pct": pnl / 10.0, "entry_price": 100.0, "qty": 10})
    return out


def _fees(per_trade):
    out = {}
    for i in range(15):
        out[f"buy:t{i}"] = per_trade / 2
        out[f"close:t{i}"] = per_trade / 2
    return out


def test_live_kill_boundary_on_each_trades_own_cost():
    """Gross PF 500 / 600 = 0.833 passes. At $1/trade: 495 / 610 = 0.811,
    still clear. At $2/trade: 490 / 620 = 0.790 -> the kill fires. The
    pre-registered 15 trades and 0.8 are untouched."""
    book = _book()
    assert risk.live_kill_blocked(book, "meanrev", KILL) is None
    assert risk.live_kill_blocked(book, "meanrev", KILL, fees=_fees(1.0)) is None
    msg = risk.live_kill_blocked(book, "meanrev", KILL, fees=_fees(2.0))
    assert msg and "after operator cost" in msg and "PF 0.79" in msg


def test_a_cost_joins_only_the_trade_it_belongs_to():
    book = _book()
    got = risk.after_cost_trades(book, {"buy:t0": 3.0, "close:t0": 4.0,
                                        "buy:zz": 99.0})
    assert got[0]["pnl"] == 93.0 and got[0]["pnl_gross"] == 100.0
    assert got[0]["pnl_pct"] == pytest.approx(10.0 - 7.0 / 1000 * 100)
    assert got[1:] == book[1:]                   # untouched, not copied wrong


def test_an_unreadable_ledger_fails_the_live_kill_closed():
    assert "fails CLOSED" in risk.live_kill_blocked(
        _book(n_win=15, n_loss=0), "meanrev", KILL, fees_problem="torn")
    # Disabled kill: nothing to fail.
    assert risk.live_kill_blocked(_book(), "meanrev", {"risk": {}},
                                  fees_problem="torn") is None


# ------------------------------------------------- the cycle, end to end

def _settings(d, fee=9.0):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ops.SETTINGS_FILE), "w") as f:
        json.dump({"version": 3, "fee_per_trade": fee, "fee_on_crypto": False,
                   "fee_on_fx": False, "frequency": "medium",
                   "updated_at": "2026-10-24T14:00:00Z"}, f)


@pytest.fixture
def cycle_env(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["max_drawdown_pct"] = 10.0
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    def _install(broker):
        monkeypatch.setattr(main, "Broker", lambda c: broker)
        return broker
    return cfg, _install


FILLED_ENTRY = {"id": "entry-1", "status": "OrderStatus.FILLED",
                "filled_avg_price": 20.0, "filled_qty": 50, "legs": [],
                "filled_at": "2026-10-23T19:45:02+00:00"}
STOP_FILLED = {"leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                            "type": "OrderType.STOP", "filled_avg_price": 12.67,
                            "filled_qty": 50, "legs": []},
               "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                          "type": "OrderType.LIMIT", "filled_avg_price": None,
                          "filled_qty": 0, "legs": []}}


def _events(cfg, name):
    return [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
            if r.get("event") == name]


def test_the_cycle_charges_its_fills_before_the_rails_read_equity(
        cycle_env, opdir, monkeypatch):
    """Cycle 2 confirms the entry fill AND finds the stop-out. The daily-loss
    rail's first look already includes the $9 entry fee (charged in
    _bootstrap_cycle); after reconcile charges the $9 close, rail equity is
    refreshed and the rail looks again. Neither cost is one run late."""
    cfg, install = cycle_env
    _settings(opdir)
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    seen = []
    real = risk.daily_loss_breached

    def spy(account, c):
        seen.append(account.get(risk.RAIL_EQUITY_KEY))
        return real(account, c)
    monkeypatch.setattr(risk, "daily_loss_breached", spy)
    install(FakeCycleBroker(make_bars(BUY_CLOSES + [20], start_day=2),
                            positions={},
                            orders={"entry-1": FILLED_ENTRY, **STOP_FILLED}))
    main.run_cycle()

    assert seen == [100_000.0 - 9.0, 100_000.0 - 18.0]
    [done] = _events(cfg, "cycle_complete")[-1:]
    detail = json.loads(done["detail"])
    assert detail["equity"] == 100_000.0                # the console's figure
    assert detail["rail_equity"] == 100_000.0 - 18.0    # the rails'


def test_the_cycle_moves_a_legacy_peak_once_and_only_once(cycle_env, opdir):
    cfg, install = cycle_env
    _settings(opdir)
    _ledger(opdir, _line("buy:old", 9.0, ts="2026-10-01T15:00:00+00:00"),
            _line("close:old", 9.0, ts="2026-10-02T15:00:00+00:00"))
    _legacy_peak(100_500.0)
    install(FakeCycleBroker(make_bars([10] * 12)))       # a quiet cycle
    main.run_cycle()
    rec = _peak_file()
    assert (rec["basis"], rec["peak_equity"], rec["moved_by_cost"]) == (
        "after_cost", 100_500.0 - 18.0, 18.0)
    install(FakeCycleBroker(make_bars([10] * 12)))
    main.run_cycle()
    assert _peak_file()["peak_equity"] == 100_500.0 - 18.0
    assert _peak_file()["moved_at"] == rec["moved_at"]


def test_a_torn_ledger_blocks_every_entry_and_says_so(cycle_env, opdir):
    cfg, install = cycle_env
    _settings(opdir)
    _ledger(opdir, _line("buy:old", 9.0), TORN)
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    assert broker.submitted == [] and broker.bracket_calls == []
    recs = Ledger(cfg["memory"]["ledger_path"]).all_records()
    blocked = [r for r in recs if r["type"] == "decision"
               and r.get("action") == "buy"]
    assert blocked and all(r.get("rail") == "rail_equity_unknown"
                           and not r.get("executed") for r in blocked)
    assert _events(cfg, "rail_equity_unknown")
    assert json.loads(_events(cfg, "cycle_complete")[-1]["detail"])[
        "rail_equity"] is None
    assert not os.path.exists(HWM), "an unknown rail equity wrote a peak"


def test_a_clean_ledger_lets_the_same_entry_through(cycle_env, opdir):
    """The pair of the test above: same fixture, a readable ledger, and the
    buy executes — so the block above is the ledger, not the fixture."""
    cfg, install = cycle_env
    _settings(opdir)
    _ledger(opdir, _line("buy:old", 9.0))
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    assert broker.bracket_calls
    assert not _events(cfg, "rail_equity_unknown")


# ------------------------------------------------- health, both bases

def test_health_shows_the_drawdown_before_and_after_cost(cfg, opdir, tmp_path,
                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_drawdown_pct"] = 10.0
    risk.update_high_water(100_000.0, gross_equity=100_100.0)
    Ledger(cfg["memory"]["ledger_path"]).log_event(
        "cycle_complete", json.dumps({"equity": 95_100.0,
                                      "rail_equity": 95_000.0}))
    dd = health.status(cfg)["drawdown"]
    assert dd["basis"] == "after_cost"
    assert dd["drawdown_pct"] == 5.0                     # rail: 95,000 / 100,000
    assert dd["drawdown_gross_pct"] == pytest.approx(4.995, abs=0.01)
    assert dd["rail_equity"] == 95_000.0 and dd["equity"] == 95_100.0


def test_health_reports_a_torn_ledger_as_a_problem_and_the_rail_engaged(
        cfg, opdir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_drawdown_pct"] = 10.0
    risk.update_high_water(100_000.0)
    Ledger(cfg["memory"]["ledger_path"]).log_event(
        "cycle_complete", json.dumps({"equity": 99_000.0}))   # predates field
    _ledger(opdir, TORN)
    s = health.status(cfg)
    assert s["drawdown"]["engaged"] is True
    assert "fails CLOSED" in s["drawdown"]["note"]
    assert any("fee ledger unreadable" in p for p in s["problems"])
    assert s["healthy"] is False


# ---------------- the advice: a fee-log problem is not a drawdown (PR #168)
#
# Health and the watchdog append advice to the ENGAGED line. For a real
# drawdown it is the peak reset (§40: the latch cannot clear itself). When the
# rail is engaged only because rail equity is unknown, that advice is wrong and
# harmful: the state clears itself once the fee log reads, and removing the
# peak would forgive a drawdown nobody reviewed. Each test checks BOTH callers.

def _advice_env(tmp_path, monkeypatch, opdir, detail, fee_lines=()):
    monkeypatch.chdir(tmp_path)
    risk.update_high_water(100_000.0)                 # after-cost basis
    rec = {"type": "event", "event": "cycle_complete",
           "ts": "2026-10-28T20:00:00+00:00", "detail": json.dumps(detail)}
    with open(os.path.join("memory", "ledger.jsonl"), "w") as f:
        f.write(json.dumps(rec) + "\n")
    cfg = {"memory": {"ledger_path": "memory/ledger.jsonl"}, "mode": "paper",
           "risk": {"max_drawdown_pct": 10.0}}
    with open("config.yaml", "w") as f:                # what the watchdog reads
        yaml.safe_dump(cfg, f)
    if fee_lines:
        _ledger(opdir, *fee_lines)
    return cfg, [rec]


def _engaged_lines(cfg, recs):
    """The ENGAGED problem from health.status and from watchdog.check."""
    out = []
    for problems in (health.status(cfg=cfg, read_only=True)["problems"],
                     watchdog.check(records=recs)):
        hits = [p for p in problems if "circuit breaker ENGAGED" in p]
        assert len(hits) == 1, problems
        out.append(hits[0])
    return out


@pytest.mark.parametrize("detail,fee_lines", [
    # the last cycle could not read the fee log and recorded rail_equity null
    ({"equity": 99_000.0, "rail_equity": None}, ()),
    # a cycle_complete that predates the field, and a fee log torn NOW
    ({"equity": 99_000.0}, (TORN,)),
], ids=["last_cycle_failed_closed", "torn_log_now"])
def test_unknown_rail_equity_points_to_the_runbook_not_the_peak_reset(
        tmp_path, monkeypatch, opdir, detail, fee_lines):
    cfg, recs = _advice_env(tmp_path, monkeypatch, opdir, detail, fee_lines)
    for line in _engaged_lines(cfg, recs):
        assert "operator fee ledger unreadable" in line
        assert risk.RAIL_EQUITY_UNKNOWN_HINT in line
        assert "docs/runbooks.md" in line and "rail_equity_unknown" in line
        assert risk.HIGHWATER_RESET_HINT not in line
        assert "rm " not in line and ".equity_highwater.json" not in line
        assert "cannot clear itself" not in line


def test_a_genuine_after_cost_drawdown_keeps_the_peak_reset_hint(
        tmp_path, monkeypatch, opdir):
    """The pair of the test above: same fixture, a readable fee log and a real
    15% drawdown after cost. The §40 reset advice must still be there, and the
    fee-log pointer must not."""
    cfg, recs = _advice_env(tmp_path, monkeypatch, opdir,
                            {"equity": 85_100.0, "rail_equity": 85_000.0},
                            (_line("buy:old", 9.0),))
    for line in _engaged_lines(cfg, recs):
        assert "15.00% from peak" in line
        assert risk.HIGHWATER_RESET_HINT in line
        assert "cannot clear itself" in line and ".equity_highwater.json" in line
        assert risk.RAIL_EQUITY_UNKNOWN_HINT not in line
        assert "rail_equity_unknown" not in line


def test_drawdown_hint_follows_the_state_flag():
    assert risk.drawdown_hint({"engaged": True}) == risk.HIGHWATER_RESET_HINT
    assert risk.drawdown_hint({}) == risk.HIGHWATER_RESET_HINT
    assert risk.drawdown_hint({"engaged": True, "rail_equity_unknown": True}) \
        == risk.RAIL_EQUITY_UNKNOWN_HINT


def test_the_hint_names_a_runbook_entry_that_exists():
    root = os.path.dirname(os.path.dirname(os.path.abspath(risk.__file__)))
    with open(os.path.join(root, "docs", "runbooks.md")) as f:
        assert "\n## rail_equity_unknown " in f.read()


# ------------------------------------------------- the decay monitor

def _trades(n=24):
    recs = []
    for i in range(n):
        tid = f"t{i}"
        recs.append({"type": "decision", "trade_id": tid, "symbol": "SPY",
                     "ts": f"2026-01-{(i % 20) + 1:02d}T21:00:00+00:00",
                     "action": "buy", "executed": True, "strategy": "meanrev",
                     "entry_price": 100.0, "qty": 10})
        recs.append({"type": "outcome", "trade_id": tid, "pnl_pct": 1.0,
                     "ts": f"2026-02-{(i % 20) + 1:02d}T21:00:00+00:00"})
    return recs


def test_the_decay_check_reads_each_trades_own_cost():
    fees = {"buy:t0": 1.0, "close:t0": 1.0}
    got = rb.after_cost(rb.closed_trades(_trades(2)), fees)
    assert got[0]["pnl_pct"] == pytest.approx(1.0 - 2.0 / 1000 * 100)
    assert got[1]["pnl_pct"] == 1.0


def test_the_null_is_charged_the_same_model_and_unchanged_when_off():
    import cost_models
    bars = make_bars([100.0 + (i % 7) for i in range(80)])
    off = rb.null_distribution({"SPY": bars}, ["SPY"], [5, 10], 24,
                               n_samples=50)
    off_block = {m: dict(v, model="off")
                 for m, v in cost_models.RECOMMENDED.items()}
    same = rb.null_distribution({"SPY": bars}, ["SPY"], [5, 10], 24,
                                n_samples=50, costs=off_block,
                                notionals=[1000.0])
    assert same == off, "a cost-free run moved the seeded stream"
    # Flat prices so every gross draw is exactly 0: a $1,000 order pays the
    # $1 minimum on each leg, so every mean is -0.2% — the SAME cost the
    # realised side pays, which is the point.
    flat = make_bars([100.0] * 80)
    gross = rb.null_distribution({"SPY": flat}, ["SPY"], [5, 10], 24,
                                 n_samples=50)
    on = rb.null_distribution({"SPY": flat}, ["SPY"], [5, 10], 24,
                              n_samples=50, costs=cost_models.RECOMMENDED,
                              notionals=[1000.0])
    assert gross == [0.0] * 50
    assert on == pytest.approx([-0.2] * 50)


def test_check_scores_the_realised_side_after_cost():
    """End to end through check(): 24 trades of +1.00% on a $1,000 entry,
    each paying $2 of its own cost, score +0.80% a trade. Flat bars and no
    cost model, so the null is exactly 0 and only the realised side moves."""
    recs = _trades(24)
    fees = {}
    for i in range(24):
        fees[f"buy:t{i}"] = 1.0
        fees[f"close:t{i}"] = 1.0
    flat = {"SPY": [{"ts": f"2026-0{1 + d // 28}-{d % 28 + 1:02d}T21:00:00+00:00",
                     "open": 100.0, "high": 100.0, "low": 100.0,
                     "close": 100.0} for d in range(84)]}
    gross = rb.check(recs, flat, n_samples=50)
    after = rb.check(recs, flat, n_samples=50, fees=fees)
    assert gross.actual_mean_pct == pytest.approx(1.0)
    assert after.actual_mean_pct == pytest.approx(0.8)


def test_decaycheck_passes_its_fees_and_model_to_the_check(opdir, monkeypatch,
                                                           tmp_path):
    """decaycheck.run must hand the strict fee map AND the model in force to
    rb.check — the half the check above cannot see."""
    seen = {}

    def spy(records, bars, **kw):
        seen.update(kw)
        return rb.Assessment(verdict=rb.INSUFFICIENT_DATA, n_trades=0,
                             min_trades=20)
    monkeypatch.setattr(rb, "check", spy)
    _ledger(opdir, _line("buy:t1", 1.25))
    cfg = {"memory": {"ledger_path": str(tmp_path / "ledger.jsonl")},
           "backtest": {"slippage_bps": 5}}
    _, meta = decaycheck.run(cfg, min_trades=20, samples=10, lookback=10,
                             offline_bars={})
    assert seen["fees"] == {"buy:t1": 1.25}
    assert seen["costs"]["equities"]["model"] == "off"   # no settings file
    assert meta["operator_cost_paid"] == 1.25


def test_decaycheck_could_not_run_on_a_torn_ledger(opdir, tmp_path,
                                                   monkeypatch, capsys):
    cfg = {"memory": {"ledger_path": str(tmp_path / "ledger.jsonl")},
           "backtest": {"slippage_bps": 5}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    _ledger(opdir, TORN)
    assert decaycheck.main(["--config", str(tmp_path / "config.yaml")]) == 2
    assert "torn" in capsys.readouterr().err


def test_decaycheck_runs_on_a_clean_ledger(opdir, tmp_path):
    """The pair: same config, readable ledger -> it runs (exit 0)."""
    cfg = {"memory": {"ledger_path": str(tmp_path / "ledger.jsonl")},
           "backtest": {"slippage_bps": 5}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    _ledger(opdir, _line("buy:t1", 1.0))
    assert decaycheck.main(["--config", str(tmp_path / "config.yaml")]) == 0
