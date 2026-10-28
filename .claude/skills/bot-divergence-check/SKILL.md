---
name: bot-divergence-check
description: Use before merging any change to the three trading bots that could make the live bot and the backtester behave differently — touching src/main.py, src/risk.py, src/backtest.py, judge context, memory, news, sizing, fill timing or scan order. Also use when reading a gate verdict, because a verdict measures a bot that does not exist if a divergence is open.
---

# The divergence register

Every gate verdict in `knowledge/backtest_candidates.md` rests on the simulator
being a faithful model of the live bot. **Where the two differ, a verdict
measures a bot that does not exist.**

`docs/divergences.md` is the list. Twenty-six are recorded (through #26). Before 2026-07-28 they
existed only as prose scattered across forty sections, so "how many are open?"
had no answer — and #8 sat closed-on-paper and open in fact for three days.

## The rule that makes it work

> **A divergence is CLOSED only when a test would fail if it reopened.**

"Fixed in code" is not closed. The repo has been wrong about that before.

## When to open one

Any change where the live path and `backtest.py` would see different inputs or
apply different logic. `backtest.py` **never imports `llm`** by design, so
anything routed through an LLM at runtime is automatically a candidate.

An entry states:

1. **What differs**, concretely, with file and line.
2. **The direction of bias** — does it make the backtest optimistic or
   pessimistic relative to live? This is the load-bearing field. #14 records
   that the sim judge cannot veto on news, so backtests are *optimistic*, and
   that the asymmetry is one-directional because invariant #2 lets the judge
   only veto or shrink.
3. **What it invalidates, precisely** — which numbers, from which date.
4. **Status** and, if closed, **the test that would fail if it reopened.**

## Deliberate divergences are allowed — and must still be registered

Not every divergence is a defect. #14, #15 and #16 are all **open by
construction**:

- **#14** — the live judge reads news memory; there is no archive of what the
  newswire said on an arbitrary day in 2007, and reconstructing one from a
  modern model's knowledge of how those years turned out would be *hindsight
  wearing a timestamp*.
- **#15** — the live judge reads `knowledge/principles.md`; `judge_model.py` is
  a **distribution** stand-in that reproduces how often and by how much the
  judge cuts, not its reasoning, so it cannot represent a principle at all.
- **#16** — Alpaca paper charges no stock-loan fee, keeps no hard-to-borrow list
  and never issues a buy-in, and `backtest.py` has no financing cost either, so
  any short-leg return is overstated by a cost nobody paid.

The first two are safe in DIRECTION because of invariant #2 — the judge may only
veto or shrink, so the sim can approve trades live would refuse and backtests
stay conservative. **#16 is the first that runs the other way**: it makes the
short leg look better than it can be, so a short-leg result clearing a bar by a
small margin has not cleared it. Registering them is what makes "open by
construction" different from "unnoticed".

## The trap that keeps recurring

Fixing one divergence creates another. `judge_model.py`'s own docstring records
the near miss: putting the judge haircut in `risk.py` would have stacked it on
top of the real judge in `main.py`, cutting every live order twice — *a ninth
divergence created by the code fixing the eighth*. It lives in a module the live
path never imports so the mistake is structurally impossible rather than merely
discouraged.

Before adding a correction, check whether the live path already applies it.

## How they get found

Three of the first twelve (#10, #11, #12) were found by **reading code for an
adjacent task**, within four days of the register being created — and #12 had
been sitting in `data/snapshots/MANIFEST.json` in plain English the whole time.

So: when you are in a file for another reason and notice the two paths diverge,
that is the finding. Write it down then. The register's own header is careful to
say "open as of <date>: none" is a statement about the ones found, not a claim
that the list is complete.
