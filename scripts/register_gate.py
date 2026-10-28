#!/usr/bin/env python3
"""Freeze a pre-registration before it is run.
`python scripts/register_gate.py <spec-id>`

Appends `{id, spec_sha256, registered_at, spec}` to research/registrations.jsonl.
That row is the goalpost. `run_gate.py` will not score a spec whose hash does
not match it.

The full spec is stored, not just its hash, for two reasons: a mismatch can then
report WHICH field moved rather than only that something did, and the
registration alone is enough to re-execute the claim years later.

Re-registering
--------------
Allowed while a claim has no verdict — thinking changes, and a spec edited
before any data is seen is just authoring. It is REFUSED once a verdict exists
for that id, because at that point the result is known and editing the pass mark
is the exact failure this whole apparatus exists to prevent. §33 RUN 1 printed
VALIDATED and was an artifact; it stayed in the record instead of being tidied
away, and this is the mechanical version of that rule.

Every re-registration appends a new row. Nothing is ever rewritten, so the
history of what was promised stays readable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gatespec as gs                                       # noqa: E402

SPEC_DIR = "research/specs"
VERDICTS = "research/verdicts.jsonl"

# --------------------------------------------------------------- §52 EDGE FREEZE
#
# Every snapshot under this directory is survivor-selected. `build_snapshot.py`
# sources its universe from `index_constituents()`, which reads CURRENT
# Wikipedia membership — so the companies that failed are absent from all of
# them by construction, not just from the four §48 happened to measure.
#
# §48 sized the bias (+130.06pp on 2000-2006: equal-weight +138.74% against
# SPY's +8.68%). §51 then produced this project's only EDGE pass and showed the
# bias was large enough to explain it — +200.28pp on the 38-name universe,
# because those names are in config.yaml precisely BECAUSE they are today's
# winners.
#
# `scripts/probe_delisted_coverage.py` asked whether the fix is buildable and
# REFUSED: yfinance serves no history for SIVB, FRC, TWTR or ATVI, and returns
# SBNY bars that all postdate the bank's seizure by seventeen months.
#
# So registering further EDGE claims on this data buys more chances at a false
# positive without buying information. That is §33's argument — "continuing to
# hunt arms is simply buying more chances for a false positive" — applied to the
# DATA rather than to the selection procedure.
#
# DIAGNOSTIC, METHOD and CAPACITY are untouched. None of them claims an edge,
# and §41's CAPACITY question (can the breaker re-close) is about deployment
# rather than returns, so survivorship does not decide it.
#
# The override exists because a wall gets edited out of the script, while a
# speed bump with an audit trail does not. Its reason is written into the
# registration row, so a future reader sees the claim AND the excuse together.
FROZEN_SNAPSHOT_DIR = "data/snapshots"
FROZEN_CLAIMS = ("EDGE",)


def _s52_reason(claim: str, path: str) -> str:
    """§52's refusal. NOT to be reworded — `test_edge_freeze.py` asserts on this
    text, and a control whose message drifts is a control nobody trusts."""
    return (f"{claim} claims on {FROZEN_SNAPSHOT_DIR}/ are FROZEN "
            f"(§52).\n\n"
            f"  snapshot: {path}\n\n"
            f"Every snapshot there is built from CURRENT index membership, so "
            f"the companies\nthat failed are missing by construction. §48 sized "
            f"that bias at up to +130pp;\n§51 showed it was large enough to "
            f"explain this project's only EDGE pass.\n"
            f"`scripts/probe_delisted_coverage.py` then REFUSED: the vendor "
            f"serves no\npre-delisting history for SIVB/FRC/TWTR/ATVI, and "
            f"SBNY's bars all postdate its\nseizure by seventeen months.\n\n"
            f"Registering another EDGE claim on this data buys more chances at "
            f"a false\npositive without buying information.\n\n"
            f"Still allowed : DIAGNOSTIC, METHOD, CAPACITY\n"
            f"Lifts the freeze: a source that passes "
            f"probe_delisted_coverage.py\n"
            f"To proceed anyway:\n"
            f"  register_gate.py <id> --override-freeze \"<why this claim is "
            f"sound regardless>\"")


# --------------------------------------------------------------- §57 READING RULE
#
# `data/pit/` is the OPPOSITE kind of directory: §56's probe certified that every
# fund in it returns history from its documented inception unbroken to the
# present, while correctly refusing to call four known-dead symbols healthy.
# Nothing there is survivor-selected. It was built, in Phase 10, precisely so an
# EDGE claim would have somewhere honest to live.
#
# It is frozen anyway, and for a reason that is not survivorship.
#
# §57 registered a reading rule BEFORE running: "fails clause (b) in three or
# more periods — the ensemble does not beat a clean index on this universe, and
# the ambiguity resolves against it. It licenses NO further EDGE registration on
# this universe." The ensemble then lost to SPY in four periods of four.
#
# That rule was written down while the outcome was unknown, which is the only
# time such a rule means anything. Leaving it as prose in a markdown file is the
# shape this repository has now been caught by twice: §23's monotonicity lesson
# sat in prose for thirty-one sections until §55 made it a clause, and `ci.yml`
# states the standard outright — a check that cannot fail is worse than no
# check. So the rule is executed here rather than remembered.
#
# NOT A CLAIM THAT THE DATA IS BAD. The data is the best this project has. What
# is exhausted is the licence to make an EDGE claim against it, and the same
# audited override lifts this one as lifts §52's.
FROZEN_PIT_DIR = "data/pit"


def _s57_reason(claim: str, path: str) -> str:
    return (f"{claim} claims on {FROZEN_PIT_DIR}/ are FROZEN (§57).\n\n"
            f"  snapshot: {path}\n\n"
            f"This data is NOT survivor-selected — §56's probe certified it, "
            f"and it was\nbuilt so an EDGE claim would have somewhere honest to "
            f"live. The freeze is\nnot about the data.\n\n"
            f"§57 committed a reading rule BEFORE the run: failing "
            f"`beats_benchmark_symbol`\nin three or more of the four periods "
            f"licenses no further EDGE registration on\nthis universe. The "
            f"incumbent ensemble then failed it in FOUR of four.\n\n"
            f"Honouring a rule only when its outcome is convenient is the "
            f"selection this\napparatus exists to prevent.\n\n"
            f"Still allowed : DIAGNOSTIC, METHOD, CAPACITY\n"
            f"Lifts the freeze: a pre-registration that supersedes §57's "
            f"reading rule on\nits own terms, argued before its data is seen\n"
            f"To proceed anyway:\n"
            f"  register_gate.py <id> --override-freeze \"<why this claim is "
            f"sound regardless>\"")


#: directory prefix -> the function that explains why EDGE is refused there.
#: A mapping rather than two `if`s so a third frozen venue is a data change,
#: and so `freeze_violation` cannot grow a branch that silently matches nothing.
FROZEN_SNAPSHOT_DIRS = {
    FROZEN_SNAPSHOT_DIR: _s52_reason,
    FROZEN_PIT_DIR: _s57_reason,
}


def _retired_reason(claim: str, path: str) -> str:
    """§77's refusal: EDGE is retired as a claim type, on every venue.

    The two venue freezes above were EXHAUSTION — each said "this data has
    given what it can". Both were escapable the same way: point a spec at a
    directory neither prefix matches. Buying a new survivorship-free dataset
    would have reopened EDGE without anyone deciding to reopen it, which made
    the freeze a statement about inventory rather than about the project.

    This is not that. It is a decision that the programme stops making edge
    claims at all.
    """
    return (f"{claim} claims are RETIRED (§77, 2026-08-23).\n\n"
            f"  snapshot: {path}\n\n"
            f"This is not a freeze on one venue. §52 froze data/snapshots/ and "
            f"§57 froze\ndata/pit/, and both were about a dataset being spent. "
            f"Either was escapable by\npointing a spec somewhere new, so the "
            f"licence to claim an edge depended on\nwhat had been bought "
            f"rather than on what had been shown.\n\n"
            f"16 EDGE registrations produced 2 passes. §51 measured +200.28pp "
            f"of\nsurvivorship inflation on the first, large enough to explain "
            f"it outright.\n§75 re-measured the second with the judge on and "
            f"ONE period of four\nsurvived. §34 found the selection procedure "
            f"itself scores no better than\nrandom, and §76's decay monitor "
            f"returned INDISTINGUISHABLE_FROM_RANDOM on\nthe live record.\n\n"
            f"The apparatus that established all of that is the asset. The "
            f"edge claim is\nnot, and continuing to spend budget against it "
            f"would be the selection this\nproject exists to prevent, aimed "
            f"at itself.\n\n"
            f"Still allowed : DIAGNOSTIC, METHOD, CAPACITY — the claim types "
            f"that describe\n                how the machinery behaves, which "
            f"is what is being built now\n"
            f"Lifts it      : a pre-registration arguing WHY the programme "
            f"should resume\n                making edge claims, argued before "
            f"its data is seen\n"
            f"To proceed anyway:\n"
            f"  register_gate.py <id> --override-freeze \"<why this claim is "
            f"sound regardless>\"")


def freeze_violation(spec: dict) -> str | None:
    """Why this spec may not be registered, or None if it may.

    Pure and path-based on purpose: it must not depend on the snapshot file
    being present, so the rule reads the same on a machine that has not
    downloaded the data.
    """
    if spec.get("claim") not in FROZEN_CLAIMS:
        return None
    path = (spec.get("snapshot") or {}).get("path", "")
    normalised = path.replace("\\", "/")
    for prefix, reason in FROZEN_SNAPSHOT_DIRS.items():
        if normalised.startswith(prefix + "/"):
            return reason(spec["claim"], path)
    # §77: EDGE is retired on EVERY venue, not only the two that were spent.
    #
    # This return used to be `None` — an EDGE claim pointed at any directory
    # neither prefix matched registered cleanly. So the licence to claim an
    # edge depended on what data had been BOUGHT rather than on what had been
    # SHOWN, and a new dataset would have reopened it without anyone deciding
    # to. Retiring the claim is a decision; the freezes were an inventory.
    return _retired_reason(spec["claim"], path)


def spec_path(spec_id: str) -> str:
    return os.path.join(SPEC_DIR, f"{spec_id}.yaml")


def has_verdict(spec_id: str, path: str = VERDICTS) -> bool:
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return any(json.loads(l)["id"] == spec_id for l in f if l.strip())


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec_id")
    p.add_argument("--registrations", default=gs.REGISTRATIONS)
    p.add_argument("--verdicts", default=VERDICTS)
    p.add_argument("--override-freeze", metavar="REASON", default=None,
                   help="register despite the §52 EDGE freeze. The reason is "
                        "recorded in the registration row.")
    args = p.parse_args()

    spec = gs.load(spec_path(args.spec_id))
    if spec["id"] != args.spec_id:
        raise SystemExit(f"spec id {spec['id']!r} does not match filename "
                         f"{args.spec_id!r}")

    # W2-1 (2026-07-29). The judge is not optional to THINK about, only to use.
    #
    # §29 built src/judge_model.py to close the live half of divergence #8 and
    # shipped it `enabled: false`. Nothing in run_gate.py ever turned it on, so
    # §35, §37, §38, §39, §40 and §41 all scored a bot with no judge while the
    # live bot cut 58% of its buys. That went unnoticed for three days because
    # a judge-less run and a judge-on run are indistinguishable from the
    # outside: both just print numbers.
    #
    # Refusing at REGISTRATION rather than at load is deliberate. Old specs keep
    # their frozen hashes and re-execute byte-identically — the record stays
    # reproducible — while no NEW claim can be frozen without its author having
    # answered the question out loud, in the file, before seeing any data.
    if "judge_model" not in spec:
        raise SystemExit(
            f"REFUSING to register {args.spec_id}: it does not declare "
            f"`judge_model`.\n\n"
            f"Add ONE of these at the top level of "
            f"{spec_path(args.spec_id)}:\n\n"
            f"  judge_model: true    # model the judge, as live does\n"
            f"  judge_model: false   # deliberately without it — say why in "
            f"`prior`\n\n"
            f"There is no default, because the default is what went wrong. "
            f"§35-§41 were\nall scored judge-less by accident, and a silent "
            f"`false` here would let that\nrecur while the spec looked "
            f"complete.")

    digest = gs.canonical_sha256(spec)
    prior = gs.registrations(args.registrations).get(args.spec_id)

    if prior and prior["spec_sha256"] == digest:
        print(f"{args.spec_id}: already registered, unchanged "
              f"({digest[:16]}…, {prior['registered_at'][:19]})")
        return 0

    if prior:
        if has_verdict(args.spec_id, args.verdicts):
            changed = gs.diff_fields(prior["spec"], spec)
            raise SystemExit(
                f"REFUSING to re-register {args.spec_id}: a verdict already "
                f"exists for it.\n\nChanged since registration:\n  "
                + "\n  ".join(changed or ["(structure differs)"])
                + "\n\nThe result is known, so editing the claim now is moving "
                  "the goalpost.\nRegister a NEW id and state in its prior what "
                  "the earlier run showed.")
        print(f"{args.spec_id}: re-registering (no verdict yet, so this is "
              f"still authoring)")
        for line in gs.diff_fields(prior["spec"], spec):
            print(f"    {line}")


    # §52. Deliberately AFTER the already-registered-unchanged early return and
    # after the verdict check: an existing registration must stay re-runnable
    # and idempotent, and a moved goalpost must still report as a moved
    # goalpost rather than as a freeze. This only ever blocks a NEW claim, or a
    # changed one — which is a new goalpost anyway.
    blocked = freeze_violation(spec)
    if blocked and not args.override_freeze:
        raise SystemExit(f"REFUSING to register {args.spec_id}: {blocked}")
    if blocked:
        reason = args.override_freeze.strip()
        if len(reason) < 20:
            raise SystemExit(
                f"REFUSING to register {args.spec_id}: --override-freeze needs "
                f"a REASON, not a token.\nGot {reason!r}. Write the argument "
                f"you would have to defend to a reader who\nfinds this row in "
                f"registrations.jsonl a year from now.")
        print("  §52 FREEZE OVERRIDDEN — recorded in the registration row:")
        print(f"    {reason}")

    # Owner decision 2026-10-28, quoted exactly: "Yes: make gates, backtests and
    # the drawdown/retire rails use after-cost results first (repete4's
    # override included). Entry cost filters later, bot by bot."
    #
    # So a NEW claim must say what operator cost it charges, in the file, before
    # any data is seen — judge_model's rule, and for its reason: a silent
    # default is how §35-§41 came to be scored judge-less. Placed AFTER the
    # already-registered-unchanged return, the verdict refusal and the freeze
    # above, so every existing registration stays idempotent, every frozen hash
    # stands, and a frozen venue still reports as a freeze; it binds only a
    # spec being frozen now.
    if "operator_costs" not in spec:
        raise SystemExit(
            f"REFUSING to register {args.spec_id}: it does not declare "
            f"`operator_costs`.\n\n"
            f"Owner decision 2026-10-28: gates read AFTER-COST results first. "
            f"Add ONE of these at\nthe top level of "
            f"{spec_path(args.spec_id)}:\n\n"
            f"  operator_costs:       # the cost-models block, every market, "
            f"every value\n"
            f"    equities: {{model: per_share, flat_per_order: 1.0, "
            f"per_share: 0.005,\n"
            f"               min_per_order: 1.0, max_pct_of_value: 1.0}}\n"
            f"    options:  {{...}}   fx: {{...}}   crypto: {{...}}   "
            f"(cost_models.RECOMMENDED)\n"
            f"  operator_costs: false # deliberately gross — say why in "
            f"`prior`\n\n"
            f"There is no default, for the reason judge_model has none.")

    os.makedirs(os.path.dirname(args.registrations) or ".", exist_ok=True)
    rec = {"id": args.spec_id, "spec_sha256": digest,
           "registered_at": datetime.now(timezone.utc).isoformat(),
           "spec": spec}
    if blocked:
        rec["freeze_override"] = args.override_freeze.strip()
    with open(args.registrations, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")

    print(f"registered {args.spec_id}  sha256 {digest[:16]}…")
    print(f"  claim  : {spec['claim']}  |  arms: "
          f"{', '.join(a['name'] for a in spec['arms'])}")
    print(f"  prior  : {spec['prior'].strip().splitlines()[0][:72]}")
    print(f"  K      : {spec.get('bonferroni_k', 1)}")
    print(f"  judge  : {'ON — models the live 58% haircut' if spec['judge_model'] else 'OFF — deliberately judge-less'}")
    _oc = spec["operator_costs"]
    if isinstance(_oc, dict):
        import cost_models
        _oc_txt = f"ON — {cost_models.describe(_oc, 'equities')}"
    else:
        _oc_txt = "OFF — deliberately gross"
    print(f"  cost   : {_oc_txt}")
    print("\nThe pass mark is now frozen. Run it with:")
    print(f"  python scripts/run_gate.py {args.spec_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
