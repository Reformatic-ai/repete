"""Every broker-shaped test double is either complete or explicitly excused.

WHY (Phase 5a, 2026-08-06)
--------------------------
`src/main.py` wraps two broker calls in `except Exception` and carries on:

    main.py:1536  latest_price()      -> drift guard skipped, `degradation` logged
    main.py:397   open_stop_orders()  -> the whole chandelier ratchet skipped

Both are correct for a real outage and both are indistinguishable, from inside
a test, from "the fake forgot the method". So an incomplete double does not
fail — it quietly selects a different branch and then agrees with whatever
happens there.

Both had already fired:

  * `FakeCycleBroker` (imported by nine files) had no `latest_price`. Every
    `run_cycle()` it drove skipped the drift guard and wrote a spurious
    `degradation` — the event counted against `ops.max_degradations_per_day`.
  * `_ScriptedCycleBroker` had no `open_stop_orders`. That one never fired only
    because `update_trailing_stops` returns early on `trailing_atr_mult: 0`.
    Enabling the trail in any test there would have skipped the entire ratchet
    and still passed.

Phase 6 is about to move a 692-line function. A net woven from doubles like
that would certify the refactor against the fail-open branch.

THE RULE HERE is the one Phase 4's write-retry exclusion uses: no silent
bucket. Every broker-shaped class is NAMED below as conformant or exempt with a
reason, so a double added later fails this file until somebody decides which it
is. `test_the_registry_matches_reality` is what makes that stick.
"""
import ast
import inspect
import pathlib

import pytest

import broker as broker_mod
from fakes.broker import ConformantBroker

TESTS = pathlib.Path(__file__).parent

# The real Broker's public surface. Read from the class, never retyped.
BROKER_METHODS = {n for n, _ in inspect.getmembers(
    broker_mod.Broker, inspect.isfunction) if not n.startswith("_")}

# A class is "broker-shaped" if it defines at least two of these. One method
# (almost always `bars`) is a narrow stub for a single function, not something
# that could ever be handed to `main.Broker`.
SHAPE_THRESHOLD = 2


# --------------------------------------------------------------------------
# The registry.
# --------------------------------------------------------------------------

# Complete against `Broker`, and safe to drive a whole cycle.
CONFORMANT = {
    ("tests/fakes/broker.py", "ConformantBroker"),
    ("tests/test_short_path.py", "_ScriptedCycleBroker"),
}

# Narrow doubles for one function, never passed to `main.Broker`. The reason is
# required and is checked for non-emptiness — "exempt" with no reason is how a
# real gap gets filed as a decision.
EXEMPT = {
    ("tests/test_adopted_stop_heat_trap.py", "StopBroker"):
        "drives reconcile, adoption and attach_broker_stops alone, never "
        "main's cycle: open_stop_orders and get_order are the surface those "
        "three read, plus the no-op reads reconcile touches on its way",
    ("tests/test_swing_scan.py", "FakeBroker"):
        "drives swing_scan.run_scan alone, never main's cycle: market_open, "
        "latest_price and the two order methods are its whole surface, and "
        "the scan's own no-exit-path test pins that it cannot need more",
    ("tests/test_daily_posts.py", "ScanBroker"):
        "drives daily_posts.plan_scan, which only fetches bars and quotes",
    ("tests/test_flatten_recovery.py", "StubBroker"):
        "flatten_recovery only ever calls flatten_all + positions, and the "
        "module has an AST test proving it has no other broker path",
    ("tests/test_freshness.py", "StaleBroker"):
        "feeds _fetch_and_validate_bars alone; the cycle never starts",
    ("tests/test_main_cycle.py", "BreachedFlattenFailsBroker"):
        "kill-switch path only — account() and flatten_all(), by design "
        "raising elsewhere so a stray call is a failure not a fallback",
    ("tests/test_main_cycle.py", "BreachedEmptyBook"):
        "same kill-switch path with an empty book",
    ("tests/test_operator_settings.py", "_StopOutBroker"):
        "drives reconcile_closed_positions alone to prove a stop-out pays the "
        "operator cost and an unfilled entry does not; no cycle, no entry path",
    ("tests/test_reconcile.py", "FakeBroker"):
        "reconciliation reads orders only; no cycle, no entry path",
    ("tests/test_reconcile.py", "ExplodingBroker"):
        "deliberately raises on every call — completing it would defeat it",
    ("tests/test_short_path.py", "_ClosedOrderBroker"):
        "resolve_exit_price fallback only: get_order + closed_orders",
    ("tests/test_broker_conformance.py", "_OldStyleFake"):
        "deliberately incomplete — it RECONSTRUCTS the pre-Phase-5a bug so "
        "the last test in this file can assert the registry still rejects it. "
        "Completing it would make that test vacuous. (It was caught by this "
        "very registry on the first run, which is the intended demonstration.)",
}


def _broker_shaped_classes():
    """Every class under tests/ defining >= SHAPE_THRESHOLD broker methods."""
    out = {}
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            defined = {n.name for n in node.body
                       if isinstance(n, ast.FunctionDef)}
            hits = defined & BROKER_METHODS
            if len(hits) >= SHAPE_THRESHOLD:
                out[(rel, node.name)] = hits
    return out


# --------------------------------------------------------------------------
# The shared fake really is complete.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(BROKER_METHODS))
def test_the_shared_fake_defines_every_public_broker_method(name):
    assert hasattr(ConformantBroker, name), (
        f"ConformantBroker is missing {name}() — a cycle calling it would take "
        f"a fail-open branch instead of the real one")


@pytest.mark.parametrize("name", sorted(BROKER_METHODS))
def test_the_shared_fake_accepts_the_same_calls(name):
    """Signature compatibility, not just presence. A fake whose `bars` takes
    `(symbol)` while the real one takes `(symbol, timeframe, limit)` is exactly
    as broken as one that omits it — and fails later, further from the cause."""
    real = inspect.signature(getattr(broker_mod.Broker, name))
    fake = inspect.signature(getattr(ConformantBroker, name))

    args = []
    kwargs = {}
    for pname, param in real.parameters.items():
        if pname == "self":
            args.append(object())
        elif param.default is inspect.Parameter.empty:
            args.append(object())
        else:
            kwargs[pname] = param.default
    try:
        fake.bind(*args, **kwargs)
    except TypeError as e:
        pytest.fail(f"ConformantBroker.{name}{fake} cannot accept the call "
                    f"Broker.{name}{real} accepts: {e}")


# --------------------------------------------------------------------------
# The registry stays true.
# --------------------------------------------------------------------------

def test_the_registry_matches_reality():
    """The guard that keeps the other tests meaningful. A double added later
    lands here first, and stays red until somebody says which bucket it is in —
    rather than defaulting into whichever one silence implies."""
    actual = set(_broker_shaped_classes())
    classified = CONFORMANT | set(EXEMPT)
    unclassified = sorted(actual - classified)
    stale = sorted(classified - actual)
    assert not unclassified, (
        f"broker-shaped test doubles nobody has classified: {unclassified}. "
        f"Add each to CONFORMANT (complete, may drive run_cycle) or to EXEMPT "
        f"with a reason. See this file's docstring for why silence is not an "
        f"acceptable third option.")
    assert not stale, f"registry names classes that no longer exist: {stale}"


@pytest.mark.parametrize("entry", sorted(CONFORMANT))
def test_classes_declared_conformant_really_are(entry):
    path, name = entry
    src = (TESTS.parent / path).read_text()
    cls = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.ClassDef) and n.name == name)
    defined = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    bases = {b.id for b in cls.bases if isinstance(b, ast.Name)}
    inherited = BROKER_METHODS if "ConformantBroker" in bases else set()
    missing = sorted(BROKER_METHODS - defined - inherited)
    assert not missing, (
        f"{path}::{name} is declared CONFORMANT but is missing {missing}. "
        f"Either implement them, subclass ConformantBroker, or move it to "
        f"EXEMPT with a reason.")


@pytest.mark.parametrize("entry", sorted(EXEMPT))
def test_every_exemption_carries_a_reason(entry):
    reason = EXEMPT[entry].strip()
    assert len(reason) > 20, (
        f"{entry} is exempt with no real reason. An exemption without one is "
        f"a gap filed as a decision.")


# --------------------------------------------------------------------------
# The bug this whole file exists for, pinned so it cannot come back.
# --------------------------------------------------------------------------

def test_a_double_missing_latest_price_would_be_caught_now():
    """Reconstructs the exact pre-Phase-5a shape and asserts the registry
    rejects it. Without this, every test above could pass while the mechanism
    that catches the original bug had quietly stopped working."""
    class _OldStyleFake:                      # noqa: D106 — the 2026-08-05 shape
        def account(self): ...
        def positions(self): ...
        def bars(self, symbol, timeframe, limit): ...
        def market_order(self, symbol, qty, side, client_order_id=None): ...

    defined = {n for n in dir(_OldStyleFake) if not n.startswith("_")}
    assert len(defined & BROKER_METHODS) >= SHAPE_THRESHOLD, \
        "this reconstruction is no longer broker-shaped; the test is vacuous"
    assert "latest_price" not in defined
    assert BROKER_METHODS - defined, "the old fake was complete after all"
