# Backtester vs live: the divergence register

Every gate verdict in `knowledge/backtest_candidates.md` rests on the simulator
being a faithful model of the live bot. Where the two differ, a verdict measures
a bot that does not exist.

Twenty-six such differences have been found. Until 2026-07-28 they existed **only as
prose scattered across forty sections of the gate ledger** — there was no list,
so "how many are open?" had no answer, and #8 could sit closed-on-paper and open
in fact for three days without anyone noticing. This file is the list.

**Open as of 2026-10-28: #13, #14, #15, #16, #18, #19, #21, #23, #24, #25 and #26.** Nine of the eleven
are open *by construction* rather than by defect — a sampling fact about the live
record, two judge inputs the simulator has no mechanism to represent, a cost the
paper broker does not charge, a fill-session hazard that only materialises when
the cycle runs long, a scanner that tests a live quote where the simulator
tests a completed close, a judge whose identity changed with no baseline
left to compare it against, and an operator cost the gates pin while live
charges whatever the console says today. **#19 and #23 are not.** #19 is a plain
omission, found 2026-08-09, in which a live entry filter has been silently
switched off in every gate ever run because no spec supplies the data it needs.
**#23 is a third kind again** — a live rail the simulator never modelled, whose
absence was *intended* (it is the live-side backstop §10 leaned on in writing)
and therefore never looked for. It is binding right now: one of the three
enabled strategies is retired live and fully active in every simulation. **#17 and #20 are
DEFECTS, each found and closed the same day.** #17 is the largest correction
here: the simulator let every strategy enter every symbol in the snapshot. #20
is the subtler shape — live and the simulator keyed the re-entry cooldown
differently, which is harmless under the shipped config and would have gone
live silently on the day a second strategy was scoped.

That count has been wrong before, in the direction that matters. This file said
**"Open as of 2026-07-29: none"** while #15 had been open since the initial
commit on 2026-07-16 — through the creation of this very register — and nobody
noticed for nineteen days. So read any "open as of" line as a statement about
what has been *found*, never as a claim that the list is complete. #10, #11 and
#12 were all discovered by reading code for an adjacent task within four days of
this register being created; #12 had been sitting in
`data/snapshots/MANIFEST.json` in plain English the whole time; and #15 was found
while writing `.claude/skills/`.

**A divergence is CLOSED only when a test would fail if it reopened.** "Fixed in
code" is not closed; the repo has been wrong about that before.

| # | Divergence | Status | Closed by |
|---|---|---|---|
| 1 | Heat and correlation caps missing from the simulator (§13) | **closed** | `src/backtest.py:593` — `pure_checks` is the same function live calls |
| 2 | Fill timing: sim filled at signal-bar close, live at next open (§19a) | **closed** | `tests/test_backtest.py` fill-timing cases |
| 3 | Per-strategy vs ensemble counters — sim watched one strategy, live counts all four (§19b) | **closed** | `simulate_ensemble` (`src/backtest.py:590-594`), `tests/test_ensemble_sim.py` |
| 4 | Symbol scan order: sim alphabetical, live config-order. Moved OOS return 2.66pp (§22) | **closed** | `risk.scan_order` (`src/risk.py:501-514`), shared by both paths |
| 5 | Relative-volume filter re-implementation risk | **pre-empted** | Single implementation, `src/risk.py:325-327` |
| 6 | Credit-gate re-implementation risk | **pre-empted** | Single implementation, `src/risk.py:430-431` |
| 7 | Repository vs reality — the running checkout was 57 commits behind the reviewed code (§26) | **closed** | `src/deploycheck.py`, `tests/test_deploycheck.py` (22 tests) |
| 8 | **The LLM judge is absent from the simulator** | **closed 2026-07-29** | `tests/test_judge_is_declared_not_assumed.py` |
| 9 | Drawdown rail existed on the live path only | **pre-empted** | `risk.drawdown_pct` shared (`src/risk.py:158-169`, `backtest.py:348,679`) |
| 10 | **The simulator's equity peak advanced only on buy bars** | **closed 2026-07-28** | `tests/test_sim_peak_tracks_every_bar.py` |
| 11 | **Live sampled the equity peak only when an order was attempted** | **closed 2026-07-29** | `tests/test_quiet_cycle_still_ratchets_the_peak.py` |
| 12 | **Live read RAW bars while every snapshot is split/dividend adjusted** | **closed 2026-07-29** | `tests/test_bars_are_split_adjusted.py` |
| 13 | **Live is trading in a state the backtest says is rare** (the drawdown latch) | **open** | open by construction — a sampling fact about the live record, not a defect |
| 14 | **The live judge reads news memory; the backtest's judge model cannot** | **open** | open by construction — the simulator has no mechanism to represent it |
| 15 | **The live judge reads curated principles; the backtest's judge model cannot** | **open** | open by construction — same; open since the initial commit, found 2026-08-04 |
| 16 | **Paper shorting is free; real shorting is not** | **open** | open by construction — the paper broker charges no borrow, and the bias flatters |
| 17 | **The simulator ignored per-strategy universes — 500 names traded in the sim, 38 live** | **closed 2026-08-05** | `tests/test_sim_honours_universes.py` |
| 18 | **An overrunning cycle fills at the NEXT OPEN, and nothing checks the clock** | **open** | open by construction — see below |
| 19 | **The earnings blackout is unmodelled in EVERY gate** | **open** | found 2026-08-09 (§57); no registered spec has ever passed `earnings=` |
| 20 | The re-entry cooldown was keyed by symbol in live, by (strategy, symbol) in the simulator | closed | `tests/test_cooldown_key_matches_sim.py` |
| 21 | **The live swing scanner fills inside the entry zone intraday; the simulator fills at the next open** | **open** | open by construction — the 30-min scan tests a live quote, the cycle and every §62 arm test the last completed close |
| 22 | The test suite paged the operator — `watchdog.load_env()` fell back to the repo-root `.env`, so the alert-delivery negative control posted real alerts | closed | `tests/test_alert_delivery.py::test_load_env_NEVER_reaches_the_operators_real_dotenv` |
| 23 | **The live kill retires a strategy's entries; the simulator has never modelled it** | **open** | found 2026-08-23; `tests/test_live_kill_is_live_only.py` pins the gap in both directions but does not close it — see the entry |
| 24 | **The live judge reads a per-symbol research dossier; the simulator models the judge as a distribution and cannot represent one** | **open** | open by construction — registered 2026-08-23 by the change that caused it; the calibration refuses until re-fit |
| 25 | **The judge is a different model (`qwen3.8-27b`, self-hosted) and no measurement compares it to the one whose record the bot carries** | **open** | open by construction — registered 2026-08-28 by the change that caused it; the shadow baseline needs the vendor that ran out, which is why the switch happened |
| 26 | **The operator's cost: gates charge a PINNED research block on every fill; live charges the console's current setting, and never on a short's opening order** | **open** | open by construction — registered 2026-10-28 by the change that caused it (the owner's after-cost decision); `tests/test_after_cost_backtest.py` pins the sim side, `tests/test_after_cost_rails.py` the live side |

> **Rows 13–16 were missing from this table until 2026-08-06**, and this is the
> second time this file has been wrong in the direction that matters. The prose
> above said eighteen and named five open; the table listed fourteen and showed
> exactly one open. A reader who trusted the table — the fastest thing in the
> file to read, and therefore the thing most likely to be read alone — would
> have undercounted the open divergences **five to one**.
>
> Found while writing `tests/test_doc_counts.py`, which now asserts the prose
> total, the table rows and the `Open as of` line all agree. The three could
> drift apart before because nothing compared them.
>
> **Row 21 was missing until 2026-08-11 — the third time, and the guard above
> reported green throughout.** #21 was registered by #110 with a full `## #21`
> section, but no table row, so the prose said twenty, the `Open as of` line
> named six, and `README.md` said "20 … six open" while a registered OPEN
> divergence existed a few hundred lines below. Every one of those three
> statements agreed with the other two, which is exactly what the guard checked.
>
> The lesson is narrower than "add another check". The sentence above —
> *"nothing compared them"* — named three artifacts and quietly implied they
> were all of them. The **sections** were a fourth, and the deep-dive section is
> where a divergence is actually written up; the table is a summary of it. So
> the register could gain a fully documented entry and lose it from every count
> in the repo. `test_doc_counts.py` now asserts **every `## #N` section has a
> table row** (sections ⊆ rows, not equality — #1–#7 and #9 predate the
> deep-dive convention and legitimately have no section). Found while rebasing
> the QA-sweep branch, i.e. by reading the file for an unrelated reason — the
> fourth entry in this register to be found that way.

---

## #8 — the judge is modelled and switched off

`src/judge_model.py` was built for §29 (2026-07-26) specifically to close this,
and `config.yaml` shipped `judge_model.enabled: false`. `judge_model` appeared
in **no** file under `research/specs/` and nowhere in `scripts/run_gate.py`.

So §35, §37, §38, §39, §40 and §41 — every gate run *after* the fix was
written — measured a bot with no judge, while live the judge downsizes **58.1%**
of buys and vetoes **2.4%** at a mean scale of **0.729** (164 judged buys in
`memory/ledger.jsonl`). The simulator was sizing entries roughly a third larger
than the live bot would on the majority of trades.

This one is instructive: the code landed, the divergence was declared closed,
and the switch was never flipped. That is why this register records the *test*
that closes each entry rather than the commit that fixed it.

### CLOSED 2026-07-29 (W2-1)

Closed in two independent places, because a flag alone would repeat the
original failure in a new costume:

1. **Declared, not defaulted.** `gatespec` accepts a top-level `judge_model`
   bool and `scripts/register_gate.py` REFUSES to freeze any spec without one.
   The setting enters the canonical hash, so a spec registered judge-off cannot
   be re-run judge-on and still pass the freeze check.
2. **Proven, not claimed.** `judge_model` counts what it did
   (`sized`/`cut`/`vetoed`/`zeroed`), and `run_gate.py` refuses to write a
   verdict when a spec says `judge_model: true` while those counters say the
   model never acted. A verdict labelled judge-on and measured judge-off is
   worse than no verdict, because it looks like this entry got closed.

**Absence is not `false`.** §35-§41 predate the field and keep their frozen
hashes, so they still re-execute byte-identically — the record stays
reproducible. `run_gate.py` banners those runs as judge-less rather than
defaulting them quietly. A spec that says `false` made a choice; a spec with no
field did not know there was one, and flattening the two would erase the whole
finding.

**What is NOT claimed:** the pre-W2-1 verdicts are not withdrawn or re-scored.
They remain valid measurements of a judge-less bot, and must be read as such.
Calibration also refreshed here: n=146 (through 2026-07-23, pre-§29 sizing) to
n=164 (through 2026-07-28). The judge cuts *more* than the stale file said.

## #10 — the peak that only moved on buy bars

Found 2026-07-28 while reading `src/backtest.py` for §41. Previously
undocumented anywhere.

`sim_peak = max(sim_peak, acct.equity(last_close))` sat inside the
`if action == "buy":` branch of both `simulate()` and `simulate_ensemble()`, so
a bar that only sold — or that transacted nothing — never advanced the peak.
Live ratchets inside `pre_trade_checks`, which runs for sells too, and
`src/risk.py:828-833` states the reason outright: *"the peak must keep tracking
even while entries are blocked, otherwise the breaker could never clear
itself."*

Live therefore sampled the peak on strictly more occasions than the simulator.
Because the drawdown rail measures distance *below* the peak, a higher peak
means a deeper measured drawdown means more entries blocked — so **the §40 latch
bit harder in production than in any backtest that measured it**, and the error
ran in the reassuring direction, which is how it survived.

Fixed by making both correct rather than by making one imitate the other's
artifact: the ratchet moved to the top of the bar loop and now runs
unconditionally. A high-water mark sampled only when the book happens to
transact is not a high-water mark.

**Note for §41:** live still samples the peak only when an order is *attempted*
(`pre_trade_checks` is not called on a cycle that places nothing). That is the
same class of error, one level up, and it is in scope for §41 rather than fixed
quietly here. — **Now #11 below, closed 2026-07-29.**

## #11 — live sampled the peak only when an order was attempted

Predicted by #10's own note and closed 2026-07-29 (W2-2).

§31 put `risk.update_high_water` inside `risk.pre_trade_checks`, and reasoned
explicitly about running it for sells as well as buys — *"the peak must keep
tracking even while entries are blocked"* (`src/risk.py:899-902`). That
reasoning is correct and insufficient: `pre_trade_checks` has exactly one caller
(`main.py`'s order loop) and fires **only when an order is actually attempted**.
A cycle producing no buy and no sell never touched the mark at all.

**Direction of the error.** The peak only ratchets up, so equity earned on a
quiet day was invisible to it. A book drifting up over a week of holds keeps
last week's peak, and the first 10% drawdown is then measured from a stale LOW
peak — the breaker fires **late**. This runs OPPOSITE to #10, which made the
sim's latch bite *less* than live. The two errors were not cancelling: one was
in the simulator and one is in production.

**Where it bites.** Invisibly on the 500-symbol snapshots, where something
trades on virtually every bar — which is exactly why #10's census came back
byte-identical. It bites on the book this bot actually runs: **38 symbols, with
whole days where nothing is entered.** Same shape as #10: the error hid in the
place nobody measures.

Closed by ratcheting once per cycle in `src/main.py`, immediately after the
account is read fresh from the broker (invariant #4) and before the first early
return — the daily-loss kill switch and the HALT check both return before the
order loop, so any later placement would leave the same hole. `pre_trade_checks`
still ratchets per order; this is the floor under it, and `update_high_water` is
max-based so running both is a no-op the second time.

## #12 — live read raw bars, every snapshot is adjusted

Found 2026-07-29 (W2-3).

`alpaca-py`'s `StockBarsRequest.adjustment` defaults to `None`, which the API
treats as **raw**. Neither `broker.bars()` (the live path) nor
`backtest.fetch_bars()` passed it. Every frozen snapshot, meanwhile, is split-
AND dividend-adjusted: `scripts/build_snapshot.py` uses yfinance
`auto_adjust=True`, and `scripts/build_intraday_snapshot.py` already used
`adjustment=Adjustment.ALL`.

So **the live bot has been reading a different price series from the one every
gate verdict was scored on.**

**Direction of the error, and why it is not small.** A 2-for-1 split in a raw
series is a 50% overnight gap that never happened. It collapses an SMA, spikes
ATR, and can fire an entry signal or trip a stop on an event that did not change
the value of a single share held. The bot cannot distinguish that gap from a
crash. The effect is not a consistent bias in one direction — it is noise
injected at unpredictable moments, which is worse than a bias because it cannot
be corrected for.

**This was already written down.** `data/snapshots/MANIFEST.json` records the
first intraday build going out RAW and carrying **11 unadjusted splits** "that
the simulator read as overnight collapses — it voided a gate run." The fix was
applied to that one builder and never propagated to the live path. Same failure
shape as #8: the lesson was learned, recorded, and applied in exactly one place.

Closed by passing `Adjustment.ALL` in both request builders.
`tests/test_bars_are_split_adjusted.py` asserts both, refuses `SPLIT`-only
(which would leave a dividend mismatch), and asserts the snapshot builder still
auto-adjusts — so the alignment cannot be re-broken from either side.

**This changes live inputs.** It is a data-correctness fix, not a strategy or
threshold change, so it is not gated — but it is the one item in W2 that alters
what the production bot sees, and it is recorded here rather than mentioned in
a commit message.

---

## How to add an entry

1. Number it. Numbers are permanent; a superseded entry stays with its status
   changed, never deleted.
2. State which side was wrong and **in which direction** the error ran. A
   divergence that made the bot look worse is a different risk from one that
   flattered it.
3. Name the test that closes it. If there is no test, the status is OPEN
   regardless of what the code does today.

## #13 — live is trading in a state the backtest says is rare

**Found** 2026-07-29 (§45). **OPEN.**

The `max_drawdown_pct: 10.0` rail latches trading off on a peak-to-trough
drawdown. In the 2022-2026 snapshot — the period closest to the current regime —
that rail blocked **96.86% of every buy signal the strategies emitted** (237,522
of 245,212). It is the single dominant constraint on the backtest, and it is
similarly dominant in 2000-2006 (74.5%) and 2007-2013 (93.8%).

Live has been running since 2026-07-14. Its equity drawdown reached **1.05%
today** and had never exceeded **0.10%** before that. The latch has never been
close to engaging.

So live's 14 open positions and 4 closed round-trips were all generated in the
~3% of conditions where the backtest's dominant rail is quiet. **The live record
and the backtest are not samples from the same distribution**, and a comparison
between them right now is comparing the calmest stretch of one against the
average of the other.

This is not a code defect — nothing is wired wrongly, and W2-2 already closed the
peak-sampling divergence (#11) that would have been. It is a *sampling* fact
about the live record, and it is recorded here because the natural next step
after ≥30 closed trades is exactly the comparison it invalidates.

### What would close this

A live record that spans at least one period in which the drawdown rail actually
engages, so that the live sample includes the state the backtest spends most of
its time in. Concretely: a `risk_rejection` with rail `drawdown` in the live
ledger, and the closed-trade count measured separately either side of it.

Until then, any statement of the form "live is tracking / beating / lagging the
backtest" is comparing unlike things, and **§45's table plus this entry are the
citation that says so.**


## #14 — the live judge reads news memory; the backtest's judge model cannot

**Opened 2026-07-30 (W7). Deliberate, not a defect — and open by construction.**

`memory.context_for_llm()` now includes a NEWS MEMORY block: a symbol's
accumulated news mentions over `news.memory.retention_days`, plus the realised
P&L of any prior news-linked closed trades. Every live judge call sees it.

`backtest.py` drives `judge_model`, which cannot. There is no archive of what
the newswire and eleven RSS feeds said on an arbitrary day in 2007, and the
snapshots hold prices, not headlines. Reconstructing one from a modern LLM's
knowledge of how those years turned out would not be a news feed — it would be
hindsight wearing a timestamp, and it would inflate every backtest it touched.

So the two judges see different inputs, on purpose.

### What this invalidates, precisely

Every backtest number produced from 2026-07-30 onward describes a judge with
**strictly less context** than the live one. That direction matters:

- the sim judge cannot veto on news, so the sim will approve trades live may
  refuse — backtests are, if anything, **optimistic** relative to live;
- it is not symmetric. Under invariant #2 the judge may only veto or shrink, so
  news memory can subtract live trades and can never add one. There is no
  mechanism here by which live outperforms sim *because of* this block.

That asymmetry is the reason this ships without a gate: the worst case is a
live bot that trades less than its backtest, which is the safe direction to be
wrong in.

### What would close this

Either of:

1. **A real point-in-time news archive** covering a snapshot period, replayed
   into `judge_model` so both judges read the same thing. Nothing short of
   genuinely contemporaneous text qualifies — see the hindsight problem above.
2. **`news.memory.enabled: false`**, which restores byte-identical judge
   context. That is asserted by
   `tests/test_news_memory.py::test_disabled_gives_byte_identical_context`, not
   merely intended, and it is the rollback path if a future §46 rejects this.

### The test that would fail if this silently closed

`test_disabled_gives_byte_identical_context` fails if the block stops being
suppressible, and `test_the_lesson_block_does_not_shrink_when_news_is_present`
fails if it starts displacing other context. Neither can detect a sim that
*claims* to model news memory without doing so — which is why closing this
requires the archive in (1), not a code change.

### Not a claim of value

**This is not an EDGE claim.** Nothing here has been gated, and the EDGE tally
is unchanged by it — `knowledge/backtest_candidates.md` owns that count, and it
is not restated here. The outcome half exists
to produce the two populations a §46 would compare — trades entered with news
present versus without — and there are currently **4 closed trades**. A gate
scored on 4 trades is theatre, so no gate is registered.
## #15 — the live judge reads curated principles; the backtest's judge model cannot

**Registered 2026-08-04. Deliberate, open by construction — and open since
2026-07-16, which is the part worth reading.**

`memory.knowledge_block()` reads `knowledge/principles.md` (config
`llm.knowledge_path`) and prefixes it *"KNOWLEDGE (external, unverified — weigh
below realized evidence)"*. It goes into `memory.context_for_llm()`, so every
live judge call has seen it since the first commit.

`backtest.py` never imports `llm` — stated in its own docstring — and drives
`judge_model` instead. That module is a **distribution** stand-in: it reproduces
how often the judge cuts and by how much, from a hash of
`(symbol, timestamp, salt)`. It has no prompt, no text, and no reasoning. It
cannot represent a principle at all, in either direction. This is not a gap that
better modelling closes; there is nothing in `judge_model` for a principle to
attach to.

### Why it took until now to appear here

It did not open today. It opened on 2026-07-16 with the initial commit, sat
through the creation of this register on 2026-07-28, and was not caught when
that register recorded **"Open as of 2026-07-29: none."** That statement was
wrong, and the register's own header already says why it might be — *"a
statement about the twelve found, not a claim that twelve is all there are."*
Three of the first twelve were found by reading code for an adjacent task; so
was this one, while writing `.claude/skills/`.

The entry is dated by when it was **registered**, not by when it opened, and
both dates are given because only one of them is flattering.

### What this invalidates, precisely

Every backtest number this project has ever produced describes a judge with
**strictly less context** than the live one — not from 2026-07-30 as #14 says of
news, but from the beginning.

The direction is the same as #14's, and for the same reason:

- the sim judge cannot veto on a principle, so the sim approves trades live may
  refuse — backtests are, if anything, **optimistic** relative to live;
- it is not symmetric. Under invariant #2 the judge may only VETO or DOWNSIZE
  and may never enlarge or create a trade, so a principle can subtract live
  trades and can never add one.

That asymmetry is the whole reason `knowledge/principles.md` may be edited
without a gate in front of it: the worst case is a live bot that trades less
than its backtest, which is the safe direction to be wrong in. **It is not a
licence to put anything there.** A principle that induces a veto the evidence
does not support still costs real trades; it just cannot cost them in a way a
backtest would flatter.

### The budget it shares

~~`knowledge_block()` caps at `learning.max_context_chars // 4`. It has no
budget of its own — it takes a quarter of learning's.~~ **Stale since
2026-08-04**; struck rather than deleted, per this register's own rule that a
superseded claim stays visible. The block was given its own
`llm.knowledge_max_context_chars` on that date, with the derived `// 4` kept
only as a fallback.

The rest of this paragraph stood, and understated the problem: growing
`principles.md` past its cap silently truncates the tail rather than failing, so
length is a real constraint and not a style preference. (This is the same trap
`news.memory.max_context_chars` was given its own budget to avoid.) See the note
below — the truncation was not hypothetical, and it was not the knowledge block
that paid for it.

### Note, 2026-08-11 — the budget was raised, after four blocks were found missing

**No status change. #15 stays OPEN**, and nothing here could close it: the
simulator still has no prompt for a principle to attach to. This is the #17
precedent — the same gap, measured from another side, recorded under the
existing number rather than given a new one.

`llm.knowledge_max_context_chars` went **1000 → 1500** and
`learning.max_context_chars` **4000 → 12000** (§61). The owner asked for the
principles file to be able to grow; it was sitting at exactly 1000 of 1000
chars, fitting with zero headroom, so the next principle written would have been
the one that disappeared.

**What the paragraph above did not know.** The constraint that actually bound
was not the knowledge budget — it was the total. Three sub-budgets were derived
shares (`lessons // 2`, `market_context // 4`, `scoreboard // 4`), which is
exactly 100% of the cap between them, before knowledge, news memory, the book,
the trade block, calibration or the regime label got anything. Measured against
the live memory files on 2026-08-11, `context_for_llm` assembled **5,613 chars
against a 4,000 cap** and the closing `ctx[:4000]` dropped **NEWS MEMORY, YOUR
LAST RESOLVED CALLS, YOUR RECENT CALIBRATION and CURRENT REGIME** entirely,
cutting TODAY'S MARKET CONTEXT mid-word. Every judge call, for months.

So the concern this entry recorded — that a block without its own budget eats
the tail — had already happened, to four other blocks, while the knowledge block
was the one being watched.

**Direction of bias: UNCHANGED.** The sim judge still has strictly less context
than the live one, so backtests remain optimistic relative to live, and
invariant #2 still means a principle can only subtract live trades and never add
one. A wider knowledge block makes the live judge see *more* of what the
simulator cannot represent, which widens the gap without flipping its sign.

**What is new is that the gap is now bounded and visible.** Every block has a
named budget (`memory.context_budgets`), `preflight.warnings` reports at startup
when they oversubscribe their total — non-blocking, deliberately, because this
makes the bot worse rather than unsafe — and a `context_evicted` ledger event
records any cycle where the cap actually bit, naming the blocks lost. Pinned by
`tests/test_context_budget.py`, whose eviction cases carry vacuity guards
because the two guards that should have caught this could not: both
`test_principles_do_not_shrink_the_lesson_block` and
`test_unset_knowledge_path_gives_byte_identical_context` run on an empty
`tmp_path`, where the assembled context never approaches the cap at any budget.

**Rollback:** removing `learning.context_budgets` and restoring the two
numbers reproduces the old behaviour byte-for-byte, asserted by
`test_an_unset_config_is_byte_identical`.

### What would close this

Nothing available. Closing it requires the real judge in the simulator, which
`backtest.py` refuses by design — a model call per bar would be slow,
non-reproducible, and contaminated by hindsight absorbed in training. Both of
the honest exits are the same shape as #14's:

1. **A judge model that reads text**, which would reintroduce every problem
   `judge_model.py`'s docstring lists; or
2. **`llm.knowledge_path` unset**, which restores byte-identical judge context.
   That is asserted by
   `tests/test_principles_context.py::test_unset_knowledge_path_gives_byte_identical_context`,
   not merely intended, and it is the rollback path.

### The test that would fail if this silently closed

`test_unset_knowledge_path_gives_byte_identical_context` fails if the block
stops being suppressible, and
`test_principles_do_not_shrink_the_lesson_block` fails if it starts displacing
other context. Neither can detect a simulator that *claims* to model principles
without doing so — which is why closing this needs (1), not a code change.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged; `knowledge/backtest_candidates.md` owns that count. Unlike news
memory, this one does not even produce two populations a future gate could
compare — the principles apply to every judgment, so there is no
without-principles arm in the live record. It is ungateable by construction as
well as unmodelled, and it is recorded here so that is a stated fact rather than
an oversight.

---

## #16 — paper shorting is free; real shorting is not

**Registered 2026-08-05, when `xsmom` gained a short leg. Open by construction,
and the bias runs in the flattering direction.**

Alpaca's paper environment charges **no stock-loan fee**, maintains **no
hard-to-borrow list**, and **never issues a buy-in**. A real short pays borrow
of roughly 0.3–3%/yr on an easy-to-borrow large cap, can spike far higher on a
crowded name, and can be **recalled by the lender at the worst possible moment**
— which is to say, exactly when the position is going against you and everyone
else is trying to cover the same name.

None of that exists in `src/backtest.py` either. The simulator models fills,
slippage and the rails; it has no concept of a financing cost that accrues per
day held, and no concept of a position closed by someone other than the bot.

### What this invalidates, precisely

**Any measured short-leg return is overstated by a cost that was never
charged**, in both the simulator and the paper record. The size of the overstatement
is not constant — it scales with holding period and with how hard the name is to
borrow, so it is largest on exactly the positions a momentum short leg is most
likely to hold: names that have fallen a long way and are crowded on the short
side.

The direction is unambiguous and it is the unsafe one. Unlike #14 and #15, where
the sim's judge has *less* context and therefore approves trades live would
refuse — leaving backtests conservative relative to live — this makes the short
leg look **better** than it can be. A short-leg result that clears a bar by a
small margin has not cleared it.

It also breaks a symmetry the long leg never had to think about: a long position
has no financing cost in a cash account and cannot be taken away from you. Every
number this project has produced so far is a long-only number, so this is the
first divergence that applies to one leg and not the other, and any per-leg
attribution has to carry the caveat rather than compare the two as like for like.

### What would close this

Two honest exits, and neither is available today:

1. **A borrow-cost model** — a per-day financing charge in `backtest.py` and in
   the live P&L, driven by a real borrow-rate source. That needs a data feed
   this project does not have, and inventing a flat rate would replace an
   unmeasured cost with a fabricated one, which is worse: it would look
   modelled.
2. **A real margin account**, which charges the real fee and enforces the real
   recalls. That is a live-trading decision gated behind ≥30 closed trades, ≥60
   days and attorney review, so it cannot be the near-term answer.

Until one of those lands, the mitigation is to state the bias whenever a
short-leg number is quoted, which is what this entry exists for.

### The test that would fail if this silently closed

None, and that is a statement about the divergence rather than an omission. There
is no assertion that can detect "the broker did not charge something", because a
fee that is never charged leaves nothing in the record to assert against. That
makes this **structurally weaker** than #13–#15, each of which at least has a
test pinning the mechanism it describes.

The nearest available guard is
`tests/test_xsmom_short_leg.py::test_the_shipped_config_still_ships_xsmom_disabled`
— which does not detect the divergence at all, only the fact that nothing is
currently exposed to it.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged and stays frozen under §52; `knowledge/backtest_candidates.md` owns
that count. This entry adds a required caveat to a future short-leg DIAGNOSTIC,
and asserts nothing about whether the leg works.

---

## #17 — the simulator ignored per-strategy universes

**Found and CLOSED 2026-08-05**, while reading `backtest.py` for the Phase 3
short-leg work. It is the largest correction this register has recorded.

`strategies.in_universe` has gated live entries since PR #90 introduced
per-strategy universes — `main.py:1685`, entries only, with a comment explaining
why it cannot live inside `generate()`. `simulate_ensemble` had **no
counterpart**. Every strategy could enter every symbol in the snapshot, so on a
500-name wide snapshot the incumbent traded 500 names in the simulator and 38
live.

### Why it hid

The only two universe-scoped strategies, `xsmom` and `reclaim`, are **both
cross-sectional**. `prepare_one` scopes their ranking, so `generate` answers
"insufficient history for ranking" for a symbol outside their universe and the
filter appeared to be working. That is a coincidence of those two being
cross-sectional, not a filter — a per-symbol strategy with a `universe:` key
would have traded the whole snapshot with nothing failing.

The three ENABLED strategies have no `universe:` key, which means the core 38.
They were the ones trading 500.

### What it cost, measured

Shipped configuration, `bars_wide_2022-01-01_2026-07-24.json.gz`, one arm, the
only change being this fix:

| | before | after |
|---|---|---|
| entry signals | 245,213 | 5,347 |
| **blocked by `drawdown`** | **243,814 (99.4%)** | **4** |
| executed | 29 | 969 |
| bars fully in cash | 92.39% | 4.55% |
| avg deployment | 4.09% | 75.18% |
| return | −5.26% | +107.77% |

**The mechanism is in the census, not in the return column.** With 500 candidate
names the signal flow tripped the 10% drawdown latch almost immediately, and
once latched it refused essentially every entry for the rest of the run — the
simulator spent 92% of its bars fully in cash. Confined to the 38 names it can
actually trade, the book never trips it: four drawdown blocks in the entire run.

### What this does and does not say about the record

§48 concluded that "the drawdown rail was masking measurement" and measured it
blocking 94.58% of buy signals. **That conclusion survives — but a large share of
the signal flow it was blocking came from names the live bot cannot trade.** The
rail was masking measurement of a bot that did not exist.

**The +107.77% is NOT evidence of an edge, and must not be read as one.** It is
a survivor-selected universe (§51 sized the same effect at **+200.28pp**), it is
not compared against SPY here, and §52's freeze stands unchanged. This entry
reports a simulator correction, not a result.

**No verdict is reopened or re-scored.** §43, §48, §50 and §51 stand as
measurements of the bot as simulated then. §41 set that precedent exactly: its
own simulator finding could have reopened ten verdicts and reopened none. The
consequence that IS recorded: **re-running any wide-snapshot gate today will not
reproduce its recorded numbers**, and the reason is this entry rather than
nondeterminism or a dependency bump.

### CLOSED by `tests/test_sim_honours_universes.py`

Six tests. Entries: a strategy scoped to the sector map cannot enter outside it,
an unrecognised `universe:` key enters nothing (inheriting `universe_for`'s
fail-to-nothing polarity), a symbol in no enabled universe is never entered, and
what the simulator entered is a subset of what `in_universe` — the same function
`main.py` calls, not a copy — allows.

Exits: `in_universe` starts answering False partway through a run, and positions
already open must still close on `strategy_sell` rather than being stranded to
`end_of_data`. That is the half that is easy to break while fixing the other,
and it is the property `in_universe`'s docstring exists to protect.

Mutation-proven: removing the filter and neutering it are both caught, and a
control mutation of an unrelated entry guard correctly SURVIVES — so the tests
are specific to this filter rather than to any change in the loop.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged at 1-in-15 and frozen; `knowledge/backtest_candidates.md` owns that
count.

### Note, 2026-08-06 — the same gap re-enacted itself INSIDE live, and is now floored

The entry above is about the simulator trading a wider universe than the bot.
The mirror image also existed and nobody had looked for it: **the bot silently
trading a NARROWER universe than the one every gate scored.**

`_fetch_and_validate_bars` caught a per-symbol bar failure, logged `data_error`,
and carried on with whatever was left. There was no floor and no record that the
cross-section had shrunk. It has fired once — the ledger's only `data_error` in
its entire history:

```json
{"event": "data_error",
 "detail": "QQQ: ('Connection aborted.', RemoteDisconnected(...))",
 "ts": "2026-08-05T19:47:25.555162+00:00"}
```

QQQ is one of the core 38. That cycle then ran on 37 with nothing anywhere
saying so.

**This does NOT reopen #17**, and it is deliberately a note rather than #19: it
is the same gap between the scored universe and the traded one, measured from
the other side, and no new sim/live divergence was created by finding it. What
changed is that the gap is now bounded and visible — `risk.min_universe_fraction`
blocks entries below 80% of the requested cross-section (exits always run), and
a `universe_truncated` event records every loss down to a single symbol.
Pinned by `tests/test_universe_floor.py`.

**The floor takes no credit for the total-outage case.** On 2026-07-31 the
laptop lost DNS and every symbol failed inside 40ms; SPY was therefore missing
and the stale-SPY rail aborted the cycle, as it was always going to.
`test_a_total_outage_still_aborts_on_spy_rather_than_the_floor` holds that
boundary, with the freshness rail deliberately left armed so the test cannot
pass for the wrong reason.

---

## #18 — an overrunning cycle fills at the next open, and nothing checks the clock

**Registered 2026-08-06. OPEN by construction.**

Found while instrumenting cycle duration, not while looking for it.

### The mechanism

The cycle fires at **15:45 ET** against a **16:00 close**, so the entire budget
is fifteen minutes. **No code anywhere compares the clock to the close before
submitting an order.** Grep for a cutoff and there is none: not in
`_process_signal`, not in `broker.market_order`, not in preflight.

Exits go out as `TimeInForce.DAY` (`src/broker.py:181`). Alpaca does not reject
a DAY order placed after the bell — it **queues it for the next session's
open**. So a cycle that runs long does not fail loudly. It silently converts a
**same-close fill into a next-open fill**, across an overnight gap.

Every gate assumes the same-close fill. §19 states it directly
(`knowledge/backtest_candidates.md:665-681`): *"The live scheduler runs one
cycle at 15:45 ET and fills immediately, at today's close."* That assumption is
load-bearing for every recorded number, and it is true only while the cycle
finishes in time.

### Why it is open rather than closed

The same reason as #16. A test can prove the *alarm* fires — and one does, in
`tests/test_cycle_timing.py`. No test can detect **"the broker filled this at
the wrong session"**: the fill is legitimate, the order is legitimate, and
nothing in the response distinguishes a 15:59 fill from an 09:30 one except a
timestamp nobody compares. That makes this **structurally weaker than #13-#15**
and exactly as weak as #16.

Closing it would require either a hard cutoff that refuses to submit inside N
minutes of the bell — a behaviour change, and a real one, since refusing an
exit is not obviously safer than filling it late — or fill-session attribution
in `record_fill_quality`. Both are decisions, not cleanups. Neither is in scope
here.

### What was actually shipped

Visibility, not a fix:

- `cycle_timing` on **every** cycle (`src/main.py`, in `run_cycle`'s `finally:`,
  so a crashed cycle records too) carrying `duration_s`, `finished_at_et` and
  `margin_min`.
- One alert per day when `margin_min < ops.min_close_margin_min` (default 5).

### The measurement that prompted it

Seven launchd cycles, the only ones that exist: min **1.63 min**, median
**~2.6**, max **7.72** on 2026-08-05 — which spent a 54 s stall and two
~90-100 s broker socket hangs, and finished at 15:52:44 with **7m16s of
margin**. Nothing had ever recorded any of it.

**No verdict is reopened.** No gate has been shown to be affected, because no
cycle has yet been shown to cross the bell. This entry records a hazard that is
now instrumented rather than a correction to a past number.


---

## #19 — The earnings blackout has never been modelled in a single gate

**Found 2026-08-09, while writing §57.** `tsmom` ships with
`earnings_blackout_days: 3` (`config.yaml`), so live refuses to open a momentum
position within three days of an earnings date. The simulator supports it:
`simulate_ensemble` blocks on `earnings_mod.next_within(earnings.get(sym, []),
ts, blackout_days)`.

But the guard is `if blackout_days and earnings and ...`, and **`earnings` is
`None` in every gate ever run.** `run_gate.py` passes it only when a spec
declares an `aux:` block, and grepping all 40 files in `research/specs/` plus
every row of `research/registrations.jsonl` finds **not one** that does. The
short-circuit has silently disabled a live entry filter in every section from
§35 onward.

### What this does and does not invalidate

**Direction is nameable rather than unknown.** The filter only ever REMOVES
entries, so every gate scored a bot that took *more* trades than live would —
including trades live would have refused for sitting on an earnings date. It
inflates trade count and it moves per-trade P&L by an unmeasured amount.

**No verdict is reopened.** Fifteen EDGE claims were rejected; a filter that
removes trades does not turn a rejection into a pass, and §41 set the precedent
that a simulator finding does not retroactively re-score sections. What it does
mean is that the trade COUNTS quoted from §35 onward describe a bot slightly
different from the one running.

**It does not affect §57 at all**, which is why it was safe to find here: ETFs
have no earnings, `next_within([])` returns False, and the filter would be a
no-op on that universe regardless.

### What would close this

A spec that declares an `aux: {earnings: ...}` block against a FROZEN earnings
file, plus a test asserting that a spec whose config sets
`earnings_blackout_days` and which supplies no earnings data is REFUSED rather
than silently scored. Today the absence is invisible; the fix is to make it
loud. Not done here — it belongs with the universe it affects, which is the
38-name book and not this one.

---

## #20 — the re-entry cooldown was keyed by symbol in live and by (strategy, symbol) in the simulator

**Found and closed 2026-08-10** (§58), while reading the entry loop for an
unrelated rail. Not a sampling fact, not a modelling limit — a plain defect, and
the second of that kind in this register after #17.

`risk.cooldown_days_for(cfg, name)` is per-strategy by design:
`risk.reentry_cooldown.strategies` names which strategies the rule applies to,
because §9 measured it and **adopted it for meanrev while rejecting it for
tsmom** (PF 2.16 → 2.25 for one; maxDD worsened for the other).

`backtest.simulate_ensemble` keyed its `last_exit` map by `(strategy, symbol)`
to match. `main.py` built and read the same map keyed by **symbol alone**.

### The mechanism

In live, meanrev exiting AAPL set `last_exit["AAPL"]`. tsmom's entry check then
read that same slot, found a recent exit, and refused the entry — applying to
tsmom a rule §9 had explicitly measured and rejected *for tsmom*. The simulator,
keying by pair, let tsmom in.

### Why it stayed invisible

`strategies: [meanrev]` is the shipped scope, and with exactly one scoped
strategy the two keyings cannot disagree — every lookup that reaches
`cooldown_blocked` is meanrev's own. It was a defect waiting on a config change.

That is the dangerous shape, not a harmless one. The config change would have
been made on the strength of a gate, and the gate would have been scored under
the simulator's rule. The evidence licensing the change would have described a
bot that was not the one running, and nothing in the output would have said so.

### What this invalidates

**Nothing.** With one scoped strategy the behaviours are identical, so no gate
verdict and no live decision rests on the difference. It is recorded because the
register's standard is that a divergence is listed when it is found, not when it
first costs something.

### CLOSED by `tests/test_cooldown_key_matches_sim.py`

Live now keys by `(strategy, symbol)`, with `strategies.DEFAULT_OWNER` for
legacy ledger rows that predate the strategy tag — dropping those would silently
shorten the cooldown on the oldest positions, which are the ones most likely to
be re-entered.

The test asserts the behaviour (one strategy's exit does not block another's
entry, and the same strategy's still does), and reads both sources so it fails
if either side drifts back. "Fixed in code" is not closed.

## #21 — the live swing scanner fills inside the entry zone intraday; the simulator fills at the next open

**Status: open by construction** (2026-08-11, Phase 15).

`swing_sectors` derives its entry conditions and zone entirely from completed
daily bars — one implementation, `assess()` — and two callers ask it the same
question with different prices. The 15:45 cycle and every §62 arm test the
LAST COMPLETED CLOSE against the zone and fill at the next open, which is
exactly the backtester's model. The live `swing_scan` job tests a LIVE QUOTE
against the same zone every 30 minutes and fills at that quote.

### Direction of bias: genuinely ambiguous

An intraday trigger catches deeper pullbacks (fills lower inside the zone —
better entries) and also catches knives the daily close would have dodged (a
fund that touched the zone at 11:00 and closed 3% below it enters live and
does not enter in the sim). Neither effect has been measured, which is why
this is a register entry and not a footnote.

### What would close it

An hourly-resolution arm on §25's Alpaca hourly snapshot (one vendor, one
price basis for signal and fill — the trap `src/intraday.py` documents),
registered as its own spec family. Until then, every §62 number describes the
CYCLE's swing behaviour, not the scanner's.

### What bounds it meanwhile

The scan re-runs the drift guard at order time; the coid is shared with the
cycle so the two paths cannot double-enter; and while `swing_sectors` ships
`enabled: false` the scanner places nothing at all — the divergence is
currently between the simulator and a dry run.

## #22 — the test suite paged the operator

Not a sim/live divergence; the same shape — a control that could not see its
own failure — in the channel that reports the real ones.

`watchdog.load_env()` fell back to the repo-root `.env` when the cwd held
none. `tests/test_alert_delivery.py`'s negative control runs the real
entrypoint from a temp cwd with no `.env`, so it loaded the operator's real
`ALERT_WEBHOOK_URL` and posted false "Trading agent needs attention" alerts
(HALT present — true only of the temp dir) to the live ntfy topic on every
suite run. The test stayed green because it watched only its local receiver.

Found 2026-08-22 in repete2 by polling the topic to confirm a genuine test
alert: ~120 messages in 12 hours across all three bots, the real one buried.
This repo is where the code was ported from.

### Direction of bias

Alert fatigue. A genuine page would be invisible among false ones.

### CLOSED 2026-08-22

Fallback removed — `load_env` loads the cwd `.env` only and returns what it
loaded. Every launcher already cds to the repo root, so production never used
the fallback. `test_load_env_NEVER_reaches_the_operators_real_dotenv` fails on
the old code on any host with a repo `.env` and skips, saying so, where there
is none. Verified by running the full suite and polling the topic: 0 messages.

---

## #23 — the live kill retires a strategy's entries; the simulator has never modelled it

**Found 2026-08-23**, while naming the `live_kill` rail (#143). Not a defect and
not open-by-construction — a third kind, and the entry is written to keep those
apart.

`risk.live_kill_blocked` (`src/risk.py:1224`) refuses ENTRIES for a strategy
whose live record shows profit factor below `pf_below` over at least
`min_trades` closed trades. It ships armed: `enabled: true, min_trades: 15,
pf_below: 0.8` (`config.yaml:804`). Two callers, both live —
`src/main.py:1570` and `src/swing_scan.py:181`.

`src/backtest.py` never calls it. Zero hits for `live_kill`,
`strategy_live_stats` or `closed_trades`.

### The mechanism

Its own docstring calls it **"the live-side mirror of the backtest enablement
gate"** (`src/risk.py:1226`), and `CLAUDE.md` repeats the phrase. That framing
is what hid the gap, because the two are not one mechanism wearing two hats:

- `backtest.enablement_gate` (`src/backtest.py:1623`) is an **offline,
  once-per-decision** verdict on a completed OOS run.
- `live_kill` is a **continuous, in-run** rail.

The enablement gate has no in-run analogue on either side, and the live kill has
no offline analogue. Calling either one the mirror of the other implies a
symmetry that was never built.

### It is not hypothetical, and it is binding today

Measured 2026-08-23 through the repo's own functions inside the live container:

| strategy | live closed trades | live PF | `live_kill_blocked` |
|---|---|---|---|
| tsmom | 15 | 0.145 | **FIRING** |
| meanrev | 2 | inf | None |
| ma_crossover | 1 | 0.0 | None |

Three strategies are enabled. **One of them is retired on the live bot right now
and fully active in every simulation.**

### Read the production ledger, not this repo's

The working copy's `memory/ledger.jsonl` is a frozen pre-cutover copy. It greps
**zero** for `live kill` and computes tsmom at n=11 — armed but not binding. The
Bizon's greps **fifteen** and computes n=15. State migrated at the 2026-08-20
cutover and the local copy stopped advancing.

Recorded because it is a live trap, not a footnote: the first investigation of
this divergence grepped the nearest ledger, found nothing, and could not tell
whether the claim was aspirational. Any statement about the live record here
means the deployment host.

### Direction of bias

**Toward flattering the simulator, and nameable rather than unknown.** The kill
only ever REMOVES entries. So the simulator enters trades live would refuse, for
a strategy whose live record is bad, wherever a strategy's run-to-date PF would
trip the floor — and is a no-op everywhere else. **Magnitude unmeasured.**

### Why it stayed invisible

The asymmetry was intended, which is exactly why nobody looked for it.
`knowledge/backtest_candidates.md:250` (§10) leans on the kill in writing while
flagging tsmom failing its own gate:

> `HONEST FLAG: tsmom on the CURRENT universe FAILS the gate on this recent
> window (PF 1.02 …) … No action beyond the expansion …; live_kill remains the
> realized-trades fast path.`

So the record declined to act on a PF 1.02 backtest flag *because* the live kill
would catch it. The live kill then caught it. That is the protocol working — and
it is also the finding: **the live rail made a decision the simulator's numbers
did not.** The two sides no longer agree about which strategies trade, and until
now nothing said so.

### What this does and does not invalidate

**No verdict is reopened.** No registered spec references `live_kill`; no rule in
`gatespec.RULES`/`SELF_RULES` depends on it; §10's reasoning is not invalidated
by the simulator lacking a rail it never claimed to have. §41 set the precedent
that a simulator finding does not retroactively re-score sections.

**What it does mean** is that every gate from §1 onward scored an ensemble in
which tsmom trades to the end of the window, while the live ensemble stopped
entering it on 2026-08-20.

### Not a claim of value

The EDGE tally is unchanged by this. `knowledge/backtest_candidates.md` owns
that count and it is not restated here.

### What would close this

**Not** wiring `live_kill_blocked` into `simulate_ensemble`. The slot exists —
`src/backtest.py:1097`, inside the existing `try:`, so attribution would work
automatically — and taking it would be the wrong move today. tsmom is one of
three enabled strategies in every ensemble baseline arm, so the baseline of
essentially every one of the 106 registrations would move. The byte-identity
invariant is asserted in prose (`src/backtest.py:1475`, `:1015`, `:607`, `:961`)
but `tests/test_run_gate_reproduces.py` pins runner determinism only, not
cross-version stability — so **nothing would go red** and 114 verdicts would
silently become non-comparable. Ordering is not neutral either: live checks the
kill BEFORE `pre_trade_checks`, and `pure_checks` raises on first failure, so
placing it after `:1097` would undercount `live_kill` against live.

The cheap measurement instead: instrument `by_strategy` (`src/backtest.py:942`)
with gross win and gross loss at the exit site (`:993-999`, where `owner` and
`t.pnl` are already in scope) and **report** how many entries a shadow live-kill
would have refused, without wiring it to the entry decision. `SimTrade`
(`src/backtest.py:47`) carries no strategy field, which is why `Result.trades`
cannot be partitioned after the fact — the aggregation happens at the exit site
or not at all. That yields the magnitude with zero effect on frozen verdicts,
and it is what would license deciding the open question below.

### The open question this does not answer

Should the enablement gate apply the same PF floor to SIMULATED closed trades
that the live bot applies to real ones?

**For:** a gate that scores a bot without a rail the live bot has is scoring a
bot that does not exist — this register's own opening sentence.

**Against:** the kill is a rule about LIVE evidence. Simulated closed trades are
not live evidence, and applying a live-record rule inside a backtest may just be
the enablement gate under another name.

Undecided, deliberately. It needs its own pre-registration, not a judgement call
made while writing a register entry.

### Meanwhile

`tests/test_live_kill_is_live_only.py` pins the gap in both directions — the
simulator does not call it, the live path does — so the register cannot drift
from the code in silence. That test does not close this entry. It makes the
entry falsifiable, which is a different thing.

---

## #24 — the live judge now reads a per-symbol dossier the simulator cannot represent

**Found and registered 2026-08-23**, by the change that caused it — not
afterwards. Open **by construction**, and the third of that kind after #14 and
#15, for the same reason those two are.

`src/research.py` gives the live judge a file on the name in front of it: the
news that name has carried and how those trades resolved, how the judge called
it before and whether it was right, earnings proximity, and what the book holds
in it and its sector. It reaches the prompt through
`memory.context_for_llm`'s `research` block, budgeted at 900 chars.

`src/backtest.py` has no judge. It has `judge_model.py`, which replays the live
judge as a **distribution** — a scale drawn from a histogram fitted over 250
real decisions. #15 already states why that cannot be closed by better
modelling:

> "It has no prompt, no text, and no reasoning. It cannot represent a principle
> at all, in either direction. This is not a gap that better modelling closes;
> there is nothing in `judge_model` for a principle to attach to."

A dossier is the same shape of thing. There is nothing in a histogram for
"AAPL missed Q4 guidance and the judge was wrong about it last time" to attach
to.

### Direction of bias

**Unmeasured, and genuinely two-sided** — unlike #19 or #23, where the sign was
nameable. Better per-symbol evidence should make the judge veto more of the bad
trades and shrink fewer of the good ones. Which of those dominates is exactly
what the refit will measure, and asserting a direction before measuring it is
the thing this register exists to prevent.

### What this does NOT invalidate, and the one thing it does

**No verdict is retroactively re-scored.** §41 set that precedent. Every gate
scored before today measured a judge that did not read a dossier, and that was
true when those gates ran.

**What it does invalidate is the calibration, going forward.**
`knowledge/judge_calibration.json` was fitted 2026-07-16 → 08-19 under the old
regime. The same change that opened this divergence bumped
`backtest.judge_model.context_version` 1 → 2, so `judge_model` now **refuses**
that calibration and no gate can be scored with `--judge-model` until it is
re-fit. The gap is declared as `calibration_refit_pending: true`.

That refusal is deliberate and it is the difference between this entry and the
ones that came before it: **the divergence announces itself instead of being
discovered later.** #8 sat closed-on-paper and open in fact for three days;
§35–§41 were all scored with the judge model switched off and nobody noticed.

### Not a claim of value

The EDGE tally is unchanged. `knowledge/backtest_candidates.md` owns that count
and it is not restated here.

### What would close this

Nothing available today, and the entry should not pretend otherwise. Closing it
means the simulator reasoning over the same evidence, which means a model call
per bar — rejected on the record for being slow, non-reproducible, and
**contaminated by hindsight the model absorbed in training**. That last is the
same argument that keeps signals deterministic: a model trained past 2003 knows
what 2003 did.

What is *reachable* is bounding it: after the refit, compare the deepened
judge's verdict distribution against the version-1 histogram and report how far
it moved. That converts an unmeasured gap into a measured one without closing
it, which is the same move `#23`'s "what would close this" proposes for the
live kill.

### Meanwhile

`tests/test_judge_verdict_surface.py` pins that the dossier reaches **both**
live entry paths, and that no field in it can redirect a trade —
the judge may still only veto or shrink.

---

## #25 — the judge is a different model, and nothing has measured the difference

**Opened 2026-08-28.** `llm.provider` moved from `anthropic` to `local`; the
judge is now `qwen3.8-27b`, self-hosted on the Bizon's two A100s, reached over
`ollama-net` with no API key.

### Why the honest ordering was not followed

The plan was shadow-first: `src/llm_shadow.py` runs the local model on the
**same** real signals, logs the comparison, and touches no trade. Then, knowing
whether the two agree, switch.

That baseline requires Anthropic to answer. Anthropic running out of credits is
the entire reason for the switch. So the measurement that would de-risk the
change cannot be collected until the change is no longer needed — the ordering
is not merely inconvenient, it is unreachable from here.

The alternative was to keep waiting. `on_unavailable: block` had refused **every
entry since 2026-08-21** — 53 signals — and the book had drained 22 → 15 on
exits alone. Waiting is not the neutral option it looks like; it is a decision
to keep not trading, taken by default rather than on the record.

So: switched, unmeasured, and **declared here rather than discovered later.**

### What is actually known

**First recorded, 2026-08-28 (superseded below, kept because the correction is
the point).** Four probe calls: valid schema 4/4, no `<think>` leakage, and
`downsize` 4/4 at scale 0.3–0.5. Read at the time as "downsizes harder than
Claude", with the caveat that four calls is not a distribution.

**Measured the same day, and the caveat turned out to be the finding.**
`scripts/judge_disposition.py` replays real past buy signals through the live
judge. 20 distinct `(symbol, strategy)` signals, five passes over the *identical*
set, n=100, zero degraded:

| | pooled | across the 5 passes | fitted Claude (n=250) |
|---|---|---|---|
| `veto_rate` | 0.220 | **0.100 – 0.300** | **0.044** |
| `downsize_rate` | 0.760 | 0.700 – 0.900 | 0.858 |
| `approve_rate` (full size) | 0.020 | 0.000 – 0.050 | ~0.142 |
| mean scale, non-veto | 0.474 | 0.431 – 0.520 | 0.595 |
| exposure per signal | 0.370 | 0.320 – 0.440 | 0.569 |

Three things, in order of how badly each could be misread.

**1. The spread is the headline.** Identical signals, identical context, same
afternoon: `veto_rate` moved between 0.100 and 0.300. The judge samples, so any
single pass is a draw. The first two runs of this script returned 0.300 and
0.150 and *both were reported as "the veto rate"* before the third made it
obvious. `--repeat` exists because of that, and a one-pass number from this
script should not be quoted.

**2. It vetoes far more than the fitted judge, and that survives the spread.**
Even the most permissive pass (0.100) is more than twice `0.044`; pooled it is
five times. It also almost never approves at full size — 2 of 100 against
roughly 34 of 239. Net, ~**65%** of the exposure per signal.

**3. It is not blocking the bot.** 78 of 100 signals passed the judge. That was
the live question — `on_unavailable: block` had already produced a bot that
could not enter, and a judge vetoing everything is the same bot with a
healthier-looking config. It isn't that.

**What this is NOT: an agreement rate.** Different judge *and* different context
vintage — past signals under today's book, lessons and dossier. Nothing here
pairs a local verdict with the Claude verdict on the same prompt, because those
prompts were never stored (below). It characterizes a disposition. Anyone
quoting it as "the models agree/disagree N% of the time" is quoting something
that was not measured.

### The calibration, and why this entry is not the dangerous kind

`backtest.judge_model.context_version` went 2 → 3 in the same change, so
`judge_model.load_calibration` **refuses** and no gate can be scored with
`--judge-model`. `calibration_refit_pending` stays `true`.

Note what nearly went wrong. That trigger was documented as "bump whenever what
the judge READS changes". Read literally, swapping the model requires **no**
bump — the inputs are untouched. The guard would have sat there looking healthy
while describing a judge that no longer existed. The trigger now covers *who*
reads too, including decoding settings, and
`tests/test_local_provider.py::test_switching_provider_invalidates_the_judge_calibration`
fails if a keyless provider is ever shipped against a calibration that claims to
match it.

`config.yaml` is inside `modelver`'s fingerprint, so the decision surface
re-versions on this edit by itself and `review.py` segments the track record at
the switch. Nothing has to remember.

### The latency finding, recorded because it nearly hid

Flipping the provider alone would have changed nothing observable except the
error string. vLLM serves qwen3 with `--reasoning-parser=qwen3`, so the model
generates a long reasoning trace by default:

| | latency | completion tokens | verdict |
|---|---|---|---|
| default | 47.1s | 2,261 | `downsize` |
| `enable_thinking: false` | **4.8s** | 224 | `downsize` |

`llm.timeout_seconds` is 30. Every default-mode call exceeded it, returned a
degradation, and left entries blocked — a bot configured with a working judge
and still refusing to trade, for a reason no config line named.

The 2,000 discarded tokens were also unauditable: `reasoning_content` came back
empty, `llm.py` slices the JSON out of `content` and drops the prose, and the
ledger stores only the visible `reasoning` field. The latency bought reasoning
that no record retains.

### What would close this

Restored Anthropic credits, then `src/llm_shadow.py` on live signals until the
agreement rate is measurable — the comparison this change had to skip.

**Why a replay cannot substitute, checked rather than assumed.** The obvious
shortcut is to re-run the *recorded* Claude prompts through the local model, no
credits required. It does not exist: `Ledger._store_prompt` writes prompt and
context bodies to a sidecar, but that sidecar landed **2026-08-22, one day
after the judge went down**, so it has never captured a single Claude decision.
On the Bizon the `decision_prompts` stream is absent entirely and
`memory/agent.db` holds no such rows. The 250 buys behind the calibration
predate prompt storage, and their contexts — book, lessons, dossier as of that
day — are unrecoverable. An approximate replay would compare the local model on
a prompt Claude never saw, which is a new measurement wearing an agreement
rate's name.

**What the switch bought instead, in the other direction.** The judge works
again, so the sidecar is now recording. Every decision from the first live
cycle forward stores the exact prompt this judge saw. When credits return,
Claude can be replayed against *those* — a real paired comparison, just
oriented the opposite way to the one originally planned. It needs no new code.

Short of any of that, a refit (`scripts/calibrate_judge.py --write`) replaces a
calibration fitted to the wrong judge with one fitted to this judge. It says
nothing about which judge is *better* and must not be reported as if it did —
and per the table above it needs enough passes to survive the sampling spread,
not one.

### Meanwhile

The dependency this switch created — a vLLM container outside repete's compose
project — is monitored as of 2026-08-29: `llm_client.probe()` runs in the
5-minute docker healthcheck, in `host_warnings` at the 09:35 open, in the
watchdog's daily page, and in `run_cycle` itself, which alerts *before* the
first signal is judged. A repeat of the 2026-08-21→28 shape (entries silently
refused for a week) now announces itself the same hour.
`tests/test_judge_reachability.py` pins that the probe can never become a
`preflight.run()` failure — a dead judge degrades entries, never stops the
cycle that manages the open book.

### Not a claim of value

The EDGE tally is unchanged. `knowledge/backtest_candidates.md` owns that count.
A cheaper judge that trades is not evidence of an edge; it is evidence of a bot
that is running.


## #26 — the gates and the live rails both read after-cost, but not the same cost

**Opened 2026-10-28, by the change that caused it.** Owner decision, in his
words: *"Yes: make gates, backtests and the drawdown/retire rails use after-cost
results first (repete4's override included). Entry cost filters later, bot by
bot."*

Live charges every filled order the console's cost model
(`operator_settings.py`), and since this decision the drawdown, daily-loss and
live-kill rails and the decay monitor all read results after it. The simulator
now charges the same formula (`cost_models.order_cost`, the verbatim copy) on
every simulated fill, so the two agree about **what** a cost is. They do not
agree about **which** cost, in three ways — all deliberate or inherited, none
hidden:

1. **The block.** A gate charges the block pinned in its spec
   (`operator_costs:`; the research values, `cost_models.RECOMMENDED`, written
   out in full), because a backtest that moved when someone changed a setting on
   a web page would not be reproducible. Live charges whatever
   `memory/operator_settings.json` says that cycle. **Direction:** depends on
   the setting. At the research values (US$0.005/share, US$1 minimum, 1% cap)
   they match. At the flat US$9/order the console started with (migration
   0032), live pays ~5–9× what the gate assumed, so a gate verdict is
   **optimistic** by the difference — which `operator_cost_paid_usd` on the
   verdict row lets anyone compute.
2. **Short opens.** `main.record_fill_quality` and adoption charge only
   `action == "buy"` entries, so live never charges the order that opens a
   short. The simulator charges every opening order, shorts included.
   **Direction:** the sim is **pessimistic** for a short leg by one order's
   cost. Inert today — nothing enabled shorts (xsmom ships `enabled: false`).
   Recorded as a follow-up for the live side, not fixed here.
3. **Timing inside a cycle.** Live charges a close the moment it books it, and
   rail equity is computed once per cycle (refreshed after reconcile), so an
   entry later in the same cycle's loop does not see a close charged earlier in
   that loop until the next cycle. And if a cycle's rail equity is a new high,
   the peak can sit above it by at most that cycle's reconcile costs until the
   next high. The sim charges each fill as it happens. **Direction:** live is
   conservative by at most one cycle's close costs (a few dollars) — never a
   looser rail.

Two things are **not** divergences, and are pinned so they cannot become one:
sizing (both sides size on broker / gross equity; the cost never touches the
sim's cash — `test_sizing_is_untouched_by_the_cost`) and the drawdown rail's
equity (both measure after cost — `risk.rail_equity` live, `_rail_eq()` in both
simulate paths, `test_the_sim_drawdown_rail_reads_after_cost_equity`).

### What it invalidates

Nothing retroactively. Every frozen spec predates the field, runs with no
operator cost, and reproduces byte for byte (`run_gate.apply_operator_costs`
removes any block config.yaml might carry). Gross verdicts stay gross verdicts:
read them as **upper bounds** on what the after-cost bot would have scored.
