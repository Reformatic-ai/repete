"""You cannot move a goalpost after seeing the data.

Why this file exists
--------------------
Every rejection in `knowledge/backtest_candidates.md` means something only
because the claim, arms, pass mark and honest prior were committed BEFORE the
runner existed. That has been enforced by discipline and a git commit. Once
gates are declared as data and executed by a generic runner, discipline is not
enough — a one-character edit to a threshold would silently rewrite what
"passing" meant.

§33 RUN 1 is the standing reminder of the cost: it printed VALIDATED and was an
artifact of a runner detail nobody had frozen. It stayed in the record instead
of being tidied away.

So the freeze is mechanical, and these are the tests that make it real:

  * a spec that was never registered cannot be scored
  * a spec altered after registration cannot be scored, AND the refusal names
    the field that moved — "clauses.2.pp: 1.0 -> 3.0" is the finding; "the spec
    was altered" only starts a search
  * reformatting is NOT alteration; a freeze that cries wolf gets bypassed
  * re-registering is fine before a verdict exists and refused after it

`test_a_registered_unaltered_spec_is_accepted` is the permissive half. Without
it, a `check_frozen` that raised unconditionally would pass every test above.
"""
import copy
import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import gatespec as gs
import run_gate
import register_gate

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _spec(**over):
    spec = {
        # METHOD, not EDGE, since §77 (2026-08-23) retired EDGE on every
        # venue. This file is about RE-REGISTRATION semantics — editing a spec
        # before any data is seen is authoring, not tampering — and the claim
        # type was always incidental to that. A fixture that can no longer
        # register is testing §77 by accident instead of the thing it names.
        "id": "t1", "claim": "METHOD", "title": "a test claim",
        "snapshot": {"path": "bars.json.gz", "sha256": "a" * 64},
        "cash": 100000.0, "bonferroni_k": 8,
        # W2-1: register_gate.py refuses a spec without this, so the fixture
        # carries it. Every spec frozen from 2026-07-29 onward declares whether
        # it models the judge; §35-§41 predate the field and omit it.
        "judge_model": True,
        # 2026-10-28: likewise refused without its operator cost declared
        # (owner decision: gates read after-cost results first). Declared
        # gross here; this file is about re-registration, not cost.
        "operator_costs": False,
        "arms": [{"name": "baseline"},
                 {"name": "cand", "set": {"risk.max_trades_per_day": 10}}],
        "clauses": [{"id": "a", "rule": "pf_gt_baseline"},
                    {"id": "b", "rule": "maxdd_within", "pp": 1.0},
                    {"id": "c", "rule": "significantly_better"}],
        "prior": "EDGE claims are 0 for 8; this almost certainly fails too.",
        "failure_modes": ["survivorship bias inflates the candidate"],
    }
    spec.update(over)
    return spec


@pytest.fixture
def bench(tmp_path):
    """A spec on disk, a registrations file, and a verdicts file."""
    d = tmp_path / "specs"
    d.mkdir()
    spec = _spec()
    (d / "t1.yaml").write_text(yaml.safe_dump(spec))
    return {"dir": str(d), "spec": spec,
            "reg": str(tmp_path / "registrations.jsonl"),
            "verdicts": str(tmp_path / "verdicts.jsonl")}


def _register(bench, spec=None):
    spec = spec or bench["spec"]
    rec = {"id": spec["id"], "spec_sha256": gs.canonical_sha256(spec),
           "registered_at": "2026-07-28T00:00:00+00:00", "spec": spec}
    with open(bench["reg"], "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


# ---- the three refusals ----

def test_an_unregistered_spec_cannot_be_scored(bench):
    with pytest.raises(SystemExit) as e:
        run_gate.check_frozen(bench["spec"], bench["reg"])
    assert "not registered" in str(e.value)


def test_an_altered_spec_cannot_be_scored(bench):
    _register(bench)
    moved = copy.deepcopy(bench["spec"])
    moved["clauses"][1]["pp"] = 3.0        # widen the drawdown allowance
    with pytest.raises(SystemExit) as e:
        run_gate.check_frozen(moved, bench["reg"])
    assert "changed after it was registered" in str(e.value)


def test_the_refusal_names_the_field_that_moved(bench):
    """The whole value of the guard. A refusal that says only 'something
    changed' sends someone hunting; this one has to point at the goalpost."""
    _register(bench)
    moved = copy.deepcopy(bench["spec"])
    moved["clauses"][1]["pp"] = 3.0
    with pytest.raises(SystemExit) as e:
        run_gate.check_frozen(moved, bench["reg"])
    msg = str(e.value)
    assert "clauses.1.pp" in msg
    assert "1.0" in msg and "3.0" in msg


def test_snapshot_drift_is_refused(bench, tmp_path):
    data = tmp_path / "bars.json.gz"
    data.write_bytes(b"not the registered snapshot")
    spec = copy.deepcopy(bench["spec"])
    spec["snapshot"]["path"] = str(data)
    with pytest.raises(SystemExit) as e:
        run_gate.verify_data(spec)
    assert "SNAPSHOT DRIFT" in str(e.value)


# ---- but reformatting is not tampering ----

def test_reordering_keys_does_not_break_the_freeze(bench):
    """A freeze that cries wolf is one people learn to bypass. The hash tracks
    MEANING — it is taken over the parsed structure, not the file bytes."""
    _register(bench)
    reordered = json.loads(json.dumps(bench["spec"]))
    reordered["arms"] = list(reordered["arms"])
    reordered = dict(reversed(list(reordered.items())))
    run_gate.check_frozen(reordered, bench["reg"])      # must not raise


def test_yaml_layout_does_not_change_the_hash(bench, tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(yaml.safe_dump(bench["spec"], default_flow_style=False))
    b.write_text(yaml.safe_dump(bench["spec"], default_flow_style=True,
                                width=40))
    assert gs.canonical_sha256(gs.load(str(a))) == \
        gs.canonical_sha256(gs.load(str(b)))


def test_a_registered_unaltered_spec_is_accepted(bench):
    """The permissive half. Without it, a check_frozen() that always raised
    would satisfy every refusal test above."""
    rec = _register(bench)
    got = run_gate.check_frozen(bench["spec"], bench["reg"])
    assert got["spec_sha256"] == rec["spec_sha256"]


# ---- re-registration ----

def test_reregistering_before_a_verdict_is_allowed(bench, monkeypatch, capsys):
    """Editing a spec before any data is seen is authoring, not tampering."""
    monkeypatch.chdir(os.path.dirname(bench["dir"]))
    _register(bench)
    moved = copy.deepcopy(bench["spec"])
    moved["clauses"][1]["pp"] = 3.0
    with open(os.path.join(bench["dir"], "t1.yaml"), "w") as f:
        yaml.safe_dump(moved, f)

    monkeypatch.setattr(register_gate, "SPEC_DIR", bench["dir"])
    monkeypatch.setattr(sys, "argv", ["register_gate.py", "t1",
                                      "--registrations", bench["reg"],
                                      "--verdicts", bench["verdicts"]])
    assert register_gate.main() == 0
    assert "re-registering" in capsys.readouterr().out
    # Both rows survive: history of what was promised stays readable.
    with open(bench["reg"]) as f:
        assert len([l for l in f if l.strip()]) == 2


def test_reregistering_after_a_verdict_is_refused(bench, monkeypatch):
    """Once the result is known, editing the claim IS moving the goalpost."""
    _register(bench)
    with open(bench["verdicts"], "w") as f:
        f.write(json.dumps({"id": "t1", "passed": False}) + "\n")
    moved = copy.deepcopy(bench["spec"])
    moved["clauses"][1]["pp"] = 3.0
    with open(os.path.join(bench["dir"], "t1.yaml"), "w") as f:
        yaml.safe_dump(moved, f)

    monkeypatch.setattr(register_gate, "SPEC_DIR", bench["dir"])
    monkeypatch.setattr(sys, "argv", ["register_gate.py", "t1",
                                      "--registrations", bench["reg"],
                                      "--verdicts", bench["verdicts"]])
    with pytest.raises(SystemExit) as e:
        register_gate.main()
    msg = str(e.value)
    assert "REFUSING to re-register" in msg
    assert "clauses.1.pp" in msg          # names the goalpost that moved


# ---- validation rejects a spec that is not a pre-registration ----

@pytest.mark.parametrize("field", ["prior", "failure_modes", "arms", "clauses"])
def test_a_spec_missing_a_required_field_is_rejected(field):
    spec = _spec()
    del spec[field]
    with pytest.raises(gs.SpecError):
        gs.validate(spec)


def test_a_vague_prior_is_rejected():
    """'it might work' is not a prior. The field carries no machine meaning —
    it exists so the author states what they expected BEFORE the number lands,
    which is the only thing that makes a surprise legible later."""
    with pytest.raises(gs.SpecError, match="prior"):
        gs.validate(_spec(prior="might work"))


def test_a_spec_with_no_named_failure_mode_is_rejected():
    with pytest.raises(gs.SpecError, match="failure_modes"):
        gs.validate(_spec(failure_modes=[]))


def test_a_truncated_snapshot_hash_is_rejected():
    """§31 registered a 16-char prefix. A prefix is fine for a human reading a
    log and useless as a guard — collisions aside, it invites pasting the short
    form and never noticing the full file changed."""
    with pytest.raises(gs.SpecError, match="64-char"):
        gs.validate(_spec(snapshot={"path": "b.gz", "sha256": "abc123"}))


def test_a_single_arm_is_rejected():
    with pytest.raises(gs.SpecError, match="at least two arms"):
        gs.validate(_spec(arms=[{"name": "baseline"}]))


def test_an_unknown_clause_rule_is_rejected():
    with pytest.raises(gs.SpecError, match="unknown clause rule"):
        gs.validate(_spec(clauses=[{"id": "a", "rule": "vibes"}]))


# ---- overlays ----

def test_shared_applies_to_every_arm_before_its_own_overlay():
    """Every arm sized identically, so a difference between arms can never be a
    sizing difference — §35's SHARED_RISK made this explicit and the spec
    format has to keep it."""
    spec = _spec(shared={"set": {"risk.risk_per_trade_pct": 2.0}})
    base = {"risk": {"risk_per_trade_pct": 8.0, "max_trades_per_day": 50},
            "strategies": {"tsmom": {"enabled": True}}}
    for arm in spec["arms"]:
        cfg = gs.apply_overlay(base, spec, arm)
        assert cfg["risk"]["risk_per_trade_pct"] == 2.0
    cand = gs.apply_overlay(base, spec, spec["arms"][1])
    assert cand["risk"]["max_trades_per_day"] == 10


def test_replace_swaps_a_subtree_so_nothing_leaks():
    """§35 replaces `strategies` outright. With merge semantics the baseline's
    enabled strategies would leak into the candidate and the arms would no
    longer be the two things the registration named."""
    spec = _spec(arms=[{"name": "baseline"},
                       {"name": "c", "replace": {"strategies":
                                                 {"xsmom": {"enabled": True}}}}])
    base = {"risk": {}, "strategies": {"tsmom": {"enabled": True},
                                       "meanrev": {"enabled": True}}}
    cfg = gs.apply_overlay(base, spec, spec["arms"][1])
    assert cfg["strategies"] == {"xsmom": {"enabled": True}}


def test_the_base_config_is_never_mutated():
    base = {"risk": {"max_trades_per_day": 50}, "strategies": {}}
    before = copy.deepcopy(base)
    spec = _spec()
    for arm in spec["arms"]:
        gs.apply_overlay(base, spec, arm)
    assert base == before


# ---- one arm is legal only when nothing is being compared ----

def _one_arm(clauses):
    return {"id": "t", "claim": "EDGE", "title": "t",
            "snapshot": {"path": "b.gz", "sha256": "a" * 64},
            "arms": [{"name": "baseline"}], "clauses": clauses,
            "prior": "The incumbent probably fails its own enablement gate.",
            "failure_modes": ["survivorship flatters any long strategy"]}


def test_a_single_arm_spec_is_legal_when_no_clause_compares():
    """§39 puts the INCUMBENT on trial against benchmarks from its own run.
    There is no second arm by design, and the old blanket rule would have
    blocked it."""
    gs.validate(_one_arm([{"id": "a", "rule": "enablement_gate"},
                          {"id": "b", "rule": "beats_exposure_matched"},
                          {"id": "c", "rule": "beats_buy_hold"},
                          {"id": "d", "rule": "min_trades", "n": 30}]))


@pytest.mark.parametrize("rule", ["pf_gt_baseline", "significantly_better",
                                  "not_worse"])
def test_a_single_arm_spec_with_a_comparative_clause_is_still_rejected(rule):
    """The relaxation must not become a hole. Anything measured against the
    `baseline` ARM still needs a second arm to be one."""
    with pytest.raises(gs.SpecError, match="at least two arms"):
        gs.validate(_one_arm([{"id": "x", "rule": rule}]))


def test_the_rejection_names_the_offending_clauses():
    with pytest.raises(gs.SpecError) as e:
        gs.validate(_one_arm([{"id": "a", "rule": "enablement_gate"},
                              {"id": "x", "rule": "pf_gt_baseline"}]))
    assert "pf_gt_baseline" in str(e.value)
    assert "enablement_gate" not in str(e.value)   # only the ones at fault


def test_maxdd_within_counts_as_comparative():
    """It reads baseline's drawdown, so it needs a baseline to read."""
    with pytest.raises(gs.SpecError, match="at least two arms"):
        gs.validate(_one_arm([{"id": "c", "rule": "maxdd_within", "pp": 1.0}]))


@pytest.mark.parametrize("spec_id", ["s35", "s37", "s38"])
def test_existing_registered_specs_still_validate(spec_id):
    """The relaxation must not disturb a spec already frozen and scored."""
    gs.validate(gs.load(os.path.join(ROOT, "research", "specs",
                                     f"{spec_id}.yaml")))
