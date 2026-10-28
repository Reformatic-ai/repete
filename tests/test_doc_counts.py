"""The numbers in the front-door docs are regenerated and checked, never typed.

WHY (Phase 8, 2026-08-06)
-------------------------
`README.md` is the artifact. It is also maintained by hand, and by 2026-08-06 it
was wrong in four measured places at once:

    tests               claimed 1,881      actual 1,967 (2,006 with #103)
    tests, again        `# 1881` in the quick-start block
    divergences         claimed 17, four open     actual 18, five open
    closed round-trips  claimed 8                 actual 10

That is the third time the test count has gone stale. A repo whose entire pitch
is "every number carries its benchmark and its sample size" cannot have a front
page that is wrong about its own sample sizes.

`docs/divergences.md` was worse, and this file found it. The prose said
**eighteen** and named **five** open; the summary table listed **fourteen** rows
and showed **one** open, because rows 13-16 had never been added. The table is
the fastest thing in the file to read and therefore the thing most likely to be
read alone — so the most-read summary undercounted the open divergences five to
one. Nothing compared the three statements, so nothing caught it.

WHAT IS DELIBERATELY *NOT* CHECKED HERE
---------------------------------------
The live round-trip count. `memory/ledger.jsonl` is gitignored — it does not
exist in a worktree and it does not exist on a CI runner. A test asserting it
would be skipped everywhere it ever ran, which is the "check that cannot fail"
this repo keeps catching (`ci.yml:55-69` applies the same reasoning to
`check_secret_exposure.py`). So `test_the_live_trade_count_is_checked_only_where
_a_ledger_exists` runs the check when a ledger is present and says **SKIPPED**
out loud when it is not, exactly as the backup drill's off-host check does.

THE EDGE TALLY (added 2026-08-15)
--------------------------------
The bottom half of this file pins the Bonferroni budget the front-door docs
state against `research/registrations.jsonl`, which owns it. It is here because
the *skills* got the same guard first and the docs did not: after §72 took K to
16, `GLOSSARY.md` went on saying **"K = 15 here, and the tally is 1 pass in
15"** in two places and `research/candidates_2026-08.md` quoted it as the
project's base rate. `README.md` had been updated. Nothing compared them.

That is the same defect this file already exists for — a count copied into
prose drifts from the ledger that owns it — with one extra lesson. The guard
that *should* have caught it globbed `.claude/skills/*/SKILL.md`, so the fix
for a stale-number class of bug was itself scoped to a directory. Hence
`test_every_doc_that_states_a_k_is_either_checked_or_deliberately_exempt`
below: a new markdown file that states a K has to be classified before it can
sit in the repo unchecked.
"""
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from edge_tally import (  # noqa: E402
    REGISTRATIONS,
    VERDICTS,
    current_k,
    edge_families,
    passing_family_ceiling,
    stated_ks,
    stated_ms,
    tracked_markdown,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
GLOSSARY = ROOT / "GLOSSARY.md"
DIVERGENCES = ROOT / "docs" / "divergences.md"

WORDS = {14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen",
         18: "Eighteen", 19: "Nineteen", 20: "Twenty", 21: "Twenty-one",
         22: "Twenty-two", 23: "Twenty-three", 24: "Twenty-four",
         25: "Twenty-five", 26: "Twenty-six", 27: "Twenty-seven",
         28: "Twenty-eight", 29: "Twenty-nine", 30: "Thirty"}


# --------------------------------------------------------------------------
# Regenerators. Each computes the figure the same way the claim means it.
# --------------------------------------------------------------------------

def collected_test_count() -> int:
    """What `pytest -q` actually collects — definitions times parametrize, not
    a count of `def test_`. Comparing a definition count to a collected count
    is the specific error that produced a false 'the README is stale' finding
    on 2026-08-06; see the plan file."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    assert m, f"could not read a collected count from pytest:\n{out[-500:]}"
    return int(m.group(1))


def divergence_table_ids() -> list[int]:
    rows = re.findall(r"^\| (\d+) \| ", DIVERGENCES.read_text(), re.M)
    return [int(n) for n in rows]


def divergence_section_ids() -> list[int]:
    """The `## #N` deep-dive headings — where a divergence is actually written
    up. The table is a summary OF these, so a section with no row is an entry
    that exists and is uncounted."""
    heads = re.findall(r"^## #(\d+)", DIVERGENCES.read_text(), re.M)
    return [int(n) for n in heads]


def divergence_open_ids() -> list[int]:
    m = re.search(r"\*\*Open as of [^:]+: ([^*]+)\*\*", DIVERGENCES.read_text())
    assert m, "the `Open as of ...` line is gone from docs/divergences.md"
    return sorted(int(n) for n in re.findall(r"#(\d+)", m.group(1)))


def divergence_prose_total() -> int:
    # [\w-], not \w: the twenty-first divergence made the total a HYPHENATED
    # word, and `\w+` cannot match one. The failure was not a wrong count — the
    # regex missed entirely and the assert below fired with "the sentence is
    # gone", which points at the wrong file. A guard that cannot express the
    # next value it will be handed is a guard with an expiry date.
    m = re.search(r"^([\w-]+) such differences have been found",
                  DIVERGENCES.read_text(), re.M)
    assert m, "the 'N such differences have been found' sentence is gone"
    word = m.group(1)
    for n, w in WORDS.items():
        if w.lower() == word.lower():
            return n
    pytest.fail(f"unrecognised number word {word!r} — add it to WORDS")


# --------------------------------------------------------------------------
# docs/divergences.md must agree with itself.
# --------------------------------------------------------------------------

def test_the_table_lists_every_divergence_with_no_gaps():
    """Rows 13-16 were missing until 2026-08-06. A gap here is not cosmetic:
    three of those four were OPEN."""
    ids = divergence_table_ids()
    assert ids == sorted(ids), f"table rows are out of order: {ids}"
    assert ids == list(range(1, max(ids) + 1)), \
        f"gaps in the divergence table: {sorted(set(range(1, max(ids)+1)) - set(ids))}"


def test_every_deep_dive_section_has_a_table_row():
    """The check that was missing on 2026-08-11, when #21 was registered with a
    full `## #21` section and no row.

    The prose, the table and the `Open as of` line all agreed with each other
    — which is precisely what the tests above assert — while a registered OPEN
    divergence sat a few hundred lines below, absent from every count in the
    repo including README's. Three artifacts were being compared and there were
    four.

    SUBSET, not equality, and the direction is load-bearing: #1-#7 and #9 have
    rows and no sections because they predate the deep-dive convention.
    Requiring a section for every row would fail on eight legitimate entries
    and would be quietly deleted the first time someone hit it.
    """
    sections, rows = divergence_section_ids(), divergence_table_ids()
    orphans = sorted(set(sections) - set(rows))
    assert not orphans, (
        f"divergence(s) {orphans} have a `## #N` section but no summary-table "
        f"row — documented in full and invisible to every count in the repo")


def test_the_prose_total_matches_the_table():
    assert divergence_prose_total() == len(divergence_table_ids())


def test_the_open_line_matches_the_rows_marked_open():
    text = DIVERGENCES.read_text()
    marked = sorted(int(n) for n, status in
                    re.findall(r"^\| (\d+) \| .*? \| (.*?) \|", text, re.M)
                    if "open" in status.lower())
    assert marked == divergence_open_ids(), (
        f"the table marks {marked} open but the `Open as of` line says "
        f"{divergence_open_ids()} — the two most-read statements in the file "
        f"disagree")


# --------------------------------------------------------------------------
# README.md must agree with reality.
# --------------------------------------------------------------------------

def test_the_readme_test_count_is_current():
    actual = collected_test_count()
    prose = re.search(r"\*\*([\d,]+) tests as of", README.read_text())
    assert prose, "the README no longer states a test count"
    claimed = int(prose.group(1).replace(",", ""))
    assert claimed == actual, (
        f"README says {claimed:,} tests, pytest collects {actual:,}. "
        f"Update BOTH the prose and the quick-start block.")


def test_the_quickstart_block_agrees_with_the_prose():
    """The count appears twice. It has been updated in one place only before."""
    text = README.read_text()
    prose = int(re.search(r"\*\*([\d,]+) tests as of",
                          text).group(1).replace(",", ""))
    block = re.search(r"pytest tests/ -q\s+#\s*([\d,]+)", text)
    assert block, "the quick-start block no longer carries the count"
    assert int(block.group(1).replace(",", "")) == prose


def test_the_readme_divergence_count_is_current():
    text = README.read_text()
    m = re.search(r"\*\*(\d+) as of [\d-]+\*\*", text)
    assert m, "the README no longer states a divergence count"
    assert int(m.group(1)) == len(divergence_table_ids())


def test_the_readme_open_closed_split_is_current():
    text = README.read_text()
    total = len(divergence_table_ids())
    n_open = len(divergence_open_ids())
    # Contiguous 1-20, not the handful of values that happened to be in use.
    # This dict skipped 7, so the day a seventh divergence went open the test
    # raised `KeyError: 7` instead of asserting anything — a guard that fails
    # by crashing tells you nothing about the thing it guards, and the crash
    # points at the test rather than at the README.
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
             7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
             16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
             20: "twenty"}
    n_closed = total - n_open
    for n, label in ((n_closed, "closed"), (n_open, "open")):
        assert n in words, (
            f"no word for {n} {label} — extend `words` rather than letting "
            f"this raise KeyError and read as a test bug")
        assert f"{words[n]} {label}" in text.lower(), \
            f"README should say '{words[n]} {label}' ({total} total, {n_open} open)"


# --------------------------------------------------------------------------
# The one that cannot run everywhere, and says so.
# --------------------------------------------------------------------------

def test_the_live_trade_count_is_checked_only_where_a_ledger_exists(capsys):
    """`memory/ledger.jsonl` is gitignored: absent in a worktree, absent in CI.

    Asserting it unconditionally would make this a test that skips everywhere
    it runs. It is conditional and LOUD about it instead — the same shape as
    the backup drill's off-host check, and the same reasoning `ci.yml` gives
    for keeping `check_secret_exposure.py` out of CI.
    """
    ledger = ROOT / "memory" / "ledger.jsonl"
    if not ledger.exists():
        with capsys.disabled():
            print(f"\n  SKIPPED: no ledger at {ledger} — the README's live "
                  f"round-trip count is unverifiable here (worktree or CI). "
                  f"It IS verified when run from the live checkout.")
        return
    actual = sum(1 for line in ledger.read_text().splitlines()
                 if '"type": "outcome"' in line)
    m = re.search(r"Live record: (\d+) closed round-trips", README.read_text())
    assert m, "the README no longer states a live round-trip count"
    assert int(m.group(1)) == actual, (
        f"README says {m.group(1)} closed round-trips, the ledger has {actual}")


# --------------------------------------------------------------------------
# The EDGE tally: the docs must agree with the register that owns it.
# --------------------------------------------------------------------------
#
# `tests/test_skills_are_current.py` makes this same argument for
# `.claude/skills/*/SKILL.md`. Both used to carry their own copy of
# `edge_families`, `K_STATED_RE` and `M_STATED_RE`, because they were written
# on branches open at the same time. Two definitions that must agree,
# maintained separately, is precisely the failure this section is about — so
# they now live once in `tests/edge_tally.py` and both files import them.


# Docs that describe the project AS IT IS. Each must agree with the register.
CHECKED_DOCS = [
    "README.md",
    "GLOSSARY.md",
    "research/candidates_2026-08.md",
]

# Docs whose ORIGINAL prose is deliberately frozen at a stale K, and which must
# therefore carry a dated correction stating the current one. Checked the other
# way round from CHECKED_DOCS: the stale value is allowed, the *absence* of the
# current value is the failure.
#
# This category exists because the 130/30 design did not fit the other two. Its
# "Why" and "Risks, stated before the fact" sections argue from the record as it
# stood on 2026-08-04 and are worth reading against their outcome, so rewriting
# them would destroy the thing the file is for. But leaving it EXEMPT meant
# nothing checked that the correction was there at all — and an exemption is
# indistinguishable from an oversight six months later.
CORRECTED_DOCS = {
    "docs/superpowers/specs/2026-08-04-130-30-long-short-design.md":
        "the design as approved 2026-08-04, kept intact; §72's tally arrives in "
        "dated `> **Corrected**` notes, in the style of the `Checked "
        "2026-08-05` note already in the file",
}

# Docs that legitimately state a K the register will disagree with, and why.
# A path here is a claim that the number in it is not a current-state claim.
EXEMPT_DOCS = {
    ".claude/skills/":
        "owned by tests/test_skills_are_current.py, which asserts the same "
        "thing against the same register",
    "knowledge/backtest_candidates.md":
        "the frozen record. Each `## §N` header states the K that claim was "
        "registered at; rewriting them to the current K would falsify what "
        "the record said at the time, which is the whole point of it",
    "research/beat_spy_report_2026-08.md":
        "same — a frozen statement of what the record said on its own date",
    "knowledge/source-review-beating-the-sp500.md":
        "a dated source review. Its only K is §67's own registration (K=15), "
        "cited as a historical fact about that section, not a claim about the "
        "current budget. Listed by name rather than exempting the "
        "source-review prefix: a future review that argues from the CURRENT "
        "budget should fail this and be classified deliberately",
    "research/INDEX.md":
        "one row per section, each carrying the K of that registration. Not a "
        "statement about the current budget",
    "research/specs/":
        "spec drafts state the K a registration would spend, frozen when the "
        "spec was written",
}

def tracked_markdown() -> list[pathlib.Path]:
    """Every markdown file GIT TRACKS. Not every markdown file on disk.

    This asks git rather than walking the filesystem, and the difference is not
    cosmetic. The first version of this globbed `ROOT.rglob("*.md")` behind a
    hardcoded skip-list of build directories, which walked straight into
    `.worktrees/` — gitignored (`.gitignore:32`) and untracked, but very much
    present on any machine doing worktree-isolated work. A `qa-sweep` worktree
    pinned at an older commit contributed **seven** unclassified files still
    stating the old K, so this section failed on that laptop and passed in CI,
    which does a clean checkout with no worktrees.

    A guard that disagrees with itself depending on where it runs is worse than
    no guard: green in CI is the reading people trust. And the fix is not to add
    `.worktrees` to the skip-list — a hand-maintained list of places to ignore
    is the same shape as the bug this whole section exists to prevent, just one
    level up. `git ls-files` cannot drift from what the repo actually contains,
    because it IS what the repo contains.
    """
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "*.md"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(ROOT / rel for rel in out.split("\0") if rel)


def test_the_register_can_still_answer_what_k_is():
    """Every assertion below is vacuous if the parse silently yields nothing —
    a renamed key or a restructured row would turn this whole section green."""
    fams = edge_families()
    assert fams, (
        f"no rows in {REGISTRATIONS} parse as EDGE claims with a "
        f"`bonferroni_k`; the tally guard below is measuring nothing")
    assert passing_family_ceiling() > 0, (
        f"no EDGE family in {VERDICTS} has a passing arm; the overclaim "
        f"ceiling below is 0 and would reject every honest number")


@pytest.mark.parametrize("rel", CHECKED_DOCS)
def test_the_bonferroni_budget_a_doc_states_matches_the_register(rel):
    """No current-state doc may state a K the register disagrees with.

    This is the skills guard's argument moved to the front door, and it is
    here because the front door is where it actually failed: `GLOSSARY.md`
    defined **Bonferroni K** as 15 for two waves after §72 made it 16, in the
    file whose job is to tell a reader what the project's words mean.
    """
    path = ROOT / rel
    assert path.exists(), f"{rel} is in CHECKED_DOCS and does not exist"
    k = current_k()
    stated = stated_ks(path.read_text())
    assert stated, (
        f"{rel} no longer states the EDGE budget anywhere. If that is "
        f"deliberate, drop it from CHECKED_DOCS; otherwise this test just "
        f"stopped checking the thing it was written for")
    wrong = sorted(stated - {k})
    assert not wrong, (
        f"{rel} states the EDGE budget as {wrong}; {REGISTRATIONS} says "
        f"K={k}.\nThe register owns this number. A doc quoting a stale one is "
        f"how the next claim gets read against the previous claim's "
        f"significance threshold.")


@pytest.mark.parametrize("rel", CHECKED_DOCS + sorted(CORRECTED_DOCS))
def test_no_doc_claims_more_edge_passes_than_the_verdicts_support(rel):
    """Applies to the corrected docs too. Their frozen text may state an
    out-of-date K, which is the whole point of the category — but nothing in
    this repo, frozen or not, may claim more passes than the verdicts support."""
    ceiling = passing_family_ceiling()
    over = sorted(stated_ms((ROOT / rel).read_text()) - set(range(ceiling + 1)))
    assert not over, (
        f"{rel} claims {over} EDGE passes; at most {ceiling} of the registered "
        f"EDGE families have even one passing arm in {VERDICTS}")


@pytest.mark.parametrize("rel", sorted(CORRECTED_DOCS))
def test_a_frozen_doc_still_carries_the_current_tally(rel):
    """The other half of `CORRECTED_DOCS`: stale is allowed, silent is not.

    `CHECKED_DOCS` asserts `stated ⊆ {k}`. That is the wrong shape for a file
    whose argument is deliberately preserved at the number it was written
    against, so this asserts the complement — `k ∈ stated` — which fails
    exactly when the dated correction is missing, wrong, or quietly dropped in
    a later edit.
    """
    path = ROOT / rel
    assert path.exists(), f"{rel} is in CORRECTED_DOCS and does not exist"
    k = current_k()
    stated = stated_ks(path.read_text())
    assert k in stated, (
        f"{rel} states the EDGE budget as {sorted(stated)} and never {k}. Its "
        f"original prose is frozen on purpose ({CORRECTED_DOCS[rel]}), which "
        f"is only defensible while a dated correction sits next to it. Add "
        f"one, or move the file to CHECKED_DOCS and update it in place.")


def test_every_doc_that_states_a_k_is_either_checked_or_deliberately_exempt():
    """The scoping bug, guarded.

    The stale `GLOSSARY.md` was not missed because the check was wrong — it
    was missed because the check globbed `.claude/skills/*/SKILL.md` and the
    stale number was somewhere else. Listing the files to check is the same
    move that failed, so the list is inverted here: every markdown file in the
    repo that states a K must be classified, and an unclassified one is a
    failure rather than a silent pass.

    A new doc quoting the tally therefore costs one line — `CHECKED_DOCS` if it
    describes the project now, `CORRECTED_DOCS` if its prose is frozen and a
    dated note carries the current number, `EXEMPT_DOCS` with a reason if the
    number is not a current-state claim at all. What it cannot do is arrive
    unexamined.
    """
    unclassified = []
    for path in tracked_markdown():
        rel = path.relative_to(ROOT).as_posix()
        if (rel in CHECKED_DOCS or rel in CORRECTED_DOCS
                or any(rel.startswith(e) for e in EXEMPT_DOCS)):
            continue
        if stated_ks(path.read_text()):
            unclassified.append(rel)
    assert not unclassified, (
        "these files state an EDGE budget and are in none of the three "
        f"classifications: {unclassified}\n"
        "Add each to CHECKED_DOCS (it describes the project as it is), to "
        "CORRECTED_DOCS (its prose is frozen and a dated note carries the "
        "current number), or to EXEMPT_DOCS with the reason its number is not "
        "a current-state claim.")


@pytest.mark.parametrize("bucket", ["CHECKED_DOCS", "CORRECTED_DOCS",
                                    "EXEMPT_DOCS"])
def test_the_classifications_still_point_at_something(bucket):
    """A classification for a file that no longer exists is a rule nobody reads
    protecting nothing — and it hides the day a checked doc gets moved under an
    exempt prefix."""
    entries = {"CHECKED_DOCS": CHECKED_DOCS,
               "CORRECTED_DOCS": CORRECTED_DOCS,
               "EXEMPT_DOCS": EXEMPT_DOCS}[bucket]
    dead = [e for e in entries
            if not (ROOT / e).exists()
            and not any(p.relative_to(ROOT).as_posix().startswith(e)
                        for p in tracked_markdown())]
    assert not dead, f"{bucket} entries match no file: {dead}"


def test_no_doc_is_classified_twice():
    """The three buckets assert contradictory things — `stated ⊆ {k}` against
    `k ∈ stated` against nothing at all. A file in two of them would be judged
    by whichever test ran, which is not a rule."""
    overlaps = [rel for rel in list(CHECKED_DOCS) + list(CORRECTED_DOCS)
                if sum([rel in CHECKED_DOCS, rel in CORRECTED_DOCS,
                        any(rel.startswith(e) for e in EXEMPT_DOCS)]) > 1]
    assert not overlaps, f"classified in more than one bucket: {overlaps}"
