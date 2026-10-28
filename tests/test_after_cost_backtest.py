"""The backtester and the gates read AFTER-COST results (owner decision 2026-10-28).

Connor, in his own words:

    "Yes: make gates, backtests and the drawdown/retire rails use after-cost
    results first (repete4's override included). Entry cost filters later, bot
    by bot."

What is pinned here:

  * OFF BY DEFAULT, byte for byte. No block, `None`, `False` and an `off` model
    all produce exactly what the code produced before the field existed —
    pinned against digests taken from origin/cost-models-v2 (b0b1387), the
    commit this work builds on, not against the new code's own output.
  * ON, every simulated fill pays `cost_models.order_cost` (the verbatim copy)
    on its shares and notional: 100 shares at $50 -> $1.00, 1,000 -> $5.00;
    a venue fee is topped up to the model, never paid twice.
  * the cost stays OUT of cash (sizing is broker-basis, as live), and comes
    off each trade's P&L, the curve (so maxDD and Sharpe), total return, the
    sim's own drawdown rail, both benchmarks, and so every gate metric.
  * the gate spec pins the block (`operator_costs:`), validates it, hashes it,
    and register_gate refuses a NEW spec that does not declare it — while every
    frozen spec still validates and re-registers idempotently.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

import backtest as bt
import cost_models
import gatespec as gs
import significance as sig
from test_ensemble_sim import synth_bars

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_gate                                              # noqa: E402

RESEARCH = {m: dict(v) for m, v in cost_models.RECOMMENDED.items()}


def _with(cfg, block):
    out = copy.deepcopy(cfg)
    out.setdefault("backtest", {})["operator_costs"] = block
    return out


@pytest.fixture(scope="module")
def cfg():
    return bt.load_config()


@pytest.fixture(scope="module")
def bars(cfg):
    picks = ["SPY", "AAPL", "MSFT", "XOM", "JNJ"]
    return synth_bars([s for s in cfg["symbols"] if s in picks])


def _digest(r):
    blob = json.dumps({"summary": r.summary(), "curve": r.equity_curve,
                       "pnls": [t.pnl for t in r.trades],
                       "trades": [(t.symbol, t.entry_ts, t.entry_price, t.qty,
                                   t.exit_ts, t.exit_price, t.fees)
                                  for t in r.trades]},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# Taken 2026-10-28 by running this exact digest over these exact bars with the
# src/ of origin/cost-models-v2 (b0b1387) — the code BEFORE the operator cost
# reached the backtester. If this fails after a deliberate change to config.yaml
# or to the simulator, regenerate it from that change's parent; if it fails
# after a change to the operator-cost code, the off path is no longer the
# identity and every frozen verdict has stopped reproducing.
GOLDEN = {
    "ensemble": "5787f50c83dd7b5ca59a4fcdb578792c11bb342ddc4f4fb3eeaa2674377b7dff",
    "meanrev": "c842ad53060adf34451162592be065f488aa46be936abda00064f119a0445e60",
    "tsmom": "cfd99d72d546b8c4210c20c92b923a5320583ff1ae22ccd326e69bb61dd2e084",
}


@pytest.mark.parametrize("block", ["absent", None, False,
                                   {m: dict(v, model="off")
                                    for m, v in RESEARCH.items()}],
                         ids=["absent", "none", "false", "model-off"])
def test_without_the_block_every_path_reproduces_the_pre_cost_goldens(
        cfg, bars, block):
    c = cfg if block == "absent" else _with(cfg, block)
    assert _digest(bt.simulate_ensemble(bars, c)) == GOLDEN["ensemble"]
    assert _digest(bt.simulate(bars, c, strategy_name="meanrev")) == \
        GOLDEN["meanrev"]
    assert _digest(bt.simulate(bars, c, strategy_name="tsmom")) == \
        GOLDEN["tsmom"]


def test_a_cost_free_summary_carries_no_operator_fields(cfg, bars):
    """Absent means no cost — and keeps every trial row and verdict arm in the
    shape it had before the field existed."""
    s = bt.simulate_ensemble(bars, cfg).summary()
    assert not {"operator_costs", "operator_cost_paid_usd",
                "operator_costs_sha256"} & set(s)


# ---------------------------------------------------------- the reference

@pytest.mark.parametrize("qty,price,want", [(100, 50.0, 1.00),
                                             (1000, 50.0, 5.00),
                                             (-1000, 50.0, 5.00),   # a short
                                             (5, 8.0, 0.40)])       # 1% cap
def test_one_simulated_equities_order_pays_the_reference(qty, price, want):
    assert bt.operator_charge(RESEARCH, qty, price) == pytest.approx(want)


def test_a_venue_fee_is_topped_up_to_the_model_never_paid_twice():
    assert bt.operator_charge(RESEARCH, 100, 50.0, venue_fee=0.40) == \
        pytest.approx(0.60)
    assert bt.operator_charge(RESEARCH, 100, 50.0, venue_fee=2.00) == 0.0


@pytest.mark.parametrize("market,inputs,venue_fee,want", [
    ("options", {"contracts": 2}, 0.0, 1.35),           # a 1-lot spread order
    ("crypto", {"notional": 1000.0}, 0.0, 8.00),
    ("crypto", {"notional": 1000.0}, 2.60, 5.40),       # on top of 0.26%
    ("fx", {"units": 10_000, "pip": 0.0001, "quote_rate": 1.39}, 0.0, 1.278),
])
def test_the_other_markets_reference_amounts_through_this_bots_copy(
        market, inputs, venue_fee, want):
    """This bot trades equities only; the copied module must still price the
    other four reference orders the spec names, or it is not the console's."""
    gross, _ = cost_models.order_cost(RESEARCH, market, **inputs)
    assert cost_models.charge_amount(gross, venue_fee) == pytest.approx(want)


# ---------------------------------------------------------- the cost, ON

@pytest.fixture(scope="module")
def pair(cfg, bars):
    return (bt.simulate_ensemble(bars, cfg),
            bt.simulate_ensemble(bars, _with(cfg, RESEARCH)))


def test_every_fill_pays_its_own_order_cost(pair):
    _, on = pair
    assert on.trades
    for t in on.trades:
        want = (bt.operator_charge(RESEARCH, t.qty, t.entry_price)
                + bt.operator_charge(RESEARCH, t.qty, t.exit_price))
        assert t.operator_cost == pytest.approx(want), t
    assert on.operator_cost_paid_usd == pytest.approx(
        sum(t.operator_cost for t in on.trades), abs=0.01)


def _same_book(a, b):
    return ([(t.symbol, t.entry_ts, t.qty, t.entry_price, t.exit_price)
             for t in a.trades]
            == [(t.symbol, t.entry_ts, t.qty, t.entry_price, t.exit_price)
                for t in b.trades])


def test_sizing_is_untouched_by_the_cost(pair, cfg, bars):
    """Live sizes on broker equity, which never includes the cost. Same
    entries, same quantities, same fills — the cost lives in its own
    accumulator, never in cash.

    The research block alone is too small to move a whole-share quantity, so
    the second half charges the schema's ceiling ($1,000 an order, 1% of the
    book) with the drawdown rail off — the one rail that is SUPPOSED to see
    the cost. If any of it reached cash, every later order would size off a
    smaller book and the quantities would part."""
    off, on = pair
    assert _same_book(off, on)
    assert on.avg_deployment_pct == off.avg_deployment_pct
    c = copy.deepcopy(cfg)
    c["risk"]["max_drawdown_pct"] = 0
    heavy = {m: dict(v) for m, v in RESEARCH.items()}
    heavy["equities"].update(model="flat", flat_per_order=1000.0)
    gross = bt.simulate_ensemble(bars, c)
    charged = bt.simulate_ensemble(bars, _with(c, heavy))
    assert charged.operator_cost_paid_usd == 1000.0 * 2 * len(charged.trades)
    assert _same_book(gross, charged)
    assert charged.avg_deployment_pct == gross.avg_deployment_pct


def test_the_cost_comes_off_pnl_curve_return_and_profit_factor(pair):
    off, on = pair
    paid = on.operator_cost_paid_usd
    assert paid > 0
    for a, b in zip(off.trades, on.trades):
        assert b.pnl == pytest.approx(a.pnl - b.operator_cost)
    assert sig.trade_pnls(on) == [t.pnl for t in on.trades]
    assert on.total_return_pct == pytest.approx(
        off.total_return_pct - paid / 100_000 * 100, abs=0.002)
    # The curve's last mark precedes the end-of-data liquidation (whose
    # slippage is not in it either), so it carries every cost but those
    # final exit orders'.
    final_exits = sum(bt.operator_charge(RESEARCH, t.qty, t.exit_price)
                      for t in on.trades if t.exit_reason == "end_of_data")
    assert on.equity_curve[-1] == pytest.approx(
        off.equity_curve[-1] - (paid - final_exits), abs=0.02)
    assert on.profit_factor <= off.profit_factor
    assert on.max_drawdown_pct >= off.max_drawdown_pct


def test_both_benchmarks_pay_the_same_model(pair):
    """Like-for-like, as the slippage already is: the benchmark's two orders
    pay the cost too. Lower by a little, never higher."""
    off, on = pair
    assert on.buy_hold_return_pct < off.buy_hold_return_pct
    assert on.benchmark_return_pct < off.benchmark_return_pct
    one = bt.buy_and_hold_return(
        [{"open": 50.0, "close": 50.0}, {"open": 50.0, "close": 50.0},
         {"open": 50.0, "close": 60.0}], 0.0, 0.0, 5_000.0, RESEARCH)
    # 100 shares, +$1,000, two $1.00 orders: (1000 - 2) / 5000.
    assert one == pytest.approx(998 / 5_000 * 100)


def test_the_summary_stamps_the_block_and_what_it_paid(pair):
    _, on = pair
    s = on.summary()
    assert s["operator_costs"] == cost_models.validate(RESEARCH)
    assert s["operator_cost_paid_usd"] == on.operator_cost_paid_usd
    assert s["operator_costs_sha256"] == bt.operator_costs_sha256(
        cost_models.validate(RESEARCH))


def test_the_single_strategy_path_charges_too(cfg, bars):
    off = bt.simulate(bars, cfg, strategy_name="meanrev")
    on = bt.simulate(bars, _with(cfg, RESEARCH), strategy_name="meanrev")
    assert on.operator_cost_paid_usd > 0
    assert on.total_return_pct < off.total_return_pct
    assert [t.qty for t in on.trades] == [t.qty for t in off.trades]


def test_a_malformed_block_stops_the_run_rather_than_charging_nothing(cfg,
                                                                       bars):
    bad = {m: dict(v) for m, v in RESEARCH.items()}
    del bad["equities"]["per_share"]
    with pytest.raises(ValueError, match="per_share"):
        bt.simulate_ensemble(bars, _with(cfg, bad))


def test_the_sim_drawdown_rail_reads_after_cost_equity(cfg):
    """Flat prices: gross equity never moves, so the gross rail never fires.
    A $1,000-per-order flat cost (the schema's ceiling) costs 1% of the book
    per order; with a 1.5% drawdown limit the AFTER-COST rail must start
    refusing entries within two orders. If the sim's rail still read gross
    equity, the census would show no drawdown blocks at all."""
    c = copy.deepcopy(cfg)
    c["risk"]["max_drawdown_pct"] = 1.5
    picks = [s for s in c["symbols"] if s in ("SPY", "AAPL", "MSFT")]
    up = synth_bars(picks, n=260)
    heavy = {m: dict(v) for m, v in RESEARCH.items()}
    heavy["equities"].update(model="flat", flat_per_order=1000.0)
    gross = bt.simulate_ensemble(up, c)
    after = bt.simulate_ensemble(up, _with(c, heavy))
    assert gross.census["blocked"].get("drawdown", 0) < \
        after.census["blocked"].get("drawdown", 0)


# ---------------------------------------------------------- the gate spec

def _spec(**over):
    spec = {"id": "t26", "claim": "METHOD", "title": "an after-cost fixture",
            "snapshot": {"path": "bars.json.gz", "sha256": "a" * 64},
            "judge_model": False, "operator_costs": RESEARCH,
            "arms": [{"name": "baseline"}],
            "clauses": [{"id": "a", "rule": "min_trades", "n": 30}],
            "prior": "A fixture; it decides nothing and expects to fail.",
            "failure_modes": ["fixture"]}
    spec.update(over)
    return spec


def test_a_full_block_and_false_validate():
    gs.validate(_spec())
    gs.validate(_spec(operator_costs=False))


@pytest.mark.parametrize("bad", [True, "per_share", 1.0, None])
def test_anything_but_false_or_a_full_block_is_refused(bad):
    with pytest.raises(gs.SpecError, match="operator_costs"):
        gs.validate(_spec(operator_costs=bad))


def test_a_partial_or_padded_block_is_refused():
    partial = {m: dict(v) for m, v in RESEARCH.items()}
    del partial["fx"]
    with pytest.raises(gs.SpecError, match="operator_costs"):
        gs.validate(_spec(operator_costs=partial))
    padded = {m: dict(v) for m, v in RESEARCH.items()}
    padded["equities"]["per_shar"] = 0.005           # a typo validate drops
    with pytest.raises(gs.SpecError, match="drop"):
        gs.validate(_spec(operator_costs=padded))


def test_the_block_is_in_the_frozen_hash():
    other = {m: dict(v) for m, v in RESEARCH.items()}
    other["equities"]["per_share"] = 0.0035
    hashes = {gs.canonical_sha256(_spec()),
              gs.canonical_sha256(_spec(operator_costs=other)),
              gs.canonical_sha256(_spec(operator_costs=False))}
    legacy = _spec()
    del legacy["operator_costs"]
    hashes.add(gs.canonical_sha256(legacy))
    assert len(hashes) == 4


def test_every_frozen_spec_still_validates():
    specs = sorted(f for f in os.listdir(os.path.join(ROOT, "research", "specs"))
                   if f.endswith(".yaml"))
    assert len(specs) >= 100
    for f in specs:
        gs.load(os.path.join(ROOT, "research", "specs", f))


def _register(tmp_path, spec):
    d = tmp_path / "research" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec['id']}.yaml").write_text(yaml.safe_dump(spec))
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "register_gate.py"),
         spec["id"], "--registrations", str(tmp_path / "reg.jsonl"),
         "--verdicts", str(tmp_path / "v.jsonl")],
        cwd=str(tmp_path), capture_output=True, text=True)


def test_register_gate_refuses_a_new_spec_that_does_not_declare_it(tmp_path):
    spec = _spec()
    del spec["operator_costs"]
    r = _register(tmp_path, spec)
    assert r.returncode != 0
    assert "operator_costs" in (r.stdout + r.stderr)
    assert not (tmp_path / "reg.jsonl").exists(), "a refusal wrote a row"


@pytest.mark.parametrize("value", [False, RESEARCH], ids=["false", "block"])
def test_register_gate_accepts_a_declared_spec(tmp_path, value):
    r = _register(tmp_path, _spec(operator_costs=value))
    assert r.returncode == 0, r.stderr
    rec = json.loads((tmp_path / "reg.jsonl").read_text().splitlines()[-1])
    assert rec["spec"]["operator_costs"] == value


def test_an_existing_registration_without_the_field_stays_idempotent():
    """s43d predates the field. Re-registering it unchanged must still be the
    no-op it always was — the refusal binds only a spec being frozen now."""
    r = subprocess.run([sys.executable, "scripts/register_gate.py", "s43d"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "already registered, unchanged" in r.stdout


# ---------------------------------------------------------- the runner

def test_the_spec_decides_never_config_yaml():
    """config.yaml is not in any hash: a block found there must never reach a
    spec that did not declare one."""
    cfg = {"backtest": {"operator_costs": RESEARCH, "slippage_bps": 5}}
    for setting in (None, False):
        out = run_gate.apply_operator_costs(copy.deepcopy(cfg), setting)
        assert "operator_costs" not in out["backtest"]
    out = run_gate.apply_operator_costs({}, RESEARCH)
    assert out["backtest"]["operator_costs"] == RESEARCH


def test_absent_is_not_false():
    assert run_gate.operator_costs_setting({}) is None
    assert run_gate.operator_costs_setting({"operator_costs": False}) is False


def test_the_verdict_records_the_block_its_hash_and_what_each_arm_paid():
    src = open(os.path.join(ROOT, "scripts", "run_gate.py")).read()
    assert '"operator_costs": op_setting,' in src
    assert '"operator_costs_sha256": bt.operator_costs_sha256(op_block),' in src
    assert 'rec[SUMMARY].get("operator_cost_paid_usd", 0.0)' in src
    # Pinned onto the config BEFORE any arm runs.
    assert src.index("apply_operator_costs(") < src.index("run_arms(spec, base_cfg")
