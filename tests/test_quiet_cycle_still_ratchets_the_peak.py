"""DIVERGENCE #11 — the live equity peak was only sampled when an order ran.

The defect
----------
§31 put `risk.update_high_water` inside `risk.pre_trade_checks`, and reasoned
carefully about running it for sells as well as buys so "the peak must keep
tracking even while entries are blocked". That reasoning is right and it is not
enough, because `pre_trade_checks` has exactly one caller (`main.py`'s order
loop) and it fires **only when an order is attempted**.

A cycle that generates no buy and no sell therefore never touched the mark.

Why that is not cosmetic, given the peak only ratchets UP: equity earned on a
quiet day is invisible to it. A book that drifts up over a week of holds keeps
last week's peak, and the first 10% drawdown is then measured from a stale LOW
peak — so the breaker fires late. Symmetrically, a genuine new high never
registers, and the bot sits nearer the rail than its equity says.

It is divergence #10 one level up. There the SIMULATOR ratcheted only inside
the buy branch; here LIVE ratchets only inside an order attempt. Both are
invisible on the 500-symbol snapshots, where something trades on virtually
every bar — and both bite hardest on the book this bot actually runs: 38
symbols, with whole days where nothing is entered.

What closes it
--------------
A ratchet in the cycle itself, immediately after the account is read fresh from
the broker (invariant #4) and before any decision is taken, so the mark cannot
depend on what the cycle chooses to do. `pre_trade_checks` still ratchets per
order; this is the floor under it, not a replacement, and `update_high_water`
is max-based so running both is a no-op the second time.
"""
import yaml

import main
import risk

from test_main_cycle import FakeCycleBroker, cycle_env   # noqa: F401
from conftest import make_bars

# Flat closes: SMA3 and SMA5 are equal on every bar, so no crossover fires and
# the cycle reaches its end having attempted nothing.
QUIET_CLOSES = [10] * 12


class _Broker(FakeCycleBroker):
    """FakeCycleBroker with the equity dialled per test."""

    def __init__(self, bars, equity, **kw):
        super().__init__(bars, **kw)
        self._equity = equity

    def account(self):
        return {"equity": self._equity, "cash": self._equity,
                "last_equity": self._equity, "buying_power": self._equity}


def _armed(cycle_env, dd_cap=10.0):
    """Turn the drawdown rail ON in the fixture config and return `install`.

    The shared `cfg` fixture ships no `max_drawdown_pct`, so the rail is
    DISABLED there and the ratchet is correctly skipped. Discovered by this
    file failing for that reason — and worth stating, because without arming it
    every assertion below would pass against a bot that does nothing at all.
    """
    cfg, install = cycle_env
    cfg["risk"]["max_drawdown_pct"] = dd_cap
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    return install


def _quiet(install, equity):
    broker = install(_Broker(make_bars(QUIET_CLOSES), equity))
    main.run_cycle()
    return broker


# ---- the defect, replayed ----

def test_a_cycle_that_trades_nothing_still_ratchets_the_peak(cycle_env):
    """THE ONE THAT MATTERS. Before the fix this left the peak at 100,000."""
    install = _armed(cycle_env)
    risk.update_high_water(100_000.0)

    broker = _quiet(install, 150_000.0)

    assert broker.submitted == [], (
        "the fixture was supposed to be a QUIET cycle — if it traded, this "
        "test proves nothing about the no-order path")
    assert risk.read_high_water() == 150_000.0, (
        "a quiet cycle left the peak stale, so a later drawdown would be "
        "measured from the wrong high — divergence #11")


def test_the_peak_still_only_goes_up(cycle_env):
    """The property §31 built the ratchet for. A per-cycle sample must not
    become a per-cycle OVERWRITE, or the breaker could never fire: a peak that
    follows equity down means drawdown is always 0%."""
    install = _armed(cycle_env)
    risk.update_high_water(150_000.0)

    _quiet(install, 90_000.0)

    assert risk.read_high_water() == 150_000.0, (
        "the cycle lowered the peak to current equity — that disables the "
        "drawdown breaker entirely rather than fixing its sampling")


def test_a_quiet_cycle_at_a_new_high_reports_no_drawdown(cycle_env):
    install = _armed(cycle_env)
    risk.update_high_water(100_000.0)
    _quiet(install, 120_000.0)
    peak = risk.read_high_water()
    assert risk.drawdown_pct(120_000.0, peak) == 0.0


def test_the_ratchet_is_skipped_when_the_breaker_is_disabled(cycle_env):
    """`max_drawdown_pct: 0` disables the rail (repo convention). Writing a
    high-water file for a rail nobody enabled would leave a stale mark to trip
    over the day someone turns it on."""
    install = _armed(cycle_env, dd_cap=0)

    _quiet(install, 150_000.0)

    # Paired with test_a_cycle_that_trades_nothing_still_ratchets_the_peak,
    # which runs the SAME cycle at the SAME equity with the rail armed and
    # asserts 150,000. Without that pairing this would pass against a build
    # where the ratchet never runs at all.
    assert risk.read_high_water() in (0, 0.0, None), (
        "the disabled rail still seeded a peak — a stale mark left for whoever "
        "turns max_drawdown_pct on later")


# ---- it is in the cycle, not only in the order loop ----

def test_the_ratchet_runs_before_any_decision(cycle_env):
    """Placement is the fix. Reading the peak AFTER the decision loop would
    reintroduce the bug for any cycle that exits early — the daily-loss kill
    switch returns before the loop, and so does a HALT.

    Rewritten in W4-7 (2026-07-29) after the refactor moved the fresh account
    read into `_bootstrap_cycle`. This test FAILED on that move, which is what
    it is for — but the failure was structural, not behavioural, so the fix is
    to track the new shape rather than to relax the property. Both halves are
    still pinned, in the two functions that now hold them.
    """
    import inspect
    boot = inspect.getsource(main._bootstrap_cycle)
    src = inspect.getsource(main._run_cycle)

    # 1. The account is still read FRESH from the broker, not from memory or a
    #    prior cycle (invariant #4), and the bootstrap hands it back.
    assert "account = broker.account()" in boot
    assert "return cfg, ledger, memory, broker, account, positions" in boot

    # 2. `_run_cycle` unpacks that result BEFORE it ratchets, so the equity the
    #    peak is measured against is this cycle's.
    #
    # Tracked forward again on 2026-08-02, when the HALT split into freeze/exits
    # modes added `halted` to the tuple — structural for the same reason as
    # W4-7, and the property is unchanged. Under `freeze` the bootstrap still
    # returns None and no ratchet happens (nothing traded, so nothing to
    # measure); under `exits` the cycle runs and the ratchet must still precede
    # every early return, which is exactly what the ordering below pins.
    unpack = src.index(
        "cfg, ledger, memory, broker, account, positions, halted = started")
    # 2026-10-28 (owner decision: the rails read after-cost results): the
    # ratchet takes RAIL equity — broker equity less the operator cost charged
    # so far, computed in _bootstrap_cycle from the same fresh account read.
    # Same placement, same property; only the figure it ratchets on moved.
    ratchet = src.index('risk.update_high_water(risk.rail_equity(account),')
    # The daily-loss kill switch is the first thing in _run_cycle that can
    # return early. W4-7 moved its body into _kill_switch_fired, so the call is
    # what marks the boundary now.
    kill = src.index("_kill_switch_fired(")

    assert unpack < ratchet < kill, (
        "the per-cycle ratchet must sit between the fresh account read and "
        "the first early return, or a cycle that halts skips it")

    # 3. And nothing returns between the two. An early exit slipped in here
    #    would restore divergence #11 for exactly the cycles that take it, and
    #    the ordering assertion above would not notice.
    between = src[unpack:ratchet]
    assert "return" not in between, (
        f"an early return was added between the account read and the "
        f"ratchet:\n{between}")


def test_gutting_the_cycle_ratchet_would_fail_this_file():
    """Meta-assertion, per repo practice. If the per-cycle ratchet were deleted
    and only the order-loop one remained, the first test here would pass a
    stale 100,000 — so name the line rather than trusting it stays."""
    import inspect
    src = inspect.getsource(main._run_cycle)
    assert src.count("risk.update_high_water") == 1, (
        "expected exactly one per-cycle ratchet in _run_cycle; the per-order "
        "one lives in risk.pre_trade_checks")
    assert "divergence #11" in src.lower() or "DIVERGENCE #11" in src
