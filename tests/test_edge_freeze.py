"""The §52 EDGE freeze, and the probe that justifies it (2026-08-03).

Why this file exists
--------------------
§51 produced this project's only EDGE pass in fifteen claims — and its own
write-up showed the pass is explicable by survivorship. The 38-name universe
carries **+200.28pp** of inflation, because those names are in `config.yaml`
precisely BECAUSE they are today's winners.

§51 named the fix: replicate on a universe not selected on the outcome.
`scripts/probe_delisted_coverage.py` asked whether that is buildable and
REFUSED, for two separate reasons:

  * yfinance returns NO history for SIVB, FRC, TWTR or ATVI — the losers are
    simply absent, which is the survivorship bias itself;
  * SBNY returns 475 bars that ALL POSTDATE the bank's 2023-03-12 seizure by
    seventeen months, still labelled "Signature Bank", on the pink sheets. A
    builder checking only "did we get bars?" would splice in a successor and
    produce a dataset that never observes the collapse. **That is worse than
    the bias**, and it is why check 2 exists at all.

So `register_gate.py` refuses new EDGE claims on `data/snapshots/`. This is
§33's argument — "continuing to hunt arms is simply buying more chances for a
false positive" — applied to the DATA rather than the selection procedure, and
it is mechanical for the reason §36 gave: a discipline that is only a habit
gets forgotten by the next session.

What these tests protect, each pinned in both directions:

  * the freeze BINDS on new EDGE claims, and does NOT bind on DIAGNOSTIC /
    METHOD / CAPACITY — a freeze that stopped everything would be abandoned;
  * it does NOT break already-registered specs — the machinery it guards has to
    keep working, or the freeze becomes the outage;
  * the override WORKS and COSTS something — a wall gets edited out of the
    script, a speed bump with an audit trail does not;
  * the probe's check 2 catches ticker reuse, which check 1 alone cannot see.
"""
import json
import subprocess
import sys

import pytest

sys.path.insert(0, "scripts")
import probe_delisted_coverage as probe                      # noqa: E402
import register_gate as rgate                                # noqa: E402

FROZEN = "data/snapshots/bars_wide_2000-01-01_2006-12-31.json.gz"


def _spec(claim="EDGE", path=FROZEN):
    # `operator_costs: false` since 2026-10-28: register_gate.py refuses a NEW
    # spec that does not declare its operator cost (owner decision: gates read
    # after-cost results first). These fixtures are about the freeze, so they
    # declare it gross and the freeze stays the thing under test.
    return {"id": "sX", "claim": claim, "title": "t", "judge_model": True,
            "operator_costs": False,
            "snapshot": {"path": path, "sha256": "0" * 64},
            "arms": [{"name": "baseline"}],
            "clauses": [{"id": "a", "rule": "min_trades", "n": 30}],
            "prior": "Expected rejected; a fixture, not a registration.",
            "failure_modes": ["fixture"]}


# ------------------------------------------------------------- what it blocks

def test_a_new_EDGE_claim_on_a_frozen_snapshot_is_refused():
    msg = rgate.freeze_violation(_spec("EDGE"))
    assert msg is not None
    assert "FROZEN" in msg


def test_the_refusal_says_what_lifts_it():
    """A refusal that does not name its exit turns into a permanent blocker
    somebody eventually deletes. It must point at the probe and at the
    override."""
    msg = rgate.freeze_violation(_spec("EDGE"))
    assert "probe_delisted_coverage.py" in msg
    assert "--override-freeze" in msg
    assert "DIAGNOSTIC" in msg


@pytest.mark.parametrize("claim", ["DIAGNOSTIC", "METHOD", "CAPACITY"])
def test_non_EDGE_claims_are_NOT_frozen(claim):
    """The paired half. §49-style measurement and §41-style capacity work must
    continue — none of them claims an edge, and a freeze that stopped all
    research would be worked around rather than respected."""
    assert rgate.freeze_violation(_spec(claim)) is None


def test_a_snapshot_outside_the_frozen_directories_is_now_RETIRED_not_allowed():
    """SUPERSEDED BY §77 (2026-08-23), and kept rather than deleted.

    This test used to assert `is None` — that the freeze was about the
    survivor-selected snapshots and "not about EDGE claims in general", so a
    vendor snapshot passing the probe would register normally. That was the
    correct reading of §52 and §57, both of which were EXHAUSTION arguments
    about a specific dataset.

    §77 retired the claim type itself. The licence to claim an edge no longer
    depends on which folder the data sits in, which is the whole point: it
    previously depended on what had been BOUGHT rather than what had been
    SHOWN.

    The assertion is inverted with its history attached rather than removed,
    because a deleted test takes the reason with it."""
    v = rgate.freeze_violation(
        _spec("EDGE", "data/vendor_pit/sp500_pit_2000-2026.json.gz"))
    assert v is not None and "RETIRED" in v
    assert "FROZEN" not in v, "this must refuse as retirement, not as a venue freeze"


def test_the_check_does_not_need_the_file_to_exist():
    """Path-based on purpose: the rule must read the same on a machine that has
    not downloaded 100MB of snapshots."""
    assert rgate.freeze_violation(
        _spec("EDGE", "data/snapshots/does_not_exist.json.gz")) is not None


# --------------------------------------------- §57: the certified venue closed too

PIT = "data/pit/bars_etf_2000-01-01_2006-12-31.json.gz"


def test_an_EDGE_claim_on_the_CERTIFIED_data_is_refused_too():
    """The whole point of Phase 10 was to build a venue where an EDGE claim
    would be honest, and §57 then spent it: the incumbent lost to SPY in four
    periods of four, against a reading rule committed before the run.

    This is the test that makes that rule mechanical. Without it the rule is
    prose in a markdown file, which is what §23's monotonicity lesson was for
    thirty-one sections."""
    msg = rgate.freeze_violation(_spec("EDGE", PIT))
    assert msg is not None
    assert "FROZEN" in msg and "§57" in msg


def test_the_pit_refusal_does_not_blame_the_data():
    """A reader who is told `data/pit/` is frozen will assume the data is bad
    and go looking for a better source — which is exactly wrong, and would
    throw away the one clean universe this project owns. The refusal has to say
    so in its own text, and name its own exit."""
    msg = rgate.freeze_violation(_spec("EDGE", PIT))
    assert "NOT survivor-selected" in msg
    assert "--override-freeze" in msg
    assert "DIAGNOSTIC" in msg


def test_the_two_freezes_give_DIFFERENT_reasons():
    """Not cosmetic. `data/snapshots/` is frozen because the data is biased and
    `data/pit/` because the licence is spent; conflating them would tell a
    future reader that §56's probe failed, which it did not. A single shared
    message would pass a `"FROZEN" in msg` assertion while destroying the
    distinction the two sections paid for."""
    survivorship = rgate.freeze_violation(_spec("EDGE", FROZEN))
    spent = rgate.freeze_violation(_spec("EDGE", PIT))
    assert survivorship != spent
    assert "probe_delisted_coverage.py" in survivorship
    assert "probe_delisted_coverage.py" not in spent


@pytest.mark.parametrize("claim", ["DIAGNOSTIC", "METHOD", "CAPACITY"])
def test_the_pit_freeze_stops_EDGE_and_nothing_else(claim):
    """§58 and §59 are DIAGNOSTIC and must register against this data without
    an override. A freeze that also blocked measurement would end research on
    the only certified universe in the repository."""
    assert rgate.freeze_violation(_spec(claim, PIT)) is None


def test_a_pit_LOOKALIKE_directory_is_not_frozen_by_s57():
    """The ORIGINAL INTENT SURVIVES §77, and is what this still tests.

    `data/pit_v2/` is a different directory from `data/pit/`, and the check is
    prefix matching rather than substring — a lookalike must not inherit §57's
    refusal by accident. That is still true and still worth pinning.

    What changed is the outcome, not the mechanism: it now refuses under §77's
    retirement instead of registering. So the assertion moves from "allowed" to
    "refused for the RIGHT REASON", which is a sharper test than the original —
    it would catch a sloppy `in` replacing `startswith`, which `is None` never
    could once everything refuses."""
    v = rgate.freeze_violation(_spec("EDGE", "data/pit_v2/bars.json.gz"))
    assert v is not None
    assert "RETIRED" in v
    # Not `"§57" not in v` — §77's message CITES §57 in its evidence
    # paragraph, which is correct. What must be absent is §57's own refusal
    # HEADLINE, which is what a substring match would produce.
    assert "FROZEN (§57)" not in v, (
        "a data/pit LOOKALIKE inherited §57's freeze — prefix matching has "
        "become substring matching")


# ------------------------------------------ end-to-end through the CLI

def _run(*args, cwd=None):
    # sys.executable, not a hardcoded .venv path — the repo's own convention
    # (tests/test_alert_delivery.py, test_alerts_are_silent_in_tests.py). CI
    # runs a bare interpreter with no .venv, and hardcoding one made these
    # tests pass locally and fail in CI, which is the worst of both.
    return subprocess.run(
        [sys.executable, "scripts/register_gate.py", *args],
        capture_output=True, text=True, cwd=cwd or ".")


def test_cli_refuses_a_new_EDGE_spec_and_exits_nonzero(tmp_path):
    spec = _spec("EDGE")
    spec["id"] = "zz_freeze_probe"
    import yaml
    with open("research/specs/zz_freeze_probe.yaml", "w") as f:
        yaml.safe_dump(spec, f)
    try:
        r = _run("zz_freeze_probe",
                 "--registrations", str(tmp_path / "reg.jsonl"))
        assert r.returncode != 0
        assert "FROZEN" in r.stderr or "FROZEN" in r.stdout
        assert not (tmp_path / "reg.jsonl").exists()
    finally:
        import os
        os.remove("research/specs/zz_freeze_probe.yaml")


def test_cli_records_the_reason_when_overridden(tmp_path):
    """THE POINT OF THE OVERRIDE. It is not a bypass — it writes the excuse into
    the permanent record next to the claim, so a future reader sees both."""
    spec = _spec("EDGE")
    spec["id"] = "zz_freeze_probe"
    import os

    import yaml
    with open("research/specs/zz_freeze_probe.yaml", "w") as f:
        yaml.safe_dump(spec, f)
    reg = tmp_path / "reg.jsonl"
    try:
        r = _run("zz_freeze_probe", "--registrations", str(reg),
                 "--override-freeze",
                 "Vendor snapshot with verified delisted coverage; the "
                 "path check cannot see that.")
        assert r.returncode == 0, r.stderr
        rec = json.loads(reg.read_text().strip().splitlines()[-1])
        assert "verified delisted coverage" in rec["freeze_override"]
    finally:
        os.remove("research/specs/zz_freeze_probe.yaml")


def test_cli_rejects_a_token_override_reason(tmp_path):
    """Its paired half. An override that accepted "x" would be a bypass with
    extra typing, and the audit trail would record nothing worth reading."""
    spec = _spec("EDGE")
    spec["id"] = "zz_freeze_probe"
    import os

    import yaml
    with open("research/specs/zz_freeze_probe.yaml", "w") as f:
        yaml.safe_dump(spec, f)
    try:
        r = _run("zz_freeze_probe", "--registrations", str(tmp_path / "r.jsonl"),
                 "--override-freeze", "because")
        assert r.returncode != 0
        assert "not a token" in (r.stderr + r.stdout)
    finally:
        os.remove("research/specs/zz_freeze_probe.yaml")


def test_an_ALREADY_REGISTERED_spec_still_re_registers_idempotently(tmp_path):
    """The freeze must not become an outage. s50a is a registered EDGE claim on
    a frozen snapshot; re-running register_gate on it must still report
    'already registered, unchanged' rather than refusing, or every historical
    registration becomes unreproducible."""
    r = _run("s50a")
    assert r.returncode == 0, r.stderr
    assert "already registered, unchanged" in r.stdout


# -------------------------------------------------------------- the probe

def _rows(dates):
    return [(d, 100.0) for d in dates]


def test_probe_check2_catches_ticker_reuse():
    """THE LOAD-BEARING PROBE ASSERTION. SBNY's real shape: bars exist, but all
    of them postdate the delisting. Check 1 alone would call that covered."""
    def fetch(t):
        if t == probe.CONTROL:
            return _rows(["2020-01-02", "2026-07-09"])
        return _rows(["2024-08-15", "2026-07-09"])   # every bar after the death
    res = probe.probe(fetch)
    assert res["check2_no_ticker_reuse"] is False
    assert res["verdict"] == "REFUSED"
    assert all(t["reused"] for t in res["tickers"])
    # A REUSED TICKER IS ALSO NOT COVERED, and check 1 must say so. Found by a
    # surviving mutation: scoring coverage on `len(rows) > 0` instead of
    # `len(pre) > 0` left the verdict correct (check 2 still fired) while the
    # report printed "coverage: PASS" for a name whose every bar postdates its
    # own death. The verdict was right for the wrong reason, which is the kind
    # of thing that survives a refactor.
    assert res["check1_coverage"] is False
    assert not any(t["covered"] for t in res["tickers"])


def test_probe_check1_catches_missing_history():
    def fetch(t):
        return _rows(["2020-01-02"]) if t == probe.CONTROL else []
    res = probe.probe(fetch)
    assert res["check1_coverage"] is False
    assert res["verdict"] == "REFUSED"
    assert not any(t["reused"] for t in res["tickers"])   # absent != reused


def test_probe_PASSES_when_pre_delisting_history_exists():
    """The paired half, and the acceptance test for any future vendor. A probe
    that could never pass would just be a comment."""
    def fetch(t):
        return _rows(["2020-01-02", "2021-06-01", "2022-01-03"])
    res = probe.probe(fetch)
    assert res["check1_coverage"] is True
    assert res["check2_no_ticker_reuse"] is True
    assert res["verdict"] == "PASS"


def test_probe_refuses_to_conclude_anything_if_the_CONTROL_fails():
    """If everything returns nothing, the honest reading may be 'the network is
    down', not 'the vendor lacks delisted data'. §33 RUN 1's rule applied to a
    probe: an unmeasured question is not an answered one."""
    res = probe.probe(lambda t: [])
    assert res["verdict"] == "UNDETERMINED"
    assert res["check1_coverage"] is None
    assert "control" in res["reason"]


def test_every_probed_ticker_carries_a_delisting_date():
    """Check 2 is only checkable because each entry knows when the company
    stopped being itself. A bare ticker list could not distinguish reuse from
    coverage."""
    for ticker, delisted_on, what in probe.DELISTED:
        assert len(delisted_on) == 10 and delisted_on[4] == "-"
        assert what.strip()
