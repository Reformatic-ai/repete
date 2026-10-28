# Backtest candidates (from external knowledge review, 2026-07-16)

Rules distilled from the autonomoustrading.io review + our own research.
NOTHING here goes live without passing `backtest.enablement_gate`
(walk-forward OOS: positive, PF>=1.3, >=15 trades, beats B&H raw /
risk-adjusted / exposure-matched).

## METHOD NOTE — frozen snapshots required (discovered 2026-07-17)
Alpaca historical bars for an IDENTICAL request drift between fetches minutes
apart during market hours (same window, same code — verified simulate() is
bit-for-bit deterministic on fixed input). Consequence: **any variant
comparison across separate API fetches is invalid.** All gate runs must use
one frozen `--bars-file` snapshot per comparison (2026-07-17 snapshot:
`memory/bars_snapshot_2020_2026-07-10.json`). The 07-16 tsmom-gate adoption
was a casualty of this and was reverted after frozen-data re-validation.

## METHOD NOTE — every WIDE-SNAPSHOT number here predates divergence #17 (2026-08-05)
Until 2026-08-05 `simulate_ensemble` applied **no per-strategy universe filter**,
so on a 500-name wide snapshot the three enabled strategies traded all 500 names
while live they trade 38. Measured on `bars_wide_2022-01-01_2026-07-24`, shipped
config, with the fix as the only change: entry signals 245,213 → 5,347,
**drawdown-blocked 243,814 → 4**, deployment 4.09% → 75.18%, bars fully in cash
92.39% → 4.55%.

**Consequence: re-running §43, §48, §50 or §51 today will NOT reproduce their
recorded numbers**, and this is the reason — not nondeterminism, not a
dependency bump.

**No verdict is withdrawn or re-scored.** They stand as measurements of the bot
as simulated then; §41 set that precedent when its own simulator finding could
have reopened ten verdicts and reopened none. §48's conclusion that the drawdown
rail was masking measurement SURVIVES, with the correction that much of the
signal flow it blocked came from names the live bot cannot trade — so the rail
was masking measurement of a bot that did not exist. The post-fix return is NOT
evidence of an edge: same survivor-selected universe, §51 sized that at
+200.28pp, and §52's freeze is unchanged. Mechanism and closing tests:
`docs/divergences.md` #17.

## 1. Earnings-blackout entry filter — SPLIT VERDICT 2026-07-17
**Spec:** block new entries in single names (ETFs exempt) within N calendar
days before a scheduled earnings report (tested N = 0, 3, 5). Exits
unaffected.
**Rationale:** gaps break stop assumptions; overnight gap risk is
uncompensated for a 2-day-minimum swing hold.
**Data source:** yfinance `get_earnings_dates` (src/earnings.py: 24h cache,
fail-open, ETFs naturally exempt; ~50 dates/symbol back to 2014 incl.
scheduled future dates). Frozen calendar for gate runs:
memory/earnings_snapshot.json.
**Result (frozen bars + frozen calendar, same snapshot both strategies):**
- **tsmom: ADOPTED, N=3** — wins IS (+3.89 PF 2.62, dd 1.13 vs unfiltered
  +3.50 PF 2.29, dd 1.31), passes the gate, OOS +1.38 PF 2.22 ≥ baseline
  +1.25 PF 2.01. Trend entries right before a report carry gap risk with no
  compensating edge. Live as `strategies.tsmom.earnings_blackout_days: 3`.
- **meanrev: REJECTED** — the filter hurt IS monotonically (+0.43 → +0.38
  (N=3) → +0.35 (N=5)): pre-earnings dips that bounce are part of the
  dip-buying edge.
The param is therefore PER-STRATEGY (`strategies.<name>.earnings_blackout_days`),
not a global risk rail.

## 2. Time-varying stop width by vol regime — REJECTED 2026-07-17
**Spec:** stop_atr_mult 2.0 in low-vol regime, test 2.5–3.0 in high-vol
(regime from src/regime.py, deterministic at entry).
**Rationale:** fixed 2×ATR may whipsaw in high-vol regimes.
**Result (frozen snapshot, full grid × both strategies):** widened high-vol
stops were equal-or-worse in every combination — tsmom IS 3.50 (hv off) vs
3.47/3.46 (hv 2.5/3.0); meanrev IS 0.82 (hv off) vs 0.76/0.76. The 2×ATR
stop is already wide enough at this timescale. Param-gated code path
(`risk.brackets.stop_atr_mult_high_vol`) remains for future retests; off in
config.

## 3. Regime gate on tsmom entries — ADOPTED 2026-07-16, REVERTED 2026-07-17
**Spec:** block tsmom entries when SPY < SMA50 (regime down/*), test vs
current behavior (tsmom already requires symbol > its own SMA200).
**Rationale:** the judge repeatedly flags long momentum in down regimes; make
it a testable deterministic rule instead of paying LLM skepticism per trade.
**07-16 result (live-fetch runs — later found unreliable):** appeared to win
IS (+4.10 vs +3.51) with OOS tied; adopted.
**07-17 re-validation (frozen snapshot, both variants same data):** UNGATED
wins IS by return (+3.50 vs +3.31; the gated variant has better PF 2.78 vs
2.29 and lower dd), OOS statistically tied (+1.25 PF 2.01 ungated vs +1.23
PF 2.21 gated). The pre-registered rule (gate must WIN IS and OOS >= ungated)
is not met → REVERTED to `index_sma_period: 0`. The 07-16 IS "win" was API
re-fetch drift, not signal. Honest residual: the gate is roughly return-free
and slightly PF/dd-favorable — a defensible retest candidate after the next
genuine bear regime provides OOS data that actually exercises it.

## 4. Meanrev max-positions-per-cycle cap — REJECTED 2026-07-16, CONFIRMED 07-17
**Spec:** limit meanrev to 1 new entry per cycle (dips cluster on market-wide
down days, concentrating correlated entries).
**Result (07-17 frozen snapshot, both variants same data):** cap=1 wins IS
(+0.82 PF 1.22 vs uncapped +0.43 PF 1.08) but clearly LOSES OOS: +0.73
PF 1.58 / 133 trades vs uncapped +1.33 PF 2.12 / 170 trades. The clustered
dip entries the cap removes were profitable out-of-sample — an in-sample
mirage, now confirmed on clean single-snapshot evidence (the 07-16 rejection
had compared across fetches). Config unchanged (no cap). Param-gated code
path remains; all variants in memory/backtest_trials.jsonl.

## §5 Vol-targeted sizing (2026-07-18) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from the 2023–2026 research pass (vol-aware risk is the one
process-side upgrade with durable evidence). `risk.vol_target`: position
dollars scaled by target_annual_vol / realized_vol(20d), clamped [0.5, 1.5].
METHOD: frozen snapshot memory/bars_snapshot_2020_2026-07-10.json,
earnings_snapshot.json, slippage pinned 5 bps (matches prior rounds),
default params, split 0.7. Pre-registered adoption rule (before running):
adopt per strategy iff (a) OOS return ≥ baseline − 0.25pp, (b) OOS maxDD
strictly improves, (c) IS return ≥ baseline − 1pp.

- tsmom:  base IS +3.50 / OOS +1.25 maxDD 0.7 → vt IS +1.74 / OOS +0.76
  maxDD 0.6. Fails (a) and (c) → **REJECTED**.
- meanrev: base IS +0.43 / OOS +1.33 maxDD 0.3 → vt IS −0.02 / OOS +1.10
  maxDD 0.2, PF 2.12→2.16. Passes (a) −0.23pp, (b), (c) → **ADOPTED**
  (marginal, rule-driven; scoped via risk.vol_target.strategies=[meanrev];
  monitor live).

## §6 Down-regime gross-exposure cap (2026-07-18) — REJECTED (cannot bind)

`risk.regime_exposure`: in a down-trend regime, total gross exposure capped
at 50% of equity. Same frozen-snapshot method + pre-registered rule. Result:
IS/OOS identical to baseline for both tsmom and meanrev — at 1%-per-trade
fixed-fractional sizing with max 5 positions, gross exposure never
approaches 50%, so the cap cannot bind and (b) cannot be satisfied. A
forced-binding sanity check at cap=3% did alter behavior (tsmom OOS +1.22
maxDD 0.5 vs +1.25 maxDD 0.7) but 3% is not a deployable setting. Config
stays disabled; param-gated rail remains for a future where sizing scales.

## §7 Chandelier trailing stop (2026-07-19) — ADOPTED for tsmom, REJECTED for meanrev

Candidate from the common-trade comparison + literature: ratcheting
volatility trail on top of the existing 2×ATR initial stop. Effective stop
= max(initial stop, high-water-since-entry − 3.0×ATR(14)), never lowered
(LeBeau chandelier; Kaminski & Lo 2014 and Han/Zhou/Zhu 2016 find stop
rules add value specifically under momentum/serial correlation — so the
PREDICTION is: helps tsmom, hurts or does nothing for meanrev, whose exits
are already fast). Single pre-registered config: trailing_atr_mult=3.0,
no grid. METHOD: frozen snapshot memory/bars_snapshot_2020_2026-07-10.json,
earnings_snapshot.json, slippage pinned 5 bps, current live params,
split 0.7, strategies tsmom + meanrev.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS return ≥ baseline − 0.25pp, (b) OOS maxDD strictly improves,
(c) IS return ≥ baseline − 1pp.

## §8 Risk-based (stop-distance) sizing (2026-07-19) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from common-trade (Van Tharp / fixed-fractional-risk school;
Balsara 1992: smoother equity curves). Position dollars =
equity × risk_pct / stop_distance_fraction, so every trade risks the same
equity fraction entry-to-stop, instead of the same notional. Pre-registered
risk_pct = 0.10% (≈$100 true risk on $100k at a typical 4–8% ATR stop →
$1.2k–2.5k notional, deliberately the same scale as today's 1%-notional
sizing so the comparison is allocation shape, not leverage). When active it
REPLACES vol_target scaling for that strategy (both normalize by realized
vol; compounding them double-counts). Existing caps still clamp.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS return > baseline, (b) OOS maxDD ≤ baseline + 0.25pp,
(c) IS return ≥ baseline. (Return-dominant branch: the claim is better
risk-normalized allocation, not tail-cutting.)

## §9 Same-ticker re-entry cooldown (2026-07-19) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from common-trade (min_days_between_entries_same_ticker=5).
Blocks a NEW entry in a symbol for N calendar days after that symbol's
last exit — anti-whipsaw churn control. Weakest evidence base of the three
(practitioner hygiene; no strong academic support). Single value: 5 days.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS PF ≥ baseline PF, (b) OOS return ≥ baseline − 0.25pp,
(c) OOS maxDD ≤ baseline. Expected to bind mostly on meanrev
(dip re-entries cluster); prediction: neutral-to-small effect.

### §7–§9 RESULTS (2026-07-19, same session as registration; baselines
reproduced yesterday's numbers exactly, confirming snapshot integrity)

Baselines: tsmom IS +3.88 dd 1.1 | OOS +1.38 PF 2.223 dd 0.7 (51 trades);
meanrev (with then-live vol_target) IS −0.02 dd 0.8 | OOS +1.10 PF 2.161
dd 0.2 (170 trades).

§7 trail 3.0×ATR:
- tsmom: IS +4.60 dd 1.1 | OOS +2.37 PF 2.598 dd 0.5 (94 trades — trail
  exits + re-entries roughly double activity). Passes (a) +0.99pp, (b)
  0.5<0.7, (c). **ADOPTED** (`risk.brackets.trailing_atr_mult: 3.0`,
  `trailing_strategies: [tsmom]`; live ratchet in main.update_trailing_stops
  via broker.replace_stop). The Kaminski-Lo/HZZ momentum prediction held.
- meanrev: OOS +1.11 dd 0.2 — dd does NOT strictly improve (tie). Fails (b)
  → **REJECTED** (exits already fast; +0.01pp is noise). As predicted.

§8 risk-sizing 0.1%:
- tsmom: IS +6.19 dd 2.3 | OOS +1.96 dd 1.1. Fails (b) (1.1 > 0.7+0.25)
  → **REJECTED** — return bought with proportionally more drawdown.
- meanrev: IS +0.21 dd 1.4 | OOS +2.38 PF 2.226 dd 0.3. Passes (a) +1.28pp,
  (b) 0.3 ≤ 0.45, (c) → **ADOPTED**, and it beats + SUPERSEDES vol_target
  (§5) head-to-head on this snapshot (+2.38 vs +1.10), so vol_target is now
  disabled (both normalize by realized vol; only one may be active).

§9 cooldown 5d:
- tsmom: OOS +2.35 PF 4.034 dd 1.0. Fails (c) (dd 1.0 > 0.7) → **REJECTED**
  despite the eye-catching PF — the rule was registered first and maxDD is
  the condition it fails. A future round may re-register with a dd-tolerance
  branch if live evidence warrants.
- meanrev: IS +0.50 PF 1.123 dd 0.6 | OOS +1.06 PF 2.245 dd 0.2. Passes
  (a) 2.245 ≥ 2.161, (b) −0.04pp ≥ −0.25, (c) dd ≤ → **ADOPTED**
  (`risk.reentry_cooldown: {days: 5, strategies: [meanrev]}`).

COMBINED-OVERLAY VERIFICATION (meanrev risk_sizing + cooldown, since each
was adopted alone): IS +1.34 PF 1.163 dd 1.1 | OOS +2.24 PF 2.282 dd 0.3 —
holds up (OOS well above baseline, dd inside §8 tolerance, best IS of any
arm). Verification only, not an adoption decision.

METHOD NOTE: adoption split by strategy forced per-strategy scoping into
the params (`trailing_strategies`, `risk_sizing.strategies`,
`reentry_cooldown.strategies`) — a global flag would have applied a tsmom
result to meanrev and vice versa.

## §10 — Universe expansion 25 → 38 unique (2026-07-21) — ADOPTED

Motivation: evidence velocity. At ~2 closes/week every learning mechanism
(lessons, calibration, live_kill, postexit) is starved; a wider gated
universe multiplies independent setups at UNCHANGED per-trade risk, and the
correlation heat cap (adopted earlier today) prevents the wider book from
collapsing into one bet.

Candidates (18, sector-diverse liquid large caps): XOM CVX WMT COST PG KO HD
MCD CAT BA LLY ABBV GS BAC ORCL AVGO CRM DIS.

METHOD (per the standing METHOD NOTE): ONE frozen snapshot — ~4.5y
(1097 daily bars/symbol, all 38 complete) fetched once 2026-07-21, plus a
frozen yfinance earnings calendar; every comparison ran offline from those
files. Live params only (no grid re-search — re-picking params inside a
universe decision would be data mining). walk_forward split 0.7; trials in
memory/backtest_trials.jsonl. Note: a first attempt on a 2y snapshot
produced zero OOS trades for tsmom/meanrev — the 30% OOS window (150 bars)
was shorter than their 200-bar SMA warmup. Windowing artifact, not signal;
the run was discarded and refetched longer. Recorded so nobody repeats it.

RESULTS (OOS, same snapshot, current 25 names vs expanded 38 unique):

| strategy     | current univ                    | expanded univ                  | gate (expanded) |
|--------------|---------------------------------|--------------------------------|-----------------|
| tsmom        | +0.02%, PF 1.02, 57t, dd 0.46%  | +0.72%, PF 1.72, 54t, dd 0.56% | PASS            |
| meanrev      | +0.65%, PF 1.57, 74t, dd 0.32%  | +0.84%, PF 1.51, 103t, dd 0.43%| PASS            |
| ma_crossover | +1.19%, PF 2.14, 56t, dd 0.45%  | +1.59%, PF 2.49, 54t, dd 0.56% | PASS            |

DECISION (pre-registered rule: adopt only if every enabled strategy passes
the gate on the expanded universe): all three pass, 3/3 improve or hold →
**ADOPTED**, config `symbols:` extended to 38 unique names. CORRECTION, same day: the original list held 25 names (not 20 — XOM/PG/HD/KO/DIS were already present past the visible fold), so the expansion is 25→38 and the first config edit briefly duplicated those five (43 entries). The dashboard boot screen surfaced the 43; duplicates removed. The backtest itself was always clean — its bars dict deduped to 38 unique symbols.

HONEST FLAG: tsmom on the CURRENT universe FAILS the gate on this recent
window (PF 1.02 — momentum has been thin in the 20-name universe lately).
This is exactly the drift class src/revalidate.py (also added 2026-07-21)
exists to catch quarterly. No action beyond the expansion (which restores
tsmom's OOS to PF 1.72); live_kill remains the realized-trades fast path.

## §11 — risk_sizing.risk_pct 0.1 → 5.0 (2026-07-23) — ADOPTED, but it does not bind

Owner requested 5% per-trade risk. Re-run on the SAME frozen snapshot §8 used
(`memory/bars_snapshot_2020_2026-07-10.json`, 25 symbols × 1637 bars,
2020-01-02 → 2026-07-09), live params, no grid re-search, per the METHOD NOTE.

**This is NOT a re-validation of §8.** §8 deliberately pre-registered 0.10%
*because* it produced the same notional scale as 1%-notional sizing, so the
test measured "allocation shape, not leverage". 5% asks a different question of
the same data, so it is recorded here as a new section.

Applying §8's pre-registered rule (adopt iff (a) OOS return > baseline,
(b) OOS maxDD ≤ baseline + 0.25pp, (c) IS return ≥ baseline):

| risk_pct | IS return | OOS return | OOS maxDD | OOS trades | OOS PF | verdict |
|---|---|---|---|---|---|---|
| 0.1 (baseline, §8) | +1.34% | +2.24% | 0.3% | 160 | 2.282 | — |
| 1.0 | +1.87% | +2.63% | 0.5% | 161 | 2.164 | passes |
| 2.0 | +1.87% | +2.63% | 0.5% | 161 | 2.164 | passes |
| **5.0** | **+1.87%** | **+2.63%** | **0.5%** | **161** | **2.164** | **PASSES** |

(a) +2.63 > +2.24 ✓  (b) 0.5 ≤ 0.55 ✓  (c) +1.87 ≥ +1.34 ✓ → ADOPTED.

**The finding that matters more than the verdict:** 1.0, 2.0 and 5.0 are
*byte-identical*. They are the same setting. `risk.size_order()` applies
`max_order_value_usd` ($2,000) AFTER the risk-based calculation, so on $100k
equity every one of them clamps to a $2,000 position:

| risk_pct | stop 4% | stop 7% | stop 15% |
|---|---|---|---|
| 0.1 | $2,000 / $80 loss | $1,400 / $98 | $600 / $90 |
| 1.0 / 2.0 / 5.0 | $2,000 / $80 | $2,000 / $140 | $2,000 / $300 |

So the gate validated **"$2,000-capped sizing beats 0.1%-uncapped sizing"**,
not "5% risk is safe". True per-trade risk stays 0.08–0.3% of equity.

Two corrections to claims made on 2026-07-22 while raising this parameter,
both refuted by the code and re-measured here:
1. "One 5% stop-out risks $5,000 and trips the 3% daily kill switch" — **false**.
   The clamp caps a single loss at ~$80–300; the kill switch fires at $3,000.
2. "At `max_portfolio_heat_pct: 4.0` every meanrev entry would be rejected" —
   **false**. Four open positions total ~$560 of heat against a $4,000 cap; a
   fifth entry is allowed. The cap was never binding, so the 4.0 → 10.0 raise
   was unnecessary and has been **reverted**.

**Consequence / open question:** the effective position-size lever is
`max_order_value_usd`, not `risk_pct`. Raising it would make 5% genuinely bind,
and only then could a single stop-out approach `daily_loss_limit_pct`. That
pairing must get its own gate run before it is changed.

## METHOD NOTE 2 — the OOS window is contaminated (2026-07-23)

The 70/30 walk-forward split puts OOS at **2024-07-23 → 2026-07-09**, and
§1–§11 scored **~11 adopt/reject decisions on that same window**. It is
therefore a SELECTION set, not a clean holdout: repeatedly choosing the variant
that scores best on a fixed window is in-sample fitting one level up.

Consequences, binding on all future work:
1. Quoted OOS figures (incl. "OOS +2.63%, PF 2.16, 3.58x exposure-matched") are
   an **upper bound on the true edge**, not evidence of it.
2. The only clean holdout that exists is the **LIVE FORWARD RECORD** — which is
   exactly what the go-live gate's 30-closed-trades requirement is for.
3. Backtests here **generate hypotheses; only live trading confirms them.** No
   deployment or risk increase may be justified by the mined OOS alone.
4. Every new section must record the **running trial count**, and the bar rises
   with it: with N prior comparisons, a marginal pass is more likely to be luck
   than edge. Prefer candidates that pass by a clear margin.

Trial count through §75: 241 arm runs — from `research/verdicts.jsonl`, checked
by `tests/test_trial_count.py`, printed by `scripts/trial_report.py`. This line
was last hand-typed as "through §11: ~11" and then left for sixty-two
sections; it is now the one the guard reads, so it cannot drift again.

**That figure is a floor, not N.** The register begins at §35. Seven EDGE
spends predate it (s35's own prior: "EDGE claims stand at 0 for 7 entering
this test"), and every §1–§34 grid arm lives only in the gitignored
`memory/backtest_trials.jsonl`, which `run_gate.py` never wrote to and which
stopped on 2026-07-27. The audit's ~480 estimate layered that untracked log on
top; this count deliberately does not, because a number mixing a tracked
source with an untracked one cannot be re-derived by anyone else.

**Why it matters more than K.** Declared K is 16, and K counts only EDGE.
Of the 225 arm runs, 38 are EDGE and **187 are DIAGNOSTIC or CAPACITY — runs
that chose which EDGE claim to spend budget on while spending none.** That is
the selection Bonferroni-on-K cannot see and Deflated Sharpe exists to price.

## §12 — position-slot cap `max_open_positions` (2026-07-23) — RULE PRE-REGISTERED

**Motivation (live evidence, not theory).** In 9 live days the bot generated
**146 buy signals** and executed **6**. Since 07-17 the ONLY rail blocking
entries is `max_open_positions: 5` (**68 blocks**); the five slots are
permanently held by long-horizon tsmom index positions entered on one day.
Meanwhile the actual RISK rails — portfolio heat (4%), correlation cap,
concentration (10%) — have **never once evaluated a candidate**, because
`max_open_positions` sits upstream of them in `risk.pre_trade_checks`. The
book's real risk is nowhere near any of them (heat ~$560 of a $4,000 cap).

So the binding constraint today is a **concurrency cap, not a risk cap**, and
the bot is rejecting ~100% of its own signal supply. Its own counterfactual
ledger scores the blocked trades **9 bad_veto / 1 good_veto**.

At the observed rate (1 closed trade / 8.86 days) the go-live gate's 30 closed
trades is **~8.7 months** away — and conditioned on the current book (5 slots
held by trend positions with ratcheting trails, 0 entries in 7 days) the
near-term rate is ~0.

**Candidate:** raise `max_open_positions` 5 → 8 → 12, letting the heat and
correlation caps become the real limit on concurrency.

**PRE-REGISTERED RULE (written before running):** adopt the largest N such that
(a) OOS return >= baseline (N=5) return,
(b) OOS maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute (the
    `daily_loss_limit_pct` ceiling — a book that can lose more than the kill
    switch tolerates in one day is not a capacity gain, it is leverage),
(c) OOS profit factor >= 1.3 (the standing gate bar) and >= baseline PF - 0.15,
(d) OOS trade count strictly increases (the whole point is evidence velocity).
If several N pass, prefer the SMALLEST that captures most of the trade-count
gain — capacity we cannot yet justify with live evidence is not free.

### §12 RESULT — SPLIT: meanrev PASSES, tsmom FAILS. Global raise NOT adopted.

One frozen snapshot (`bars_snapshot_2020_2026-07-10.json`, 25 symbols, frozen
earnings calendar), live params, no grid re-search. Trial count: ~13.

| strategy | N | OOS ret | maxDD | PF | trades | verdict |
|---|---|---|---|---|---|---|
| meanrev | 5 (base) | +2.63% | 0.5% | 2.164 | 161 | — |
| meanrev | **8** | **+3.03%** | 0.5% | **2.216** | **180** | **PASS** |
| meanrev | 12 | +2.98% | 0.5% | 2.156 | 182 | PASS |
| tsmom | 5 (base) | +2.12% | 0.6% | 2.398 | 97 | — |
| tsmom | 8 | +2.47% | 0.7% | 1.977 | 156 | **FAIL (c)** |
| tsmom | 12 | +2.64% | 1.1% | 1.730 | 230 | **FAIL (b)+(c)** |

**tsmom fails clause (c):** PF 2.398 → 1.977 → 1.730, well under the 2.248
floor, while win rate slips 43% → 39%. Its return RISES (+2.12 → +2.64), which
is exactly the trap clause (c) was written to catch: the extra trades are not
edge, they are more risk bought at a worse rate. At N=12 drawdown also nearly
doubles, breaching (b). **Pre-registration did real work here** — on return
alone tsmom's N=12 looks like the best cell in the table.

**meanrev passes at both.** Per the tie-break (smallest N capturing most of the
trade gain: N=8 captures 19 of 21 extra trades), the meanrev answer is **N=8**.

**Why this cannot simply be adopted:** `max_open_positions` is GLOBAL. Raising
it to 8 would admit mostly *tsmom* — the strategy that produces 93% of live buy
signals and whose marginal trades are the ones that degrade. That is the
opposite of the intent.

**Root cause, visible in the live book:** tsmom is a trend follower holding
positions for weeks (all 5 live slots, entered on one day, 7+ days held, trails
ratcheting, no exit in sight). meanrev exits on SMA5 with `max_hold_days: 7` and
would cycle fast — but can never get a slot. A single shared pool lets the
slow strategy starve the fast one.

## §13 — PER-STRATEGY slot allocation (2026-07-23) — PRE-REGISTERED, NOT YET RUN

**Candidate:** replace the single global `max_open_positions` with a per-strategy
allocation (e.g. `strategies.<name>.max_open_positions`), leaving the global
value as a hard ceiling over the whole book. Rationale is §12's split result
plus the live starvation mechanism above — not a desire for more exposure.

**PRE-REGISTERED RULE:** adopt only if, on the frozen snapshot,
(a) meanrev's allocation improves its OOS trade count with PF >= baseline - 0.15,
(b) tsmom's allocation is NOT raised above 5 (§12 showed it degrades),
(c) whole-book OOS maxDD <= 3.0pp absolute (`daily_loss_limit_pct` ceiling),
(d) the portfolio-heat and correlation caps are shown to bind at least once in
    the run — if a wider book never exercises the real risk rails, the slot
    count was never the true constraint and the change is not justified.

Requires code: `risk.pure_checks` counts positions globally today. Until it is
implemented and passes, **`max_open_positions` stays at 5.**

### §13 RESULT — ADOPTED for meanrev (alloc 8); tsmom + ma_crossover pinned at 5.

First, a correction that changes every earlier number in this file.

**THE BACKTESTER WAS MISSING TWO LIVE RAILS.** `simulate()` called
`risk.pure_checks` only; the **portfolio-heat cap and the correlation cap** live
in `risk.pre_trade_checks` and were therefore **never simulated**. Every gate
run through §12 was measured against FEWER rails than live trades under, so the
sim overstated achievable trade counts. Both are now applied in the sim
(`n_heat_blocked` / `n_corr_blocked` are recorded on every Result).

Re-measured baselines with the rails present:

| strategy | before (no rails) | after (rails simulated) |
|---|---|---|
| meanrev | +2.63%, PF 2.164, 161 tr | **+2.49%, PF 2.076, 160 tr** |
| tsmom | +2.12%, PF 2.398, 97 tr | unchanged (never reached a cap at N=5) |

Earlier sections' absolute figures are therefore mildly optimistic for meanrev
and should be re-run before being cited again.

**§13 ladder (rails simulated, one frozen snapshot, trial count ~16):**

| meanrev alloc | OOS ret | maxDD | PF | trades | heat blk | corr blk |
|---|---|---|---|---|---|---|
| 5 (base) | +2.49% | 0.5% | 2.076 | 160 | 0 | 7 |
| **8** | **+2.84%** | **0.5%** | **2.116** | **177** | 0 | 7 |
| 12 | +2.75% | 0.5% | 2.044 | 178 | 0 | 8 |

Clauses: (a) PASS at 8 and 12 — trades up, PF above the 1.926 floor.
(b) PASS — tsmom is pinned at 5, and is now pinned EXPLICITLY in config so it
cannot absorb the new headroom. (c) PASS — maxDD 0.5% vs the 3.0pp ceiling.
Tie-break selects **alloc 8** (177 of 178 trades, 94% of the gain).

**(d) PARTIALLY MET — stated plainly rather than reinterpreted.** The clause
required the heat cap AND the correlation cap to bind. The **correlation cap
bound 7-8 times** (a real risk rail is now the constraint, which was the
intent). The **heat cap bound 0 times** and structurally cannot at ~2%
deployment: measured book heat is ~$560 against a $4,000 cap. That is a flaw in
how I drafted (d) — requiring it to bind implicitly required a deployment
increase — not a failure of the evidence. Recorded here so the shortfall is
visible rather than buried.

**What this does and does not buy.** It removes the live starvation mechanism
(tsmom holding every slot for weeks while meanrev, which exits on SMA5, waits).
It does NOT increase per-trade risk, deployment, or any risk rail: per-trade
sizing, heat, correlation, concentration, the daily-loss kill switch and the
swing guard are all unchanged. The backtest gain is +17 OOS trades over ~2
years — real but modest. **It does not shorten the go-live gate; it only lets
evidence accrue somewhat faster.**

## METHOD NOTE 3 — the §1–§13 snapshot is GONE; those numbers are unreproducible (2026-07-23)

Every gate from §1 to §13 was scored on `memory/bars_snapshot_2020_2026-07-10.json`
(25 symbols x 1637 bars). `memory/` is in `.gitignore`, so **the file was never
committed and no longer exists on any machine reachable from this repo.**

This is a process failure with a specific consequence: *the verdicts survive as
prose, but nobody can re-derive them.* "Re-running §8 reproduces §8" is now an
unfalsifiable claim. Sections §1–§13 should be read as **recorded history, not
as re-checkable evidence.**

Fixed going forward:
- `scripts/build_snapshot.py` rebuilds snapshots from **yfinance** (no API keys,
  so any future session can do it) into **`data/snapshots/` — committed** — with
  a SHA256 in `MANIFEST.json`. `--verify` fails loudly on drift.
- Snapshots are gzipped JSON (~1.8 MB vs ~7.8 MB); `backtest.load_bars_file`
  accepts `.json.gz`.

**The new snapshot is NOT a drop-in replacement.** yfinance serves
split/dividend-ADJUSTED prices, the retired Alpaca snapshot served raw; and the
new file carries all **38** current symbols where the old one had 25. Different
universe AND different price basis. Therefore **no figure from §1–§13 may be
compared against a figure from §14 onward.** Every section below re-measures its
own baseline inside its own snapshot.

### Re-baselined on `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…)

38 symbols, 62,244 bars, live config, 70/30 walk-forward:

| strategy | OOS return | PF | trades | maxDD | gate |
|---|---|---|---|---|---|
| meanrev | +3.00% | 1.638 | 266 | 0.8% | PASS |
| tsmom | +2.07% | 2.170 | 93 | 0.6% | PASS |
| ma_crossover | +2.94% | 2.400 | 88 | 1.1% | PASS |

All three enabled strategies **re-pass the enablement gate on a different data
vendor, a wider universe and an adjusted price basis.** That is a genuine
robustness result — vendor-specific artifacts are one of the ways a backtest
edge turns out to be nothing — and it is the strongest thing the backtests say.
It is still not live evidence, and METHOD NOTE 2 still applies: this window has
been mined, and the live forward record remains the only clean holdout.

## METHOD NOTE 4 — multiple-testing correction is now arithmetic, not judgement (2026-07-23)

METHOD NOTE 2 clause 4 promised the bar would rise with the trial count, but
specified only "prefer a clear margin" — a judgement call, and judgement calls
drift toward the answer you wanted. `src/significance.py` replaces it:

- **Moving-block bootstrap** (block ~ sqrt(n)) over the OOS closed-trade P&L
  sequence, so a single lucky market stretch cannot masquerade as edge the way
  it would under naive per-trade resampling.
- **Bonferroni correction**: K arms in a section are each judged at 0.05/K.
- A candidate whose CI includes zero is reported **INCONCLUSIVE — not a pass.**

Two honest limits, both recorded in the module docstring: the two arms share
the same bars but are resampled independently, which *overstates* the variance
(so passing is harder than it should be — safe direction); and no bootstrap can
undo the fact that the OOS window itself has been mined.

## §14–§17 — PRE-REGISTERED RULES, WRITTEN BEFORE ANY CANDIDATE WAS RUN

Committed as its own commit, ahead of the results commit, so git timestamps
prove the rules preceded the numbers.

Protocol for all four, enforced by `scripts/gate_compare.py`:
1. every arm runs in-sample; 2. the winner is picked **on IS only**;
3. baseline and IS-winner run OOS; 4. the OOS difference goes through the
Bonferroni-corrected block bootstrap with K = number of arms.

**Deterministic rules (identical across the four sections):** adopt only if the
IS-selected arm, out of sample, satisfies ALL of —
(a) return >= baseline return,
(b) PF >= 1.30 (standing bar) AND >= baseline PF - 0.15,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute
    (`daily_loss_limit_pct`: a book that can lose more in a drawdown than the
    kill switch tolerates in a day is leverage, not capacity),
(d) >= 15 closed OOS trades.
**Plus:** the bootstrap CI on per-trade P&L must exclude zero in the
candidate's favour. Deterministic-pass + inconclusive-bootstrap = **KEEP THE
INCUMBENT**; the rules passing inside the noise band is not a result.

- **§14 xsmom re-test.** Rejected 2026-07-15 by 0.13pp on the 25-symbol
  universe — the closest rejection in this repo — and never retried on the 38
  names that lifted the other three. Judged by the standing `enablement_gate`,
  since this is enable/don't-enable rather than a parameter change.
- **§15 exit rules.** `max_hold_days: 7` and `exit_sma_period: 5` were adopted
  at the original enablement and have **never been gated at all** — the largest
  ungated surface in the system. Arms grid both.
- **§16 chandelier ATR multiple.** §7 registered a single value (3.0) with no
  grid; the swing literature spans 2.5–3.0. Arms: 2.0 / 2.5 / 3.0.
- **§17 Donchian breakout.** The absent classical swing family. Fits existing
  plumbing (single-asset, no cross-section, like meanrev). Judged by
  `enablement_gate`; it may only be enabled if it passes cleanly.

Running trial count entering §14: ~13 registered comparisons plus grid arms.

### §14–§17 RESULTS (2026-07-23) — FOUR CANDIDATES RUN, **ZERO ADOPTED**

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…, 38 symbols,
62,244 bars). Rules were committed in the preceding commit; results in this one.

**§14 xsmom re-test — NOT ENABLED.**
On the 38-name universe xsmom clears the standing `enablement_gate` outright:
OOS **+2.91%, PF 3.067, 46 trades, maxDD 1.1%** — the highest profit factor of
any strategy in this repo. It fails the significance clause: mean $+63.33/trade
with a 95% CI of **[-19.68, +186.65]**, P(mean>0) = 87.5%. Per the pre-registered
rule (deterministic pass + inconclusive bootstrap = keep the incumbent), it
**stays disabled.** The highest PF in the repo is also its least certain number;
46 trades with 51.6% of gross profit in 3 of them is not 46 pieces of evidence.

**§15 exit rules — REJECTED.**
`max_hold_days` 5/7/14 x `exit_sma_period` 3/5/10 (5 arms). IS winner `hold14`
(+3.94% IS, the best of the five). Out of sample it is a dead heat with the
incumbent: **+2.99% vs +3.00%**, per-trade difference **-$0.04**, CI
[-17.76, +17.55]. All five arms land within 0.6pp of each other OOS.

Worth recording: the arm that looked best **out of sample** was `exit_sma3`
(+3.29%, PF 1.754). Selecting on OOS would have "adopted" it. Selection happens
in-sample only, which is the entire reason that did not happen. The finding is
that the largest ungated surface in the system turns out to be a **flat**
surface — the incumbent 7-day / SMA5 exits are fine, and nothing here is worth
changing.

**§16 chandelier ATR multiple — REJECTED BY NOISE (the interesting one).**
Arms 2.0 / 2.5 / 3.0(incumbent) / 4.0 on tsmom. IS winner `trail2.5`, and out of
sample it beats the incumbent on **every single point estimate**:

| arm | OOS ret | PF | maxDD | trades |
|---|---|---|---|---|
| 3.0 (incumbent) | +2.07% | 2.170 | 0.64% | 93 |
| **2.5 (selected)** | **+2.54%** | **2.558** | **0.46%** | **110** |
| 2.0 | +2.02% | 1.865 | 0.88% | 158 |
| 4.0 | +2.32% | 2.562 | 0.65% | 76 |

Every deterministic rule passes. The bootstrap does not: per-trade difference
**$+0.77**, CI **[-36.50, +37.29]**. Higher return, higher PF, lower drawdown,
more trades — and all of it inside the noise band at n≈100 with fat-tailed trend
P&L. **Incumbent kept at 3.0.**

This is the case the significance clause exists for, and the honest reading is
uncomfortable: with ~100 trades of trend P&L, this method cannot distinguish a
genuine 23% return improvement from luck. That is a statement about how little
these sample sizes support, not about 2.5 being wrong. **§16 is the strongest
candidate to re-test once live trades exist** — flagged, not adopted.

**§17 Donchian breakout — REJECTED, stays disabled.**
New strategy (`src/strategies/donchian.py`, 20-bar entry / 10-bar exit / SMA200
filter — the classical Turtle pair, chosen before running, not fitted). OOS
**+1.54%** against an exposure-matched bar of **+1.95%** — fails by 0.41pp, plus
PF ex-top-3 of **0.874**, i.e. it loses money without its three best trades.

The mechanism did behave as predicted in the module docstring (34% win rate,
PF 1.584 — win rarely, win large). It simply is not good enough. Code and tests
are kept: the strategy is registered, `enabled: false`, and a test pins it that
way, so the failure is documented rather than deleted.

## §18 — Concentration + bootstrap edge report (2026-07-23) — NEW STANDING DIAGNOSTIC

`scripts/edge_report.py`. Profit factor answers "did it make money here"; it does
not answer "on how many independent events does that rest". Those two questions
rank the strategies **in nearly opposite order**:

| strategy | PF | top3% of gross profit | PF ex-top3 | median trade | edge vs zero |
|---|---|---|---|---|---|
| xsmom | 3.067 | 51.6% | 1.484 | -$36.96 | not shown (87.5%) |
| ma_crossover | 2.400 | 54.7% | **1.087** | -$25.42 | not shown (97.1%) |
| tsmom | 2.170 | 32.0% | 1.475 | -$14.48 | **at uncorrected only** (99.0%) |
| meanrev | 1.638 | **6.6%** | 1.531 | **+$15.98** | **at uncorrected only** (99.0%) |
| donchian | 1.584 | 44.8% | **0.874** | -$21.17 | not shown (84.7%) |

Read by headline PF the ranking is xsmom > ma_crossover > tsmom > meanrev.
Read by how broadly the profit is earned it is close to the reverse. **meanrev
is the only strategy whose median trade makes money**, and the only one whose
result does not depend on a handful of events.

**The finding that matters most, and it is not a comfortable one:** after
Bonferroni-correcting for the five strategies compared, **not one of them has an
OOS per-trade edge distinguishable from zero.** meanrev and tsmom clear the
uncorrected 5% bar (P(mean>0) = 99.0% each); ma_crossover, xsmom and donchian do
not clear even that.

What this does and does not mean:
- It is **not** "the strategies are worthless". Every point estimate is positive,
  and absence of evidence at n=46–266 is not evidence of absence.
- It **is** "the backtest cannot certify any of these edges", and no gate that
  looked only at return/PF/drawdown would ever have said so. §1–§17 all passed
  through gates that could not see this.
- **ma_crossover is the weakest by evidence and is currently ENABLED** (PF 2.400
  is 54.7% three trades; PF ex-top-3 is 1.087, i.e. break-even). It is left
  enabled — disabling it weakens nothing and would cut trade velocity, and the
  decision belongs to the owner — but it should not be described as a validated
  edge. It is the ensemble's baseline/teaching device, and it is performing like
  one.
- It is the sharpest possible argument for the standing invariant: **the live
  forward record is the only holdout**, and 30 closed trades is the point at
  which any of this starts to be checkable.

Running trial count after §18: ~18 registered comparisons plus grid arms.

## §19 — Faster entries (2026-07-23) — RULE PRE-REGISTERED, results in the next commit

**Motivation (owner request, plus a divergence nobody had noticed).** The owner
asked for the agent to enter "as soon as it sees an opportunity" rather than
waiting for a fixed time. Investigating that surfaced a structural
**live-vs-backtest divergence**:

- `backtest.py` computes signals on the **close of bar i** and fills at the
  **open of bar i+1**. Every gate number in this file rests on that model.
- The live scheduler runs one cycle at **15:45 ET** and fills **immediately**,
  at today's close — a full bar earlier than the research assumes.

So the live bot has never traded the model it was validated against. A cycle at
the **open**, reading the last COMPLETED daily bar, is therefore not a
compromise between "faster" and "faithful" — it is *both*.

**§19a — 09:35 ET open cycle. SHIPPED (no gate required).** This is not a
strategy change: same strategies, same params, same rails. It changes *when*
the existing signals are acted on, and it moves live behaviour toward the
backtest rather than away from it. `datacheck.drop_forming_bar()` trims today's
partial bar (mid-session `broker.bars()` returns a forming bar whose close is
just the current price); the 15:45 cycle deliberately does NOT opt in, because
15 minutes before the bell the forming bar is effectively the close, which is
what every gate measured. 9 tests pin the guard, including one that fails if
the scheduler ever drops the `--open-cycle` flag.

Explicitly NOT done: intraday re-evaluation every N minutes. That runs
RSI(2)/SMA200 against a partial bar — an input no gate has measured — and
`config.yaml:63` warns against exactly it. The owner asked for faster; faster
was available without going there.

**§19b — `max_trades_per_day` 3 -> 5 / 8 / 12. PRE-REGISTERED, NOT YET RUN.**
The cap is enforced in the simulator (`backtest.py`, `fills_by_date`), so it is
honestly testable. With meanrev now allocated 8 slots (§13) and a second daily
cycle, a cap of 3 is more likely to bind than it was.

**PRE-REGISTERED RULE (written before running):** adopt the largest N whose
IS-selected arm, out of sample, satisfies ALL of —
(a) return >= baseline (N=3) return,
(b) PF >= 1.30 AND >= baseline PF - 0.15,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed trades,
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L excludes zero in
the candidate's favour. Deterministic pass + inconclusive interval = **KEEP THE
INCUMBENT** (this clause has already rejected §16, where the candidate beat the
incumbent on every point estimate). If several N pass, prefer the SMALLEST that
captures most of the trade-count gain.

Running trial count entering §19: ~18 registered comparisons plus grid arms.

### §19b RESULT — the gate could not answer this; measured directly. ADOPTED 3 -> 5.

**The pre-registered rule was INAPPLICABLE, and saying so matters more than the
verdict.** All four arms (3/5/8/12) came back **byte-identical** — same return,
PF, drawdown and trade count to the last digit.

The reason is a **second backtester/live divergence**, in the same family as the
missing heat/correlation caps found in §13:

| | live | backtest |
|---|---|---|
| `_trades_today()` counts | **the whole ensemble** (`risk.py`) | one strategy alone (`fills_by_date`) |
| effective cap for meanrev | 3 shared with 3 other strategies | 3 all to itself |

`simulate()` replays ONE strategy at a time, so a *global* rail is measured as a
per-strategy rail. meanrev alone averages 0.54 entries/day and never reaches 3 —
hence four identical arms. The instrument cannot see the parameter. Reporting
"deterministic PASS, significance inconclusive" would have been a verdict
generated by a broken measurement, so the rule is recorded as inapplicable
rather than applied.

**Measured directly instead.** Replaying the three ENABLED strategies over the
OOS window and summing their entries per calendar date:

- 447 entries across 231 entry-days, mean **1.94/day**
- distribution: 122 days with 1, 41 with 2, 39 with 3, 22 with 4, 5 with 5,
  1 with 6, 1 with 7

| cap | binds on | entries refused |
|---|---|---|
| **3 (incumbent)** | 29 of 231 days | **39 (8.7%)** |
| **5 (adopted)** | 2 of 231 days | 3 (0.7%) |
| 8 | 0 days | 0 |
| 12 | 0 days | 0 |

**Adopted 5.** The pre-registered "prefer the SMALLEST N that captures most of
the gain" clause decides it cleanly: 5 recovers 92% of what the cap was
refusing, and 8 and 12 recover **literally nothing** beyond it. Raising further
would be buying capacity that the evidence says is never used.

Note what this claim is and is not. It is **not** "returns improve" — no edge
estimate is involved and none is asserted. It is "the rate limiter was refusing
8.7% of the entries the strategies had already decided on, and 5 stops that."
That is a capacity measurement, which is why it is adoptable where §16's
apparent improvement was not: §16 asked the data to certify a better edge and
the data could not; this asks how often a counter fires, and the data answers
exactly.

**Upper-bound caveat:** the three strategies were replayed independently, so in
a joint run they would compete for slots, heat and correlation headroom. 447 is
an **upper bound** on combined demand and 8.7% an upper bound on what the cap
costs. The direction of the error is against the change, not for it.

**Known limitation, recorded not fixed:** the backtester still cannot model
ensemble-wide rails (`max_trades_per_day`, and the global `max_open_positions`
for the same reason). Fixing it means a joint multi-strategy simulator — a real
piece of work, and out of scope here. Until then, any gate on a GLOBAL rail must
be treated as structurally uninformative and measured the way §19b was.

Running trial count after §19: ~19 registered comparisons plus grid arms.

## §20 — RE-GATE UNDER THE ENSEMBLE SIMULATOR (2026-07-23) — RULES PRE-REGISTERED

`backtest.simulate_ensemble()` now replays **all enabled strategies against one
shared set of rails**, the way the live bot trades. `simulate()` is unchanged
and still measures a single strategy in isolation.

**Equivalence proof (why ensemble numbers can be trusted):** with exactly one
strategy enabled, the ensemble reproduces `simulate()` **trade-for-trade** —
verified for all five strategies on the frozen snapshot, pinned as a
parametrized test. So ensemble behaviour is single-strategy behaviour *plus
contention*, and nothing else.

### The ensemble baseline — a number that has never existed before

Live config, OOS, `bars_2020-01-01_2026-07-10.json.gz`:

**return +3.232% | PF 1.812 | maxDD 1.069% | 183 trades | 42% win | 5.73% deployed**

Compare with what every previous gate implicitly assumed — the three strategies
scored separately, each on its own $100k:

| | ensemble (shared $100k) | solo (own $100k each) |
|---|---|---|
| meanrev | **23 trades** | 285 trades, +2.963% |
| tsmom | 92 trades | 93 trades, +2.096% |
| ma_crossover | 68 trades | 91 trades, +2.730% |
| total | 183 trades, +3.232% | 469 trades, +7.789% |

### The finding: §13 does not do what §13 claimed

§13 (shipped this morning) diagnosed meanrev as starved by tsmom holding every
slot, and fixed it by raising meanrev's allocation to 8. Measured under the
ensemble, **meanrev executes 23 of the 285 trades it makes alone** — it is still
starved, and the 8 slots are close to irrelevant.

The mechanism is **priority order**, which §13 never touched. Entry priority is
`ma_crossover(1) -> tsmom(2) -> meanrev(4)`, so the two trend strategies are
offered every symbol first and consume the GLOBAL ceiling of 8 before meanrev is
consulted at all. Giving meanrev a private allocation of 8 cannot help when the
global pool is already empty.

The §13 diagnosis was right. The §13 fix addressed the wrong mechanism, and only
a single-strategy simulator could have made that look like a success.

### PRE-REGISTERED RULES (written and committed before any candidate was run)

All arms scored with `simulate_ensemble()` on the frozen snapshot, IS-only
selection, then OOS. Baseline = the live config exactly as shipped.

**§20a — entry priority order.** Arms: current (ma_x, tsmom, meanrev);
meanrev-first; meanrev-first-then-tsmom; round-robin-by-fewest-open. Adopt the
arm whose OOS satisfies ALL of —
(a) ensemble return >= baseline return,
(b) ensemble PF >= 1.30 AND >= baseline PF - 0.15,
(c) ensemble maxDD <= baseline x 1.5 AND <= 3.0pp absolute,
(d) ensemble trade count >= baseline (evidence velocity is the point),
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L excludes zero in
the candidate's favour. Deterministic pass + inconclusive CI = **KEEP THE
INCUMBENT** (this clause has already rejected §16 and gated §14).

**§20b — re-gate the §13 slot allocation** under the ensemble, now that the
instrument can see contention. Arms: current (8/5/5, global 8); equal (5/5/5);
meanrev-heavy (12/5/5, global 12). Same clauses.
**If the ensemble shows §13's allocation is not doing the work its section
claims, the honest outcome is to revert or restate §13, not to keep it because
it is already shipped.**

**§20c — re-gate `max_trades_per_day` (§19b, 3 -> 5)** under the ensemble. §19b
was adopted on a direct measurement that SUMMED three independent replays, an
explicit upper bound on combined demand. The ensemble measures the real thing.
Same clauses; **revert to 3 if 5 is not justified.**

Running trial count entering §20: ~19 registered comparisons plus grid arms.

### §20 RESULTS (2026-07-23) — the instrument reversed two of my own decisions

**§20b — §13's slot allocation is INERT. Restated, not reverted.**

| arm | OOS ret | PF | maxDD | trades |
|---|---|---|---|---|
| baseline 8/5/5, global 8 | +3.232% | 1.812 | 1.069% | 183 |
| **equal 5/5/5, global 8** | **+3.232%** | **1.812** | **1.069%** | **183** |
| meanrev 12, global 12 | +2.932% | 1.520 | 1.092% | 257 |

`5/5/5` is **byte-identical** to the shipped `8/5/5`. meanrev's allocation of 8
does nothing, because the GLOBAL ceiling of 8 is consumed by the two
higher-priority strategies before meanrev is ever consulted. Widening to 12
FAILS the deterministic rules outright (return down, PF 1.812 -> 1.520).

Left at 8 — it costs nothing and becomes correct if priority is reordered — but
**§13's section overstates what it achieved.** §13 said it "fixes tsmom starving
fast-cycling meanrev". It does not. The diagnosis was right and the fix was
aimed at the wrong mechanism, which only a single-strategy simulator could
have made look like a success.

**§20c — §19b REVERTED. `max_trades_per_day` back to 3.**

| cap | OOS ret | PF | trades |
|---|---|---|---|
| **3 (restored)** | **+3.237%** | **1.821** | 182 |
| 5 (§19b, shipped hours earlier) | +3.232% | 1.812 | 183 |
| 8 | +3.232% | 1.812 | 183 |
| 12 | +3.232% | 1.812 | 183 |

§19b's evidence summed three INDEPENDENT single-strategy replays and concluded a
cap of 3 was refusing 8.7% of entries. That sum was an explicit upper bound —
the strategies compete in reality — and jointly the cap **barely binds at all**,
because the global slot ceiling binds first. The difference between 3 and 5 is
one trade and 0.005pp, with 3 marginally ahead on both return and PF.

Reverted to 3. A circuit breaker should be as tight as it can be when loosening
it buys nothing. **§19b was a no-op recorded as a win; it stood for four hours.**

**§20a — entry priority is the REAL starvation mechanism. Deterministic PASS,
significance INCONCLUSIVE — escalated to the owner rather than self-approved.**

| arm | OOS ret | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| baseline (ma_x 1, tsmom 2, meanrev 4) | +3.232% | 1.812 | 1.069% | 183 | **23** |
| meanrev-first | +4.387% | 1.864 | 1.069% | 232 | 78 |
| **meanrev-then-tsmom (IS-selected)** | **+4.324%** | **1.827** | **1.069%** | **256** | **103** |
| meanrev 2nd | +4.201% | 1.818 | 1.069% | 242 | 80 |

All six deterministic clauses PASS: return +34% relative, trades +40%, PF
essentially unchanged, and maxDD **identical to three decimals**. The bootstrap
is inconclusive: per-trade P&L $16.89 vs $17.66, CI [-23.28, +25.96].

Per the pre-registered rule (deterministic pass + inconclusive CI = keep the
incumbent), **the incumbent stands and nothing was changed.**

But the rule has a drafting flaw worth naming, the same class as §13 clause (d)
and §19b: **it tests per-trade EDGE, while this candidate claims more trades at
the SAME edge.** A velocity change should not be expected to move per-trade P&L,
so the significance clause is measuring the wrong quantity — and the honest
response is to say so and hand the decision to the owner, not to quietly
reinterpret a rule I wrote before seeing the result. Escalated.

Running trial count after §20: ~23 registered comparisons plus grid arms.

### §20a ADOPTED — by OWNER DECISION on an escalated call, 2026-07-23

The owner was shown the numbers, the passing deterministic clauses, the
inconclusive interval, and the argument that the interval measures the wrong
quantity — and chose to adopt `meanrev -> tsmom -> ma_crossover`.

**Recorded plainly: this was NOT a rule-pass.** The pre-registered clause said
keep the incumbent; the owner overrode it with the reasoning on the table. That
is a legitimate way to decide, and it is different from the change having
cleared the bar. Anyone reading this later should not cite §20a as evidence of
edge.

**Re-verified at the SHIPPED config** (`max_trades_per_day: 3`, since §20a was
gated while the cap was still 5 — the arm as measured is not quite the arm that
ships, and pretending otherwise would be sloppy):

| | OOS return | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| old priority | +3.237% | 1.821 | 1.069% | 182 | 23 |
| **shipped** | **+4.749%** | **1.949** | **1.069%** | **245** | **100** |

All six deterministic clauses still PASS at the shipped config, and per-trade
P&L now points slightly upward ($19.38 vs $17.78) rather than down — though the
interval is still inconclusive, [-20.98, +29.00]. **Max drawdown is unchanged to
three decimals**, which is the single most reassuring number here: the extra
trades did not buy return with risk.

`ma_crossover` was demoted to priority 3 rather than disabled (owner's earlier
decision to keep it enabled stands). It is the weakest strategy by evidence, so
it should not get first refusal on every symbol.

## METHOD NOTE 5 — edge claims vs capacity claims (2026-07-23)

The significance clause has now been the wrong instrument **three times**:

| section | the claim | why the bootstrap misfired |
|---|---|---|
| §13 clause (d) | rails should bind | required a deployment increase to be satisfiable |
| §19b | the rate cap refuses entries | arms were byte-identical; it measured nothing |
| §20a | more trades at the same edge | it tests per-trade edge, which is not the claim |

The pattern: `src/significance.py` answers **"is the per-trade edge better?"**
That is exactly right for an EDGE claim and irrelevant to a CAPACITY claim —
"this rail is refusing N trades the strategies already decided on" is a COUNT,
deterministic given the data, not a noisy estimate.

**Binding on future sections:** state at pre-registration time whether a
candidate is an EDGE claim or a CAPACITY claim.
- **EDGE** -> the bootstrap CI must exclude zero. Unchanged.
- **CAPACITY** -> the bootstrap must show per-trade P&L is **not significantly
  WORSE** (`ci_high > 0`), and the deterministic clauses carry the decision.
  Capacity without an edge is just more slippage, so the drawdown and PF clauses
  stay mandatory.

This is written AFTER §20a and does not retroactively convert it into a pass.
§20a remains an owner override; the note exists so the next section is drafted
correctly rather than escalated.

## §21 — EXIT SPEED UNDER THE ENSEMBLE (2026-07-23) — RULES PRE-REGISTERED

**Claim type: CAPACITY** (declared before running, per METHOD NOTE 5).

**Why this is not a re-run of §15.** §15 gated `max_hold_days` and
`exit_sma_period` and found a **flat surface** — all five arms within 0.6pp OOS —
and concluded the incumbent exits were fine. But §15 was measured **solo**, and
in a solo run a position closing early frees a slot that **nobody else can use**.
Under the ensemble that slot goes straight to tsmom or ma_crossover.

That is exactly the blind spot that made §19b a no-op and §13 inert. §15's
"flat surface" may be an artifact of the instrument rather than a fact about
exits, and the only way to know is to re-run it on the simulator that can see
slot contention.

**Arms** (deliberately small — K inflates the Bonferroni correction, so a wide
grid actively raises the bar against the candidate):
`exit_sma_period` 3 / 5 (incumbent) / 7, and `max_hold_days` 5 / 7 (incumbent) / 10.
All scored with `simulate_ensemble()`; IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) ensemble return >= baseline return,
(b) ensemble PF >= 1.30 AND >= baseline PF - 0.15,
(c) ensemble maxDD <= baseline x 1.5 AND <= 3.0pp absolute,
(d) **closed trades > baseline** — this is the entire point of the section; an
    arm that does not increase evidence velocity has no claim here even if its
    return is prettier,
**and**, per the CAPACITY branch of METHOD NOTE 5, the Bonferroni-corrected
bootstrap must show per-trade P&L is **not significantly WORSE** (`ci_high > 0`).
The PF and drawdown clauses are non-negotiable: capacity without an edge is just
more slippage.

**Fallback arm-set if exits are genuinely flat:** universe expansion beyond the
current 38 names, judged by the same clauses. §10 took it 25 -> 38 and every
strategy re-passed; more liquid large-caps means more independent setups per
week at unchanged per-trade risk, with the correlation cap preventing it from
becoming one bet.

**Stated before the run so it cannot be quietly dropped:** the plausible outcome
is that both fail, and the honest report is *"the velocity levers are exhausted
— §20a took the last big one (meanrev 23 -> 100 OOS trades) and what remains is
calendar time."* That is a legitimate result and will be recorded as one.

Running trial count entering §21: ~23 registered comparisons plus grid arms.

### §21 RESULTS — both velocity levers REJECTED

**§21a exit speed — REJECTED on clause (a).** IS winner `hold5`; OOS
+4.357% vs baseline +4.749%. Every arm produced MORE trades and LESS return, and
**every arm had an identical max drawdown (1.069%)**. Faster exits buy ~8 extra
trades and cost ~0.4pp.

Worth recording as a positive result about the method: **§15's "flat surface"
verdict REPLICATED under the ensemble.** Not every solo verdict was an artifact,
and it is useful to know the instrument does not overturn everything it touches.

**§21b universe expansion 38 -> 68 — REJECTED on clause (d).** A fresh 68-name
snapshot (`bars_..._u60.json.gz`, 30 added liquid large-caps + sector ETFs) run
in config order: +2.249% vs +2.086%, PF 1.498 vs 1.457, but **246 trades vs 253
— fewer, not more.** More symbols compete for the same 8 slots, so breadth
dilutes velocity rather than adding it. Clause (d) is the whole point of a
capacity section, so this fails even though return and PF nudged up.

**Honest conclusion, as pre-committed:** the velocity levers are exhausted.
What remains is calendar time.

## §22 — SYMBOL ORDER: §20a REVERTED (2026-07-23)

**A fourth sim/live divergence, and this one was mine, introduced the same day.**

`simulate_ensemble()` iterated `sym_bars` in whatever order the caller supplied
— and a snapshot loaded from disk arrives **alphabetically sorted**. Live builds
its scan list as `scan_symbols = list(cfg["symbols"])` (main.py), i.e. **config
order**. Contention is resolved first-come by symbol, so whoever is scanned
first gets first refusal on the shared slots and the daily fill budget.

Running the identical config in the two orders moved OOS return by **2.66pp**
(+4.749% sorted vs +2.086% config). Every §20 number was measured sorted.

**Consequence: §20a does not hold.** Re-measured in live order:

| | OOS return | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| ma_crossover -> tsmom -> meanrev | **+2.669%** | **1.955** | 1.073% | 179 | 25 |
| meanrev first (§20a, shipped) | +2.086% | 1.457 | 1.073% | 253 | 104 |

It FAILS clause (a) (return lower) and FAILS clause (b) badly (PF 1.955 ->
1.457, against an allowance of −0.15). **REVERTED to the gated order.**

The velocity is real — 179 -> 253 trades, meanrev 25 -> 104 — but it is **not
free**, and "free velocity at identical drawdown" was precisely the claim §20a
was adopted on. The owner approved that change on numbers I had measured wrong.
Reverted, and the correct numbers are on the record.

**Fixed:** `simulate_ensemble()` now normalises to config order regardless of
input dict order, mirroring live. Two tests pin it — one proving scan order
changes outcomes when a slot is contested, one proving the ensemble is now
insensitive to the caller's dict ordering.

**A previously undocumented property of the LIVE bot, worth knowing:** because
live scans `cfg["symbols"]` in file order and slots are scarce, **symbols listed
early in config.yaml get systematic first refusal.** SPY/QQQ/DIA/IWM lead the
list, so index ETFs are structurally favoured over names further down. That is
an arbitrary bias nobody chose. It is NOT changed here — reordering the universe
is itself a candidate that must be gated — but it is now on the record.

Running trial count after §22: ~27 registered comparisons plus grid arms.

## §23 — RELATIVE-VOLUME ENTRY FILTER (2026-07-24) — RULES PRE-REGISTERED

**Claim type: EDGE** (declared before running, per METHOD NOTE 5).

**Motivation.** Two independent professional sources (SMB Ep 18; Humbled Trader
— see `knowledge/swing_trading_playbook.md`) both treat **volume expansion** as
core entry confirmation. Checking the repo: **not one of our five strategies
reads the volume field**, although every bar carries it. That is a verified
blind spot, not a deliberate choice.

It is also the right SHAPE of idea. §21 concluded the velocity levers are
exhausted, and the Humbled Trader source independently reports taking 2–5 swing
trades per month against Repete's ~10/month — i.e. chasing more trades was
likely the wrong direction all along. **A volume filter REDUCES trade count by
design and must justify itself on per-trade quality.** That is why it is an EDGE
claim, not a CAPACITY one.

**Mechanism.** `rvol = volume(entry bar) / mean(volume over the prior N bars)`,
N = 20. An entry is blocked unless `rvol >= threshold`. Deterministic, no new
data, no new machinery. Applied to entries only — **exits are never filtered**
(a position must always be able to leave, per the standing ownership rule).

**Arms:** baseline (no filter) / 1.5 / 2.0 / 2.5. Scored with
`simulate_ensemble()` on `data/snapshots/bars_2020-01-01_2026-07-10.json.gz`,
IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) ensemble return >= baseline return,
(b) PF >= 1.30 **AND >= baseline PF + 0.10** — an EDGE claim must *improve* the
    profit factor, not merely hold it; a filter that removes trades while
    leaving quality flat has bought nothing,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed OOS trades (below that the sample cannot support a verdict),
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L **EXCLUDES ZERO
in the candidate's favour**.

Note (b) is deliberately *stricter* than prior sections' `>= baseline - 0.15`.
Those were capacity claims where PF was allowed to soften slightly in exchange
for volume. This claim is the opposite trade, so the bar moves the other way.

**Stated before the run, so it cannot be spun afterwards:** **nothing in this
repo has ever cleared the EDGE bar** — not §14 xsmom (the highest PF here), not
§16, not §20a. The honest prior is that this fails too. It is worth running
because it is cheap, correctly shaped, and closes a verified blind spot — not
because it is likely to work. It will be recorded whichever way it falls.

Running trial count entering §23: ~27 registered comparisons plus grid arms.

### §23 RESULT — REJECTED. The best deterministic numbers in this repo, and still not evidence.

Snapshot `bars_2020-01-01_2026-07-10.json.gz`, ensemble simulator, 70/30 split,
IS-only selection. IS winner among candidates: **rvol1.5**.

| arm | OOS ret | PF | $/trade | trades | maxDD |
|---|---|---|---|---|---|
| baseline (no filter) | +2.669% | 1.955 | $14.91 | 179 | 1.073% |
| **rvol >= 1.5 (IS-selected)** | **+4.067%** | **2.898** | **$31.78** | 128 | **0.643%** |
| rvol >= 2.0 | +1.985% | 2.240 | $21.82 | 91 | 0.425% |
| rvol >= 2.5 | +0.826% | 1.705 | $14.00 | 59 | 0.591% |

**All six deterministic clauses PASS** — and not marginally. Return +52%
relative; PF +0.94 against a required +0.10; **drawdown FELL 40%**; per-trade
P&L **more than doubled**. On the deterministic rules alone this is the
strongest candidate ever run in this repo.

**REJECTED anyway.** Two independent diagnostics say it is noise:

**1. The bootstrap fails even UNCORRECTED.** Bonferroni (K=4) CI
[-$16.50, +$64.72]; uncorrected (K=1) CI [-$10.65, +$52.94]. Both include zero.
**P(candidate better) = 85.8%** — short of even the uncorrected 95% bar. The
pre-registered EDGE rule requires the CI to exclude zero. It does not.

**2. The threshold response is NON-MONOTONE, which is the more damning one.**
PF runs 1.955 → **2.898** → 2.240 → 1.705: it peaks at 1.5 and falls away on
*both* sides, ending *below* baseline at 2.5. If "volume confirms trade quality"
were a real mechanism, requiring *more* confirmation should not reverse it. A
single interior peak with decay either side is the signature of **a fitted
parameter, not a mechanism** — the 1.5 arm is where the noise happened to land.

That second point matters beyond §23. A candidate can pass every deterministic
clause by a wide margin and still be an artifact, and the deterministic clauses
**cannot see it** — only the CI and the shape of the parameter response can.
Where a grid is available, **check monotonicity**: a lone interior optimum is
weak evidence regardless of how good the winning cell looks.

**What ships:** nothing. `min_rvol` stays **unset** in `config.yaml` (a test
pins this), so live behaviour is unchanged. The code — `strategies.base.rvol()`
and `risk.rvol_blocked()` — is **kept as dormant, tested capability**: it closes
the verified blind spot that no strategy read the volume field at all, it is one
shared implementation across live and both simulators (so it cannot become a
fifth sim/live divergence), and it fails open on a missing volume feed. 17 tests.

**Prior confirmed.** §23's registration stated the honest expectation was
failure because nothing here has ever cleared the EDGE bar. That is now
**0 for 4** on EDGE claims (§14 xsmom, §16 chandelier, §20a priority, §23 rvol).
The pattern is consistent and worth stating plainly: **at these sample sizes
(n ≈ 60–260, fat-tailed P&L) this method cannot certify an edge, and every
apparent one so far has failed to survive contact with the interval.**

Running trial count after §23: ~31 registered comparisons plus grid arms.

## §24 — SCAN-ORDER ROTATION (2026-07-24) — ADOPTED as a correctness fix

**Claim type: neither EDGE nor CAPACITY — a CORRECTNESS fix.** Registered as
such before running, because the justification is not "makes more money."

**The defect.** Contention for slots is resolved first-come by scan position,
and live built its scan list as `list(cfg["symbols"])`. So slot allocation —
a consequential trading decision — was determined by **the order symbols were
typed into a YAML file**. SPY/QQQ/DIA/IWM occupy config positions 0–3 of 38.
The live book confirmed the prediction exactly: all five held names came from
config positions 0, 1, 2, 3 and 7.

This was flagged as a known live property in §22 and left alone. It should not
have been: the coming live record is the only clean holdout (§18 — the backtest
cannot certify any edge), and a record that samples ~5 index ETFs does not test
the strategies on the 38-name universe they were gated on.

**The fix.** `risk.scan_order()` / `risk.rotate_scan_order()` — offset the
universe by `date.toordinal()`, advancing one symbol per calendar day.
Deterministic (a date, never randomness, so gate re-runs reproduce), preserves
relative order, adds no fitted parameter, and is **one shared helper called by
live and both simulators** — a per-call-site re-implementation would have been
the fifth sim/live divergence.

**It does what it was built to do:**

| | config order | rotated |
|---|---|---|
| distinct symbols traded | 35 / 38 | **37 / 38** |
| share of trades from config positions 0–7 | **45.3%** | **20.3%** |

**And it costs return on this window — reported as pre-committed:**

| | OOS return | PF | maxDD | trades |
|---|---|---|---|---|
| config order | +2.669% | 1.955 | 1.073% | 179 |
| rotated | +1.829% | 1.526 | 1.174% | 187 |

**ADOPTED ANYWAY, and the evidence supports that rather than excusing it:**

1. **The difference is not significant.** Per-trade $14.91 vs $9.78; 95% CI on
   the difference **[-$19.72, +$9.50]** — includes zero at the *uncorrected*
   level, i.e. the most generous possible reading for "config order is better."
2. **The sign FLIPS in sample.** In-sample, config order +9.098% vs rotated
   **+9.366% — rotation wins.** A genuine advantage should not reverse between
   windows. Losing IS and winning OOS is the signature of noise.
3. **The "before" number is not a legitimate baseline to defend.** Config order
   was never chosen as a trading decision; it is an accident of typing. On this
   particular window it happened to over-weight index ETFs during a strong index
   run (52 of 179 trades vs 20 of 187 after rotation). **Keeping an arbitrary
   ordering because it scores higher on the mined OOS window is precisely the
   selection-on-noise error that forced the §20a revert.**

**What it buys:** a live forward record that samples the whole universe. That
record is the only thing that can ever settle whether these strategies work, and
until now it was going to be a record of five index ETFs.

Running trial count after §24: ~32 registered comparisons plus grid arms.

## PHASE 0 — INTRADAY FEASIBILITY (2026-07-24) — VERDICT: PROCEED, with one large caveat

Owner scoped an intraday project and chose **(A) intraday EXECUTION of the
existing swing strategies** — same signals, same holding horizons, finer
entry/exit timing — with day trading explicitly **out of scope**. Phase 0 was a
cheap kill-check to run *before* building. Findings:

### 1. Data — SOLVED (this was expected to be the blocker)

yfinance caps intraday history at ~2y hourly / 60d 5-minute, which would have
left no in-sample period outside the already-mined OOS window. **Alpaca's basic
tier returns 6 years of both.** Built
`data/snapshots/bars_1Hour_2020-08-01_2026-07-10.json.gz`: **38 symbols,
800,093 bars, 14.7 MB gzipped — committed and hash-manifested**, same window as
the daily snapshot so IS/OOS splits align.

**Recorded reproducibility regression:** unlike `build_snapshot.py` (yfinance,
no credentials), `build_intraday_snapshot.py` **requires Alpaca keys**. A future
session without keys can verify the committed hash but cannot rebuild the file.
That is a real step down from METHOD NOTE 3's standard and is stated, not hidden.

11 of 38 symbols have shorter series (~17.5k vs 23.6k bars) — Alpaca's intraday
coverage for those names starts later. Not fatal, but any intraday gate must
report per-symbol coverage rather than assume a balanced panel.

### 2. Cost — NOT the binding constraint

| | value |
|---|---|
| daily OOS baseline | 187 trades, net $1,828.92 |
| slippage paid @ 5 bps × 2 | $182.55 |
| cost as share of gross edge | **9.1%** |
| **breakeven trade multiplier** | **~11×** |
| round-trip cost vs median trade | 10 bps vs 341 bps = **2.9%** |

Costs only dominate beyond ~11× the trade count. **Phase 1 does not multiply
trade count** — it changes *when* within a day an entry happens, not how many
signals fire. The 11× ceiling is a constraint on day trading (B), which is out
of scope, not on (A).

### 3. Effect size — LARGE, and this is the real finding

Matched all 187 OOS trades to hourly bars:

| | median | mean |
|---|---|---|
| intraday range on the ENTRY DAY | **229 bps** | 287 bps |
| absolute trade outcome (entry→exit) | 341 bps | 510 bps |

**The intraday band is 67% the size of the entire trade outcome.** Entry timing
within the day is not a rounding error on a 13-day median hold — it is a
first-order determinant of the result.

### THE CAVEAT THAT MATTERS MOST

A 67% effect size **cuts both ways, and it is an overfitting hazard, not a
promise.** Nothing here says finer bars give *better* entries; it says entry
timing has a large lever. Tuning that lever on the already-mined OOS window
would be fitting noise with the biggest lever available in this project — a
faster version of the mistake that forced the §20a revert.

Two further honest limits on Phase 1:
- "Time-equivalent parameters" (daily SMA200 → hourly SMA1400) is an
  **approximation, not an identity**. RSI(2) on hourly bars measures intraday
  mean reversion, which is a different phenomenon from the daily signal that was
  gated. The translation must be validated, not assumed.
- Per-symbol coverage is uneven (see above), so an intraday panel is not a
  like-for-like replacement for the daily one.

**Verdict: PROCEED to Phase 1**, because the data exists, costs are not
prohibitive, and the effect is large enough to measure. **Not** because it is
likely to be positive — §18 still stands (no strategy shows an out-of-sample
edge distinguishable from zero), and every velocity/timing change attempted this
session has failed its gate (§19b, §20a reverted; §21, §23 rejected).

Phase 1 must be pre-registered as an **EDGE** claim with the §23 monotonicity
check applied, and reported whichever way it falls.

## §25 — INTRADAY FILL TIMING (2026-07-24) — RULES PRE-REGISTERED

**Claim type: EDGE.** This claims *better fills*, so it must improve per-trade
P&L. Declared before running, per METHOD NOTE 5.

**Design, and why it is not the design Phase 0 originally sketched.** The Phase 0
plan said "re-express daily parameters in hourly-equivalent terms (SMA200d →
SMA1400h)." That was wrong and is abandoned: hourly RSI(14) measures intraday
mean reversion, not the daily RSI(2) phenomenon that was gated, so scaling
periods creates genuinely *different strategies* needing full re-gating — the
whole ~32-trial contamination problem again.

Phase 0 measured that the large lever is **when you fill** (entry-day intraday
range 229 bps median against a 341 bps median trade outcome — **67%**), not what
indicator you compute. So §25 **holds every signal fixed** and varies only the
fill hour. No re-fit, no new parameter search, no new contamination.

**Data basis — non-negotiable, and it nearly went wrong.** Signals AND fills both
come from the **Alpaca hourly snapshot** (`intraday.resample_daily()` rolls it up
to daily bars for the signals). Mixing the yfinance dividend-ADJUSTED daily file
with the Alpaca RAW hourly file would book the adjustment gap — up to **8.8%** on
SPY — as invented P&L on every fill. Consequently **§25 numbers are NOT
comparable with §1–§24**; the baseline is re-established inside this data.

**Regular session only** (bar starts 09:00–15:00 ET). Filling pre/post-market at
regular-session slippage would be fantasy and would bias the result optimistic.

**Arms** (discrete, no continuous knob): baseline = **09:00** (at the open,
today's behaviour) / 10:00 / 11:00 / 12:00 / 13:00 / 14:00 / 15:00 ET. Entries
and exits move together — filling entries at noon while exiting at the open
would be an incoherent experiment. IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) return >= baseline return,
(b) PF >= 1.30 **AND >= baseline PF + 0.10**,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed OOS trades,
(e) **< 10% of fills fell back** to the daily open (`n_fill_fallback`) — thin
    coverage must not masquerade as a result,
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L **excludes zero**
in the candidate's favour.

**Plus the §23 MONOTONICITY CHECK, which matters more here than anywhere.** A
real time-of-day effect should vary *smoothly* across the session. A lone
winning hour with worse neighbours on both sides is a fitted parameter — exactly
how §23 was caught, and with a 67% lever the same trap is bigger here. A
non-monotone winner is reported as an artifact regardless of its numbers.

**Stated before the run:** EDGE claims are **0 for 4** (§14, §16, §20a, §23).
The honest prior is that this fails too. It is worth running because Phase 0
showed the effect is large enough to measure and this design adds no new
contamination — not because it is likely to pay.

Running trial count entering §25: ~33 registered comparisons plus grid arms.

### §25 RUN 1 — VOID. The data had 11 unadjusted stock splits.

The gate ran and returned "deterministic PASS on all 7 clauses, significance
INCONCLUSIVE". **Those numbers are discarded, not reported as a verdict**,
because the underlying data was corrupt.

**What gave it away.** A single stray line in the run log:
`bracket stop would be non-positive (entry 171.64, ATR 159.93)`. An ATR of
159.93 against a $171 price is a ~93% daily true range — impossible for a
mega-cap. Chasing it found the cause.

**The defect.** `build_intraday_snapshot.py` fetched Alpaca bars with the
default `adjustment=RAW`. Alpaca raw prices are **not split-adjusted**, so the
snapshot contained 11 corporate actions the simulator read as overnight
collapses:

| symbol | date | move | split |
|---|---|---|---|
| AAPL | 2020-08-31 | −74.8% | 4:1 |
| AMZN | 2022-06-06 | −94.9% | 20:1 |
| GOOGL | 2022-07-18 | −94.9% | 20:1 |
| NVDA | 2024-06-10 | −90.1% | 10:1 |
| AVGO | 2024-07-15 | −90.0% | 10:1 |
| TSLA | 2020-08-31 | −79.8% | 5:1 |
| …plus NVDA 4:1, TSLA 3:1, WMT 3:1, XLE 2:1, XLK 2:1 | | | |

Every one fired stop-losses on healthy positions, poisoned ATR and every
trend/momentum computation, and distorted the buy-and-hold benchmark. **Any
result computed on it is meaningless.**

**Why it slipped through.** The daily snapshot uses yfinance with
`auto_adjust=True`, so splits have never been an issue in this repo. The
intraday builder switched vendors — and I carried the *dividend* adjustment
concern across (it is documented in `intraday.py`) while missing that Alpaca
also leaves *splits* raw. **Changing data vendors changes more than one
assumption at a time.**

**Fixed two ways.** `adjustment=Adjustment.ALL` on the request, and — more
importantly — a **detector in `sanity_check()` that REFUSES to write a
snapshot** containing any >35% overnight move. A mega-cap does not move 35%
overnight; that is a corporate action, not a price. Nothing but a stray log
line caught this the first time, which is not a control.

**This is the sixth sim-vs-reality defect in this project** (§13 missing rails,
§19a fill timing, §19b global counters, §22 symbol order, §24 config-order
bias, and now §25 unadjusted splits). The standing lesson holds and hardens:
**before trusting any simulator number, ask what the real world does that the
simulation does not.**

§25 is re-run below on the corrected snapshot.

### §25 RUN 2 (adjusted data) — REJECTED. Filling at the open is the best arm.

Snapshot rebuilt with `adjustment=ALL` (sha256 7c6946a2…, 800,093 bars). Signals
and fills both from this one file. IS-only selection picked **10:00 ET**.

| fill hour | OOS return | PF | maxDD | trades |
|---|---|---|---|---|
| **09:00 (baseline, at the open)** | **+2.369%** | **1.712** | 1.339% | 163 |
| 10:00 (IS-selected) | +1.940% | 1.549 | 0.969% | 160 |
| 11:00 | +1.541% | 1.374 | 1.276% | 175 |
| 12:00 | +0.943% | 1.222 | 1.179% | 179 |
| 13:00 | +1.578% | 1.406 | 1.119% | 175 |
| 14:00 | +1.302% | 1.325 | 1.014% | 176 |
| 15:00 | +0.969% | 1.236 | 1.062% | 179 |

Clauses (a) and (b2) **FAIL** — the selected arm returns less than baseline and
its PF is lower, not +0.10 higher. The bootstrap is inconclusive
(diff −$2.41, CI [−$51.11, +$46.67]). **REJECTED; the incumbent stands.**

**The finding: filling at the open is the best of the seven arms**, and PF
declines fairly steadily across the session (1.712 → 1.549 → 1.374 → 1.222 →
1.406 → 1.325 → 1.236). Later fills are worse, not better. The bot's existing
09:35 open cycle (§19a) is already doing the right thing.

### THE POINT OF ALL THIS — the corrupt run said the OPPOSITE

Run 1, on split-corrupted data, reported the baseline as **the worst** arm
(PF 1.267 vs 1.427 at 10:00) and every deterministic clause **PASSING**. Run 2,
on correct data, reports the baseline as **the best** arm with two clauses
**FAILING**.

**The corruption did not merely add noise — it inverted the conclusion.** Had
run 1 been reported, the recommendation would have been "fill later in the day,"
which the corrected data says is precisely wrong. The only thing that surfaced
it was one implausible number in a log line (`ATR 159.93` on a $171 price).

Two lessons, recorded because they generalise beyond §25:
1. **An implausible intermediate value is worth more than a plausible final
   answer.** Run 1's headline numbers looked entirely reasonable.
2. **Changing data vendors changes more than one assumption at a time.** The
   dividend-adjustment difference was caught and documented; the split-
   adjustment difference, in the same switch, was not. A guard now refuses any
   snapshot containing a >45% session-over-session move
   (`build_intraday_snapshot.sanity_check`), verified to fire on an injected
   10:1 split and stay silent on the clean file.

**Phase 1 verdict: intraday fill timing does not improve results.** Combined
with Phase 0 (costs fine, effect size large), the honest conclusion is that the
large intraday lever exists but the bot cannot exploit it by shifting fill
hours — and shifting them later actively hurts. **EDGE claims are now 0 for 5.**

## §26 — TWO LIVE DIVERGENCES FOUND BY AUDITING PRODUCTION (2026-07-25)

Not a gate. An audit of the **running** bot against this repo, prompted by the
owner asking for a full rating. It found two things no backtest could have
surfaced, because both live entirely on the live side of the sim/live boundary.

### DIVERGENCE #7 — the repo and production had silently diverged

The bot placing orders was at commit `5b03654` (Phase D, 2026-07-22). This repo
was at `f5dfdf5`. **57 commits, 86 files, 8,151 insertions apart.** Nothing from
§14 onward was running: not the rebuilt snapshot, not `significance.py`, not the
edge report, not `simulate_ensemble`, not §20–§25, not scan rotation.

Config drift was small but load-bearing:

| key | running | HEAD | gated by |
|---|---|---|---|
| `risk.max_open_positions` | 5 | 8 | §13 |
| `risk.risk_sizing.risk_pct` | 0.1 | 5.0 | §11 |
| `risk.scan_rotation` | absent | true | §24 |
| `strategies.*.max_open_positions` | absent | 5/8/5 | §13 |

The cost, from the live ledger's block reasons: **68 entries refused for "max
open positions reached (5)"** — a ceiling §13 had raised to 8 two days earlier —
and slot allocation still decided by YAML ordering, the exact bias §24 exists to
remove. Live output was 7 executed entries in 10 days, 6 of them on one day,
then a full book that stopped turning over. **1 closed trade.**

Worse than the drift: the running checkout lived inside a **Claude
session-outputs scratch directory**, with the launchd jobs pointing at it. A
routine cleanup of that folder would have destroyed the only live track record
in the project — the one asset here that cannot be reconstructed.

**The generalisable lesson, and it is new in kind.** The first six divergences
were all *simulator vs reality*. This one is *repository vs reality*: every gate
in this document was correctly run, correctly recorded, and correctly merged —
and none of it was running. **A gate verdict is not in production until something
checks that it is.** Passing a gate and shipping a gate are different events, and
until now only the first was ever verified.

### DIVERGENCE #8 — the share-granularity floor (a live-only selection bias)

11 live entries were refused with `computed quantity is zero (account too small
for caps)`. All 11 were tsmom. The cause is arithmetic:

`size_order()` gives tsmom a notional of `equity x risk_per_trade_pct` =
**$999** on ~$100k. Shares are whole. So `qty = int(999 // price)`, and:

| symbol | price | qty @ full | qty @ 0.5 downsize | status |
|---|---|---|---|---|
| LLY | $1,182 | **0** | 0 | **can never be bought** |
| GS | $1,076 | **0** | 0 | **can never be bought** |
| CAT | $894 | 1 | **0** | dies on any downsize |
| TMO | $573 | 1 | **0** | dies on any downsize |
| AMD | $536 | 1 | **0** | dies on any downsize |

Two distinct defects sit inside that table.

1. **Two of 38 names are structurally unreachable.** GS was **APPROVED at full
   size** (`verdict: approve, scale: 1.0`) and still produced qty 0. No judge
   verdict, no rail, and no market condition was involved — the position size is
   simply smaller than one share.
2. **"Downsize" silently means "veto" for high-priced names.** A routine
   `scale: 0.5` on AMD at $536 halves $999 to $500 and rounds to zero shares.
   The judge is permitted to shrink a position (invariant 2); vetoing by
   arithmetic accident is a different act, and it is the one that happened.

**Correction, recorded rather than quietly dropped.** A first draft of this
section claimed the ledger files these as a generic risk rejection so the
judge's calibration scoreboard never sees them. **That is true of the RUNNING
bot (`5b03654`) and false of HEAD.** `main.py:588-610` already separates the two
causes — "sizing budget is below one share" vs "LLM downsize xN truncated an
N-share order to 0" — ledgers each with its own reason, and logs a
`rails_reject` judgment so calibration does see it. The reporting half of this
defect was fixed before the audit; reconciling production to HEAD delivers that
fix. **The trades are still lost either way** — better labelling does not buy
LLY at $1,182. What follows concerns the loss, not the label.

### SECOND CORRECTION (2026-07-25, before §27 was built on it)

The paragraph that stood here claimed:

> `simulate_ensemble()` sizes in continuous dollars and has no LLM downsize
> step. It happily takes a fractional position in LLY at $1,182. Every OOS
> number in this document therefore includes trades the live bot is incapable
> of placing.

**Half of that is wrong, and it is the half the argument rested on.**
`src/backtest.py:705` (and `:374` for the single-strategy path) call
**`risk.size_order()`** — the same function live uses, ending in
`int(dollars // price)`. Whole-share rounding is modelled in the backtest
*exactly* as it is live. LLY and GS produce qty 0 in the simulator too. The
backtest is **not** counting trades the live bot cannot place; on that half,
sim and live agree.

Found while designing §27 on top of this section, which is the only reason it
was found at all. A gate built on a false premise is precisely the failure this
document exists to prevent, so the error is corrected in place rather than
quietly dropped — the same treatment §22 gave §20a.

**What IS a genuine live-only divergence, and it is larger.** The backtest never
applies the judge's verdict: there is no `review["scale"]` anywhere in
`backtest.py`. And from the live ledger, the judge **downsizes 53% of all buys**
(77 of 146; scales `0.4:5, 0.5:49, 0.6:15, 0.7:6, 0.8:2`, median 0.5). So any
name whose full-size order is exactly **1 share** is taken in the simulator and
deleted live on roughly half of all decisions.

Measured against the frozen snapshot's last closes at ~$100k equity:

| | names | which |
|---|---|---|
| qty 0 at full size — **sim and live agree** | 2 | LLY $1,189 · GS $1,055 |
| qty 1 in sim, **0 live on any downsize** | 7 | **SPY · QQQ · DIA** · META · COST · CAT · AMD |
| unaffected | 29 | |

**24% of the universe, on 53% of decisions** — and the affected set is not
obscure names, it is the three index ETFs and four mega-caps. The exclusion is
still correlated with price, so it still strips the highest-priced names from a
momentum book: AMD was refused three times while showing +82% 63-bar momentum.

So the original conclusion survives — backtest trade counts overstate what live
can achieve — but the mechanism is the **absent judge**, not fractional sizing,
and the effect is bigger than first reported, not smaller.

This is the same species of defect as §24 (allocation decided by something that
is not the strategy) and it was invisible for the same reason: nothing compared
what the simulator *could* trade against what the broker *would* accept.

**Not fixed here — deliberately.** Every remaining candidate fix touches a gated
risk parameter and must be pre-registered like anything else:

- **fractional/notional orders — RULED OUT, not deferred.** Every entry goes
  through `broker.bracket_market_order()`, and Alpaca does not accept fractional
  quantities on `OrderClass.BRACKET` / `OTO`. Buying these names fractionally
  would mean submitting them **without protective stop legs**, trading a safety
  guarantee for reach. That is the wrong side of the trade and the option is
  closed, not pending a gate.
- raising the **position budget** (a gated risk parameter — needs a gate);
- flooring qty at 1 on an approved entry (changes true risk per trade — needs a
  gate, and would silently exceed the intended risk slice on a $1,182 share).

The observability fix is already in HEAD (see the correction above), so nothing
in this section ships without a gate. Registering one is the natural §27, and it
should be registered as a **CAPACITY claim** under METHOD NOTE 5 — the argument
is "the book can reach names it currently cannot", not "each trade gets better".
Treating it as an EDGE claim would misfire exactly as §19b did.

**Standing consequence.** Until this is resolved, every backtest number in this
document is measured on a universe the live bot cannot fully trade. That is not
a reason to discard them; it is a reason to expect live trade counts to run
below backtest trade counts, and to check that gap rather than assume it away.

## §27 — POSITION BUDGET (2026-07-25) — RULES PRE-REGISTERED

**Claim type: CAPACITY** (METHOD NOTE 5), declared before running. The claim is
*"the book reaches names it currently cannot"*, NOT *"each trade earns more."*
Typing this as EDGE would demand `ci_low > 0` — rejecting the candidate for
failing a claim it never made, which is the §19b misfire exactly.

`src/significance.py` now carries both tests in code (`.significant` for EDGE,
`.not_worse` for CAPACITY) so the rule stops being restated slightly differently
in each section.

### Motivation

§26's second correction: 2 of 38 names are unbuyable at any scale (LLY $1,189,
GS $1,055) and 7 more — **SPY, QQQ, DIA**, META, COST, CAT, AMD — are taken in
the simulator at qty 1 and deleted live whenever the judge downsizes, which is
**53% of all buys**. 24% of the universe, on half of all decisions.

### Why an arm moves TWO knobs

The effective budget is set by two coupled parameters, each binding a different
strategy:

| knob | binds | value | effective budget |
|---|---|---|---|
| `risk.risk_per_trade_pct` | tsmom, ma_crossover (notional path) | 1.0 | $999 |
| `risk.max_order_value_usd` | meanrev (clamps `risk_sizing`) | 2000 | $2,000 |

§11 already recorded that risk_pct 1.0/2.0/5.0 are byte-identical for meanrev
**because this cap clamps them**. So moving one knob tests one strategy. An arm
moves both in lockstep, which keeps this a single-parameter grid rather than a
2-D search — and `tests/test_gate_budget.py` pins that the cap can never clamp a
candidate arm's notional budget, so no arm can silently collapse to baseline.

| arm | `risk_per_trade_pct` | `max_order_value_usd` | budget @ $100k |
|---|---|---|---|
| baseline | 1.0 | 2,000 | $999 / $2,000 |
| b2.0 | 2.0 | 2,000 | $2,000 |
| c2.5 | 2.5 | 2,500 | $2,500 |
| d3.0 | 3.0 | 3,000 | $3,000 |
| e4.0 | 4.0 | 4,000 | $4,000 |

### The tension that makes this a real experiment

`max_portfolio_heat_pct: 4.0` caps total open stop-risk. Bigger positions burn
heat faster, so the book may hold **fewer** positions. Reach trades against
depth, `pure_checks` already models heat, and the ensemble simulator can see it.
This is not a free parameter to crank — it is a genuine trade-off, and the arm
grid extends to 4.0% specifically so the point where depth loss overwhelms
reach gain is inside the tested range rather than beyond it.

### Selection

IS-only, and **selected on REACH first, return as tiebreak**. Selecting on
return would quietly convert a capacity gate back into an edge hunt.

### PRE-REGISTERED RULE

Adopt the IS-selected arm only if, out of sample:

- **(a)** return ≥ baseline − 0.25pp
- **(b1)** PF ≥ 1.30 · **(b2)** PF ≥ baseline − 0.10
- **(c1)** maxDD ≤ baseline × 1.5 · **(c2)** maxDD ≤ 3.0pp absolute
- **(d)** ≥ 15 closed OOS trades
- **(e)** **distinct symbols traded strictly increases** — the actual capacity
  claim, demonstrated rather than assumed
- **(f)** Bonferroni-corrected bootstrap on per-trade P&L has **`ci_high > 0`**
  (not significantly worse)

plus the **§23 monotonicity check**. That check was itself rewritten during this
build: the first draft tested only unimodality, which **accepts a lone spike**
like `[1.0, 1.1, 9.0, 1.2, 1.0]` — precisely the fitted-parameter shape §23 was
caught by. A decorative guard is worse than none because it reads as a control.
It now also requires the winner's lead over its better neighbour to sit within
the spread of the remaining arms, with the scale taken from the data so the
guard has no tunable of its own.

### STATED BEFORE THE RUN — this gate under-measures the fix

`simulate_ensemble` has **no judge**, so the 7 names lost live on a downsize are
invisible to it. The gate can only see the LLY/GS half. **A marginal result here
corresponds to a materially larger live benefit.**

Recorded now, before any numbers exist, so it cannot be reached for afterwards
to argue a failed gate into a pass. It cuts the other way too: if the gate FAILS
on drawdown or PF, that failure is measured on the smaller half and the real
cost would be larger, not smaller.

**Separate sensitivity diagnostic, not part of the rule and not adopted on:**
re-run each arm applying the empirically observed judge-scale distribution
(`0.4:5, 0.5:49, 0.6:15, 0.7:6, 0.8:2, approve 1.0:67`) as a multiplier, to
estimate the invisible half. It introduces randomness and the judge is not
random, so it informs and never decides.

**Prior:** EDGE claims are 0 for 5. CAPACITY claims have a better record because
the bar is lower and honest about being lower — but §21b (universe 38→68) was a
capacity idea that failed on trade count, and the heat cap makes the same
outcome plausible here.

Runner: `scripts/gate_budget.py` — **committed**, unlike §25's ad-hoc heredoc,
so this gate can be re-executed exactly. Running trial count entering §27: ~38
registered comparisons plus grid arms.

### §27 RESULT — REJECTED. The budget is a LEVERAGE knob, not a reach knob.

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…, verified before
the run). Ensemble simulator. IS winner on reach-then-return: `e4.0`.

| arm | OOS return | PF | maxDD | trades | **symbols** | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+1.83%** | **1.526** | **1.17%** | 187 | **37** | 5.38% |
| b2.0 | +3.43% | 1.501 | 2.49% | 183 | 37 | 10.98% |
| c2.5 | +4.37% | 1.501 | 3.10% | 183 | 37 | 13.87% |
| d3.0 | +5.25% | 1.498 | 3.77% | 183 | 37 | 16.81% |
| e4.0 [SELECTED] | +6.89% | 1.482 | 5.06% | 183 | 37 | 22.40% |

Clauses **(c1) FAIL** (maxDD 5.06% vs the 1.76% allowed by baseline × 1.5),
**(c2) FAIL** (5.06% > 3.0pp absolute), **(e) FAIL** (reach unchanged).
Significance passes the capacity bar (`ci_high > 0`, INCONCLUSIVE) and
monotonicity passes. **REJECTED; the incumbent stands.**

**What the numbers actually say.** Return scales ×3.8 from baseline to e4.0 and
drawdown scales ×4.3 — drawdown grows *faster* than return, and PF drifts
slightly DOWN (1.526 → 1.482). That is the signature of pure position-size
leverage, not of reaching better opportunities. The parameter buys return by
taking proportionally more risk, which is a decision about risk appetite and
emphatically not an edge.

Trade count is flat across every candidate arm (183) and reach is flat at 37 of
38 (only PG goes untraded, at every budget). The heat cap **never bound once**
(`heat-blk 0` in all ten runs), so the reach-versus-depth tension predicted at
registration simply did not materialise — worth recording, because the arm grid
was extended to 4.0% specifically to find that point and there isn't one.

### THE MORE IMPORTANT FINDING — clause (e) was UNSATISFIABLE by construction

Reach is **37 at every arm, in every run**, out of sample and in. No budget
value could ever have increased it. The clause registered as *"the actual
capacity claim"* could not have passed regardless of the parameter, so it tested
nothing.

That is the **second decorative control caught in a single session** — the §23
monotonicity check had the same disease hours earlier, passing any unimodal
series including the lone spike it existed to catch. Both looked like controls
and neither could fire.

**Binding on future sections:** a pre-registered clause must be shown to be
SATISFIABLE before the section is registered — run the extreme arm and confirm
the clause *can* flip. A clause that cannot fire is not a conservative test, it
is a decoration that makes a weak gate look strict.

### THIRD CORRECTION TO §26 — "LLY and GS can never be bought" is too broad

The gate traded **LLY 2 times and GS 5 times out of sample**. The claim in §26
was extrapolated from *today's* closes and the *notional* budget alone, and it
does not survive contact with the run:

- their OOS closes ranged **$621–$1,236 (LLY)** and **$441–$1,106 (GS)** — for
  much of the window they sat comfortably *below* the $999 notional budget;
- meanrev sizes through `risk_sizing`, clamped at `max_order_value_usd`
  **$2,000**, not $999 — so the high-priced names are reachable through that
  path at any price under $2,000.

What survives unchanged is the **observed live fact**: 11 real entries were
refused with `computed quantity is zero`, all of them tsmom, and one of those
(GS) had been **approved at full size**. The ledger is not in doubt. What was
wrong was generalising those eleven records into "these names can never be
bought" — they cannot be bought *by tsmom, at today's prices*, which is a much
narrower statement and the one that should have been written.

Three corrections to one section is not a good look, and it is recorded in full
rather than tidied: §26 was written from a live-ledger audit without re-deriving
its claims against the simulator, and every one of the three errors came from
that same shortcut.

### Where this leaves the live defect

The gate has **ruled out raising the budget** — on drawdown, decisively, at
every arm including the mildest. It cannot rule the *live* problem in or out,
because `simulate_ensemble` has no judge and the downsize-to-zero interaction is
invisible to it. §27 answered the question it could answer and the answer is no.

The remaining candidate is the **1-share floor** (a downsize may shrink a
position but never delete it). It is deliberately NOT adopted here: it is not
gateable — the arms would be byte-identical in a simulator with no judge, the
§19b misfire — so it needs an explicit owner decision recorded as such, in the
manner of §20a, rather than a gate verdict it cannot earn.

**Standing note for the next registration.** Clause (c1) — maxDD ≤ baseline ×
1.5 — is near-unpassable whenever baseline drawdown is very small (1.17% here),
because any increase in deployment breaches it. That is arguably correct for a
risk rail and **it does not change this verdict**; it is recorded now, after the
result, precisely so it cannot be quietly relaxed in a later section without the
change being visible.

### §26 ADDENDUM — the drift guard had the mirror-image blind spot (2026-07-25)

`src/deploycheck.py` shipped at ~15:00 to catch divergence #7. At ~15:25,
checking whether the launchd re-point had taken effect, it was pointed at the
production checkout and reported **clean**. It was not clean:

```
branch:      audit/production-drift-2026-07-25
HEAD:        045ca67   (4 commits AHEAD of origin/main)
PR #21:      OPEN — unreviewed
deploycheck: behind 0, config clean -> NO ALERT
```

`behind_upstream()` asks `HEAD..origin/main` — *what does main have that I
lack*. Nothing did. It had no way to ask the opposite. Divergence #7 was
production **behind** main; this was production **ahead** of it, running code no
review had passed, and the module written specifically to catch "the running
code is not the reviewed code" could not see it.

**Fixed** with `ahead_of_upstream()` (`origin/main..HEAD`), reported as its own
clause. One count covers both shapes: a feature branch, and `main` carrying
unpushed local commits — the second being the more dangerous, because the branch
name looks right. Checking the branch NAME instead would have missed it.
Injection-proven against the real state above rather than a synthetic one; the
repository was itself the failing case.

### THE PATTERN — three decorative controls in one session

| control | looked like | actually |
|---|---|---|
| §23 monotonicity check | rejects fitted parameters | passed `[1.0, 1.1, 9.0, 1.2, 1.0]` — the exact lone spike it existed to catch |
| §27 clause (e) | "the actual capacity claim" | unsatisfiable by construction; reach was 37/38 at every arm, so it could never fire |
| `deploycheck` | catches production drift | reported clean on this repo's own drifted production checkout |

Each was written in good faith, read as rigour, and could not do its job. Two
were caught only because something forced them against a real case; the third
because its own repository happened to be broken in the way it was built to
detect.

**Binding rule, now with three data points:**

> **A control must be demonstrated FIRING on the real failure it was built for.**
> Not on a hypothetical, not on a unit-test fixture that was written from the
> same mental model as the code. The §25 split detector met this bar — it was
> run against an injected 10:1 split before being trusted. The three above did
> not, and all three were hollow.

Corollary for pre-registration: before a clause is registered, run the extreme
arm and confirm the clause *can* flip. A clause that cannot fire does not make a
gate conservative; it makes a weak gate look strict, which is worse than an
absent one because it stops anybody looking further.

## §28 — ONE-SHARE FLOOR: NOT REGISTERED. The probe said the gate could not work.

**No gate was written for this.** That is the finding.

### Why it was going to be registered

§26's live defect: GS at $1,098 against a $999 notional budget was refused with
`computed quantity is zero`, while the judge had **APPROVED it at full size**
(`verdict: approve, scale: 1.0`). No verdict and no rail rejected that trade —
whole-share arithmetic did. Owner decision 2026-07-25: fix that case, honour
every downsize.

The design needed almost no code. The floor belongs in **`risk.size_order()`**,
the one function the simulator shares with live (`backtest.py:705`), so no new
sim/live gap is created — and the judge's authority then falls out of the
existing `int(full_qty * review["scale"])` in `main.py` with no special case:
approve keeps the floored share, any downsize truncates 1 back to 0.

I had told the owner this could not be gated. **That was wrong**, and the error
is worth naming: I was reasoning about the *downsize* case, which lives in
`main.py` where the simulator has no judge. The case actually chosen —
"approved at full size, still bought nothing" — is `size_order()` returning 0,
and `size_order()` is shared. Measurable after all.

### The satisfiability probe — the new rule's first save

Per the rule written after §27 (*a clause must be shown able to fire before its
section is registered*), the floor was implemented behind `risk.min_one_share`
and the arms were diffed **before** any rules were drafted.

| | trades | symbols | return |
|---|---|---|---|
| IS baseline | 486 | 38 | +9.366% |
| IS floored | 486 | 38 | +9.366% |
| **IS delta** | **0** | **0** | **0.000pp — byte-identical** |
| OOS baseline | 187 | 37 | +1.829% |
| OOS floored | 183 | 37 | +1.784% |
| **OOS delta** | **−4** | **0** | **−0.045pp** |

**Three independent reasons not to register:**

1. **Reach does not increase — clause (e) fails, exactly as in §27.** +0 symbols
   in-sample and out. LLY, GS, COST, CAT are *already* traded by the baseline,
   because meanrev sizes through the `max_order_value_usd` $2,000 clamp and
   because their prices sat under $999 for much of the window. The capacity
   claim — "reaches names it currently cannot" — is simply **false**.
2. **In-sample is byte-identical**, so IS-only selection would have had nothing
   to select on. A gate whose selection step is a coin flip is not a gate.
3. **Out of sample it is mildly negative**: fewer trades, lower return.

### The mechanism, which is the genuinely interesting part

The floor is **not additive — it is a perturbation.** The OOS trade-level diff
is **29 trades in, 33 out**, and most of the churn is in names that were never
budget-constrained at all: SPY, NVDA, GOOGL, XOM, JNJ, BAC. One forced share of
LLY on 2026-01-08 consumes a slot, heat and cash, which shifts every subsequent
entry decision down the line. The book reshuffles.

And the sharpest number: **the floored-in trades themselves earned +$448.71**,
while total P&L fell from +$1,828.92 to +$1,783.81 — **−$45.11**. The new trades
made money; the trades they displaced would have made more. Pure opportunity
cost. "This trade would have been profitable" is not the same claim as "taking
it makes the book better," and only the ensemble simulator can tell them apart.

(The magnitude — 0.045pp against a 1.83pp baseline — is inside the noise band
every other section has measured. The reason not to adopt is clause (e) being
false, not this number.)

### What this does to §26 divergence #8

It reframes it, and downward. "The bot refuses names whose single share exceeds
its position budget" reads like a bug and is closer to **a rail doing its job**:
a name you cannot size properly is a name your account is too small to trade,
and forcing the position spends a slot that a correctly-sized one would use
better. The defect is real and still ugly — an approved trade killed by
rounding — but **fixing it is not worth anything measurable**, and on this
snapshot it costs slightly more than it returns.

What stands unchanged: HEAD already *reports* the two causes distinctly and logs
a `rails_reject` judgment, so the judge's calibration sees it. Visibility was
the part worth having.

### Disposition

`risk.min_one_share` ships **false**, with the code path retained and tested —
the same treatment as `vol_target`, `regime_exposure` and `donchian`. Enabling
it would be adopting an unmeasured change, and the measurement that exists says
don't.

**Incidental fix found by the probe's tests:** `size_order()` raised
`ZeroDivisionError` on a non-positive price, which would have taken a whole
cycle down rather than skipping one bad symbol. It now returns 0 — fail-safe.
The bug predates §28 and had never been exercised because upstream freshness and
cross-vendor guards had always caught bad bars first.

### For the record: the §27 rule has now paid for itself twice

§27 clause (e) was unsatisfiable and nobody noticed until after the gate ran.
§28's clause (e) was unsatisfiable in exactly the same way, and the probe caught
it **before a single rule was written**. Checking satisfiability first cost one
script and about two minutes.

---

## §29 — UNCAP THE BOOK AND RAISE SIZING (owner decision, 2026-07-26)

**PRE-REGISTERED BEFORE THE SIZING SWEEP RAN.** The uncapped-count baseline
(arm B below) had already been measured when this was written and its numbers
appear here; the `risk_per_trade_pct` sweep — the arm that actually decides the
question — had not. Thresholds in "Decision rule" are fixed from this point.

### Why this is not §27 again

§27 raised `risk_per_trade_pct` 1.0 → 4.0 with `max_order_value_usd` 2000 → 4000
and was **REJECTED** on maxDD 5.06% against a 3.0pp ceiling. Two things have
changed, and neither is "we decided we liked the answer better":

1. **The ceiling moved, by owner decision, and is recorded as such.** The owner
   set a **10.0pp maxDD tolerance** on 2026-07-26. §27's 5.06% would clear it.
   This is a change of risk appetite, not a re-analysis — stated plainly so
   nobody later reads §29 as having refuted §27's arithmetic. It did not.
2. **The simulator §27 ran on was wrong.** It never applied the judge's
   downsize (divergence #8, live half). Measured this session: the judge cuts
   **53.5% of buys at mean scale 0.752**, so every OOS figure in this document
   before today — §27's included — is roughly **34% optimistic**. Confirmed
   directly: tsmom OOS +0.62% without the model, **+0.41% with it**.

### The finding that reframes the whole document

`max_order_value_usd: 2000` on ~$100k equity was a 2% ceiling on every position,
and it fired **before every other rail**. That single line is why this document
is full of rails that measured as inert:

| section | recorded verdict | actual cause |
|---|---|---|
| §11 | `risk_pct` 1.0 / 2.0 / 5.0 byte-identical | clamp fired first |
| §18 | `regime_exposure` REJECTED, "gross never nears the cap" | clamp fired first |
| §20b | per-strategy slots INERT | clamp fired first |
| heat cap | "four positions total ~$560 against a $4,000 cap" | clamp fired first |

Measured on the frozen snapshot, it held **average deployment at 2.5% of
capital**. The bot was ~97% in cash. That, not signal quality, is the leading
explanation for OOS returns sitting near zero.

### Arms

All on `memory/bars_snapshot_2020_2026-07-10.json` + `earnings_snapshot.json`,
slippage pinned 5 bps, 70/30 walk-forward, tsmom, `--judge-model` ON for every
arm so they are mutually comparable and comparable to live.

- **A — status quo ante**: caps as of 2026-07-25, judge model on.
  Measured: OOS **+0.41%**, 100 trades, PF 1.331, maxDD 0.5%, deployment 1.9%.
- **B — counts uncapped, sizing unchanged** (`max_open_positions: 0`,
  per-strategy 0, `max_trades_per_day: 15`, `max_order_value_usd: 0`).
  Measured: OOS **+1.94%**, 312 trades, PF 1.607, maxDD 0.73%, deployment 5.04%,
  0 heat blocks, 130 correlation blocks.
- **C — the sweep, NOT YET RUN**: arm B plus `risk_per_trade_pct` ∈
  {1, 2, 4, 6, 8}. This is the arm that decides the question, because arm B
  showed the count caps were never the binding constraint on deployment — the
  1%-of-equity sizing *target* is.

### Decision rule — fixed before arm C runs

Adopt the **highest** `risk_per_trade_pct` that satisfies ALL of:

- (a) OOS maxDD **≤ 10.0pp** (owner ceiling, 2026-07-26)
- (b) OOS profit factor **≥ 1.3** (the standing enablement bar, unchanged)
- (c) OOS return **> arm B's +1.94%** — scaling must buy something
- (d) OOS trades **≥ 15** (standing bar)
- (e) profit factor must not fall below **1.3**; if PF degrades monotonically
      with size while return rises, prefer the knee, not the maximum — §12
      measured exactly that shape for tsmom (2.398 → 1.977 → 1.730)

If no arm satisfies (a)-(d), `risk_per_trade_pct` **stays at 1.0** and that is
recorded as the result. Raising it anyway would be adopting a change the
measurement rejected, which is the one thing this document exists to prevent.

### Counter-evidence carried forward, not buried

§12 measured tsmom's PF **degrading** as slots rise (2.398 → 1.977 → 1.730)
while return rises — extra trades bought at a worse rate. Arm B is the first
test of that claim at non-trivial deployment, and **it did not replicate**: PF
went 1.331 → 1.607 as trades went 100 → 312. The honest reading is that §12 was
measured behind the clamp, at 2.5% deployment, and may simply not describe the
uncapped regime. It is not yet refuted — one arm is not a refutation — and it
remains the reason clause (e) exists.

### Known limitations of the judge model, stated up front

It reproduces the judge's *distribution*, not its *decisions*. Nothing can
recover which specific trades a model would have vetoed in 2024. The calibration
rests on **146 decisions over 7 live days in one market regime**, heavily
serially correlated, so the effective sample is far below 146 and the tails are
unmeasured. It is strictly closer to live than modelling no judge at all, which
is the bar it has to clear — not accuracy it does not have.

### §29 RESULT — ADOPTED at `risk_per_trade_pct: 8.0`

Arm C, run 2026-07-26 on the frozen snapshot, judge model on, counts uncapped:

| risk% | OOS return | trades | PF | maxDD | deployment | return/maxDD |
|---|---|---|---|---|---|---|
| 1 | +1.94% | 312 | 1.607 | 0.73% | 5.0% | 2.65 |
| 2 | +4.00% | 322 | 1.567 | 1.55% | 10.6% | 2.58 |
| 4 | +8.30% | 322 | 1.539 | 3.39% | 22.4% | 2.45 |
| 6 | +13.25% | 322 | 1.547 | 5.12% | 34.2% | 2.59 |
| **8** | **+17.45%** | 319 | 1.549 | **7.51%** | 43.6% | 2.32 |

Every arm clears (a) maxDD ≤ 10.0pp, (b) PF ≥ 1.3 and (d) trades ≥ 15. Arms
2-8 clear (c) return > +1.94%. Clause (e)'s knee does not trigger: PF is
1.607 → 1.567 → 1.539 → 1.547 → 1.549, which flattens rather than degrading
monotonically, so §12's slot-quality decay did not reappear as a sizing decay.
The rule selects the highest qualifying arm: **8.0**.

**What this result is NOT.** Return rose 9x and maxDD rose 10.3x; return/maxDD
went 2.65 → 2.32. Risk-adjusted quality got slightly *worse*. This is leverage,
correctly measured, not a better strategy — and it still loses outright to B&H
+36.21%, passing the enablement gate only through the exposure-matched hatch.
Anyone reading "+17.45%" without this paragraph has misread the table.

**The boundary was not found.** 8 was the top of the pre-registered range and it
cleared the ceiling with 2.5pp to spare. 10 might also clear. Extending the
sweep now, having seen the shape, would be shopping — it needs its own gate.

**Regime exposure, enabled on §18's own condition.** §18 rejected
`regime_exposure` as untestable because "gross never nears the cap ... Revisit
only if sizing scales up." At risk 8.0, deployment is 43.6% and it binds:

| arm | OOS return | PF | maxDD | return/maxDD |
|---|---|---|---|---|
| off | +17.45% | 1.549 | 7.51% | 2.32 |
| `down_max_gross_pct: 50` | **+20.25%** | **1.694** | **5.71%** | **3.55** |

Better on all three axes. Unlike the sizing raise this IS a risk-adjusted gain,
and it is the documented momentum-crash result (Kaminski-Lo, Daniel-Moskowitz).
**Adopted at 50.**

### §30 CANDIDATE — tighter down-regime gross cap. NOT ADOPTED.

A 30% arm scored better than 50 on every metric (+22.55%, PF 1.791, maxDD
4.90%, return/maxDD 4.60). It is **not adopted**, because 50 was the value §18
had already configured whereas 30 was chosen after seeing the OOS result.
Testing two values and keeping the winner is in-sample selection performed on
the holdout, and one contaminated pick is how a document like this stops being
trustworthy. A proper gate needs a pre-registered range and a fresh split.

### §31 CANDIDATE — `daily_loss_limit_pct` is now inconsistent with the DD ceiling

Not a backtest question; an operational one, flagged rather than silently
changed. `daily_loss_limit_pct: 3.0` flattens the book and engages HALT on a
single -3% day. At 5% deployment that was nearly unreachable. At **43.6%
deployment** a ~7% adverse move in the held names reaches it, and the owner's
accepted drawdown tolerance is now **10pp** — so the kill switch would fire and
liquidate at roughly a third of the drawdown the owner has said is acceptable.

Left at 3.0 deliberately. Raising a kill switch is a safety decision and it is
the owner's, not something to fold into a sizing gate.

### §31 RESOLVED — drawdown circuit breaker built, daily limit realigned

Owner approved both on 2026-07-26.

**`max_drawdown_pct: 10.0` (new).** Peak-to-trough from an equity high-water
mark. Blocks new **entries** only; exits always run. A rail that stopped you
selling during a drawdown would trap you in the book it exists to protect you
from, at the moment getting out matters most.

Deliberately distinct from `daily_loss_limit_pct`, which flattens and HALTs:

| | measures | acts |
|---|---|---|
| `daily_loss_limit_pct` | one session | **liquidate + HALT** — hard stop |
| `max_drawdown_pct` | peak to trough, any duration | **block entries** — soft brake |

A book that bled 10% over three weeks, never losing 5% in one session, passed
every rail the bot had before today.

**`daily_loss_limit_pct: 3.0 → 5.0`.** At 5% deployment a -3% day was nearly
unreachable; at 43.6% it is not. Against the owner's 10pp tolerance the old
value would have liquidated the book at roughly a THIRD of the accepted
drawdown — converting a survivable correction into a realised loss at the low.
Half the total tolerance is the reasoning: one day consuming half the drawdown
budget is a genuine emergency.

**Enforced in the simulator too.** Live reads a persisted high-water file; the
backtester keeps a running peak. Both call the same `risk.drawdown_pct()`, so
they cannot disagree about what a drawdown is — a live-only rail would have
been divergence #9, created by the work closing #8. Pinned by
`test_the_backtester_enforces_the_same_rail`.

At the adopted settings it does not bind: OOS maxDD is 5.71% against a 10.0
limit. It is a backstop, not an active constraint, which is the correct state
for a circuit breaker.

### Method note: a negative-control harness that lied

Two mutations happened to leave `src/risk.py` **exactly 11 bytes shorter** than
the original. CPython validates `.pyc` files on (mtime, size) at one-second
resolution, so the second mutation silently reused bytecode compiled during the
first — and the harness reported a **broken guard as verified**. The ratchet
test was green while the ratchet was disabled.

Caught because the same mutation went red when run standalone and green inside
the loop. Fixed by purging `src/__pycache__` and setting
`PYTHONDONTWRITEBYTECODE=1` per mutation; all three suites re-run, 18/18 red.

Worth recording because the failure direction is the dangerous one: a false RED
is noisy and self-correcting, a false GREEN is a rail you believe is tested and
is not. Any mutation-testing harness in this repo needs the same treatment.

---

## §31 — CROSS-ASSET RISK GATE (pre-registered 2026-07-27, before any run)

**Claim type: EDGE.** Not capacity, not leverage. The claim is that entries
taken while credit is deteriorating are *worse trades*, and that skipping them
raises per-trade expectancy. If it only shifts exposure it has failed.

### Why this idea, and why now

Prompted by comparing this repo against `TauricResearch/TradingAgents`
(94.7k stars, Apache-2.0). That project has no broker, no backtest engine and no
statistical gating — but it does have something this one does not: **information
breadth.** It reads fundamentals, macro, sentiment and prediction markets.

Every strategy here — `ma_crossover`, `tsmom`, `meanrev`, `xsmom`, `donchian` —
is a price-only rule over 38 of the most heavily arbitraged instruments in
existence. `:611` reports no OOS per-trade edge distinguishable from zero across
all five after Bonferroni correction. **That is the expected result for
price-only rules on SPY/QQQ/AAPL/NVDA, not a surprise.** News exists in the
system but only widens the universe through nominations (`main.py:592`); it has
never entered a signal.

So the one change worth testing is an input the price series does not already
contain.

### Why cross-asset prices rather than FRED

The obvious borrow is FRED macro. It carries a trap: FRED serves **current**
values and most macro series are revised. Backtesting against revised data is
lookahead, and it would quietly invalidate this gate rather than fail it loudly.
ALFRED vintages are the correct fix and are a separate project.

Cross-asset **prices** have no revision problem. They are point-in-time by
construction, they arrive through the same yfinance path as everything else, and
they carry the same orthogonality argument: information not contained in the
traded symbol's own price history.

Channel: **HYG:LQD** — high-yield credit against investment grade. When credit
risk appetite deteriorates, that ratio falls before equity indices do, often
enough to be worth one honest test. `UUP` (dollar) is fetched into the snapshot
for a later question and is **not** part of this gate.

### Data

Separate snapshot `credit_2020-01-01_2026-07-10.json.gz`,
sha256 `9b8ac92c77137a819e62339f50e35b34f1b85e9e2be4f126c7dedd7ccb457621`,
3 symbols (HYG, LQD, UUP), 4914 bars, yfinance `auto_adjust=True`.

Verified before registering: HYG/LQD/UUP each have **1638 bars**, identical to
SPY's 1638 in the price snapshot, and **zero** SPY session dates lack a HYG bar.
No alignment gap to paper over.

**The price snapshot is NOT rebuilt.** `bars_2020-01-01_2026-07-10.json.gz`
(sha256 `6abb20b5…`) stays byte-identical, so §31's arms are measured on exactly
the price data §27 and §29 were. Adding three tickers to it would have changed
its hash and silently orphaned every earlier verdict anchored to it.

### The rule under test

A filter over existing entry signals, in the shape of §23's `rvol_blocked`:
one implementation shared by live and both simulators, **fails open** on missing
data, and is **never applied to exits** — a position must always be able to
leave. Not a sixth strategy; the strategies are unchanged.

### Arms (3 — K deliberately small, it inflates the Bonferroni correction)

| arm | rule |
|---|---|
| **baseline** | no gate (incumbent) |
| **sma50** | block new entries while HYG:LQD < its own 50-bar SMA |
| **sma100** | block new entries while HYG:LQD < its own 100-bar SMA |

### Pass mark — fixed now, before any numbers exist

Selection on **IS only**; OOS reported once. Split 70/30 as in §27.

An arm is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` on OOS in full — return > 0, ≥15 trades,
  PF ≥ 1.3, and beats B&H *or* the risk-adjusted bar *or* the exposure-matched
  benchmark (`backtest.py:969`)
- **(b)** OOS profit factor **strictly greater** than baseline's. An EDGE claim
  that does not raise PF is not an edge claim
- **(c)** OOS max drawdown ≤ baseline's + 1.0pp absolute
- **(d)** ≥ 15 OOS trades **and** ≥ 60% of baseline's trade count. A filter that
  reaches the PF bar by taking 6 trades has not found an edge, it has found a
  smaller sample
- **(e)** Bonferroni-corrected block bootstrap (`significance.compare`, K=3)
  returns better-than-baseline, not INCONCLUSIVE

**(b), (d) and (e) are the ones that matter.** A gate that merely sits out bad
weather will pass (a) and (c) by lowering exposure — that is the capacity
result, and this is not a capacity claim.

### Prior — stated before the run, honestly

**EDGE claims in this repo are 0 for 5.** §23 (relative volume) was the closest
structural analogue and was REJECTED. The base rate for what follows is a
rejection, and the registration exists so that outcome cannot be renegotiated
afterwards.

Two specific ways this fails that are worth naming now:

1. HYG:LQD < SMA is a *slow* signal. It will likely be below its average through
   most of the drawdowns the drawdown breaker already handles, so it may be
   measuring the same thing twice.
2. Credit stress and equity weakness are contemporaneous more often than credit
   leads. If so this removes trades roughly at random with respect to outcome —
   which shows up as flat PF and lower trade count, i.e. (b) and (d) fail
   together.

**Running trial count entering §31:** ~41 registered comparisons plus grid arms
(38 entering §27, plus §27's 5 arms scored as one comparison, plus §29).

**Runner:** `scripts/gate_cross_asset.py` — committed, not a heredoc, so this
gate can be re-executed exactly.

### §31 RESULT — REJECTED. Credit direction does not sort this bot's trades.

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 `6abb20b5…`) and
`credit_2020-01-01_2026-07-10.json.gz` (sha256 `9b8ac92c…`), both **verified by
the runner before scoring** — it refuses to run on a drifted file. Ensemble
simulator, 38 symbols, 70/30 split, IS ends 2024-07-23, OOS starts 2024-07-24.
The OOS credit slice carries 100 bars of lead-in so `sma100` is warm at its
first OOS bar rather than silently disabled for 100 sessions.

**In-sample** (selection here and only here, on profit factor — the EDGE measure):

| arm | IS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| baseline | +46.47% | 1.662 | 10.84% | 542 | 40.03% |
| **sma50 [IS winner]** | **+71.39%** | **1.784** | 9.95% | 767 | 59.11% |
| sma100 | +39.92% | 1.715 | 10.42% | 412 | 33.60% |

**Out-of-sample** (reported once):

| arm | OOS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| **baseline** | **+14.42%** | **1.296** | 11.11% | 429 | 70.59% |
| sma50 [SELECTED] | +12.42% | 1.266 | 10.19% | 330 | 60.18% |
| sma100 [not selected] | +16.82% | 1.400 | 8.85% | 309 | 57.75% |

Clause **(a) FAIL** — `enablement_gate()` refuses it twice over: OOS PF 1.266 <
1.3, and +12.42% beats neither B&H +49.70%, nor the risk-adjusted bar, nor the
exposure-matched benchmark +29.91% at 60.2% deployment. Clause **(b) FAIL** — PF
1.266 vs baseline 1.296, *lower*, and an EDGE claim that lowers profit factor
is not an edge claim. Clauses (c), (d1), (d2) pass. Clause **(e) FAIL** —
INCONCLUSIVE: +$37.64/trade vs +$33.62/trade, diff +$4.01 with a 98.33%
Bonferroni CI of [-$126.08, +$176.25]. The interval is thirty times wider than
the effect.

**REJECTED. The incumbent stands; `risk.credit_sma_period` ships as 0.**

### The failure mode was the one named in the registration

Registered beforehand: *"if the filter removes trades roughly at random with
respect to outcome, PF stays flat while the count drops — clauses (b) and (d)
fail together, and that pattern is a REJECT, not a near miss."*

That is what happened. The filter removed **99 of 429 OOS trades (23%)** and
profit factor moved from 1.296 to 1.266 — down, not up. Removing a quarter of
the trades changed per-trade expectancy by less than the noise in a single
trade. Credit direction is not sorting good entries from bad ones; it is
sampling them.

### The part worth reading twice

**`sma100` — the arm that was NOT selected — has the best OOS row in the table.**
Higher return than baseline, PF 1.400 against 1.296, and the lowest drawdown of
the three.

Selection happened in-sample, where `sma100` placed *third of three* on profit
factor (1.715 vs `sma50`'s 1.784). So the pre-registered procedure picked
`sma50`, and `sma50` lost out of sample.

This is precisely the situation pre-registration exists for. With the OOS table
in front of me I can construct a completely convincing story for `sma100`: a
slower credit average is less twitchy, it avoids whipsaw around the moving
average, of course 100 beats 50. That story would be written *after* seeing the
number it explains, which makes it worth nothing. Two candidate arms and a coin
flip produce a winner half the time.

**`sma100` is not adopted, and this result may not be used as evidence for it.**
Its OOS number is now burned: it has been looked at, so it can never again serve
as an out-of-sample test of that arm. Testing `sma100` honestly requires a fresh
pre-registration on a split this run did not touch — the same treatment §30 is
waiting on. Recorded here so that a later session cannot find the number,
reasonably conclude it looks promising, and quietly enable it.

**EDGE claims are now 0 for 6.**

### What this does and does not rule out

It does **not** show that cross-asset information is useless. It shows that
*this* measure (HYG:LQD versus its own SMA), used *this* way (a binary
all-symbols entry block), does not improve *these* strategies on *this*
snapshot. The registration named the likely reason in advance: credit stress and
equity weakness are largely contemporaneous, so a slow credit average mostly
re-detects drawdowns the circuit breaker already handles.

What is genuinely learned: a market-wide on/off switch is a blunt instrument.
Every strategy sees the same boolean on the same day, so the filter can only
move exposure in time — it cannot rank one symbol against another. The
cross-sectional version of this idea (rank symbols by sensitivity to credit,
rather than gate the whole book) is a different claim, needs its own
registration, and is not tested here.

The rail itself stays in the codebase, switched off, with its tests
(`tests/test_credit_gate.py`, 10 boundary-paired cases proving it cannot read
past the decision bar and fails open on every missing-data shape). A rejected
arm keeps its implementation so the next registration does not have to rebuild
and re-verify it — but it stays at 0, and a strategy change is never adopted by
enabling it and seeing what happens.

---

## §32 — WIDE-UNIVERSE CROSS-SECTIONAL FACTORS (pre-registered 2026-07-27, before the runner existed)

**Claim type: EDGE.** Per-trade expectancy, not exposure, not reach.

### Why, and why this is different from §14–§31

Every gate before this one moved a *rule* over the same 38 mega-caps. All five
strategies are price-only, and `:611` reports no OOS per-trade edge
distinguishable from zero across all of them. **That is the expected result for
those instruments, not bad luck** — SPY, AAPL and NVDA are the most heavily
arbitraged prices in existence.

`src/strategies/xsmom.py` has been a correct Jegadeesh–Titman 12-1
implementation since it was written, and has never been enabled. It could not
have worked: ranking needs dispersion, and the top 25% of 38 mega-caps is nine
names that move together. §21b's 38→68 expansion failed on trade count because
68 is still tiny.

§32 changes the **universe**, not the rules. That is the one variable no
previous section has moved.

### Data

`bars_wide_2020-01-01_2026-07-10.json.gz`, sha256
`825116a6d66e4d991a1928c3a5b9aed653966f52547e91b6b4afec78e24413a7`,
**500 symbols, 818,787 bars.** Built from current S&P 1500 membership
(1,504 fetched → 1,388 with a real 2020 → top 500 by median 2020 dollar volume).
Window matches the existing snapshot, so §32 stays comparable with §14–§31.

Dispersion this buys, measured before the run: trailing-1y returns run
p10 **−27.1%**, median **+10.1%**, p90 **+65.0%**. A 92-point spread to rank
across, where 38 mega-caps had almost none.

**SPY is force-included.** `simulate_ensemble` reads it for the regime label and
vol bucket (`backtest.py:640`) and `risk.regime_exposure` is enabled — a
universe without SPY silently stops that rail binding. The first build omitted
it; caught before it scored anything. It is rankable like any other symbol
(1 of 500).

#### Survivorship bias — named now, with its direction

Today's index membership backtested from 2020 omits everything that delisted or
was removed. This **inflates** results, and momentum exploits the deletion more
than buy-and-hold does.

Two things reduce it, neither eliminates it:

1. **S&P 1500, not S&P 500.** Names that fell from large-cap into mid/small are
   still present, recovering part of the loser distribution a large-cap-only
   list would silently delete.
2. **The comparison largely cancels it.** `backtest.py` computes
   `buy_hold_return_pct` from the *same* `sym_bars`, so both sides of the gate
   see the same survivor set.

Consequence, stated before the numbers exist: **a REJECTION here is trustworthy;
a PASS is provisional** and must be confirmed on a split this run never touched
before anything is enabled.

### Shared risk block — identical across ALL arms

Only the STRATEGY differs between arms. If the baseline kept `risk_per_trade_pct:
8.0` while candidates ran at 2.0, this would measure a sizing change wearing a
factor's clothes.

| key | §32 value | shipped | why |
|---|---|---|---|
| `risk_per_trade_pct` | **2.0** | 8.0 | 50 names × 8% is 400% of equity; cash runs out at ~12 positions |
| `max_position_pct` | **2.5** | 10.0 | equal-weight ceiling for a ~50-name book |
| `max_portfolio_heat_pct` | **20.0** | 4.0 | at 4.0 every arm is capped by heat rather than by its signal, and all six return the same answer |
| `max_trades_per_day` | **50** | 15 | at 15 the book takes four days to build, unequally across arms |
| `min_holding_days` | 2 | 2 | **unchanged — invariant 3** |
| `max_drawdown_pct` | 10.0 | 10.0 | unchanged |

**This means §32 measures SIGNAL QUALITY, not deployability at the shipped
rails.** An arm that passes here has not been shown to work at the current risk
posture, and Phase 4 must re-run any winner at shippable settings. Recorded now
so a pass cannot later be read as "ready to enable".

### Arms — the family is fixed HERE, K = 6

Fixing all six now is what stops this becoming "run momentum, fail, quietly try
low-vol", which is sequential testing with no correction.

| arm | strategy config |
|---|---|
| **baseline** | shipped ensemble (ma_crossover, tsmom, meanrev) on the wide universe |
| **xsmom-12-1-10** | xsmom only; `rank_lookback_bars 231`, `skip_bars 21`, `buy_top_fraction 0.10`, `exit_below_fraction 0.5` |
| **xsmom-12-1-20** | as above, `buy_top_fraction 0.20` |
| **xsmom-6-1-10** | xsmom only; `rank_lookback_bars 126`, `skip_bars 21`, `buy_top_fraction 0.10` |
| **lowvol-60-10** | lowvol only; `vol_period 60`, `buy_bottom_fraction 0.10`, `exit_above_fraction 0.5` |
| **both-10** | xsmom-12-1-10 **and** lowvol-60-10 enabled together, priority xsmom→lowvol |

`both-10` is an ensemble arm rather than a new blended-rank module on purpose: it
needs no new code, and it is how the live bot would actually run two
cross-sectional strategies.

### Pass mark — fixed before any number exists

Selection on **IS profit factor** (the EDGE measure). Selecting on total return
would let an arm win by holding more, which is a capacity result and not the
claim. OOS reported **once**. Split 70/30, as §27 and §31.

An arm is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` on OOS in full (`backtest.py:969`)
- **(b)** OOS profit factor **strictly greater** than baseline's
- **(c)** OOS max drawdown ≤ baseline's + 1.0pp absolute
- **(d)** ≥ **30** OOS trades (raised from §31's 15 — a 500-name universe has no
  excuse for a thin sample, and 30 is the same bar `review.py:24` sets for going
  live)
- **(e)** Bonferroni-corrected block bootstrap (`significance.compare`, **K=6**)
  returns **significantly better**, not INCONCLUSIVE

**(b) and (e) are the ones that matter.** A wide universe alone will raise
absolute return simply by having more to buy; that is capacity, and it is not
what is being claimed.

### Prior — before the run

**EDGE claims in this repo are 0 for 6.** Nothing about a wider universe changes
that base rate on its own.

Three ways this fails, named now so a near miss cannot be talked into a pass:

1. **Survivorship carries it.** Momentum looks strong precisely because the
   universe is the set of names that survived to 2026. Shows up as a large
   IS/OOS gap.
2. **Costs eat it.** Cross-sectional rebalancing turns over far more than the
   incumbent. At 5bps slippage a 300-trade OOS arm pays real money; PF falls
   toward 1.0 while gross return looks fine.
3. **It is beta, not alpha.** Buying the top decile of a 500-name universe in a
   bull sample is a leveraged long. Caught by `enablement_gate()`'s B&H and
   exposure-matched clauses, which is why (a) is non-negotiable.

**Running trial count entering §32:** ~44 registered comparisons plus grid arms
(41 entering §31, plus §31's three arms scored as one comparison).

**Runner:** `scripts/gate_wide_universe.py` — committed, hash-verifies the
snapshot before scoring, refuses to run on drift.

### §32 RESULT — REJECTED. And the run found something worse than a failed arm.

Snapshot `bars_wide_2020-01-01_2026-07-10.json.gz` (sha256 `825116a6…`),
hash-verified by the runner before scoring. 500 symbols, 70/30 split, ensemble
simulator, shared risk block. Total runtime 13m35s.

**In-sample** (selection here and only here, on profit factor):

| arm | IS return | PF | maxDD | trades |
|---|---|---|---|---|
| baseline | +62.16% | 1.493 | — | 2477 |
| xsmom-12-1-10 | +0.17% | 1.007 | — | 256 |
| xsmom-12-1-20 | −9.16% | 0.631 | — | 288 |
| xsmom-6-1-10 | +35.83% | 1.950 | — | 503 |
| **lowvol-60-10 [IS winner]** | **+79.15%** | **4.644** | — | 541 |
| both-10 | +74.73% | 3.115 | — | 707 |

**Out-of-sample** (reported once). OOS buy-and-hold on this universe: **+29.03%**.

| arm | OOS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| baseline | **−2.55%** | 0.864 | 10.77% | 358 | 23.5% |
| xsmom-12-1-10 [not selected] | +63.17% | 5.061 | 11.62% | 269 | 47.6% |
| xsmom-12-1-20 [not selected] | +32.05% | 3.094 | 8.36% | 370 | 47.1% |
| xsmom-6-1-10 [not selected] | +38.43% | 2.273 | 12.56% | 461 | 60.0% |
| **lowvol-60-10 [SELECTED]** | **+6.71%** | 1.347 | 7.93% | 490 | 83.2% |
| both-10 [not selected] | +5.55% | 1.262 | 7.72% | 564 | 83.5% |

Clause **(a) FAIL** — `enablement_gate()`: +6.71% beats neither B&H +29.03%, nor
the risk-adjusted bar, nor the exposure-matched benchmark +24.16% at 83.2%
deployment. Clauses (b), (c), (d) pass. Clause **(e) FAIL** — INCONCLUSIVE:
+$13.69/trade vs −$7.13/trade, diff +$20.82, 99.17% Bonferroni CI
[−$42.57, +$98.70].

**REJECTED. Nothing is enabled. EDGE claims are now 0 for 7.**

---

### The finding that matters more than the verdict

**In-sample selection did not predict out-of-sample performance on this data.**

| arm | IS PF | OOS PF | IS rank | OOS rank |
|---|---|---|---|---|
| lowvol-60-10 | 4.644 | 1.347 | **1** | 4 |
| both-10 | 3.115 | 1.262 | 2 | 5 |
| xsmom-6-1-10 | 1.950 | 2.273 | 3 | 3 |
| baseline | 1.493 | 0.864 | 4 | 6 |
| xsmom-12-1-10 | 1.007 | 5.061 | 5 | **1** |
| xsmom-12-1-20 | 0.631 | 3.094 | **6** | 2 |

Spearman rank correlation IS→OOS: **−0.543 on profit factor, −0.600 on total
return.** The in-sample best finished 4th; the in-sample **worst** finished 2nd.

**Stated with the caution it deserves: n = 6 arms.** A Spearman of −0.543 at n=6
is nowhere near significant (|ρ| ≈ 0.83 would be needed at p=0.05), so this is
**not** evidence that selection actively anti-predicts. What it is evidence of:
the strong positive relationship the entire walk-forward procedure assumes is
**not present here**, and cannot be assumed in §14–§31 either.

**The likely cause is that this split is not a sample, it is a regime cut.**
IS covers 2020-01→2024-07: the COVID crash, the 2021 melt-up, the 2022 bear.
OOS covers 2024-07→2026-07. Those are not two draws from one distribution, they
are two different market environments. "Out-of-sample" has been functioning as
"out-of-regime" — which is a harder and more honest test, but it also means a
single 70/30 split cannot rank strategies reliably in either direction.

This applies **retroactively to every gate from §14 onward.** It does not
invalidate the rejections — a rejection under a noisy selector is still a
failure to demonstrate an edge, and the bar was never cleared. It does mean
that if any section had produced a PASS, that pass would have rested on a
selection step with no demonstrated predictive validity.

**Consequence: the next experiment is a METHOD claim, not another factor.**
Hunting more arms with a selector that does not rank is how a false positive
eventually gets adopted. §33 tests the selection procedure itself across
multiple folds.

---

### The trap, larger than in §31

`xsmom-12-1-10` returned **+63.17% OOS with a profit factor of 5.061**, against
buy-and-hold's +29.03% — more than double the benchmark, at a comparable
drawdown. It is the single best OOS row this repository has ever produced.

It was ranked **fifth of six in-sample** (PF 1.007) and was not selected.

Everything about the finding above says this number should be treated as
approximately uninformative. The selection metric did not rank; the arm that
looks spectacular here is the one the procedure judged nearly worst four years
earlier; and the OOS window is a single strong bull regime in which a
concentrated top-decile momentum book is a leveraged long.

**`xsmom-12-1-10` is NOT adopted, and its OOS number is now burned** — it has
been observed, so it can never again serve as an out-of-sample test of that arm.
Recorded here in full, and prominently, precisely because it is the most
tempting number in the repo and the easiest one for a later session to find,
rationalise and enable.

---

### Two further findings worth keeping

**The incumbent ensemble does not generalise.** The baseline went **−2.55% OOS
with PF 0.864** on 500 names, having been comfortably positive on 38. Whatever
`ma_crossover`/`tsmom`/`meanrev` are doing, it does not survive contact with a
wide universe. That is a fact about the shipped strategies, measured here for
the first time.

**A wide universe produces positions with no stop.** The run logged repeated
`bracket stop would be non-positive (entry 185.96, ATR 101.74) — falling back to
plain market order`. Investigated: these are **real** market data, not
corruption — CZR, NCLH, CCL, APA and OKE during the March 2020 crash, and CAR in
April 2026, at ATR/price ratios of 0.36–0.62. On 38 mega-caps this essentially
never happened. On 500 names it does, and the fallback means those positions run
**without stop-loss protection**. Independent of any gate verdict, the wide
universe is not shippable until that path is handled deliberately.

### What §32 does and does not rule out

It does not show that cross-sectional factors are useless — it shows that a
single 70/30 split on 6.5 years cannot tell which of six candidates is better,
in either direction. Both statements about momentum and low-volatility here are
unresolved rather than answered.

`lowvol` stays in the registry, **disabled**. `xsmom` stays **disabled**. A
rejected arm keeps its implementation so the next registration need not rebuild
it; it does not get enabled to see what happens.

---

## §33 — DOES WALK-FORWARD SELECTION PREDICT? (pre-registered 2026-07-27, before the runner existed)

**Claim type: METHOD.** A new type. §14–§32 tested *strategies* using a fixed
procedure; §33 tests *the procedure*. Nothing in this section can enable or
disable a strategy.

### Why this comes before any further factor hunting

§32 measured a Spearman rank correlation of **−0.543** between in-sample and
out-of-sample profit factor across six arms. The in-sample best finished fourth;
the in-sample worst finished second. At n=6 that is not significant in either
direction — which is exactly the problem. **The procedure has never been shown
to rank at all.**

Every gate from §14 onward selects on one in-sample window and reports one
out-of-sample number. If that selection step carries no information, then:

- every REJECTION remains sound (the bar was not cleared, whatever the selector
  did), but
- any PASS would rest on a coin flip dressed as a method, and
- continuing to hunt arms is simply buying more chances for a false positive.

Running another EDGE claim before answering this would be negligent. §32's own
`xsmom-12-1-10` row — +63.17% OOS at PF 5.061, ranked fifth of six in-sample —
is what that false positive would look like when it arrives.

### The hypothesis under test

> Selecting the in-sample best arm produces better out-of-sample performance
> than picking an arm at random from the same family.

That is the assumption every previous section has relied on without checking.

### Design — expanding-origin folds

The same six §32 arms (the family is already fixed and already burned, so no new
multiple-comparison budget is spent on *strategies* here; the trials counted are
folds, not arms).

Five expanding-origin folds on the wide snapshot. Expanding rather than rolling
because it matches deployment: the bot always has all history up to today.

| fold | IS bars | OOS bars |
|---|---|---|
| 1 | 0–600 | 600–800 |
| 2 | 0–800 | 800–1000 |
| 3 | 0–1000 | 1000–1200 |
| 4 | 0–1200 | 1200–1400 |
| 5 | 0–1400 | 1400–1638 |

Every fold runs all six arms IS and OOS: 60 simulations.

### Metrics — fixed now

1. **Per-fold Spearman** between IS and OOS profit-factor rank across the six
   arms, and the mean across folds.
2. **Selection lift**: OOS profit factor of the IS-selected arm minus the *mean*
   OOS profit factor of all candidate arms in that fold. Positive means
   selecting beat picking at random.
3. **Hit rate**: folds in which the IS-selected arm placed in the top half OOS.

### Pass mark — the procedure is VALIDATED only if all hold

- **(a)** mean per-fold Spearman **> 0**
- **(b)** mean selection lift **> 0**
- **(c)** hit rate **≥ 4 of 5** folds

Deliberately a low bar. This is not asking the procedure to be good; it is
asking it to be better than a coin, which is the minimum for any of the last
nineteen sections to mean what they say.

### Prior — before the run

**Expected to FAIL.** §32's point estimate was negative, the incumbent ensemble
does not generalise across universes, and the folds span violently different
regimes (COVID crash, 2021 melt-up, 2022 bear, 2023–26 recovery). A procedure
that ranks strategies across regime boundaries would be a genuinely surprising
result.

### The remedy, committed BEFORE seeing the outcome

So it cannot be invented to fit whatever comes back:

**If §33 FAILS**, single-split selection is retired. Its replacement — to be
pre-registered as §34 and tested on its own terms — is **fold-majority
selection**: an arm is selectable only if it ranks in the top half in **≥60% of
folds**, and the number reported is the pooled out-of-fold result rather than
one window's. Any future EDGE claim runs under that method.

**If §33 PASSES**, the existing verdicts stand as they are, and §14–§32's
rejections keep exactly the weight they already have.

Either way **no strategy is enabled by this section.**

**Running trial count entering §33:** ~50 registered comparisons plus grid arms.
§33 adds 5 folds as one METHOD comparison.

**Runner:** `scripts/gate_fold_stability.py` — committed.

### §33 RUN 1 — **INVALID.** The verdict was VALIDATED and it was an artifact.

Recorded in full rather than deleted. A discarded run is an unrecorded degree of
freedom, and this one printed the most convenient possible answer.

**What it reported:**

```
[PASS] (a) mean spearman > 0        [+0.691]
[PASS] (b) mean selection lift > 0  [+0.474]
[PASS] (c) hit rate >= 4/5          [5/5]
VERDICT: the walk-forward selection procedure is VALIDATED on this data.
```

All three clauses, 5/5 folds. It would have licensed continuing to hunt
strategies with the existing method.

**Why it is invalid.** The OOS windows were 200 bars, sliced as `[is_end, stop)`
with no history. `xsmom-12-1` requires **253 bars** of lookback:

| fold | xsmom-12-1-10 | xsmom-12-1-20 | xsmom-6-1-10 | lowvol-60-10 | both-10 |
|---|---|---|---|---|---|
| 1 | **0** | **0** | 148 | 237 | 237 |
| 2 | **0** | **0** | 103 | 202 | 202 |
| 3 | **0** | **0** | 156 | 194 | 194 |
| 4 | **0** | **0** | 108 | 155 | 155 |

*OOS trade counts.* Both 12-1 momentum arms placed **zero trades in every
fold** — they could not signal at all. Their profit factor was 0.000 by
construction.

**The mechanism that manufactured the result:** those two arms also ranked last
*in-sample* (PF 0.99 and 0.63, on their own merits). So two of six arms sat at
the bottom of **both** rankings for entirely unrelated reasons — genuinely weak
in-sample, structurally mute out-of-sample — and that alone forces a large
positive rank correlation. Meanwhile `both-10` returned byte-identical numbers
to `lowvol-60-10` in every fold, because its momentum half contributed nothing,
so it was not an independent arm either.

**Three of five candidate arms were not data points.** A rank correlation over
six arms where three are degenerate measures the fold design, not the selector.

**Whose error this is.** Mine, in the fold design. §31 handled the identical
lead-in problem correctly for the credit series — *"the OOS slice keeps `period`
bars of lead-in before the first OOS price bar, so arm sma100 is not silently
disabled for its first 100 sessions"* — and the same reasoning was not applied
here three hours later.

**How it was caught.** Not by the pass mark, which was satisfied. By reading the
per-arm OOS trade counts in the trials log while the run was still going. A
profit factor of exactly 0.000 repeated across folds is not a weak arm, it is an
arm that never traded.

**The correction.** Each arm's OOS slice now carries lead-in equal to **that
arm's own** `strategies.max_lookback_bars(cfg)` — 253 bars for the 12-1 arms,
148 for 6-1, 100 for low-vol — so every arm's first possible signal lands exactly
on the fold boundary. No contamination: a strategy cannot signal before it has
its lookback, so no trade can open inside the lead-in.

The runner now **raises** on any arm with zero OOS trades rather than scoring it.
A zero silently ranks last; a raise cannot be ignored.

**Nothing from Run 1 is carried forward.** Its numbers are not quoted anywhere as
evidence, and the +0.691 Spearman in particular must never be cited: it measures
a broken slice.

### §33b RESULT — **NOT VALIDATED.** In-sample selection does not predict.

Corrected run, per-arm lead-in, snapshot hash-verified. Five expanding-origin
folds, six arms, 60 simulations.

| fold | IS→OOS Spearman | selection lift | selected arm's OOS rank | |
|---|---|---|---|---|
| 1 | −0.943 | −0.163 | 4/5 | MISS |
| 2 | −0.657 | −0.636 | 5/5 | MISS |
| 3 | +0.371 | **+4.900** | 1/5 | HIT |
| 4 | +0.829 | +0.400 | 2/5 | HIT |
| 5 | −0.486 | −1.226 | 4/5 | MISS |

```
[FAIL] (a) mean spearman > 0        [-0.177]
[PASS] (b) mean selection lift > 0  [+0.655]
[FAIL] (c) hit rate >= 4/5          [2/5]
```

**Clause (b) is not evidence and is not cited as such.** It clears only on fold
3's +4.900 outlier. The **median lift is −0.163** and three of five folds are
negative; drop fold 3 and the mean is −0.406. A criterion carried by one
observation out of five is a criterion that has not been met in any meaningful
sense, and it was included in the registration precisely so it could not be
quietly leaned on now.

**The procedure is NOT VALIDATED.**

### What the result actually says

Not "selection is backwards". The per-fold Spearman spans **−0.943 to +0.829**
— from near-perfect inverse ranking to near-perfect correct ranking, across
adjacent windows of the same data. The mean of −0.177 is indistinguishable from
zero, and with that spread it is not a meaningful summary of anything.

The honest statement is: **selecting the best in-sample arm carries no reliable
information about which arm performs out of sample.** Sometimes it is right,
sometimes it is exactly wrong, and nothing in-sample tells you which fold you
are in.

That is a more useful finding than either tidy alternative. "Anti-predictive"
would have been exploitable — invert the selector. "Predictive" would have
validated §14–§32. Neither is true.

### Consequences for §14–§32

**Every rejection stands.** A rejection is a failure to clear a numeric bar, and
the bar was not cleared regardless of what the selector did. §31, §32 and the
five before them are unaffected.

**No pass from that procedure would have been trustworthy.** This is the load-
bearing point. Seven EDGE claims have been rejected; had one been accepted, it
would have rested on a selection step now shown to carry no information. §32's
`xsmom-12-1-10` — +63.17% OOS at PF 5.061, ranked fifth of six in-sample — is
exactly what that false positive looks like, and it is now doubly disqualified.

The 0-for-7 record was never the problem. **The instrument was.**

### The registered remedy, triggered

Single-split selection is **RETIRED**. Its replacement, committed to before this
run and now in force for every future EDGE claim:

**Fold-majority selection** — an arm is selectable only if it ranks in the top
half in **≥60% of folds**, and the number reported is the **pooled out-of-fold**
result rather than one window's. Pre-registered separately as §34 and tested on
its own terms before anything runs under it.

### Honest limits of §33b itself

Five folds, six arms, one snapshot, 6.5 years. This is not a proof that
walk-forward selection can never work here — it is a demonstration that it has
not been shown to work, on the only data available, using the exact procedure
nineteen prior sections relied on. A method that has never been validated is not
the same as a method proven useless, and §34 has to clear its own bar rather
than inherit trust from this rejection.

**No strategy was enabled or disabled by §33. `lowvol` and `xsmom` remain off.**

---

## §34 — IS FOLD-MAJORITY SELECTION ANY BETTER? (pre-registered 2026-07-27, before the runner existed)

**Claim type: METHOD.** §33b retired single-split selection. This tests whether
the registered replacement is actually an improvement, rather than assuming it
because it sounds more careful.

### The trap this registration exists to avoid

Fold-majority selection picks the arm that ranks top-half in ≥60% of folds. The
obvious way to evaluate it is leave-one-fold-out: hold out fold *k*, select using
the other four, score on *k*.

**That is lookahead.** For fold 3, "the other four" includes folds 4 and 5 — the
future. A deployment standing at fold 3 has folds 1–2 and nothing else. An
evaluation that quietly uses later folds measures an oracle, not a method, and
it would flatter the replacement exactly when it matters most.

So §34 scores the **causal** variant: to select for fold *k*, only folds
**1…k−1** may be used. Fold 1 has no history and is not scored. That leaves
**four evaluations** — which is a small number, said now rather than discovered
later.

The non-causal variant is computed too, reported as a **diagnostic upper bound**
only. It decides nothing. If the causal and oracle numbers diverge sharply, that
gap is itself the finding.

### Three selectors, same held-out folds

| | rule |
|---|---|
| **S1 single-split** | argmax candidate IS profit factor in fold *k*. The retired method §33b rejected. |
| **S2 fold-majority (causal)** | among candidates, those ranking top-half in ≥60% of folds 1…k−1; tie-break on mean OOS PF over those folds. |
| **S3 random** | expected value = mean candidate OOS PF in fold *k*. The bar any selector must clear to be worth having. |

Scored on the held-out fold's realised **OOS profit factor**.

### Pass mark — fixed before computing anything

Fold-majority replaces single-split only if **all** hold:

- **(a)** mean held-out OOS PF **> S3 random**
- **(b)** hit rate (selected arm in the top half of candidates) **≥ 3 of 4**
- **(c)** it beats **S1 single-split** on both (a) and (b)

Clause (c) matters most. A replacement that is merely a different way to be
wrong is not a replacement.

### Prior — before the run

**No strong expectation, and that is the honest position.** §33b showed
single-split carries no information; it does not follow that fold-majority
carries any. Aggregating across folds helps only if arm quality is stable across
regimes, and §32/§33b are precisely the evidence that it may not be.

Most likely outcome: **all three selectors indistinguishable at n=4**, which
would mean the repository has no validated way to choose between strategies at
all — a harder finding than either alternative, and one that would have to be
stated plainly rather than worked around.

### What §34 cannot do

Four evaluations, six arms, one snapshot, and the **same measurements that
produced §33b**. This is not a fresh sample. A pass here is a licence to test
fold-majority on new data, **not** permission to trust it or to enable anything
under it. Written now so a pass cannot be read as more than it is.

**Running trial count entering §34:** ~51 registered comparisons plus grid arms.
No new simulations — §34 re-reads the trials log §33b already wrote.

**Runner:** `scripts/gate_selector_compare.py` — committed.

### §34 RESULT — **DOES NOT REPLACE.** No selector here beats chance.

Four held-out folds (fold 1 unscorable — no history). No simulations; computed
from the trials log §33b wrote.

| selector | mean held-out OOS PF | hit rate |
|---|---|---|
| **S3 random** (the bar) | **2.590** | — |
| S1 single-split *(retired)* | 3.449 | 2/4 |
| **S2 fold-majority (causal)** | **2.604** | **2/4** |
| *oracle variant — diagnostic only* | *2.819* | *3/4* |

```
[PASS] (a) fold-majority mean PF > random
[FAIL] (b) fold-majority hit rate >= 3/4
[FAIL] (c) beats single-split on BOTH
```

**Fold-majority does not replace single-split.** Clause (a) clears by 0.014 —
2.604 against 2.590 — which is a rounding artefact, not an improvement.

### The diagnostic that settles the interpretation

The **oracle** variant selects using every fold except the held-out one,
including folds in the future. It cheats, deliberately, to establish an upper
bound on what any amount of cross-fold history could buy.

It scored **2.819** — barely above random's 2.590, and *below* the retired
single-split method's 3.449.

**Even with lookahead, aggregating across folds cannot pick a winner.** That
rules out the comfortable explanation. Fold-majority is not underperforming
because it lacks history; it underperforms because **arm quality is not stable
across folds at all.** The instability is in the data, not in the estimator, and
no smarter estimator over these measurements will fix it.

### Single-split's higher mean is not a reprieve

S1 scored 3.449, the best of the three — and it stays retired. Its hit rate is
**2/4**, identical to fold-majority and to a coin. The mean is carried entirely
by fold 3, where `lowvol-60-10` returned PF 9.121; strip that fold and the
ordering collapses. §33b rejected it on five folds of evidence and one outlier
in four does not overturn that.

### The honest state of this repository

**There is no validated way to choose between strategies here.** Not
single-split, not fold-majority, not an oracle with the future in hand. Three
selectors, all indistinguishable from picking at random on this data.

That is the most important sentence written in this file today, and it is
harder than either alternative would have been.

### What follows from it — the constructive part

If selection cannot be trusted, then **stop selecting.** Every future EDGE claim
must be built so its verdict does not depend on a selector:

1. **Pre-specify a single arm** before the run. No family, no choosing, no
   multiple-comparison budget spent on selection. One hypothesis, one number.
   This is weaker per run and honest per run.
2. **Or require the whole family to move.** If an effect is real it should lift
   every arm in the family, not one. A result that appears in one arm and not
   its neighbours is what selection noise looks like.
3. **Or require an effect large enough to survive any selection** — a margin so
   wide that which arm you picked cannot change the verdict.

Every EDGE claim from here carries the stated limitation: *selection in this
repository is unvalidated, so this result rests on a pre-specified hypothesis
rather than a chosen one.*

### Limits of §34 itself

Four evaluations, six arms, one snapshot, and the same measurements that
produced §33b. It cannot prove fold-majority is useless in general — only that
it showed no advantage here, on the data available, against the method it was
registered to replace. As pre-registered: a pass would have been a licence to
test on fresh data. A failure is not proof of the negative either.

**Nothing enabled. `lowvol` and `xsmom` remain off. EDGE claims stand at 0 for 7.**

---

## §35 — ONE HYPOTHESIS, NO SELECTION (pre-registered 2026-07-27, before the runner existed)

**Claim type: EDGE.** The first claim in this repository built under the rule
§34 imposed: **the verdict must not depend on a selector.**

### Why this is shaped differently from §14–§32

Every prior EDGE claim ran a family of arms, picked the in-sample winner, and
reported its out-of-sample number. §33b and §34 showed that picking step carries
no information — single-split, fold-majority and even an oracle holding the
future are all indistinguishable from random on this data.

So §35 does not choose. **One hypothesis, fixed now, tested once.** If it fails
there is no second arm to fall back on, and that is the point: a result that
cannot be produced by choosing cannot be an artifact of choosing.

### The hypothesis, and where it comes from

> Cross-sectional 12-1 momentum earns a positive per-trade edge on a wide,
> liquid US equity universe, after costs, relative to the incumbent ensemble.

Buy the top decile by trailing 12-month return skipping the most recent month;
exit when a holding falls below the median. Parameters are the **literature
convention** — Jegadeesh & Titman (1993), 231-bar lookback with a 21-bar skip —
**not** values tuned on any data in this repository.

That provenance matters. I have seen §32's numbers, so I cannot claim a
hypothesis chosen today is untainted by them. What I can claim, and what is
checkable, is that these parameters were published in 1993 and are the standard
specification, so they were not reverse-engineered from this repo's results.
`xsmom-12-1-10` in §32 used the identical parameters for the same reason.

### Data — a period this repository has never touched

`bars_wide_2014-01-01_2019-12-31.json.gz`, sha256
`dca8dc85b8770f74697262750b767e796635f5b481c54efb475b12d9687916cd`,
**500 symbols, 754,888 bars**, liquidity-screened on **2014** only.

Verified before registering: **zero date overlap** with
`bars_wide_2020-01-01_2026-07-10.json.gz`. 385 of 500 symbols are shared; the
dates are disjoint, which is what matters. SPY force-included for the regime
rail.

**No IS/OOS split.** There is nothing to select, so there is nothing to split
for. The entire 2014–2019 period is out-of-sample with respect to every number
this repository has ever produced.

#### Survivorship bias is WORSE here — stated plainly

The universe is today's S&P 1500 membership. Over 2014–2019 that is **twelve
years** of survivorship, against six for the 2020 snapshot. Every company that
existed in 2014 and failed before 2026 is absent, and momentum exploits that
absence more than buy-and-hold does.

Consequence, fixed before the numbers exist: **a REJECTION here is trustworthy,
because the bias runs in the candidate's favour. A PASS is provisional and
cannot be adopted on this evidence alone** — it would require a
survivorship-free universe to confirm, which this repository does not have and
cannot build from free data.

### Arms — two, and neither is chosen

| | |
|---|---|
| **baseline** | shipped ensemble (ma_crossover, tsmom, meanrev), same universe, same risk block |
| **xsmom-12-1** | xsmom only; `rank_lookback_bars 231`, `skip_bars 21`, `buy_top_fraction 0.10`, `exit_below_fraction 0.5` |

Shared risk block identical to §32's (`risk_per_trade_pct 2.0`,
`max_position_pct 2.5`, `max_portfolio_heat_pct 20.0`, `max_trades_per_day 50`),
so this measures signal, not sizing. `min_holding_days 2` and `1Day` unchanged.

### Pass mark — fixed now

The hypothesis is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` in full (`backtest.py:969`)
- **(b)** profit factor **strictly greater** than baseline's
- **(c)** max drawdown ≤ baseline + 1.0pp
- **(d)** ≥ 30 trades
- **(e)** block bootstrap `significance.compare` returns **significantly
  better** at **K = 8**

**On K = 8.** Only one comparison is made here, so K = 1 would be the
conventional choice. It is set to 8 deliberately: this is the **eighth** EDGE
claim this repository has run, and family-wise error accumulates across a
research programme even when the hypotheses differ. After seven failures, an
eighth result at conventional significance is weak evidence, and the correction
should say so rather than the prose having to.

### Prior — before the run

**EDGE claims are 0 for 7.** Momentum specifically has been tested twice on the
2020–2026 universe and rejected both times.

The one honest reason to expect anything different: 2014–2019 contains no
COVID crash and no 2022 bear, and cross-sectional momentum is documented to fail
hardest at sharp reversals. A calmer sample is where it should look best. **That
is also the reason to distrust a pass** — "it works in the easy period" is not
an edge, and clause (a)'s buy-and-hold and exposure-matched comparisons are what
stop that argument.

### What a pass would and would not mean

It would mean one pre-specified hypothesis survived one clean period at a
conservative correction. It would **not** mean the strategy is enabled, that
alpha is demonstrated, or that the go-live gate (30 closed trades / 60 days,
`review.py:24`) is closer. Those need live evidence that no backtest produces.

**Running trial count entering §35:** ~52 registered comparisons plus grid arms.

**Runner:** `scripts/gate_s35_momentum.py` — committed.

### §35 RESULT — **REJECTED**, and it kills the most tempting number in this file.

Snapshot `bars_wide_2014-01-01_2019-12-31.json.gz` (sha256 `dca8dc85…`),
hash-verified before scoring. 500 symbols, whole period, no split, no selection.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+64.36%** | **1.431** | 11.12% | 4,524 | 500 | 69.6% |
| **xsmom-12-1** | **−4.04%** | **0.758** | 18.49% | 248 | 141 | 21.1% |
| buy-and-hold | +90.41% | — | 21.1% | — | — | — |

```
[FAIL] (a) clears enablement_gate() in full
[FAIL] (b) PF strictly > baseline
[FAIL] (c) maxDD <= baseline + 1.0pp
[PASS] (d) trades >= 30
       INCONCLUSIVE: -$16.28/trade vs +$14.23/trade, diff -$30.51,
       99.38% CI [-$91.39, +$104.13]  (Bonferroni K=8)
[FAIL] (e) significantly better
```

**The hypothesis lost money.** −4.04% over six years, in a period where
buy-and-hold returned **+90.41%** and the incumbent ensemble returned +64.36%.
Profit factor 0.758 — it lost more on its losers than it made on its winners.
Every clause fails except the trade count.

**EDGE claims are now 0 for 8.**

### Why this rejection is worth more than the seven before it

It is the first claim in this repository whose verdict **cannot** be an artifact
of selection, because there was no selection. One hypothesis, its parameters
published by Jegadeesh & Titman in 1993, fixed in a commit before the runner
existed, tested once on a period with **zero date overlap** with anything this
repo had touched, corrected at K=8 for the whole research programme.

Every earlier rejection was measured with an instrument §33b later showed to be
uninformative. This one was not.

### What it does to §32's `xsmom-12-1-10`

§32 produced the single most seductive row in this file: **+63.17%
out-of-sample at profit factor 5.061**, better than double buy-and-hold. It was
flagged there as untrustworthy on procedural grounds — it ranked fifth of six
in-sample, and §33b showed the ranking carried no information. That was an
argument about method, not about the strategy.

§35 settles it with evidence. **The identical parameters — 231-bar lookback,
21-bar skip, top decile — lost 4% on a clean, untouched six-year period while
the market rose 90%.**

A rule that returns +63% in one window and −4% in another is not an edge that
comes and goes. It is noise, and §32's number was a draw from it. The temptation
is now closed by measurement rather than by caution, which is a much stronger
place to leave it.

### The prior said this was momentum's best case

Registered before the run: 2014–2019 contains no COVID crash and no 2022 bear,
and cross-sectional momentum is documented to fail hardest at sharp reversals,
so a calm sample is where it should look **best**.

It lost money anyway, on a universe carrying **twelve years of survivorship
bias running in its favour**. That combination — the friendliest period, the
friendliest universe, the literature's own parameters — is why this rejection
should be read as strong.

### What is NOT concluded

That cross-sectional momentum does not exist. It is one of the most replicated
anomalies in finance and this is one implementation, on one universe, inside one
bot's risk rails, at 5bps costs, holding a top decile with a median exit. Any of
those choices could be what failed.

What **is** concluded: **this bot, as built, does not extract it** — and after
eight pre-registered attempts, the base rate for the next idea should be set
accordingly.

### Where that leaves the search

Eight EDGE claims, eight rejections, one of them now clean. Three of the four
most-cited retail anomaly families have been tested here and none survived:
time-series momentum (§14–§21), cross-sectional momentum (§32, §35), low
volatility (§32), plus relative volume (§23) and cross-asset credit (§31).

**No strategy is enabled. `lowvol` and `xsmom` remain off.** The live record
stands at 1 closed trade against a 30-trade gate, and nothing in this section
moves it.

---

## §36 — THE REFEREE BECOMES A PROGRAM (2026-07-28, tooling — not a claim)

This section registers no hypothesis and produces no verdict about the market.
It records a change to **how** every future section is run, which is exactly the
kind of change that must be written down before it is used.

### Why

Seven gate runners (`scripts/gate_*.py`), **1,464 lines**, each re-implementing
the same five steps around `backtest.simulate_ensemble`,
`backtest.enablement_gate` and `significance.compare`. Compute was never the
constraint — §32 scored 500 symbols in 13m35s. Hand-writing a runner per
hypothesis was.

The owner asked whether the bot could continuously learn new strategies. It
cannot, and it should not yet: §33b and §34 established that in-sample selection
carries no information here — fold-majority scored 2/4 and an oracle holding the
future scored 2/4, the same as a coin. **More hypotheses through an uninformative
referee produces false positives faster, not alpha.** §35 is the worked example.

So the referee was automated instead of the idea supply.

### What must not be lost, and how it is kept

Every rejection in this file means something only because the claim, arms, pass
mark and honest prior were committed **before the runner existed**. Automation
that weakens that is worse than no automation.

`research/specs/<id>.yaml` states the claim as data. `register_gate.py` records
its canonical sha256 in `research/registrations.jsonl` **with the full spec**.
`run_gate.py` then refuses three ways:

1. **not registered** — no frozen pass mark exists, so a result could not be wrong
2. **altered since registration** — and it names the field, e.g.
   `clauses.1.pp: 1.0 -> 3.0`, because "the spec changed" only starts a search
3. **snapshot drift** — the data is not what the registration named

Re-registration is permitted while a claim has no verdict (editing before any
data is seen is authoring) and **refused once a verdict exists**. Every
registration appends; nothing is rewritten.

The hash is taken over the parsed structure, not the file bytes: reformatting is
not tampering, and a freeze that cries wolf is one people learn to bypass.

Two fields are mandatory and carry no machine meaning — `prior` and
`failure_modes`. A registration that does not say what the author expected, and
how the result could fool them, is rejected by the validator.

### What this does NOT do

- **It cannot enable anything.** A passing verdict is a recommendation; enabling
  remains the owner's decision (invariant 2).
- **It generates no hypotheses.** Deliberately. Revisit only if a selection
  method ever clears its own gate.
- **It does not fix §33.** Throughput is not information. The first claim run
  under §34's fold-majority method is still the open question.

### Known gaps, named rather than discovered later

- **§27 cannot be reproduced through it, and not because of the runner.**
  `gate_budget.py` already carries `SUPERSEDED_BY = "§29 (2026-07-26)"` and warns
  that re-running it measures a phantom: §29 replaced both levers it tests
  (`max_order_value_usd` → 0, `risk_per_trade_pct` → 8.0). Its verdict stands on
  a bot that no longer exists.
- **§31 needs aux lead-in slicing.** Its credit series must be sliced with a
  lead-in equal to the longest SMA period so the first OOS bar has history. The
  spec format passes aux snapshots whole. Until that is expressed declaratively,
  §31 stays on its committed runner.

### §36 VALIDATION — the runner reproduces §35 exactly

Before being trusted with a new claim, `scripts/run_gate.py` was pointed at
§35 via `research/specs/s35.yaml`, transcribed from the committed registration.
A referee that cannot reproduce a verdict it has already seen is not a referee,
it is a new source of error.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| baseline | +64.36% | 1.431 | 11.12% | 4,524 | 500 | 69.59% |
| xsmom-12-1 | −4.04% | 0.758 | 18.49% | 248 | 141 | 21.10% |
| buy-and-hold | +90.41% | — | — | — | — | — |

```
[FAIL] (a) enablement_gate     [FAIL] (b) pf_gt_baseline
[FAIL] (c) maxdd_within        [PASS] (d) min_trades
[FAIL] (e) significantly_better
       INCONCLUSIVE: -$16.28/trade vs +$14.23/trade, diff -$30.51,
       99.38% CI [-$91.39, +$104.13]  (Bonferroni K=8)
VERDICT: REJECTED   (908s wall, 1 worker)
```

Every figure matches the §35 table above — returns, profit factors, drawdowns,
trade counts, symbol counts, all five clause outcomes, and the bootstrap CI to
the cent. Checked arithmetically rather than by eye, since eyeballing would be
the weakest link in a change whose entire point is mechanical checking.

**This is one reproduction, not two.** The plan called for a second,
structurally different gate; reading the code retired both candidates and the
reasons are recorded above under "Known gaps". The second validation is
determinism at production scale instead — the same gate re-run at `--workers 2`,
which must produce an identical verdict.

**Nothing about §35's conclusion changes.** It was REJECTED on 2026-07-27 and it
is REJECTED now. EDGE claims remain 0 for 8. The only new fact is that the
generic runner is faithful to the bespoke one.

### §36 VALIDATION 2 — the verdict does not depend on worker count

Same spec, same frozen hash, re-run at `--workers 2`:

| | 1 worker | 2 workers | identical |
|---|---|---|---|
| every arm summary | | | ✅ |
| all five clauses | | | ✅ |
| bootstrap CI | | | ✅ |
| **wall clock** | **908s** | **556s** | — |

`spec_sha256`, `candidate`, `passed`, `clauses`, `comparison` and `arms` all
compare equal. Per-arm seconds differ (748/159 vs 554/350) because the two arms
now contend for CPU — that is timing, not result, and it is deliberately not
part of what a verdict record claims.

The wall-clock number is the one that matters for the Bizon. Run serially a gate
costs the SUM of its arms; run in parallel it costs its SLOWEST arm. Here that
is 908s → 556s on two cores, bounded below by the 748s baseline arm. A six-arm
gate on 64 cores stops costing the sum and starts costing the longest one.

That is the whole benefit, and it is worth being precise about what it is not:
**faster gates do not make the referee more informative.** §33b and §34 stand
unchanged. This buys throughput on a measuring instrument whose calibration is
still the open problem.

---

## §37 — FAMILY COHERENCE ON THE FINANCIAL CRISIS (pre-registered 2026-07-28, before the snapshot finished building)

**Claim type: EDGE.** The first use of §34's shape 2. Spec
`research/specs/s37.yaml`, frozen at `eeb75c54dfa8f90b…` before any number from
the 2007-2013 snapshot was read.

### Why this shape

§34 retired selection here: single-split, fold-majority and an oracle holding
the future all scored 2/4, indistinguishable from a coin. Its prescription was
to stop selecting, and it named three shapes. §35 used shape 1 (one
pre-specified arm). This is shape 2:

> *"Require the whole family to move. If an effect is real it should lift every
> arm in the family, not one. A result that appears in one arm and not its
> neighbours is what selection noise looks like."*

So there is **no candidate and nothing is chosen.** Three momentum lookbacks —
6-1, 9-1, 12-1 (126/189/231 bars, 21-bar skip) — each face the full five-clause
pass mark, and **one failure sinks the claim.** No selector can be credited or
blamed for the verdict, which is the entire point.

### Data — the first bear market this repository has ever scored

`bars_wide_2007-01-01_2013-12-31.json.gz`, sha256 `2ba815e1b0d2ca5d…`,
**500 symbols, 880,384 bars**, screened on 2007 median dollar volume so the
universe cannot peek at the test window. 1,181 fetched → 1,025 with 2007 history
→ 500 kept.

Zero date overlap with anything previously scored here. §35 named "benign
period — no COVID crash and no 2022 bear" as a failure mode and it has stood
unanswered since.

### Pass mark — every family member, all five

`(a)` enablement_gate · `(b)` PF > baseline · `(c)` maxDD ≤ baseline + 1.0pp ·
`(d)` trades ≥ 30 · `(e)` significantly better. **K = 9** — eight EDGE claims
precede this; three arms are tested but they are ONE claim, so the count rises
by one.

### Prior

**This fails.** EDGE claims stand at 0 for 8, and §35 already ran xsmom-12-1 on
2014-2019 where it lost 4.04% while the market gained 90.41%. The reason to run
it anyway is that if momentum has a regime where it earns its keep, a crisis is
the most-claimed one — and a three-arm requirement makes a lucky single-arm
result impossible to mistake for an effect.

### Failure modes — survivorship is worse here than anywhere previous

**The universe comes from TODAY'S index membership, so every company that went
bankrupt or was delisted in the 2008 crisis is absent by construction.** The
crash is systematically softened and long-only momentum benefits most. Direction
named in advance: this inflates every arm, so **a PASS here is weak evidence
while a FAIL is strong.**

Also registered: the 2007 liquidity screen itself reflects pre-crisis
conditions; the three arms share a snapshot and universe so "all three moved"
is far weaker than three independent confirmations; low trade counts make wide
intervals likely and INCONCLUSIVE is not support; and a PASS would still owe a
walk-forward on a period without a survivorship advantage before it meant
anything.

### The recent period is a DIAGNOSTIC, not a claim

The owner asked for 2022-2026 as well. **§32 already scored
`bars_wide_2020-01-01_2026-07-10` — 500 symbols, the xsmom and lowvol families —
and rejected them.** 2022-2026 is a subset of that period, so a run there
re-reads measurements that have already driven ~51 registered comparisons.

It is therefore reported the way §34 reported its oracle variant: **a diagnostic
that decides nothing.** It answers "does this look like it works lately?", which
is a fair question that cannot carry a verdict.

### §37 RESULT — **REJECTED, 0/3.** And for the first time, *significantly worse*.

Snapshot `bars_wide_2007-01-01_2013-12-31.json.gz` (sha256 `2ba815e1b0d2ca5d…`),
hash-verified. 500 symbols, whole period, no split, no selection. 479s wall on
4 workers.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+0.78%** | 1.030 | 10.63% | 567 | 323 | 10.44% |
| xsmom-6-1 | −14.90% | 0.356 | 26.32% | 186 | 129 | 12.04% |
| xsmom-9-1 | −25.92% | 0.013 | 26.11% | 184 | 94 | 7.49% |
| xsmom-12-1 | −23.35% | 0.011 | 33.43% | 138 | 69 | 8.12% |
| buy-and-hold | **+92.48%** | — | 55.9% | — | — | — |

**Every member fails every clause except trade count. 0/3 clear.**

### What is new here

Every prior EDGE rejection was *inconclusive* or *not better*. This is the first
time in this file that the significance test returns **SIGNIFICANTLY WORSE**,
and it does so for all three arms with the entire Bonferroni-corrected interval
below zero:

| arm | $/trade vs baseline | 99.44% CI (K=9) |
|---|---|---|
| xsmom-6-1 | −$81.51 | [−$145.32, −$17.13] |
| xsmom-9-1 | −$142.27 | [−$180.06, −$106.14] |
| xsmom-12-1 | −$170.56 | [−$212.54, −$127.14] |

**The damage is monotone in lookback.** 6-1 → 9-1 → 12-1 gets steadily worse,
in per-trade P&L and in profit factor (0.356 → 0.013 → 0.011). That is a
dose-response, not noise: the longer the formation window, the worse it did.
The family requirement was written to catch a lucky single arm; instead all
three agreed, in an ordered way.

### Why this rejection is stronger than §35's

§35 tested one arm on a benign period and lost. This tested three arms on a
period containing the worst crisis in modern market history — **the regime where
trend-following makes its loudest claim** — and the universe is tilted in
momentum's favour by construction, because every firm that went bankrupt in 2008
is absent from today's index membership. Lehman, Bear Stearns and Washington
Mutual are not in this snapshot. It holds the 2008 crash **as experienced by the
survivors**.

Momentum lost badly on data selected to flatter it, in the regime it is most
often sold for. That was the registered asymmetry: a PASS would have been weak,
a FAIL is strong. It failed.

**EDGE claims stand at 0 for 9.**

### An unregistered observation that matters more than the claim

**The baseline returned +0.78% over seven years while buy-and-hold returned
+92.48%.** That is not what §37 registered to test and it is not a verdict — but
it should not hide behind the momentum result.

Part of it is exposure: the ensemble averaged **10.44% deployment**, so it was
in cash most of the time, and `enablement_gate` reports the exposure-matched
benchmark at +11.13%. Even against that adjusted bar the incumbent underperforms.

This is a registered-in-hindsight question, so it decides nothing here. It is
written down as the thing to pre-register next: **the incumbent ensemble's own
edge over a matched-exposure benchmark, on a period it was never tuned on.**

---

## §38 — THE SAME FAMILY ON 2022-2026. **DIAGNOSTIC — decides nothing.**

Spec `research/specs/s38.yaml`, frozen `43de6f9b403a82f1…`. Snapshot
`bars_wide_2022-01-01_2026-07-24.json.gz` (sha256 `081dc08727a8cd8e…`), 500
symbols, 571,488 bars, screened on 2022 liquidity. Identical arms, clauses and
K to §37 so the two are readable on one scale. 116s wall.

`claim: DIAGNOSTIC` is a first-class type, not a caption: §32 already scored
2020-2026 with these families, this period is a subset, and a result from
re-read data is a re-description of evidence already spent.

### The numbers, against §37's

| arm | §37 (2007-2013) | §38 (2022-2026) |
|---|---|---|
| baseline | +0.78% · PF 1.030 · 10.4% deploy | **−11.62%** · PF 0.453 · 8.3% deploy |
| xsmom-6-1 | **−14.90%** · PF 0.356 | **+115.83%** · PF 2.435 |
| xsmom-9-1 | **−25.92%** · PF 0.013 | **+96.81%** · PF 2.800 |
| xsmom-12-1 | **−23.35%** · PF 0.011 | **+94.20%** · PF 2.716 |
| buy-and-hold | +92.48% | +45.11% |

And the significance test, same arms, same K=9, opposite signs:

| arm | §37 | §38 |
|---|---|---|
| xsmom-6-1 | SIGNIFICANTLY **WORSE** −$81.51/trade | SIGNIFICANTLY **BETTER** +$131.71/trade |
| xsmom-9-1 | SIGNIFICANTLY **WORSE** −$142.27 | SIGNIFICANTLY **BETTER** +$160.04 |
| xsmom-12-1 | SIGNIFICANTLY **WORSE** −$170.56 | SIGNIFICANTLY **BETTER** +$157.24 |

**A complete sign reversal, significant in both directions, on the same
strategy with the same parameters.** This is §34's "arm quality is not stable"
finding made vivid — not merely unstable, but confidently opposite depending on
which seven years you point it at.

§38 was REJECTED anyway, on drawdown alone: 17.7-22.1% against a 13.35% ceiling.
All four other clauses passed for all three arms.

### The mechanical explanation, and why it deflates the good number

**Deployment.** xsmom averaged **7.5-12.0%** invested in §37 and **66.6-80.3%**
in §38. `xsmom` only buys names whose momentum is positive. Through 2007-2013
almost nothing qualified, so it sat in cash and the few trades it did place were
bad. Through 2022-2026 nearly everything qualified, so it was near fully
invested while the market rose 45%.

So the +115% is substantially a **participation** number, not a selection edge.
The per-trade significance test compares means over 1,260 trades at 80%
deployment against 292 trades at 8% — very different animals, and that test does
not adjust for exposure. `enablement_gate`'s exposure-matched benchmark did pass
for all three, so the effect is not *purely* beta; but per-trade significance
across such different exposure profiles is a weaker statement than it looks.

### The trap this registration named in advance, and walked into

> *"If these arms happen to look good here while failing in §37, that is the
> textbook shape of a period-specific fluke, and §34 already established this
> repository cannot tell such a fluke from an effect. It would be a reason to
> distrust the number, not to enable anything."*

Written before the run. That is exactly what happened, and the pre-registration
is the only reason it reads as a warning rather than as a discovery.

**Nothing is enabled. §37's REJECTED stands. EDGE claims remain 0 for 9.**

### What both runs agree on, which nobody registered

The incumbent ensemble is the weak arm in both periods:

- §37: **+0.78%** over seven years while buy-and-hold made **+92.48%**
- §38: **−11.62%** over four and a half years while buy-and-hold made **+45.11%**

Two independent periods, one of them never touched before, and the live
configuration loses to buy-and-hold in both — losing money outright in the
recent one. Low deployment (8-10%) explains part of it, and neither figure is a
registered claim.

**This is now the most important open question in this file**, and it is about
the incumbent rather than any candidate: *does the live ensemble have any edge
over a matched-exposure benchmark on periods it was not tuned on?* It should be
pre-registered and answered before another candidate strategy is tested.

---

## ALREADY-SEEN OBSERVATION (2026-07-28) — the incumbent against its own exposure

**Not a result. Not a claim. Arithmetic on numbers already in this file**, written
down before §39 is registered so that §39 cannot later be read as discovering it.

`enablement_gate` defines the fair bar for a bot whose rails keep it mostly in
cash: `buy_hold_return_pct × (avg_deployment_pct / 100)`. Applied to the
incumbent ensemble's own baseline runs:

| period | incumbent | B&H | deploy | exposure-matched bar | |
|---|---|---|---|---|---|
| 2007-2013 (§37) | +0.78% | +92.48% | 10.44% | **+9.65%** | fails |
| 2014-2019 (§35) | +64.36% | +90.41% | 69.59% | +62.92% | passes by 1.44pp |
| 2022-2026 (§38) | −11.62% | +45.11% | 8.31% | **+3.75%** | fails |

Roughly **one in three**, and the single pass is a 1.44pp margin at 70%
deployment — the one period where the bot was mostly invested.

### Why this is not §39

Every one of these numbers was produced as a *baseline* for someone else's
claim. Reading them now is legitimate — they exist — but a "test" whose answer
can be looked up is not a test. §38 is the standing lesson: a result from data
already read is a re-description of spent evidence.

So §39 runs on **2000-2006**, which this repository has never scored. What the
table above does is make the prior honest: §39's registration can say *"the
incumbent probably fails"* and mean it, with the reason on the page rather than
in someone's head.

### The uncomfortable framing

`enablement_gate` is this repository's standard for **"may a strategy be enabled
live?"** It has rejected nine candidates. It has never once been pointed at the
strategies that are already running.

---

## §39 — THE INCUMBENT FACES ITS OWN GATE (pre-registered 2026-07-29, before the run)

Spec `research/specs/s39.yaml`, frozen `bd5b2be88f59c8d1…`. Snapshot
`bars_wide_2000-01-01_2006-12-31.json.gz` (sha256 `432cec564ccee6ab…`), 500
symbols, 878,288 bars, screened on 2000 liquidity. A period this repository had
never scored.

**One arm, no candidate.** The subject is the shipped configuration exactly as
production runs it — no `shared` risk overlay, because imposing one would mean
measuring a bot that does not exist. Every clause compares it to benchmarks
derived from its own run.

### §39 RESULT — **REJECTED. The incumbent fails its own gate, 3 of 4.**

| | | |
|---|---|---|
| incumbent | **+5.26%** | PF 1.202 · maxDD 11.51% · 101 trades · 85 symbols · **8.07% deployed** |
| buy-and-hold | **+138.54%** | maxDD 28.2% |
| exposure-matched bar | **+11.18%** | 138.54% × 8.07% |

```
[FAIL] (a) enablement_gate  PF 1.202 < 1.3; return beats neither B&H, the
                            risk-adjusted bar, nor the exposure-matched bar
[FAIL] (b) beats_exposure_matched  +5.26% vs +11.18%
[FAIL] (c) beats_buy_hold          +5.26% vs +138.54%
[PASS] (d) min_trades              101 vs 30
```

**It fails the fair bar, not just the harsh one.** Clause (c) — losing to a
138% market while 92% in cash — is close to arithmetically inevitable and is
the least interesting failure here. Clause (b) is the one that matters: scaled
to the exposure it actually carried, the bot returned **less than half** what
simply holding the index at the same average weight would have returned. The
selection is subtracting value, not merely under-deploying.

And `enablement_gate` — the function that rejected nine candidates — rejects the
incumbent on the same profit-factor floor it applies to them: **1.202 against a
1.3 minimum.**

### Where this leaves the count

| period | incumbent vs exposure-matched | |
|---|---|---|
| 2000-2006 (§39, unseen) | +5.26% vs +11.18% | **fails** |
| 2007-2013 (§37) | +0.78% vs +9.65% | fails |
| 2014-2019 (§35) | +64.36% vs +62.92% | passes by 1.44pp |
| 2022-2026 (§38) | −11.62% vs +3.75% | fails |

**One pass in four**, and that pass is a 1.44pp margin in the single period
where the bot was mostly invested. §39 is the only one of the four that was a
genuine pre-registered test; the other three are the already-seen observation
recorded before it.

### What this does and does not license

It does **not** say the live bot loses money — the paper ledger has one closed
trade and decides nothing. It says the shipped configuration, run over four
periods spanning 26 years, does not clear the bar this project holds candidates
to, and fails it on unseen data in the direction its own prior predicted.

**Nothing is being tuned in response.** Fitting parameters to 2000-2006 now
would manufacture precisely the overfit §33 and §34 established this repository
cannot detect. The registered failure modes stand: survivorship flatters the
incumbent here too, and the universe breadth (500 symbols vs the 38 it was tuned
on) is an uncontrolled confound.

### The honest reading

Nine candidate rejections were all asking *"is this new thing better than what
we run?"* — a question whose answer barely matters if what we run does not clear
the bar either. The candidates were being measured against an incumbent that
had never been measured.

**EDGE claims 0 for 10.** Nothing enabled. `mode: paper` unchanged.

### Process note — the first run crashed after computing everything

`run_gate.py` picked the candidate arm with `spec["arms"][1]`, which raised
IndexError on a one-arm spec **after** a 388-second run had produced every
number. Validation had been relaxed to permit one arm; that line was not
updated, and no test caught it because `evaluate()` is always called with an
explicit candidate — the bug lived in the gap between a tested function and its
caller.

Fixed by extracting `default_candidate()` with its own tests, and confirmed by
control that restoring the old line turns the new test RED. The re-run used the
identical frozen spec (`bd5b2be8…`, unchanged), and the simulation is
deterministic — 268s versus 388s is CPU contention, not a different experiment.

---

## §40 — WHERE DOES THE CAPITAL GO? (2026-07-29) — **DIAGNOSTIC. And it found the thing.**

Not a claim. No verdict. `scripts/census.py`, SHIPPED config unmodified
(`risk_per_trade_pct 8.0`, `max_position_pct 10.0`,
`max_portfolio_heat_pct 4.0`, `max_open_positions 0`).

### The census

| period | signals | executed | rate | fully in cash |
|---|---|---|---|---|
| 2000-2006 | 422,592 | **101** | 0.02% | **84.3%** of bars |
| 2007-2013 | 423,926 | **155** | 0.04% | **86.8%** |
| 2014-2019 | 336,408 | 1,732 | 0.51% | 18.9% |
| 2022-2026 | 245,213 | **29** | 0.01% | **92.4%** |

Median deployment is **0.00%** in three of the four periods.

### What blocks them

| period | top blocker | share of all signals |
|---|---|---|
| 2000-2006 | **drawdown** | **94.58%** |
| 2007-2013 | **drawdown** | **96.71%** |
| 2014-2019 | zero_qty 63.36%, **drawdown 20.65%** | |
| 2022-2026 | **drawdown** | **99.43%** |

### THE DRAWDOWN CIRCUIT BREAKER IS A ONE-WAY LATCH

`risk.update_high_water` ratchets the peak upward and **never down** — correct,
and documented as such: *"a drawdown rail measured against a peak that follows
equity downward would never fire."* Entries are blocked at
`max_drawdown_pct: 10.0`; exits keep running.

Combine those two and follow it through:

1. Equity peaks at **P**.
2. A drawdown of ≥10% blocks every entry.
3. Open positions exit normally. The book goes to cash.
4. In cash, equity is **flat** — permanently ~0.9P.
5. The peak stays **P**, so the drawdown stays ≥10%.
6. **The bot never buys again. For the rest of the run.**

There is no reset, no decay, no recovery path. A circuit breaker that cannot
re-close is a kill switch. That is why 2000-2006 executed 101 trades out of
422,592 signals, and why 2022-2026 executed **twenty-nine**.

It also explains the 2014-2019 outlier exactly: a smooth bull market rarely
draws down 10%, so the latch mostly never tripped — the only period the bot
participated is the one where the rail never fired.

### This is a LIVE defect, not a backtest artifact

`pre_trade_checks` reads the same persisted high-water mark
(`memory/.equity_highwater.json`) through the same `update_high_water`. The live
bot has the identical latch. Measured tonight:

```
peak $99,933.29   equity $99,783.49   drawdown 0.15%   cap 10.0%
entries blocked: NO   headroom: 9.85pp (trips at $89,939.96)
```

Production has not tripped. **It will on the first 10% drawdown, and will then
stop buying permanently** until someone deletes that file by hand.

### What it does to the ten rejections

§29 adopted this rail on 2026-07-26. **§31, §32, §33, §33b, §34, §35, §37, §38
and §39 all ran after that date.** Every one of them measured a bot that locks
itself out of the market after its first 10% drawdown.

That does not make those verdicts wrong, and none of them is being withdrawn.
It does mean the question they answered was narrower than the question they
appeared to answer: **"no edge found" and "no edge expressible" have been
indistinguishable**, and §40 is the first evidence that the second was in play.

The specific mistake to avoid: treating this as *"so the strategies are fine
after all."* Nothing here shows that. §38 already demonstrated the same arms
flipping sign across periods, and §34 showed no selector beats a coin. A bot
that can trade is a precondition for measuring edge, not evidence of it.

### Second finding: 63% of 2014-2019's signals died at zero quantity

`zero_qty` — the computed position rounds below one share — blocked **213,155**
signals in the one period the bot was actually invested. That is `min_one_share`
(§28), registered as a candidate and never gated.

### The correction I owe: three of four rows were not the incumbent

§35, §37 and §38 each applied a shared overlay (`risk_per_trade_pct 2.0`,
`max_position_pct 2.5`) so their arms differed only by strategy. Production
ships **8.0** and **10.0**. Only §39 measured the real thing, yet the
ALREADY-SEEN OBSERVATION above presented all four rows as "the incumbent". At
shipped config, measured here:

| period | incumbent | exposure-matched bar | |
|---|---|---|---|
| 2000-2006 | +5.26% | +11.18% | fails |
| 2007-2013 | **+2.59%** | +9.02% | fails |
| 2014-2019 | **+69.02%** | +62.36% | **passes by 6.66pp** |
| 2022-2026 | **−5.26%** | +1.84% | fails |

Still one pass in four. The margin in the passing period is wider than the
earlier table showed (6.66pp, not 1.44pp), and the failures are unchanged.

### Nothing was changed

No config value, no rail, no threshold. **The fix is a separate pre-registered
decision.** `config.yaml` already records a heat cap being raised and reverted
the same day on reasoning that later stopped holding; loosening a risk rail
because a diagnostic pointed at it is the same move. The latch has no reset
path, which is a correctness question rather than a tuning one — but it is the
owner's call, made deliberately, not at the end of a long session.

---

## §41 — CAN THE DRAWDOWN CIRCUIT BREAKER RE-CLOSE? (CAPACITY, K=11)

**VERDICT: REJECTED.** One period of four passed. The claim was registered as a
conjunction — s41a, s41b, s41c and s41d were written and frozen together, before
any of them ran — so one failure sinks it. `risk.drawdown_decay` stays absent
from `config.yaml` and the latch is still in place.

| spec | period | frozen sha |
|---|---|---|
| s41a | 2000-2006 | `3b5314bc1729b081…` |
| s41b | 2007-2013 | `8ffb2bc25e97a08e…` |
| s41c | 2014-2019 | `fd0b3acd77a3262e…` |
| s41d | 2022-2026 | `ab96d91b99890f1f…` |

### Results

| period | arm | return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|---|
| 2000-2006 | baseline | +5.26% | 1.202 | 11.51% | 101 | 8.07% |
| | decay | +78.04% | 1.436 | **17.67%** | 1,888 | 66.61% |
| 2007-2013 | baseline | +2.59% | 1.117 | 12.04% | 155 | 9.75% |
| | decay | +84.92% | 1.487 | **21.15%** | 1,870 | 70.21% |
| 2014-2019 | baseline | +69.02% | 1.486 | 11.59% | 1,732 | 68.97% |
| | decay | +99.43% | 1.572 | 11.59% | 2,087 | 83.28% |
| 2022-2026 | baseline | −5.26% | 0.449 | 10.78% | 29 | 4.09% |
| | decay | +35.06% | 1.245 | **15.73%** | 1,598 | 69.52% |

| clause | s41a | s41b | s41c | s41d |
|---|---|---|---|---|
| (a) maxdd_within pp 3.0 | **FAIL** 17.67 vs 14.51 | **FAIL** 21.15 vs 15.04 | PASS 11.59 vs 14.59 | **FAIL** 15.73 vs 13.78 |
| (b) not_worse | PASS* | PASS* | PASS* | PASS* |
| (c) min_trades 30 | PASS | PASS | PASS | PASS |
| (d) deployment_at_least 25.0 | PASS | PASS | PASS† | PASS |

\* every (b) is INCONCLUSIVE — see below. † non-binding by construction, declared
in the spec before the run.

### What is established

**§40's diagnosis is confirmed beyond argument.** The latch was the binding
constraint on deployment, and it was not close. Removing it moved 2022-2026 from
**29 trades to 1,598** and deployment from 4.09% to 69.52%; 2000-2006 from 101 to
1,888. Whatever else is true, the bot was not choosing to sit in cash — it was
locked out.

**The failure was predicted in the frozen prior**, quoted verbatim from all four
specs: *"the honest expectation is that maxdd_within is the clause most likely to
fail: a bot that re-enters after a drawdown is a bot that can take a second
drawdown, and 3.0pp is not a generous allowance."* Pre-registration working as
intended — the outcome was named before it happened rather than explained after.

**The rejection reason is consistent in three periods of four.** The decay arm
roughly doubles maximum drawdown: +6.16pp, +9.11pp, +4.95pp. Letting the bot
trade costs about twice the drawdown, and the registered mark refuses it on
exactly that ground.

### Two things that must not be over-read

**Return is not evidence here, and was deliberately not a clause.** More
deployment means more exposure to the market's own direction; on a rising period
the decay arm will look better for reasons that have nothing to do with the rail.
Anyone quoting +78%, +85% or +99% from this table as a result is misreading the
spec. Exposure-matched, the decay arm clears its bar on three periods and misses
on 2000-2006 (+78.04% against +92.28%) — a mixed picture, and **§38 already
established that this ensemble reverses sign between eras.** No edge claim of any
kind may be drawn from §41.

**Every (b) PASS is INCONCLUSIVE and carries no information.** The confidence
intervals exclude nothing: `[$-276, $+172]`, `[$-198, $+187]`, `[$-48, $+64]`,
`[$-246, $+557]`. The point estimates even move in opposite directions across
periods — $52.11→$41.34 on 2000-2006 against $16.70→$45.41 on 2007-2013.
"Not significantly worse" at these sample sizes is a statement about statistical
power, not about trade quality.

### An anomaly worth flagging rather than explaining away

On 2014-2019 the two arms report **identical maximum drawdown to the digit —
11.59% both** — while deployment rose 68.97% → 83.28% and trades rose 1,732 →
2,087. That is plausible if the period's worst drawdown is a single market-wide
drop both arms were fully exposed to, and 2014-2019 is the one period where the
latch barely operated (drawdown was 20.65% of blocks, not 94-99%). It is
recorded here as unexplained rather than assumed benign; anyone building on §41
should confirm it before relying on that row.

### A correction to this session's own commentary

After s41a alone, the interim summary given to the owner stated that *"removing
the lockout did not make the bot beat its own benchmark."* s41b, s41c and s41d
each contradict it on the exposure-matched measure. The claim was generalised
from one period — the precise error the four-spec conjunction exists to prevent,
committed in the narration rather than in the spec. Recorded rather than edited
away, in the same spirit as §33 Run 1.

### What happens next — and what must not

**`pp: 3.0` will not be widened and §41 re-scored.** The mark was frozen before
the data existed, and re-scoring a rejected candidate against a loosened bar is
the move this entire programme exists to prevent. `config.yaml` still carries the
record of a heat cap raised and reverted the same day on reasoning that stopped
holding.

The legitimate follow-up is a **different claim**, not a second look at this one.
§41 tested the decay at unchanged sizing, which confounds "the bot trades more"
with "the bot risks more": deployment rose roughly eightfold in three periods, so
of course drawdown grew. A §42 could pair the decay with reduced per-trade sizing
and ask whether return improves **at matched drawdown**. That is a new
hypothesis, requiring a fresh registration citing this rejection in its prior,
and it is the owner's decision to make — not an automatic next step.

**EDGE claims remain 0 for 10.** §41 is CAPACITY and does not touch that record.

### Nothing was adopted

`risk.drawdown_decay` is absent from `config.yaml`, so `risk.decayed_peak()` is
the identity function in production. Verified after the change: 2022-2026
reproduces §40 exactly — −5.26%, PF 0.449, 29 trades, 4.09% deployment, 243,814
drawdown blocks, 245,213 signals. Every result from §1 to §40 stands unaltered.

**The latch is still live, and still has no reset path.** §40's warning is
unchanged: production trips at a 10% drawdown and then stops buying permanently
until `memory/.equity_highwater.json` is deleted by hand. §41 establishes that
the obvious fix costs more drawdown than the pre-registered allowance permits —
it does not establish that the latch is acceptable.

---

## §42 — DIAGNOSTIC: what the judge does to the census (W2-1, 2026-07-29)

**Not a claim. No verdict. Nothing is enabled or rejected by this section.**
`scripts/census.py` on the SHIPPED config, run twice, changing exactly one
thing: whether `backtest.judge_model` is on. Both runs come from the same
commit, so the judge is the only variable.

### The control reproduces §40 to the digit

| period | §40 executed | judge-OFF today | §40 in cash | judge-OFF today |
|---|---|---|---|---|
| 2000-2006 | 101 | **101** | 84.3% | **84.3%** |
| 2007-2013 | 155 | **155** | 86.8% | **86.8%** |
| 2014-2019 | 1,732 | **1,732** | 18.9% | **18.9%** |
| 2022-2026 | 29 | **29** | 92.4% | **92.4%** |

Returns match too (+5.26 / +2.59 / +69.02 / −5.26). This matters: it is what
licenses reading the table below as *the judge's effect* rather than as drift
from four days of unrelated commits.

### The paired result

| period | executed off → on | deployment off → on | return off → on | PF off → on | drawdown share off → on |
|---|---|---|---|---|---|
| 2000-2006 | 101 → **753** | 8.07% → 20.95% | +5.26% → +9.45% | 1.202 → **1.122** | 94.58% → 74.45% |
| 2007-2013 | 155 → **269** | 9.75% → 10.92% | +2.59% → +2.42% | 1.117 → **1.082** | 96.71% → 93.76% |
| 2014-2019 | 1,732 → **3,057** | 68.97% → 69.25% | +69.02% → +56.71% | 1.486 → **1.396** | 20.65% → 20.97% |
| 2022-2026 | 29 → **54** | 4.09% → 4.72% | −5.26% → −7.71% | 0.449 → **0.367** | 99.43% → 96.86% |

### Two findings, in opposite directions

**1. Modelling the judge makes the bot trade MORE — in all four periods.**
1.7x to 7.5x more executions. That reads backwards, since the judge only ever
SHRINKS a position, until the latch is put back in the picture: smaller
positions produce shallower drawdowns, a shallower drawdown trips the 10% rail
less often, and a rail that trips less often blocks fewer entries. On 2000-2006
the drawdown share of all blocks falls 94.58% -> 74.45% and executions go 101 ->
753.

So **§35-§41 measured a bot MORE locked out than production is.** §40's
headline — "84-92% fully in cash, 94-99% of blocks are the drawdown rail" —
overstates the real bot's lockout. The latch is still the dominant blocker in
three of four periods (74%, 94%, 97%), and §40's diagnosis stands in direction.
Its magnitude does not.

**2. Profit factor falls in all four periods.** 1.202→1.122, 1.117→1.082,
1.486→1.396, 0.449→0.367, and return falls in three of four. The judge-less
simulator was **flattering the incumbent's trade quality**, which is the more
important of the two findings and the one that would have been easiest to miss,
because it runs against the direction of the first.

### What this does NOT do

**It reverses nothing.** All ten EDGE verdicts are rejections. A simulator that
overstated the incumbent's profit factor makes a rejection *more* robust, not
less — the candidate had to beat a bar that was set too high, and still failed.
No verdict is withdrawn, re-scored, or revisited on this basis, and the EDGE
tally stands at **0 for 10**.

**It does not re-open §41.** §41's rejection was measured judge-less, so whether
the decay arm still roughly doubles maxDD with the judge on is unmeasured. That
is a reason to register a NEW claim, never a reason to re-score a rejected one.

**It is not evidence about live.** The judge model reproduces a *distribution*
(n=164 decisions over 12 days, one regime, heavily serially correlated), not the
judge's actual decisions. Nothing here says the live bot would have executed 753
times on 2000-2006. It says a simulator that models the haircut behaves
differently from one that ignores it, by enough that the difference is not
ignorable.

### Renumbering note

The deferred owner decision — pair the drawdown decay with reduced per-trade
sizing and test at MATCHED drawdown — becomes **§43** when it is registered.
This DIAGNOSTIC took §42 because it ran first. Numbers are permanent; the
earlier plan text calling that claim "§42" is superseded here rather than
edited away.

**SUPERSEDED THE SAME DAY (2026-07-29, corrected in W4-6).** The paragraph
above reserved §43 for the deferred decay claim, and then §43 was taken by the
incumbent re-test, which registered and ran first — on the identical
"whichever runs first takes the number" rule this note itself states. The
reservation was never corrected at the time, so the record contradicted itself
for several hours.

**The deferred decay-plus-sizing claim becomes §44.** Reserving a number for a
claim nobody has registered was the mistake; numbers are assigned at
registration, and a reservation is not a registration. Corrected in place
rather than rewritten, for the same reason §33 Run 1 is still in this document.

---

## §43 — THE INCUMBENT FACES ITS OWN GATE ON A SIMULATOR THAT MATCHES LIVE

**REJECTED, 0 of 4.** Specs `s43a`–`s43d`, frozen together 2026-07-29 as one
claim (`f3f5901e6a99cce5…`, `9bd8d5394c287d89…`, `4edd461cfcb0bb34…`,
`bcc25284ec0c668a…`), K=12, conjunction — all four must pass. Raw output:
`research/s43_run_2026-07-29.txt`.

**§39's pass mark, verbatim, clause for clause.** Same four clauses, same
`n: 30`, same single arm, same shipped `config.yaml` with no risk overlay. The
only thing that moved is the fidelity of the simulator: Wave 2 closed
divergences #8 (the judge), #11 (live peak sampling) and #12 (raw bars). That
is the design — any change in verdict is attributable to fidelity, not to a bar
someone quietly made easier.

`judge_model: true` on all four, and the runner's refusal check confirms it
fired rather than merely being declared: 129,179–419,540 entries sized per
period, 72,932–237,450 cut, 3,123–10,384 vetoed.

### Result

| period | return | exposure-matched bar | margin | PF | deploy | trades |
|---|---|---|---|---|---|---|
| 2000-2006 | +9.45% | +29.02% | **−19.57pp** | 1.122 | 20.95% | 753 |
| 2007-2013 | +2.42% | +10.10% | **−7.68pp** | 1.082 | 10.92% | 269 |
| 2014-2019 | +56.71% | +62.61% | **−5.90pp** | 1.396 | 69.25% | 3,057 |
| 2022-2026 | −7.71% | +2.13% | **−9.84pp** | 0.367 | 4.72% | 54 |

Clauses (a), (b) and (c) FAIL in all four periods. Clause (d) `min_trades`
passes in all four. Nothing is enabled; the incumbent stands as the incumbent
only because nothing has been proposed to replace it.

### The finding: the one pass in this project's history was an artifact

2014-2019 is the only period in which this bot has ever cleared its own gate.
§35 recorded it passing the exposure-matched bar, and on the judge-off census it
clears by **+6.66pp** (+69.02% against a +62.36% bar). With the judge modelled
it **fails by −5.90pp** (+56.71% against +62.61%). Deployment barely moves
(68.97% → 69.25%), so the bar stays where it was and the bot falls under it.

That single flip changes the honest count from **one period in four** to
**zero in four**. The pass was not a marginal result that survived scrutiny — it
was produced by a simulator that omitted a component the live bot had been
running for weeks.

### The prior called every number before the run

The frozen prior computed clause (b) for all four periods from §42's
already-published census and named the expected margins. **Every cell matched
the run exactly** — +9.45/+29.02, +2.42/+10.10, +56.71/+62.61, −7.71/+2.13, and
profit factors 1.122 / 1.082 / 1.396 / 0.367.

This is worth stating plainly in both directions. It is pre-registration working
as intended: the prediction was specific to a fraction of a percentage point and
could have been embarrassed by any of forty numbers. It is also **weaker
evidence than it looks**, and the spec's own failure modes say so: the census
inputs were already known, so (b) and (c) were arithmetic, not discovery. The
genuinely new information is clause (a) `enablement_gate`, which is not
computable from a census, and it failed in all four.

### What §43 does NOT license

- **It does not reverse anything.** Ten EDGE claims were rejections and remain
  rejections. §43 is the eleventh EDGE rejection. **EDGE claims: 0 for 11.**
- **It does not say the live bot loses money.** The paper ledger holds **two**
  closed round-trips, both winners. n=2 decides nothing in either direction.
- **It is not a licence to tune.** Fitting parameters to any of these periods
  now would manufacture exactly the overfit §33 and §34 established this
  repository cannot detect. No threshold is being adjusted in response.
- **Survivorship still flatters the incumbent** in all four periods, so these
  FAILs are strong evidence and a PASS would have been weak. The 500-symbol
  universe against 38 tuned symbols remains an uncontrolled confound.

### The honest reading

§39 established that the incumbent fails its own gate on one unseen period.
§43 establishes that it fails on all four, on the first simulator this project
has had that models what the live bot actually does — and that the lone
historical pass does not survive the repair.

The question this programme has been asking since §14 is *"is this candidate
better than what we run?"* §43 says the more useful question is whether what we
run should be running at all. That is the owner's decision, not a gate's, and it
is now supported by four periods spanning 26 years rather than one.

**Nothing enabled. `mode: paper` unchanged. `drawdown_decay` still absent from
`config.yaml`. EDGE 0 for 11.**

---

## §44 — DECAY + REDUCED SIZING: DOES RETURN IMPROVE AT MATCHED DRAWDOWN? (EDGE, K=13, 2026-08-02)

**VERDICT: REJECTED.** Conjunction of four periods — one clause failure in one
period sinks the whole claim. `risk.drawdown_decay` stays absent from
`config.yaml` and `risk_per_trade_pct` stays at 8.0.

This is the follow-up §41 itself named as the legitimate next step and refused
to take on its own authority: "pair the decay with reduced per-trade sizing and
ask whether return improves at matched drawdown... requiring a fresh
registration citing this rejection in its prior... the owner's decision to
make." Registered only after that decision was made, 2026-08-02.

| spec | period | frozen sha |
|---|---|---|
| s44a | 2000-2006 | `e0c6e976a7c94017…` |
| s44b | 2007-2013 | `077fa7ff2a3419cb…` |
| s44c | 2014-2019 | `5306ab4d50617635…` |
| s44d | 2022-2026 | `2418e96e4a11bdde…` |

### The sizing value, derived not tuned

`risk_per_trade_pct: 5.0`, from 8.0 scaled by the average of §41's own three
non-anomalous maxDD ratios (baseline/decay): 11.51/17.67=0.651,
12.04/21.15=0.569, 10.78/15.73=0.685, mean 0.635 → 8.0×0.635=5.08, rounded to
5.0. Derived entirely from numbers §41 had already published before this spec
existed — no candidate run informed the choice. One arm, not a sweep: §34
already showed selecting the best of several settings after seeing results
does not predict anything on this data.

### Judge ON — a deliberate improvement over §41, not a comparable baseline

§41 was scored judge-less; §45 later named this as an open gap ("whether the
decay arm still roughly doubles maxDD with the judge on is unmeasured"). §44
closes that gap. **This means §44's numbers are NOT directly comparable to
§41's own table** — different simulator fidelity, not just a different arm.
Judge calibration for all four periods: n=164, veto 2.4%, downsize 58.1%, mean
scale 0.729 (2026-07-16..2026-07-28).

### Results

| period | arm | return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|---|
| 2000-2006 | baseline | +9.45% | 1.122 | 11.52% | 753 | 20.95% |
| | decay+5.0 | +125.48% | 1.560 | 14.47% | 4,586 | 69.63% |
| 2007-2013 | baseline | +2.42% | 1.082 | 11.45% | 269 | 10.92% |
| | decay+5.0 | +33.42% | 1.257 | **28.03%** | 3,544 | 57.69% |
| 2014-2019 | baseline | +56.71% | 1.396 | 10.72% | 3,057 | 69.25% |
| | decay+5.0 | +75.87% | 1.409 | 11.15% | 4,895 | 83.66% |
| 2022-2026 | baseline | −7.71% | 0.367 | 10.99% | 54 | 4.72% |
| | decay+5.0 | +73.47% | 1.476 | 10.64% | 3,694 | 77.78% |

| clause | s44a | s44b | s44c | s44d |
|---|---|---|---|---|
| (a) maxdd_within pp 3.0 | PASS 14.47 vs 14.52 | **FAIL** 28.03 vs 14.45 | PASS 11.15 vs 13.72 | PASS 10.64 vs 13.99 |
| (b) beats_exposure_matched | PASS +125.48 vs +96.46 | **FAIL** +33.42 vs +53.35 | PASS +75.87 vs +75.64 | PASS +73.47 vs +35.08 |
| (c) min_trades 30 | PASS | PASS | PASS | PASS |
| (d) not_worse | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |

### What is established

**Three of four periods individually cleared every registered clause,**
including the return-versus-exposure test the prior thought least likely to
pass. That is a materially closer result than the prior predicted — the honest
prior said REJECTED was the base rate on all four, most likely via clause (b)
on structural grounds (reduced sizing shrinking the very deployment needed to
beat the exposure-matched benchmark). That reasoning was wrong for three of
four periods and right for the fourth, for a different, more specific reason.

**The rejection is isolated to the period containing the 2008 crisis, and it
failed in exactly the way the prior specifically named for that period before
the run:** *"if decay lets the bot back in DURING 2008 rather than only after,
clause (a) is the more likely failure point here than in the other three
periods."* maxDD came in at 28.03% against a 14.45% allowance — not a near
miss. This is the same mechanism-consistent failure §41 found at unchanged
sizing, now confirmed to survive a sizing reduction that was sufficient
everywhere else.

**Clause (d) carried no information in any period**, same pattern §41 found for
its own capacity clause: every confidence interval is wide enough to exclude
nothing (e.g. s44a: `[$-60.17, $+74.29]`), and point estimates move in both
directions across periods (s44c candidate *worse* per-trade, s44a/b/d candidate
*better*). Read as absence of evidence, not evidence of parity.

### Two things that must not be over-read

**A REJECTED conjunction is not "close to a PASS."** Three individually-passing
periods do not partially satisfy a claim that was pre-registered as a
four-period AND. The clause structure exists precisely so that a result like
this — strong everywhere except the one period markets actually broke — reads
as REJECTED, not as 75% adopted. A mechanism that works except during a crisis
is a mechanism that is absent exactly when a circuit breaker exists to matter.

**§44's own numbers cannot be diffed against §41's table as if it were a
tighter replication.** The judge is ON here and was OFF in §41; some of the
difference between "§41's decay arm roughly doubled maxDD everywhere" and
"§44's decay+reduced-sizing arm only blew through the allowance in one period"
is the sizing reduction working as intended, and some of it is two different
simulators. This spec cannot separate the two, and no claim here rests on
being able to.

### What happens next — and what must not

**The pass marks are not loosened and s44b is not re-scored.** Same rule §41
stated for itself: re-scoring a rejected candidate against a bar chosen after
seeing the result is the exact failure this apparatus exists to prevent.

The visible next question — does the same mechanism hold if sizing is reduced
*specifically enough to survive the crisis period*, rather than by a single
value averaged across three calmer ones — is a **different claim**, requiring
a fresh registration that cites this rejection in its prior, same as this one
cited §41. It is not registered here, and it is the owner's decision whether
it is worth running, not an automatic next step.

**EDGE claims: 0 for 13.** Nothing enabled. No threshold moved. `mode: paper`
unchanged. `risk.drawdown_decay` and the reduced `risk_per_trade_pct` are both
absent from the shipped `config.yaml` — verified: neither this registration nor
its run touched the file.

---

## §45 — WHY DOESN'T THE BOOK TURN OVER? (DIAGNOSTIC, 2026-07-29)

**Numbering.** §44 is reserved a few sections above for the deferred
decay-plus-sizing claim, and that reservation stands. This diagnostic takes §45
and runs first. Reassigning a written reservation would make §44 refer to two
different things across this file's history, which is the quiet rewrite W5 spent
a day undoing; a gap the document explains is fine.

### Why this and why now

The live book barely turns over, and that is now the binding constraint on every
revenue gate:

| | measured 2026-07-29 |
|---|---|
| closed round-trips | **4** (+16.78, +10.67, +6.40, −8.59%) |
| open positions | **14** |
| paper history | **15 days** (Jul 14 → Jul 29) |
| observed close rate | **0.27 / day** |

Invariant 10 requires ≥30 closed trades. At the observed rate that is **~98 more
days — early November**, roughly seven weeks *later* than the ≥60-day clock
(~Sep 12). So the close rate, not the calendar, is what gates revenue, and
nothing has ever measured why it is what it is.

§40 built an ENTRY census — of every buy signal, what executed and what each rail
blocked. There is no equivalent for exits. Every closed trade carries an
`exit_reason` and the simulator has always known it; nothing has ever aggregated
it.

### THE PRIOR — written and committed before the census exists

DIAGNOSTIC, not EDGE. Nothing is adopted, no gate is registered, no config value
moves. But §43's forty-for-forty prediction match was recorded as *weaker
evidence than it looks* precisely because its inputs were already known, so these
predictions are timestamped ahead of the measurement.

Shipped config, the relevant parts: `min_holding_days: 2`,
`take_profit_atr_mult: 0` (stop-only — "every walk-forward winner chose no
take-profit"), `trailing_atr_mult: 3.0` gated to `tsmom` only, no re-entry
cooldown.

The four exit reasons the simulator can emit are `strategy_sell`, `stop_loss`,
`take_profit`, and `end_of_data`.

1. **`take_profit` will be 0 in all four periods.** `take_profit_atr_mult: 0`
   means `tp` is None for every position; `tsmom`'s chandelier ratchets the STOP
   and so fires as `stop_loss`. This one is near-certain and is stated to make
   the census falsifiable rather than to be impressive: if `take_profit` is
   non-zero, the bracket wiring is not what the config says.
2. **`end_of_data` will be the largest single bucket in at least three of the
   four periods, and ≥ 40% of closed trades in each.** This is the load-bearing
   prediction. `end_of_data` is a forced close at run end, not a decision — if it
   dominates, the strategies essentially never sell, every backtest closed-trade
   count is inflated by run truncation, and **live has nothing that force-closes**,
   which would explain the whole observation.
3. **Median holding period > 30 calendar days** in every period.
4. **`n_guard_skipped_exits` > 0 in every period** — `min_holding_days` is
   demonstrably biting on sells, not merely present.
5. **Live is not anomalous.** If 2–4 hold, then 4 closes in 15 days is the design
   working as specified rather than a defect, and the ≥30-trade gate is
   unreachable on this configuration in any useful timeframe. That would make it
   a *config* question — which is the owner's, and is NOT decided here.

**Prediction 2 is the one that can embarrass me.** If `end_of_data` is small and
`strategy_sell` dominates, the simulator turns the book over far faster than live
does, and the finding flips from "slow by design" to a sim/live divergence
(#13) — the opposite conclusion from the same measurement.

*(Measured results follow below, written after the run.)*

### THE MEASUREMENT (2026-07-29, judge ON, shipped config, four snapshots)

`python scripts/census.py --judge-model` — full output in
`research/census_2026-07-29_exits.txt`. `s43d` was re-run first and reproduced
**byte-identically** (−7.71%, PF 0.367, maxDD 10.99%, 54 trades, judge sized
244,673 / cut 138,709 / vetoed 6,031), so the exit census is observation-only and
no frozen verdict moved.

| period | closed | stop_loss | strategy_sell | take_profit | end_of_data | median hold | sells blocked by min_holding |
|---|---|---|---|---|---|---|---|
| 2000-2006 | 753 | **534 (70.9%)** | 219 (29.1%) | 0 | **0** | 14d | 103 |
| 2007-2013 | 269 | **158 (58.7%)** | 111 (41.3%) | 0 | **0** | 13d | 21 |
| 2014-2019 | 3,057 | **2,201 (72.0%)** | 856 (28.0%) | 0 | **0** | 15d | 428 |
| 2022-2026 | 54 | **27 (50.0%)** | 27 (50.0%) | 0 | **0** | 21d | 2 |

### Scoring the prior: 2 of 5, and the load-bearing one was wrong

| # | prediction | measured | |
|---|---|---|---|
| 1 | `take_profit` = 0 everywhere | absent in all four | **✓** |
| 2 | `end_of_data` ≥40% and largest bucket in ≥3 of 4 | **0 in all four** | **✗** |
| 3 | median hold > 30 days | 14 / 13 / 15 / 21 | **✗** |
| 4 | `guard_skips` > 0 everywhere | 103 / 21 / 428 / 2 | **✓** |
| 5 | live is slow by design; ≥30 unreachable | live is on schedule — see below | **✗** |

The prior said prediction 2 was the one that could embarrass me, and that if
`end_of_data` came back small the conclusion would invert from "slow by design"
to something else entirely. It came back at **zero**, which is further from the
prediction than the miss case I imagined.

### THE FINDING: there was never a close-rate problem

The premise of this whole diagnostic was an artifact of the measurement window.

**0.27 closes/day was computed over 15 days. The median holding period is 13–21
days in the simulator and 12 days across the four live closes.** Over a window
shorter than one holding period, almost nothing *can* have closed yet — only
trades stopped out early, which is precisely what the live record shows: three of
the four live closes are `stop_loss`, and the fourth was a 5-day `strategy_sell`.

The live book is not failing to turn over. It has not yet reached the age at
which it turns over:

| live, 2026-07-29 | |
|---|---|
| open positions | 14, entered Jul 16–22 |
| median age of an open position | **13 days** |
| median hold of the 4 closes | 12 days |
| simulator's median hold | 13–21 days |

Every open position is sitting right at the age where the simulator closes it.

**The ~98-day projection to Invariant 10's ≥30 closed trades was wrong**, and it
was wrong in the ordinary way: a startup transient extrapolated as a steady-state
rate. It is retracted. No replacement projection is offered here — one holding
period of live data is not enough to estimate a rate from, and putting a
confident new number next to a retracted one would repeat the error.

### What the exit census did find

1. **The bot does not sell; it gets stopped out.** `stop_loss` is the largest
   exit bucket in all four periods (50–72%). `strategy_sell` never exceeds 41%.
   Whatever the strategies believe they are doing, the ATR stop is making most of
   the exit decisions.
2. **`end_of_data` is zero everywhere**, which is a clean result for the whole
   research record: the book fully empties before the data runs out in every
   period, so **no closed-trade count in §35–§43 is inflated by run truncation.**
   That was worth checking and is now checked.
3. **`min_holding_days` demonstrably bites** — 103 / 21 / 428 / 2 sells blocked.
   Invariant 3's swing guard is a real constraint, not a documented one.
4. **The constraint is the ENTRY side, not the exit side.** `drawdown` blocked
   74.5% / 93.8% / 96.9% of all signals in three periods. §40 said capital
   struggles to get in; §45 confirms that once in, it leaves promptly.

### The divergence this exposed instead (#13)

Live's equity drawdown is **1.05%** against a 10% rail, and it has never exceeded
0.10% before today. In 2022-2026 — the period closest to the current regime — the
drawdown rail blocked **96.86% of every buy signal the strategies emitted.**

So the live bot is currently trading in a state that the backtest says is rare:
the ~3% of the time the drawdown latch is quiet. **The live track record is not
being drawn from the same distribution as the backtest.** Its first fifteen days
are the honeymoon before the latch engages, and any live-versus-backtest
comparison made now is comparing the calmest part of one against the average of
the other.

That is not an argument that live will do worse. It is an argument that **n=4,
already too small to decide anything, is also unrepresentative** — and the second
problem does not go away by waiting for the first to shrink.

**Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE 0 for 11.**

---

## §46 CANDIDATE (not registered) — fundamentals, blocked on a lookahead probe

**2026-07-30.** Source: Miles Deutscher, *"I Replaced My Financial Advisor With
Claude Opus 5"* (23:44). The video builds a human-in-the-loop research assistant
the author double-checks before every manual trade — the opposite posture to
this bot — and most of it is already covered here or forbidden outright. Two
things in it are explicitly NOT being adopted:

- the market screener at [18:35] ("flag the 3-5 trading furthest below price
  targets, give me the bull case") is unregistered discretionary selection, the
  exact thing §32/§33 existed to test;
- "Claude can adjust your strategy over time" at [07:50] is a strategy change
  adopted by enabling it and watching.

**The real finding is an absence.** `grep -rl "price_target|free_cash_flow|
analyst|pe_ratio|fundamental|insider" src/` returns **nothing**.
`memory/earnings_cache.json` holds earnings *dates* for blackout logic, not
figures. All five strategies are pure price/technical. Fundamentals are an input
class no claim in this file has ever been able to reference.

### Why nothing is registered yet

A fundamentals claim is only worth writing if the data can be backtested without
lookahead, and there are three ways it can fail:

| risk | why it is fatal |
|---|---|
| **restatement** | figures restated later already embed what happened next |
| **filing lag** | period-end dating trades on numbers nobody had for ~45 days |
| **survivorship** | a survivor-only universe excludes everyone who went bankrupt |

Any one of them makes a gate inflated and **indistinguishable from a real edge**
— the worst possible outcome for a record that is honestly 0 for 12.

`scripts/probe_fmp_lookahead.py` measures all three and **exits non-zero with a
refusal** if any fails, on the §28 precedent: that gate was fully drafted and
never registered because a probe showed its mechanism could not move its metric.
Finding this out after registering would be strictly worse.

### The two outcomes, decided in advance

- **all three pass** → a §46 may be pre-registered and backtested on fundamentals;
- **any one fails** → fundamentals are **live judge context only** — the W7
  shape: judge-only, so under invariant #2 they can veto or shrink and can never
  create a trade; ungated; recorded as a divergence.

Deciding this before seeing any result is the point. Choosing the shape after
looking would be selecting the experiment on its outcome, which is how §44's
prior got revised into being worse than the one it replaced.

**Blocked on the owner:** a free FMP key, installed via
`./scripts/set_fmp_key.sh` (never pasted into a chat).

**Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE 0 for 12.**

### Where the probe landed, and the sources that followed

`probe_fmp_lookahead.py` ran on 2026-08-02. All three checks returned
**UNDETERMINED** — the free FMP tier answers the deciding endpoints with HTTP
402 — and the probe treats undetermined as failure. The rule above has therefore
already fired, selecting the **judge-context-only** branch. Nothing is
registered; fundamentals stay blocked on the owner's call about a paid plan.

Two further sources of the same shape have since been assessed and produced no
change. Their reviews live beside this file so the question does not get
re-litigated a third time:

- `knowledge/source-review-oria-one.md` (2026-08-03) — 18 Claude prompts for
  deal and fundamental analysis; ten of them land inside the branch the rule
  already selected. One unregistered candidate noted: book-level sector/factor
  exposure, since `correlation_cap` is pairwise only.
- `knowledge/source-review-tradingview-mcp.md` (2026-08-03) — an MCP that
  automates the TradingView desktop app over a debug port. It cannot backtest
  this bot's code, and an interactive strategy-search loop would spend
  multiple-comparison budget that nothing counts.

---

## §47 — RANDOM-ENTRY DECAY MONITOR (DIAGNOSTIC, pre-registered 2026-08-01, before first run)

**Claim type: DIAGNOSTIC.** Like §40/§42/§45 this cannot enable or disable a
strategy, and no verdict it produces changes `mode`, `max_open_positions`, or
any threshold. It adds an *instrument*, not a candidate.

### The gap it closes

The daily-loss kill switch answers "am I losing money too fast." Nothing in this
repo answered **"did my entry signal stop carrying information."** Those are
different failure modes: a strategy that has been arbitraged away bleeds slowly
and never trips a loss limit. §46's own framing — that any found edge is
temporary and crowding-out is expected — implies a decay instrument, and there
wasn't one.

Source of the idea: AI Pathways, *"How To Actually Find A Profitable Strategy
With Claude"* (2026-07-31). Most of that video is a lower-rigor version of what
`run_gate.py` already enforces — it runs 155 ideas and *then* picks filters,
which is the multiple-comparisons trap the Bonferroni-K correction exists to
prevent, and it reports point estimates with no confidence interval. Two things
in it were genuinely absent here; this is the one worth building. (The other,
a named worst-decade 2000–2009 stress era, is **not** being built: it can only
reject, and with EDGE 0-for-12 nothing is close enough for regime-robustness to
be the deciding factor. Recorded so the decision is visible, not forgotten.)

### The null, fixed before any run

*Would entering on random dates have done as well?* Random entries are drawn on
the **same symbols**, over the **same bar series**, with holding periods
**resampled from the actual trades**, entering at the **next open** (never the
decision-bar close), with slippage charged on **both legs**. Matching exposure
is what makes it honest: an unmatched null holds a different amount of market
risk and reads as "edge" in any up market.

The statistic is the **percentile of the strategy's mean return per trade inside
the distribution of means random timing would have produced.**

### Thresholds, frozen before the first result was seen

| condition | verdict | alerts? |
|---|---|---|
| `n_trades < 20` | `INSUFFICIENT_DATA` | no |
| percentile < 5.0 | `WORSE_THAN_RANDOM` | **yes** |
| 5.0 ≤ percentile < 95.0 | `INDISTINGUISHABLE_FROM_RANDOM` | no |
| percentile ≥ 95.0 | `BEATS_RANDOM` | no |

`INSUFFICIENT_DATA` is a distinct state, never folded into a pass. A monitor
reporting "healthy" on an empty book is the *empty is not the same as absent*
trap, and this repo has been bitten by it before.

### Known asymmetry, stated rather than hidden

Real trades carry stops and take-profits; the null trades do not. Resampling
**realised** holding periods absorbs much of this (a trade stopped out on day 2
contributes a 2-bar holding) but not all: an unprotected null takes the full
loss where a real trade cut it. That biases the null **downward**, flattering
the strategy, making this monitor **lenient** — it under-fires rather than
over-fires. Right direction for an alert whose false positives cost attention;
**wrong instrument to cite as evidence of edge.**

### Alert-only, by construction

Owner decision 2026-08-01: it does **not** halt. `tests/test_decaycheck.py::
test_monitor_cannot_halt_trading` walks the module AST and fails if it ever
imports `risk` or calls `engage_halt`/`check_halt` — so invariant #2 holds
structurally, not by argument. A component that can only emit text cannot
enlarge risk.

### First run, and why its number is NOT a finding

Live, 2026-08-01: **`INSUFFICIENT_DATA` — 7 closed trades, need 20.** That is
the correct and expected result; all three enabled strategies sit at
`max_open_positions: 0` (§29), so the book cannot enter.

The machinery was then smoke-tested by **deliberately overriding the threshold**
to `--min-trades 5`, which returned `BEATS_RANDOM, +2.617%/trade (n=7), 95.2th
percentile of 500 samples`. **This is not evidence of anything.** n=7 against a
pre-registered floor of 20; the percentile sits a fraction above the boundary; a
seven-trade sample cannot separate edge from luck in either direction. It is
recorded here **only** because it is exactly the kind of flattering number a
later session would otherwise rediscover, rationalise, and cite. It is a proof
that the code path executes, nothing more.

**Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE 0 for 12.**

### Note, 2026-08-11 — the instrument changed at n=11, and the sample is not homogeneous

§61 altered the judge's context mid-accumulation. Trades 1–11 were judged on a
prompt from which NEWS MEMORY, YOUR LAST RESOLVED CALLS, YOUR RECENT
CALIBRATION and CURRENT REGIME had been silently evicted; trades 12–20 will be
judged on a prompt that carries all four. The judge may veto or downsize any
entry, so this is upstream of which trades populate this monitor's sample.

**Owner decision, taken with n=11 in front of them**, over the alternative of
waiting for n=20. Recorded here because §47's own "Known asymmetry, stated
rather than hidden" is the standard, and a monitor whose sample spans two
different instruments is exactly the thing that should not be discovered later
from a percentile.

The threshold does not move and the null does not change. What changes is what
the first reading at n=20 will be entitled to say: it will describe a bot that
was two bots, and the honest read is that it bounds nothing tightly. If a
cleaner answer is wanted, the counter restarts from trades judged under the
current prompt — which is a decision for whoever reads the first result, not
one to pre-empt here.

The monitor still cannot halt anything (`src/decaycheck.py`,
`test_monitor_cannot_halt_trading`), so this is a measurement-validity cost and
not a safety one.

---

## §48 — IS THERE SIGNAL AT FULL DEPLOYMENT? (DIAGNOSTIC, pre-registered 2026-08-03, before the run)

**Claim type: DIAGNOSTIC.** Like §40/§42/§45/§47 this cannot enable or disable a
strategy, and no verdict it produces changes `mode`, `max_drawdown_pct`, or any
other config value. `run_gate.py` prints *"DIAGNOSTIC — this decides NOTHING"*
and records no verdict. **The multiple-comparisons count does not rise: K stays
13.**

Specs `research/specs/s48a-d.yaml`, written and frozen **together** before any
of them was run — the §41/§44 convention. Frozen hashes:

| spec | period | frozen sha256 |
|---|---|---|
| s48a | 2000-2006 | `78df9ad69bd17c88…` |
| s48b | 2007-2013 | `063ecf050494c09c…` |
| s48c | 2014-2019 | `d682853578229340…` |
| s48d | 2022-2026 | `50bbcea427c73b46…` |

### The question, and why nobody has asked it

`scripts/census.py` states the problem in its own docstring:

> at that exposure a genuine edge would be invisible, so **"no edge found" and
> "no edge expressible" are currently indistinguishable** — and all ten EDGE
> rejections inherit that ambiguity.

§40 measured the exposure and §45 confirmed the constraint is the entry side:

| period | signals | executed | rate | fully in cash | blocked by drawdown |
|---|---|---|---|---|---|
| 2000-2006 | 422,592 | **101** | 0.02% | 84.3% | 94.58% |
| 2007-2013 | 423,926 | **155** | 0.04% | 86.8% | 96.71% |
| 2014-2019 | 336,408 | 1,732 | 0.51% | 18.9% | 20.65% |
| 2022-2026 | 245,213 | **29** | 0.01% | 92.4% | **99.43%** |

**Twenty-nine executions from 245,213 signals** in the period closest to today.

Two claims have already tested candidate *fixes* to the one-way latch. §41
tested `risk.decayed_peak` at unchanged sizing — REJECTED, maxDD roughly doubled
in three of four periods. §44 tested that decay paired with reduced sizing —
REJECTED on the period containing 2008, at 28.03% maxDD against a 14.45%
allowance.

**Neither ran the clean counterfactual: the rail simply absent.** That is the
gap §48 closes. `risk.max_drawdown_pct: 0` here is a *control condition*, not a
proposal — the arm that makes the baseline arm interpretable.

### What this explicitly does not do

`census.py`'s own rule governs, and is restated because it is exactly the move a
result like this invites:

> if a rail turns out to be strangling deployment, that fix is a separate
> pre-registered decision. **Loosening a risk rail because a diagnostic
> suggested it is exactly the unfalsifiable move this whole programme exists to
> prevent.**

No outcome here licenses moving `max_drawdown_pct`. The unblocked arm will have
far worse drawdown by construction; that is arithmetic, not a finding, and it is
not weighed against the owner's stated 10pp tolerance anywhere in this section.

### THE READING RULE — committed before the run

Written into all four specs' `prior` and hashed with them, so the outcome cannot
select the experiment. That is the §46 trap, and the mistake §44's revised prior
made. Read across s48a-d **together**:

1. **Unblocked arm FAILS `beats_exposure_matched` in ≥2 of 4 periods** → the
   strategies have no expressible edge on this universe even when fully
   deployed. The ambiguity resolves *against* the strategies, the rejection
   record is confirmed rather than confounded, and this licenses **no further
   EDGE registration on this universe** — specifically, the drafted
   volatility-targeting spec is not registered.
2. **Unblocked arm CLEARS it in ≥3 of 4** → the rail was masking measurement,
   and some share of the rejections were scoring a bot that was in cash. This
   licenses **exactly one thing**: a fresh EDGE registration re-testing the
   *incumbent* at full deployment, spending budget as K=14. Not new machinery,
   and not a config change.
3. **Any other split is INDETERMINATE and licenses nothing.** A rule that turns
   every possible outcome into a green light is not a rule.

Outcome 1 is the honest expectation and is the more valuable of the two: it
converts *"we do not know whether there is an edge"* into *"we know there is
not"*, which is the first thing this project could act on decisively.

### The prior, in one line

The unblocked arm clears deployment and trade-count easily — that is arithmetic
— and still fails the exposure-matched return test. Removing a risk rail cannot
create predictive power that was not there.

### Two things that must not be over-read

**A clean result under outcome 2 is still not an EDGE.** It would say the
previous measurements were confounded, not that the strategies work. Nothing
here withdraws, re-scores or revisits any of the thirteen rejections — §41 set
that precedent explicitly when its own simulator finding could have reopened ten
verdicts, and it reopened none.

**2014-2019 is a null manipulation, not a null result.** The rail blocked only
20.65% of signals there, so the two arms may come back nearly identical. §41
already flagged this period as anomalous for the same reason. It is run anyway
and counted on the same terms — dropping the inconvenient period *after* knowing
which one it is would be period selection.

*(Measured results follow below, written after the run.)*

---

### THE MEASUREMENT (2026-08-03, judge ON, full output `research/s48_run_2026-08-03.txt`)

| period | arm | return | PF | maxDD | trades | symbols | deployment |
|---|---|---|---|---|---|---|---|
| 2000-2006 | baseline | +9.45% | 1.122 | 11.52% | 753 | 367 | 20.95% |
| | **unblocked** | **+137.50%** | 1.553 | 22.23% | 3,974 | 498 | 74.22% |
| 2007-2013 | baseline | +2.42% | 1.082 | 11.45% | 269 | 177 | 10.92% |
| | **unblocked** | **+75.57%** | 1.366 | 33.84% | 3,592 | 500 | 75.96% |
| 2014-2019 | baseline | +56.71% | 1.396 | 10.72% | 3,057 | 498 | 69.25% |
| | **unblocked** | **+85.01%** | 1.471 | 11.74% | 3,785 | 498 | 84.01% |
| 2022-2026 | baseline | **−7.71%** | **0.367** | 10.99% | 54 | 49 | **4.72%** |
| | **unblocked** | **+61.67%** | 1.424 | 16.65% | 3,084 | 495 | 77.95% |

**The control reproduced.** 2022-2026 baseline came back at −7.71%, PF 0.367,
maxDD 10.99%, 54 trades — identical to the `s43d` figures §45 published. The
diagnostic is measuring the same bot the record already describes.

**One citation corrected while checking that.** The recorded census for the
2022-2026 baseline is 245,212 signals with **237,522 blocked by drawdown =
96.86%**, not the 99.43% quoted from §40's table in the pre-registration above.
Both numbers are right for their own run: §40's entry census was scored
**judge-off**, and §45 reported 96.86% for the same period **judge-on**, which
is what s48 runs. The pre-registration text is left as written rather than
edited — it quoted §40 accurately — and the judge-on figure is the one to use
alongside these results.

### The registered clauses: the unblocked arm passed 4 of 4

| period | (a) deployment ≥25% | (b) beats_exposure_matched | (c) ≥30 trades |
|---|---|---|---|
| 2000-2006 | 74.22% ✓ | +137.50% vs +102.82% ✓ | 3,974 ✓ |
| 2007-2013 | 75.96% ✓ | +75.57% vs +70.25% ✓ | 3,592 ✓ |
| 2014-2019 | 84.01% ✓ | +85.01% vs +75.96% ✓ | 3,785 ✓ |
| 2022-2026 | 77.95% ✓ | +61.67% vs +35.16% ✓ | 3,084 ✓ |

**The prior was wrong.** It said the unblocked arm would trade far more and
still fail (b). It cleared (b) in every period. Recorded plainly, because a
prior that is quietly forgotten when it misses is not a prior.

Under the pre-registered reading rule this is **outcome (2)**: ≥3 of 4. What
that licenses is exactly one thing, quoted from the frozen spec — *"a fresh
EDGE registration re-testing the INCUMBENT at full deployment, spending budget
as K=14. It does not license new machinery, and it does not license touching
`max_drawdown_pct`."*

### AND THE PRE-REGISTERED FAILURE MODE IS REAL, AND ENORMOUS

Every s48 spec listed survivorship third, and said it *"flatters the
HIGHER-DEPLOYMENT arm more than the baseline — which biases clause (b) toward
outcome (2) and therefore toward the more permissive reading."*

That has been a stated direction on every gate since §41 and was never a size.
It is now a size. `scripts/survivorship_check.py` measures it **from inside the
snapshot**, needing no external data: SPY is in the universe, and an index
ETF's price already reflects constituent changes, so the gap between the
equal-weight 500 and SPY over identical bars *is* the inflation.

| snapshot | SPY | equal-weight 500 | **inflation** |
|---|---|---|---|
| 2000-2006 | **+8.68%** | +138.74% | **+130.06pp** |
| 2007-2013 | **+51.34%** | +96.80% | **+45.46pp** |
| 2014-2019 | +97.97% | +101.94% | +3.97pp |
| 2020-2026 | +154.94% | +140.26% | −14.68pp |
| 2022-2026 | +64.39% | +54.80% | −9.59pp |

**In 2000-2006 the snapshot's own benchmark says the market returned +138.54%.
SPY, over the identical bars, returned +8.68%.** Today's 500 index members,
back-projected to 2000, all survived the dot-com crash by construction. The
inflation is largest in the two oldest periods and vanishes in the recent ones
— exactly the gradient survivorship predicts, which is a strong check that this
is the mechanism rather than a coincidence.

### Where the outperformance actually lives

`beats_exposure_matched` compares an arm against the snapshot's own
buy-and-hold, so both sides carry the same survivorship and the clause is fair
on its own terms. Setting the same returns against a **clean** benchmark asks a
different and blunter question — would the owner have done better just holding
the index:

| period | unblocked | SPY | vs SPY | survivorship inflation |
|---|---|---|---|---|
| 2000-2006 | +137.50% | +8.68% | **+128.82pp** | **+130.06pp** |
| 2007-2013 | +75.57% | +51.34% | **+24.23pp** | **+45.46pp** |
| 2014-2019 | +85.01% | +97.97% | **−12.96pp** | +3.97pp |
| 2022-2026 | +61.67% | +64.39% | **−2.72pp** | −9.59pp |

**The outperformance appears only in the two periods whose benchmark is
survivorship-poisoned, and disappears in both periods where it is not.** In
2014-2019 and 2022-2026 — the two periods the universe has had least time to
lose members — the fully-deployed bot *lost to SPY*.

This is not a refutation of the clause result and is not offered as one. The
bot also trades a survivor-only universe, so its own return is inflated too;
the SPY column is harsher on the bot than a like-for-like comparison would be.
Neither column alone settles it. What the pair establishes is narrower and
firmer: **the (b) passes are not safe to read as evidence of an edge**, and the
follow-up the rule licenses must be scored against a benchmark that is not
built from survivors.

### What this does and does not change

- **Nothing is enabled. `max_drawdown_pct` stays at 10.0.** The unblocked arm's
  drawdown was 22.23% / 33.84% / 11.74% / 16.65% against the owner's stated
  10pp tolerance — the rail is doing the job it exists to do, and `census.py`'s
  standing rule against loosening a rail on a diagnostic's say-so holds.
- **The ambiguity §40 named is half-resolved.** "No edge found" and "no edge
  expressible" are no longer indistinguishable: the bot demonstrably *can*
  express far more when unblocked, and the shipped config was measuring a bot
  in cash. Whether what it expresses is an *edge* is now the open question, and
  the survivorship measurement says the current benchmark cannot answer it.
- **No EDGE verdict is withdrawn, re-scored or revisited.** §41's precedent
  stands. EDGE remains 0 for 13 and K stays 13; a DIAGNOSTIC spends no budget.
- **The K=14 follow-up is the owner's decision, not an automatic next step** —
  §44's precedent exactly, where §41 named its own legitimate follow-up and
  refused to take it on its own authority. When registered it should score
  against a survivorship-free benchmark, and `scripts/survivorship_check.py`
  now exists to keep that honest.

**Nothing enabled. No threshold moved. `mode: paper` unchanged.**

---

## §49 — HOW MANY INDEPENDENT BETS IS THE BOOK TAKING? (DIAGNOSTIC, 2026-08-03)

**Claim type: DIAGNOSTIC.** Decides nothing, enables nothing, spends no
multiple-comparisons budget. **K stays 13.** `scripts/breadth_census.py`, full
output in `research/breadth_2026-08-03.txt`.

### Why this, and why it has never been asked

Grinold's fundamental law says risk-adjusted return has two levers and only two:

> **IR = IC × √BR**

IC is skill. BR is **breadth** — the number of *independent* decisions.
Thirteen EDGE claims have all been attempts to raise IC. **Nothing in this
project has ever measured BR.**

And nominal breadth is not breadth. `config.yaml` lists 38 symbols, but four
are broad index ETFs, four are sector ETFs, and the rest are US large caps that
largely move together. Thirty-eight names that move as one is *one* bet.

The existing guard cannot see this: `correlation_cap` is **pairwise**
(`max_correlated: 2` at `threshold: 0.85`, entries only). It asks "do two names
I hold correlate with this one", which says nothing about the concentration of
the book as a whole. §40 found the sibling heat cap bound **zero** times.

### The measurement (daily returns, 2020-01-01 → 2026-07-10, 1,637 days)

| | configured universe | live book |
|---|---|---|
| nominal positions | 38 | 19 *(of 20 held; MA absent from the snapshot)* |
| **effective bets — entropy** | **10.50** | **6.19** |
| **effective bets — participation ratio** | **4.35** | **3.24** |
| first eigenvalue | 45.7% of variance | **53.1% of variance** |
| mean pairwise correlation | 0.421 | 0.479 |
| **IR ceiling vs an independent book** | **0.526×** | **0.571×** |

Two standard measures are reported because agreeing is weak evidence and
disagreeing is informative. They disagree here — entropy is forgiving of a long
tail of small eigenvalues, the participation ratio weights the top of the
spectrum. For risk, the participation ratio is the more conservative reading.

**Twenty open positions are between three and six independent bets, and a
single factor explains 53% of the book's variance.** The achievable information
ratio is roughly **half** what the same number of independent bets would allow —
a ceiling no amount of signal work can lift.

### The sharper finding: the correlation cap is an ETF-overlap detector

Of 703 pairs in the configured universe, **13 sit at or above the 0.85 cap —
and 11 of those 13 involve an ETF**:

| | |
|---|---|
| QQQ / XLK | 0.974 |
| SPY / DIA | 0.940 |
| SPY / QQQ | 0.935 |
| XLE / XOM | 0.932 |
| XLF / JPM | 0.908 |
| *…seven more, all with an ETF leg* | |

**Only 2 of 435 stock-vs-stock pairs (0.46%) reach the cap** — JPM/BAC at 0.879
and XOM/CVX at 0.855. On the 500-name wide snapshot, where no sector ETFs
exist, the *maximum* pairwise correlation in the whole live universe is
**0.846 — below the 0.85 threshold**, so the rail cannot bind there at all.

So the cap is not functioning as a stock-diversification rail. It is an
index-overlap detector, and §22 already recorded that index ETFs get systematic
first refusal on scarce slots because they lead `config.yaml`. The rail mostly
fires on exactly the names an arbitrary ordering bias already favours.

### Scoring the prior

The plan predicted effective breadth of **~2–3 against a nominal 20+**. The
participation ratio came in at **3.24** — inside the predicted band. The
entropy measure came in at **6.19** — roughly double it. **Scored as one of two,
and the miss is recorded rather than the hit quoted.** The qualitative claim
(the book is far less diversified than its position count suggests) held; the
specific number was too pessimistic on one of the two measures.

### Two limitations, both structural

1. **The gates and live trade different universes.** The wide snapshots are 500
   individual stocks and contain **no sector ETFs** — QQQ, DIA, IWM, XLK, XLF,
   XLE and XLV are all absent. Every wide-snapshot gate, §48 included, scored a
   universe the live bot does not trade, and vice versa. This measurement uses
   the 38-symbol snapshot precisely because it matches live.
2. **Survivorship pushes this number DOWN, not up** (§48). Survivors are
   plausibly more correlated with each other than a full cohort would be, so
   the true independence is more likely to be *understated* here than
   overstated. Stated so the bias direction is on the record.

### What this licenses

**Nothing.** It is a measurement. Widening the universe, reordering it, or
retuning `correlation_cap` are each separate pre-registered decisions — the
same rule `census.py` states about rails, for the same reason: changing a
setting because a diagnostic suggested it is the unfalsifiable move this
programme exists to prevent.

What it does provide is a *denominator*. Any future claim that proposes to
raise breadth now has a number to beat, and any claim that raises IC now has an
explicit ceiling to be judged against.

**Nothing enabled. No threshold moved. `mode: paper` unchanged. K stays 13.**

---

## §50 — THE INCUMBENT AT FULL DEPLOYMENT, vs A SURVIVORSHIP-FREE BENCHMARK (EDGE, K=14, pre-registered 2026-08-03)

**Claim type: EDGE. K=14** — the first budget spent since §44. Specs
`research/specs/s50a-d.yaml`, written and frozen **together** before any was
run. Conjunction: one clause failure in one period sinks the claim.

| spec | period | frozen sha256 |
|---|---|---|
| s50a | 2000-2006 | `b3c77bbfc22aa969…` |
| s50b | 2007-2013 | `f84207bf3f507ef9…` |
| s50c | 2014-2019 | `a0e405a759758c2e…` |
| s50d | 2022-2026 | `4cf43d93da4e46ce…` |

### What licenses this

§48's reading rule, frozen before its run and quoted exactly:

> a fresh EDGE registration re-testing the **INCUMBENT at full deployment**,
> spending budget as **K=14**. It does not license new machinery, and it does
> not license touching `max_drawdown_pct`.

§48 returned outcome (2). The owner made the call on 2026-08-03.

`risk.max_drawdown_pct: 0` **is the arm, not a proposal.** The rule barred
*touching* the shipped value; "at full deployment" requires the rail off in the
simulation. `config.yaml` still ships 10.0.

### Numbering — §50 was taken by whoever registered first

`research/specs/drafts/s50a.yaml` (volatility targeting) had been written under
that number. A draft holds nothing: *"Reserving a number for a claim nobody has
registered was the mistake; numbers are assigned at registration, and a
reservation is not a registration"* (`register_gate.py`). Same rule that
renumbered §42→§43→§44. The draft is renamed to
`research/specs/drafts/vol-targeting.yaml` with `id: draft-vol-targeting`, so
it cannot squat on the next number either.

### Single arm, following §39

Every clause is a SELF rule, so there is nothing to compare against — §39's
shape, which *"puts the BASELINE ITSELF on trial against benchmarks from its own
run"*. A second arm would invite reading this as a comparison it is not. The arm
is configurationally **identical to §48's `no_drawdown_rail`**, which makes the
reproduction check free.

| clause | rule | what it is |
|---|---|---|
| a | `deployment_at_least: 25` | the claim is about *full* deployment |
| b | `beats_buy_hold` | **survivorship-MATCHED** — both sides same survivor pool |
| c | `beats_benchmark_symbol: SPY` | **survivorship-FREE** — new rule, PR #81 |
| d | `min_trades: 30` | so (b)/(c) cannot clear on noise |

**Why both (b) and (c).** Neither is sufficient alone and they fail in opposite
directions. (b)'s benchmark is not investable; (c)'s benchmark is clean but the
*strategy* still trades survivors, so the comparison runs in its favour.

**That asymmetry is pre-registered: a FAIL on (c) is decisive — it lost with a
tailwind — and a PASS on (c) is not.**

### THE OUTCOME IS PREDICTABLE, AND THAT IS DISCLOSED

Scored from numbers **§48 already published**, plus SPY from
`scripts/survivorship_check.py`:

| period | incumbent | (b) universe B&H | (c) SPY | predicted |
|---|---|---|---|---|
| 2000-2006 | +137.50% | +138.54% ✗ | +8.68% ✓ | REJECTED |
| 2007-2013 | +75.57% | +92.48% ✗ | +51.34% ✓ | REJECTED |
| **2014-2019** | **+85.01%** | **+90.41% ✗** | **+97.97% ✗** | **REJECTED** |
| 2022-2026 | +61.67% | +45.11% ✓ | +63.52% ✗ | REJECTED |

**Every period fails at least one clause before the run.** Registered anyway,
by owner decision, for two reasons written into every spec's `prior`:

1. **Spending K is the conservative direction.** The Bonferroni count penalises
   searching; a fourteenth recorded claim makes every *future* claim face a
   stricter bar. Declining to spend it — for instance by relabelling this METHOD
   to protect the EDGE record — would make the project look *less* searched than
   it is, which is the failure this ledger exists to prevent.
2. **It turns a number in prose into a machine-checked clause.** The SPY
   comparison lived only in §48's write-up and an ad-hoc script. Now it is a
   frozen rule any later claim can use.

**2014-2019 is the load-bearing period**, named as such before the run: its
survivorship inflation is only **+3.97pp** (against +130.06pp in 2000-2006), so
both benchmarks are close to honest — and §48's numbers say the fully-deployed
incumbent **lost to the index** there. A failure in that period cannot be waved
away as an artifact of the bar.

### What would actually surprise me

Clause (a) or (d) failing, or (b) passing in 2000-2006 — each would mean the arm
did not reproduce §48 and the new benchmark fields perturbed the simulator. That
is a **reproduction failure, not a result**, and voids the claim rather than
passing it. (Pre-checked: re-running `s48d` on the new code reproduced §48
exactly, both arms.)

### Standing limits

**Drawdown is reported, not gated.** §48 measured this arm at 22.23 / 33.84 /
11.74 / 16.65% maxDD against a stated 10pp tolerance. Matched drawdown was §44's
question and was rejected there; §41's precedent separates risk from return. **A
PASS here would mean "beat both benchmarks on return", not "is deployable."**

Nothing here can enable anything. `max_drawdown_pct` stays at 10.0.

*(Measured results follow below, written after the run.)*

---

### THE MEASUREMENT (2026-08-03, judge ON, full output `research/s50_run_2026-08-03.txt`)

**VERDICT: REJECTED in all four periods. EDGE is now 0 for 14.**

| period | return | PF | maxDD | trades | deployment |
|---|---|---|---|---|---|
| 2000-2006 | +137.50% | 1.553 | 22.23% | 3,974 | 74.22% |
| 2007-2013 | +75.57% | 1.366 | 33.84% | 3,592 | 75.96% |
| 2014-2019 | +85.01% | 1.471 | 11.74% | 3,785 | 84.01% |
| 2022-2026 | +61.67% | 1.424 | 16.65% | 3,084 | 77.95% |

**The reproduction check passed on all four.** Every figure above is identical
to §48's `no_drawdown_rail` arm. The new benchmark fields did not perturb the
simulator, so the run is valid rather than void.

### The clauses, against the pre-registered prediction

| period | (a) deploy | (b) vs universe B&H | (c) vs SPY | (d) trades | verdict |
|---|---|---|---|---|---|
| 2000-2006 | ✓ | ✗ +137.50 vs +138.54 | ✓ vs +10.01 | ✓ | REJECTED |
| 2007-2013 | ✓ | ✗ +75.57 vs +92.48 | ✓ vs +51.30 | ✓ | REJECTED |
| **2014-2019** | ✓ | **✗ +85.01 vs +90.41** | **✗ vs +97.39** | ✓ | **REJECTED** |
| 2022-2026 | ✓ | ✓ +61.67 vs +45.11 | ✗ vs +63.52 | ✓ | REJECTED |

**Every clause landed exactly where the prior said it would**, including
2022-2026's (c) — pre-registered as *"the narrowest of any clause in this
claim… the one number here that could plausibly land either side"*, and it came
in at 1.85pp. That the predictions held is not a success; it is what
"foregone" meant, and it was disclosed before the run.

**The load-bearing period behaved as named.** 2014-2019 carries only +3.97pp of
survivorship inflation, so both its benchmarks are close to honest — and the
fully-deployed incumbent **lost to the index there, on both bars.** That was
called out in `s50c.yaml` before the run precisely so the emphasis could not be
chosen afterwards.

### THE ONE THING THE PRIOR DID NOT ANTICIPATE

Drawdown was **reported, not gated** — and the reported column says something
the return clauses do not:

| period | bot maxDD | **SPY maxDD** | bot return / maxDD | SPY return / maxDD |
|---|---|---|---|---|
| 2000-2006 | 22.23% | **47.52%** | 6.18 | 0.21 |
| 2007-2013 | 33.84% | **55.19%** | 2.23 | 0.93 |
| 2014-2019 | 11.74% | **19.35%** | 7.24 | 5.03 |
| 2022-2026 | 16.65% | **24.50%** | 3.70 | 2.59 |

**The incumbent drew down less than SPY in all four periods**, and has a better
return-to-drawdown ratio in all four — including 2014-2019, where it lost on
return.

Three things must be said about that, in this order:

1. **It does not rescue the claim.** §50 was pre-registered on return, and it
   was REJECTED on return. A risk-adjusted reading discovered *after* seeing
   the numbers is exactly the move this ledger exists to prevent — §33 RUN 1 is
   the standing reminder of what happens when a metric is chosen post hoc.
2. **It licenses nothing.** No verdict changes, nothing is enabled, and this is
   not a candidate until somebody writes and freezes one.
3. **It is worth recording anyway**, because drawdown was made visible on
   purpose. Whether a lower-drawdown, lower-return profile constitutes an edge
   is a *different question* from the one asked here, it needs a risk-adjusted
   pass mark fixed in advance, and §44 already showed how hard that conjunction
   is to clear. Anyone pursuing it starts from a fresh registration at K=15 —
   not from this section.

### What §50 settles

- **The incumbent does not beat the survivor-only universe it trades** in three
  of four periods, and does not beat the index in two — including the period
  where the benchmark is least contaminated.
- **A FAIL on (c) is decisive**, per the pre-registered asymmetry: the strategy
  still trades survivors, so it lost *with a tailwind*.
- The survivorship correction is no longer a number in prose. It is
  `beats_benchmark_symbol`, frozen inside the harness, available to every later
  claim.

**Nothing enabled. `max_drawdown_pct` stays at 10.0. `mode: paper` unchanged.
EDGE 0 for 14; K is now 14.**

---

## §51 — IS THE RISK-ADJUSTED EDGE REAL, OR SURVIVORSHIP? (EDGE, K=15, pre-registered 2026-08-03)

**Claim type: EDGE. K=15.** Specs `research/specs/s51a-c.yaml`, written and
frozen **together** before any was run. Conjunction — one clause failure in one
universe sinks the claim.

| spec | snapshot | symbols | frozen sha256 |
|---|---|---|---|
| s51a | `bars_2020-01-01_2026-07-10` | **38 — the live universe** | `d66a71ccdd4ac25f…` |
| s51b | `bars_..._u60` | 68 | `0bde3c957c4beab3…` |
| s51c | `bars_wide_2020-01-01_2026-07-10` | 500 | `4203e070c3600ea9…` |

### What licenses this

§50's results recorded an observation its own prior had not anticipated — the
incumbent drew down **less than SPY in all four periods**, with a better
return-per-unit-of-drawdown in all four, including 2014-2019 where it *lost* on
return — and then refused to act on it:

> it **licenses nothing**… whether a lower-drawdown, lower-return profile
> constitutes an edge is a *different question*, it needs a risk-adjusted pass
> mark fixed in advance, and anyone pursuing it starts from a fresh
> registration at **K=15**.

Owner call, 2026-08-03.

### THE OBSERVATION IS POST-HOC. That is the central problem.

It was read off §50's output. Testing it on the four periods that produced it
would be testing a hypothesis on the data that generated it — **and the outcome
is already computable: it passes 4 of 4.** That would hand this project its
first EDGE pass out of a circular claim, which is precisely the §33 RUN 1 story.

### And the observed margin tracks survivorship almost exactly

| period | bot return/maxDD | SPY return/maxDD | ratio | survivorship inflation |
|---|---|---|---|---|
| 2000-2006 | 6.19 | 0.21 | **29.36×** | +130.06pp |
| 2007-2013 | 2.23 | 0.93 | **2.40×** | +45.46pp |
| 2014-2019 | 7.24 | 5.03 | **1.44×** | +3.97pp |
| 2022-2026 | 3.70 | 2.59 | **1.43×** | −9.59pp |

The advantage collapses from 29× to 1.43× as the inflation §48 measured goes to
zero — what a survivorship story predicts and a real-edge story does not. **But
it collapses *to* ~1.43×, not to 1.0×**, and both clean periods land in the same
place. That residual is the open question.

### So it is scored on data no registration has ever touched

Each of the four wide periods has been scored **six times** (§39, §41, §43, §44,
§48, §50). These three snapshots have **never appeared in
`research/registrations.jsonl`**. s51a is additionally the 38-symbol universe the
live bot actually trades — §49 established the wide snapshots contain no sector
ETFs and are therefore *not* the live universe.

**The outcome was not computed before freezing.** That is what distinguishes §51
from §50, which was deliberately registered as foregone. Specs written, hashed
and committed before any number from these snapshots was looked at.

### The claim

Single arm following §39, configurationally **identical to §48's
`no_drawdown_rail` and §50's arm** — the same bot at full deployment, measured a
third way. `risk.max_drawdown_pct: 0` is the arm, not a proposal; `config.yaml`
still ships 10.0.

| clause | rule |
|---|---|
| a | `deployment_at_least: 25` |
| b | `min_trades: 30` |
| c | `beats_benchmark_risk_adjusted: SPY` — *levered to SPY's own drawdown, does it beat SPY?* |

**`beats_benchmark_symbol` is deliberately absent.** §50 already rejected
absolute return against SPY; including it would re-run §50 under a new number.

### The prior

**Genuinely uncertain — the first claim here whose answer I do not know before
the run.** Honest expectation REJECTED at roughly 60/40, on a base rate of EDGE
0 for 14, stated as a weak prior rather than dressed up in either direction.

A failure of (a) or (b) would be a mechanical or data problem, not evidence
about risk-adjusted performance, and voids that arm rather than rejecting the
claim on its merits.

### What a PASS would and would not mean

- **NOT more money.** §50 established this exact arm loses to SPY on absolute
  return in both clean periods. A pass means choosing lower returns for lower
  drawdown — a defensible preference, not an edge in the sense the other
  thirteen claims used the word.
- **NOT survivorship-free.** The strategy still trades a survivor-selected
  universe, so §50's asymmetry holds: **a FAIL is decisive, a PASS is not.**
- **NOT replication.** One hold-out promotes a post-hoc observation to "survived
  one out-of-sample test", nothing more. It licenses no config change and no
  further claim without its own registration.
- The risk-matched comparison **assumes costless leverage**, which is false: a
  long equity book cannot be levered for free, faces margin calls precisely at
  drawdown troughs, and levering multiplies the tail beyond what maxDD measures.

**Hold-out caveat:** the 2020-2026 window overlaps the mined 2022-2026, so this
is out-of-sample for the *universe* and the *question*, not the calendar. The
genuinely unmined stretch is 2020-2021, the COVID crash. s51c is the weakest arm
— it shares both its universe and half its window with s48d/s50d — and is
included rather than dropped because excluding a conjunction's least favourable
arm would be arm selection.

*(Measured results follow below, written after the run.)*

---

### THE MEASUREMENT (2026-08-03, judge ON, full output `research/s51_run_2026-08-03.txt`)

**VERDICT: PASS in all three universes. This is the first EDGE pass in fifteen
claims.** The verdict stands and is not rescored — §41's precedent, and the
whole point of freezing a pass mark. Everything below is what the number
*means*, not a revision of whether it was cleared.

| spec | universe | return | PF | maxDD | trades | deploy | clause (c) |
|---|---|---|---|---|---|---|---|
| s51a | 38 | +191.26% | 1.690 | 11.19% | 2,114 | 74.52% | +576.54% levered vs SPY +157.43% |
| s51b | 68 | +136.94% | 1.469 | 9.90% | 3,087 | 77.55% | +466.14% levered vs +157.43% |
| s51c | 500 | +98.39% | 1.418 | 17.05% | 4,489 | 78.38% | +194.62% levered vs +157.43% |

Note before anything else: on **absolute** return only s51a beats SPY
(+191.26% vs +157.43%). s51b and s51c both *lose* to the index, exactly as §50
found. Nothing here contradicts §50.

### AND THEN I TRIED TO BREAK IT. Three findings, two of them pre-registered.

**1. s51a carries the worst survivorship in the entire project: +200.28pp.**

| hold-out universe | SPY | equal-weight | inflation |
|---|---|---|---|
| **38 (s51a)** | +154.94% | **+355.23%** | **+200.28pp** |
| 68 (s51b) | +154.94% | +246.06% | +91.11pp |
| 500 (s51c) | +154.94% | +140.26% | **−14.68pp** |

Worse than 2000-2006's +130.06pp, which §48 called enormous. The 38 names are
in `config.yaml` **because they are today's winners** — NVDA, AVGO, LLY and the
rest were selected by knowing how 2020-2026 turned out.

**`s51a.yaml` called this arm "the most independent of the mined data and the
most relevant to deployment". The relevance is right; the independence claim
was wrong, and it is my error.** Unmined is not unbiased. s51c, which the spec
called the weakest arm, is on this axis the only clean one.

**2. The two drawdowns being compared are different events.** SPY's 33.72%
maxDD is the COVID crash, **2020-02-19 → 2020-03-23**. Every arm's own maxDD is
the 2022 bear market — peak Dec 2021 / Jan 2022, trough **2022-09-30**. The
clause matches a *number*, not a *risk*. This is the pre-registered failure mode
firing verbatim: *"maxDD is a single-path statistic… one realised sequence, not
a distribution."*

**3. On s51a the low drawdown is a startup artifact — but on s51c it is not.**
Through the COVID window:

| arm | bot equity move | SPY |
|---|---|---|
| s51a (38) | **−0.99%** | −33.72% |
| s51c (500) | **−10.90%** | −33.72% |

s51a's run was seven weeks old and barely deployed; it sat the crash out for
reasons that have nothing to do with strategy, and that is what sets its 11.19%
maxDD against SPY's 33.72%. **s51c was genuinely deployed and fell a third as
far as the index.** That part is not an artifact — and it is *not what the claim
tested*, so it licenses nothing and would need its own registration.

### What §51 actually establishes

**The pre-registered asymmetry is what saves this from being a false
discovery.** Every spec said, before the run:

> the STRATEGY still trades a survivor-selected universe… **A FAIL is DECISIVE
> (it lost with a tailwind); a PASS is NOT.**

A PASS arrived, and the caveat that made it non-decisive was already frozen in
place. That is the machinery working exactly as designed — and it is the reason
this section reads as "PASS, and here is why it does not mean what it looks
like" rather than as "edge found".

- **It is not more money.** s51b and s51c lose to SPY on absolute return.
- **It is not survivorship-free.** The strategy trades survivors, and on s51a it
  trades the most selected universe in the project.
- **It is not replication.** One hold-out, and the comparison rests on a single
  crash the bot partly sat out.
- **The leverage framing is a normalisation, not a plan.** "+576.54% levered to
  SPY's drawdown" would require ~3× on a long equity book, financed, through
  margin calls that arrive at troughs.

### Standing

**Nothing enabled. `max_drawdown_pct` stays at 10.0. `mode: paper` unchanged.**
No config value moves on a PROVISIONAL PASS, and `run_gate.py` says so itself:
*"a recommendation, not an action."*

**The EDGE record is now 1 PASS in 15 claims** — and the one pass is this,
carrying the three qualifications above. Anyone quoting "first edge found"
without them is quoting half a sentence.

The honest next step, if the owner wants one, is a **registered replication on a
universe that is not selected on the outcome** — a point-in-time index
membership snapshot, which this repo does not currently have and cannot build
from its existing data.

---

## §52 CANDIDATE (not registered) — survivorship-free replication is BLOCKED, and the EDGE budget is FROZEN

**2026-08-03.** §51's write-up named the honest next step: *"a registered
replication on a universe that is not selected on the outcome — point-in-time
index membership, which this repo does not currently have and cannot build from
its existing data."*

That was a guess. `scripts/probe_delisted_coverage.py` measured it, on the §46
precedent: probe before writing a claim that depends on the data, and **exit
non-zero with a refusal** if the data cannot carry it.

### The probe REFUSED, for two separate reasons

| ticker | fate | rows | pre-delisting | |
|---|---|---|---|---|
| SIVB | SVB Financial, seized Mar 2023 | **0** | 0 | missing |
| FRC | First Republic, seized May 2023 | **0** | 0 | missing |
| TWTR | taken private Oct 2022 | **0** | 0 | missing |
| ATVI | acquired Oct 2023 | **0** | 0 | missing |
| **SBNY** | Signature Bank, seized **2023-03-12** | **475** | **0** | **TICKER REUSE** |
| *AAPL (control)* | alive | 1,637 | — | fetch path works |

**Check 1 — coverage: FAIL.** Four of five delisted names return nothing at all.
A point-in-time universe needs exactly those companies; without their prices the
losers are simply absent, which *is* the survivorship bias.

**Check 2 — ticker reuse: FAIL, and this is the one that matters.** SBNY returns
475 bars — **all of them starting 2024-08-15**, seventeen months after the bank
was seized, on exchange PNK, still labelled *"Signature Bank"*.

A builder that asked only *"did we get bars for this delisted name?"* **passes**,
splices in an OTC successor, and produces a dataset that never observes the
collapse while carrying the name of the company that collapsed. **That is not
survivorship bias — it is fabricated history wearing the costume of the fix,
and it would be worse than doing nothing.**

A control is included so that "everything returned zero" cannot be misread as
vendor coverage when it is really a broken network. The control returned 1,637
rows, so the refusal is a measurement.

### THE FREEZE — pre-committed, and mechanical

`scripts/register_gate.py` now **refuses `claim: EDGE` on any snapshot under
`data/snapshots/`.**

Every snapshot there is built by `build_snapshot.py` from
`index_constituents()`, which reads **current** Wikipedia membership — so all of
them are survivor-selected, not just the four §48 measured. §48 sized the bias
at up to **+130.06pp**; §51 showed it was large enough to explain this project's
only EDGE pass (**+200.28pp** on the 38-name universe).

Registering further EDGE claims on this data **buys more chances at a false
positive without buying information**. That is §33's argument — *"continuing to
hunt arms is simply buying more chances for a false positive"* — applied to the
**data** rather than to the selection procedure.

| | |
|---|---|
| **Still allowed** | DIAGNOSTIC, METHOD, CAPACITY — none claims an edge, and §41's capacity question is about deployment rather than returns |
| **Lifts the freeze** | any source that passes `probe_delisted_coverage.py` |
| **Override** | `--override-freeze "<reason>"`, and the reason is written into `registrations.jsonl` beside the claim |

The override exists because **a wall gets edited out of the script; a speed bump
with an audit trail does not.** A reason under twenty characters is refused, so
the row a future reader finds contains an argument rather than a shrug.

This is the §36 pattern: *"today that discipline is a habit backed by a git
commit. Here it becomes mechanical."*

### Blocked on the owner

**A data vendor with verified delisted history** — Norgate, Sharadar (Nasdaq
Data Link), Polygon, or CRSP. Same shape as §46's FMP decision, and not mine to
make. The probe is the acceptance test: point `--source` at a candidate and it
answers on the same two questions.

Until then the freeze holds, and **that is the intended outcome rather than a
side effect.**

**Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE stands at
1 pass in 15, and that one pass carries §51's survivorship qualification.**

## §53 — DOES THE 130/30 CONFIGURATION BEHAVE? — REJECTED ×3 of 4, 2026-08-05

**DIAGNOSTIC, K unchanged at 15.** Four periods, three arms each, frozen
together before any ran (`research/specs/s53a-d.yaml`, run record
`research/s53_run_2026-08-05.txt`).

**OUTCOME (1) OF THE PRE-COMMITTED READING RULE: the 130/30 configuration is
refuted on this data. `xsmom` is NOT enabled and Phase 3 stops here.** The rule
said two or more clause-(b) failures decide it; three of four failed.

| period | baseline | xsmom_long_only | **xsmom_130_30** | SPY | (b) |
|---|---|---|---|---|---|
| 2000–2006 | −7.91% | −7.91% | **−7.91%** | +10.01% | FAIL |
| 2007–2013 | +2.85% | +1.47% | **+3.73%** | +51.30% | FAIL |
| 2014–2019 | +95.89% | +118.36% | **+79.73%** | +97.39% | FAIL |
| 2022–2026 | +105.84% | +139.50% | **+81.06%** | +63.52% | PASS |

### The finding is not the failure count — it is the middle column

The third arm is why this was not a two-arm run, and it earns its wall time. In
**both periods where the short leg actually traded**, adding it made the book
worse than the same strategy long-only:

| | long_only | 130/30 | short leg cost |
|---|---|---|---|
| 2014–2019 | +118.36% | +79.73% | **−38.63pp** |
| 2022–2026 | +139.50% | +81.06% | **−58.44pp** |

A two-arm run would have shown 130/30 beating the baseline in 2022–2026
(+81.06% vs +105.84% — actually losing) and left the attribution unresolved.
With the middle arm there is nothing to resolve: `xsmom`'s **long** leg is what
moved those periods, and the short leg subtracted from it.

**And it subtracted while being handed a cost-free borrow.** Divergence #16 —
no stock-loan fee is charged, in this simulator or in paper. The short leg lost
to its own long-only arm *before* paying the cost a real one would.

### Two of the three failures are degenerate, and saying so is the point

2000–2006 and 2007–2013 did not test the configuration. The drawdown rail
blocked **97–99% of all signals** in both (26,380 of 27,124; 27,791 of 29,037),
deployment sat at 2.21% and 7.44%, and the short leg opened **zero** positions in
the first period and **two** in the second. All three arms are byte-identical in
2000–2006 for that reason.

The frozen rule counts them as failures and the verdict stands — a rule that is
re-read after the numbers arrive is not a rule. But the honest weight is on
2014–2019 and 2022–2026, which is where the configuration could express itself,
and both of those fail on the middle column rather than on the benchmark.

### The net-exposure band was never enabled — so this does not test a *banded* 130/30

`risk.net_exposure_pct: {min: 80, max: 120}` is **commented out** in
`config.yaml`. The 130/30 design called that band "what makes 130/30 a fact
rather than an intention", and it is not in force. The consequence is measured:

- `net_exposure` appears in **no** arm's block census — the rail never fired.
- Net exposure reached **−15.89%** in 2014–2019 and **−1.34%** in 2022–2026. The
  book was, at moments, net SHORT. That is not a 130/30 book.
- Gross did reach the premise: **134.60%** max in 2022–2026, **163.84%** in
  2014–2019.

So what is refuted is *`xsmom`'s short leg added to the book without a net
constraint*. A banded configuration is a **different** experiment and would need
its own pre-registration — it cannot be read out of these four runs, and
outcome (2) is not available by arguing the band would have changed them.

### What this does NOT say

- **Not that shorting cannot work.** Survivorship runs against the short leg
  here: every wide snapshot is built from today's index membership, so the names
  that fell and never recovered — precisely what a momentum short would have
  held — are deleted. §48 sized the long-side flattery at up to +130.06pp and
  §51 at +200.28pp. Outcome (1) is a decision to stop spending on this
  direction, not a proof about the direction.
- **Not an EDGE claim.** §52's freeze is untouched and K stays 15.
- **Not comparable with §43, §48, §50 or §51.** Those ran before divergence #17
  was closed, when the simulator let every strategy enter every symbol in the
  snapshot. On 2022–2026 that difference alone moves deployment 4.09% → 75.18%.
  A table putting these rows beside those compares two different simulators.
- **Nothing about `reclaim`.** It is disabled in all twelve arms, so the
  collision with the short leg (23 of `xsmom`'s 30 names sit in `reclaim`'s
  universe) is untested. **CLOSED by §54 (2026-08-05)**: the collision cannot
  occur — ownership routing, not `direction_conflict`, is what prevents it —
  and the pairing nevertheless costs 18-32pp through the sign-blind
  `sector_concentration` cap.

### What was kept

Phase 1 and 2's safety work stands regardless — the design pre-committed to
that: *"if Phase 3's diagnostic says the long book cannot deploy near 130%, stop
there: the ratio was the premise, and Phase 1's safety fixes are worth keeping
anyway."* The rails, the signed simulator, divergence #16 and #17, and the
direction-aware counterfactual are all independent of the verdict.

**Nothing enabled. No threshold moved. `mode: paper` unchanged. `xsmom.enabled`
stays `false`.**

---

## §54 — WHAT DOES RUNNING `reclaim` ALONGSIDE THE 130/30 BOOK COST? (DIAGNOSTIC, K=15, pre-registered 2026-08-05)

**Claim type: DIAGNOSTIC. K stays 15 — no Bonferroni budget is spent.** Specs
`research/specs/s54a-d.yaml`, written and frozen together before any ran; they
differ only in `id`, `snapshot` and `title`, asserted by
`tests/test_s54_arms.py`. Run: `research/s54_run_2026-08-05.txt`.

This closes the last item §53 left open — its own third failure mode:
*"`reclaim` AND THE SHORT LEG COMPETE FOR THE SAME NAMES, and reclaim is
DISABLED in every arm here, so this diagnostic does not test the interaction."*

### The finding that arrived before the run, and changed it

§54 was designed to read `census["blocked"]["direction_conflict"]` as its
headline number. Building the tests first — the order the plan chose precisely
so a defect could not be found afterwards — showed **that number is
structurally zero**, and the rail's own comment in `risk.py` was wrong about
why it exists:

> It is the ONLY thing preventing the conflict, and whichever strategy reaches
> the name first takes it while the other is **refused here**.

The other strategy is not refused there. **It is never consulted.** A held
symbol routes to `pos["owner"]` and the branch `continue`s
(`main.py:1615-1652`, `backtest.py:1157-1181`); the entry loop `break`s on the
first strategy to claim a flat name; and a queued order outliving its bar is
dropped as `already_held` above `pure_checks`. **Ownership routing** is what
prevents a long and a short in the same name. `direction_conflict` is a
last-gate backstop for a caller outside the cycle, and a backtest census can
never count it.

Reading a zero there as "the collision is rare on this data" would have been a
finding about nothing. The comment is corrected in `risk.py`; the behaviour is
pinned by `tests/test_reclaim_short_collision.py` with mutation proofs.

So the spec was rewritten to measure the interaction that does exist —
**contention** — and kept the old headline as a falsifiable prediction.

### The prediction, checked first

`prior` committed: `direction_conflict` will be zero in every arm of all four
periods, and a non-zero count means the analysis is wrong and outranks
everything else here. **16 arms checked. It fired 0 times. The prediction held.**

Two other rails were never reached either: `already_held` (0 — redundant given
the same routing) and `net_exposure` (0 — `risk.net_exposure_pct` is still
commented out, so the 130/30 band has never armed, carried over unresolved
from §53).

### The result — OUTCOME (1), the pairing costs something

| period | baseline | `reclaim_only` | `xsmom_130_30` | **`both`** | best single | delta |
|---|---|---|---|---|---|---|
| 2000–06 | −7.91% | −7.91% | −7.91% | **−7.91%** | −7.91% | +0.00pp *(degenerate)* |
| 2007–13 | +2.85% | +2.85% | +3.73% | **+3.73%** | +3.73% | +0.00pp |
| 2014–19 | +95.89% | +95.27% | +79.73% | **+63.45%** | +95.27% | **−31.82pp** |
| 2022–26 | +105.84% | +108.00% | +81.06% | **+89.57%** | +108.00% | **−18.43pp** |

`both` is worse than the better single arm in **two of four** periods →
outcome (1) of the frozen rule: *running the two together costs something
beyond what either costs alone.* Recorded as a constraint — **neither strategy
is enabled alongside the other without a new registration.**

2000–06 is degenerate: all four arms are byte-identical, the drawdown rail
blocking 19,411 of 19,984 signals. In 2007–13 `reclaim_only` is identical to
`baseline` — reclaim contributed **zero trades** there, so `both` equals
`xsmom_130_30` exactly.

### Which rail did the displacing

The rule requires reading the census. `sector_concentration` blocks:

| period | baseline | `reclaim_only` | `xsmom_130_30` | **`both`** |
|---|---|---|---|---|
| 2014–19 | 627 | 680 | 1037 | **1074** |
| 2022–26 | 647 | 657 | 849 | **923** |

It is the only rail higher in `both` than in *both* single arms across both
decisive periods, and it is the mechanism the `prior` put its 30% on, named in
advance: *"it is sign-blind, the cap is 3, and the overlap is concentrated (5
names in Technology, 4 in Consumer Defensive), so a short leg can fill a sector
and lock reclaim out of it."*

`sector_open_count` is `sum(1 for s in positions if ...)` — sign never enters —
so a short occupies a sector slot exactly as a long does. **Pinned, not fixed**
(`tests/test_reclaim.py`): there is a real argument it is wrong, since the
rail's own message says "co-moving names are one bet" and a short and a long in
the same sector are partly offsetting. Changing a shipped rail needs its own
registration, and changing it after this run would void these numbers.

Every other rail — drawdown, heat, regime_exposure, zero_qty — blocks *less* in
`both` than in `xsmom_130_30`. That is what a displaced book looks like: the
entries were never proposed, so they never reached those rails.

### What this does NOT license

- **Nothing is enabled.** `reclaim.enabled` and `xsmom.enabled` both stay
  `false`; `mode: paper` unchanged. A DIAGNOSTIC that licensed a config change
  would have been mistyped.
- **§53 is not reopened.** `both` cleared clause (b) in 2022–26 and that
  licenses nothing; §53's refutation of 130/30 was decided under its own frozen
  rule.
- **`reclaim_only` beating SPY in 2022–26 (+108.00% vs +63.52%) is not a
  result.** Reclaim's premise is a name that fell a long way and came back, and
  a snapshot built from today's index membership contains only the names that
  came back. Survivorship runs *directly along* this strategy's thesis, which
  is worse than the usual case. §52's EDGE freeze stands.
- **Divergence #16 still applies** — no borrow cost is charged, so every
  short-leg return here is overstated.

### A correction to the frozen spec

Failure mode 4 claimed the simulator never runs the correlation cap, so §54
could say nothing about it. **That is wrong.** `simulate_ensemble` reimplements
the heat, correlation and daily-cap call sites inline (`backtest.py:1026`,
`:1039`, `:937`) through the same `risk.*` helpers, rather than calling
`pre_trade_checks`. The census does measure the correlation cap, and it shows
the same direction-blind displacement, smaller: 72 / 77 → **90** in 2014–19.
The spec cannot be edited — a verdict exists and re-registration is refused,
which is the apparatus working — so the correction lives here and in the run
file. Full reasoning in `research/s54_run_2026-08-05.txt`.

**Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE stands at
1 pass in 15; K stays 15.**

## §55 — THE REFEREE GETS A SHARPER INSTRUMENT (2026-08-06, tooling — not a claim)

Same status as §36: this changes how a verdict is *measured*, so it is recorded
here and registers nothing. No spec, no `bonferroni_k`, no row in
`verdicts.jsonl`. **EDGE stands at 1 pass in 15; K stays 15.**

### The defect, and it was written down in this module from the start

`src/significance.py` has always named it, at lines 27-33:

> Baseline and candidate run on the SAME bars, so their trades are dependent. We
> resample them independently, which overstates the variance of the difference…
> That bias is in the safe direction for a gate.

"Safe direction" was the right call and an incomplete one, because nobody had
measured what it cost. §23 is where the price shows up. Its candidate trades are
a strict **subset** of the baseline's — 128 of 179 — so every shared trade sits
in both arms and its idiosyncratic P&L ought to cancel out of the difference.
Under independent resampling it does not cancel. It is counted twice.

What §23 was actually asking, in arithmetic the independent test cannot see:

    179 x $14.91  −  128 x $31.78   ⇒   the filter REMOVED 51 trades
                                        averaging **−$27.43**
                                        and KEPT 128 averaging **+$31.78**

That is a question about two **disjoint** samples. It was scored as a question
about two overlapping ones, and came back INCONCLUSIVE at P(better) = 85.8%.

### What is now in the box

| | |
|---|---|
| `significance.compare_paired` | common random numbers — ONE moving-block index draw per replicate, applied to both arms, so shared variance cancels where it is genuinely shared |
| `significance.disjoint_report` | the nested form: when the candidate really is a pure filter, KEPT vs REMOVED, which is the sharpest available reading |
| `significance.trade_keys` | `(entry_ts, symbol)` — **time first**, because the union is sorted on it and a moving block over ticker-major order would cluster by AAPL instead of by market |
| `gatespec` / `run_gate` | clause rules `significantly_better_paired`, `not_worse_paired`, and `no_interior_optimum` |

`compare()` is **not modified**. Every §1-§54 number still reproduces, pinned by
`tests/data/significance_golden.json`, which was generated from the unmodified
function *before* any of this was written and committed in the same PR.

### The measurement that changed the design — the first version was broken

Coverage was run against a **true null** (removal by coin flip, so the effect is
exactly zero), 600 seeds, before the estimator was believed:

| interval | block | two-sided rejection | nominal |
|---|---|---|---|
| percentile | sqrt(n) | **0.0750** | 0.0500 — OVER-REJECTS |
| **basic (reverse-percentile)** | **sqrt(n)** | **0.0500** | **nominal** |
| percentile | 1 | 0.0600 | |
| basic | 1 | 0.0350 | over-conservative |

The first implementation used the percentile interval, which is what `compare()`
uses, and it over-rejected by ~1.5x. **A more powerful test that does not cover
is not sharper, it is broken** — it would launder noise into verdicts, which is
the one failure this whole programme exists to prevent. The basic interval
reflects the bootstrap distribution about the observed statistic and fixes it.

Why `compare()` does not need the same correction and is not getting it: its
independent resampling is so conservative that it rejected **0 of 120** true
nulls. That bias swamps the percentile bias, in the safe direction. Removing the
independence bias is exactly what *exposes* the percentile one.

### What the pairing buys — power, measured on the same seeds and the same alpha

| removed-trade shift | `compare` | `compare_paired` |
|---|---|---|
| 0 (true null) | 0.000 | 0.035 |
| 40 | 0.000 | 0.275 |
| 80 | 0.025 | **0.645** |
| 120 | 0.160 | **0.905** |

At a shift of 80 the incumbent finds 1 real effect in 40 and the paired test
finds two in three. That gap is the whole of §55.

**The honest limit, and it is not small.** The pairing buys power in proportion
to how much variance the two arms actually SHARE. Measured: with a realistic
filter it narrows the interval ~2.1x; with an *oracle* filter — one that removes
the worst trades by looking at their outcome — it buys **nothing at all**,
because there is no shared variance left to cancel. No real filter is in the
oracle case, and testing against one would have flattered the method.

### `no_interior_optimum` — §23's own conclusion, finally executed

§23 wrote this down and it then stayed prose for thirty-one sections:

> Where a grid is available, **check monotonicity**: a lone interior optimum is
> weak evidence regardless of how good the winning cell looks.

Its profit factor across the threshold grid ran **1.955 → 2.898 → 2.240 →
1.705** — peaking at the second of four arms and ending *below* baseline. If
"more volume confirmation means better trades" were a mechanism, asking for more
confirmation should not reverse it.

The rule fails only a **strictly interior** optimum. Flat responses, monotone
ones and optima at either END all pass: an end optimum says the grid failed to
bracket the effect, which is a reason to widen the grid, not evidence of
fitting. Making it a monotonicity requirement — the obvious name — would have
been the wrong rule. `better: high|low` is validated rather than defaulted,
because an interior *peak* is the tell for profit factor and an interior
*trough* is the tell for drawdown, and a silent default reads half the metrics
backwards.

Acceptance test: §23's four real arms are replayed through it and it must FAIL.
A rule that cannot catch the case it was written for is decoration.

### What this does NOT do, stated so it cannot be spun later

- **It does not reopen §23.** Re-scoring it under a sharper instrument would be
  a fresh EDGE claim, and §52 froze those on every snapshot in `data/snapshots/`.
  §55 buys the instrument. It does not buy the right to use it.
- **It withdraws no verdict.** The fifteen rejections stand, exactly as §41 set
  the precedent when its own simulator finding could have reopened ten.
- **It is not a lower bar.** Coverage is nominal, measured, and the clause that
  proves it is in the suite.
- **It cannot help the biggest problem.** Survivorship (+130.06pp at §48,
  +200.28pp at §51) is a bias in the DATA. A better estimator measures a
  poisoned quantity more precisely.

### A harness finding, recorded because it produced a false result first

The first mutation run reported the **control as CAUGHT**, which should be
impossible — C1 edits a docstring nothing reads. The cause was not the control.
M10 changes `len(order) >= 3` to `>= 2`: **the same number of bytes**. Python
invalidates a `.pyc` on (source mtime, source size), and restoring the file
inside the same one-second tick with an unchanged size left both fields matching
the mutant. Every run after M10 executed **mutated bytecode** while the md5
check — which reads the `.py` — reported "restored".

So the harness now purges `__pycache__` on both sides of every mutation. Same
family as Phase 4's finding that a test which HANGS is worse than one that
fails: **the md5 proof is on the source, and the interpreter does not have to
agree with it.** A harness whose own residue fails the control is reporting on
itself.

Final run: **M1-M11 CAUGHT, C1 SURVIVED.** M1 is the one that matters — it
reverts the interval to the percentile form and the coverage test catches it,
which is what makes the 0.0500 above a measurement rather than a claim.

### State

2,077 tests. `min_rvol` still unset; `base.rvol()` and `risk.rvol_blocked()`
remain dormant tested capability. Nothing enabled, no threshold moved,
`mode: paper` unchanged.

## §56 — A UNIVERSE WITH NOTHING THAT LEFT (2026-08-09, tooling — not a claim)

Same status as §36 and §55: this changes what a verdict can be measured ON, so
it is recorded here and registers nothing. **EDGE stands at 1 pass in 15; K
stays 15.**

### The question §52 left open

§52 froze the EDGE budget because every snapshot in `data/snapshots/` is built
by `index_constituents()` from **current** Wikipedia membership, so the
companies that failed are absent by construction. §48 sized the inflation at
+130.06pp and §51 showed it was large enough to explain this project's only
EDGE pass, at +200.28pp. `probe_delisted_coverage.py` then asked whether the
losers could be bought back and REFUSED: yfinance serves no pre-delisting
history for SIVB, FRC, TWTR or ATVI, and returns SBNY bars that all postdate
the bank's seizure by seventeen months.

That closed one route. It did not close the other: **instead of adding back the
losers, find a universe that never had any.**

### The candidate, and what the probe can and cannot say

The Select Sector SPDR suite is a GICS partition — chosen by *construction*,
not by performance. `scripts/probe_etf_universe_survivorship.py` measures
whether that story holds in the data, and is explicit that one part of it
cannot be measured at all:

| | |
|---|---|
| **CHECKED** | each fund's history begins at its declared inception, runs unbroken to the present, and contains no hole longer than any legitimate market closure |
| **NOT CHECKED** | that the enumeration is COMPLETE. "No Select Sector SPDR was ever launched and closed" is an external sourced fact; prices cannot prove a negative. The list is a DECLARED ASSUMPTION and `report()` prints it as one |

**The negative control is what makes the probe a probe.** One predicate,
`assess()`, is applied to the fifteen candidates AND to four symbols the sibling
probe already measured dead. Every candidate must pass it and every dead symbol
must fail it. A predicate too weak to see death cannot certify anything, and the
probe refuses on that alone no matter how clean the fifteen look.

### The result: PASS, and one number worth pausing on

    control AAPL: 8436 rows
    check 1  continuous from inception : PASS
    check 2  detects the known dead    : PASS

**Every one of the fifteen funds returned history beginning EXACTLY on its
declared inception date** — the nine originals all on 1998-12-22, XLRE on
2015-10-08, XLC on 2018-06-19, SPY 1993-01-29, DIA 1998-01-20, QQQ 1999-03-10,
IWM 2000-05-26. Those dates were written into the probe from fund documentation
*before* the fetch. Fifteen independent dates matching to the day is not proof
of completeness, but it is real corroboration that the declared list describes
the funds that actually exist.

And the control fired exactly as designed: **SBNY returns 475 recent,
continuous, plausible-looking bars and was caught as 7,450 days late.** A
predicate that called SBNY healthy would have certified a seized bank.

### `data/pit/`, and why the path alone would be dishonest

`register_gate.freeze_violation` blocks `claim: EDGE` by matching the prefix
`data/snapshots/`. That is a **proxy** for "survivor-selected", good only
because everything under that directory is built from current membership — and
it means a snapshot written anywhere else clears the freeze with no override
and no argument.

Moving a file to dodge a control would be gaming it. So the certified snapshots
go to `data/pit/`, and `tests/test_pit_snapshot_requires_probe.py` fails if any
file there is not covered by a committed probe record reporting PASS, or
contains a symbol no passing probe examined. **Verified by removing the record:
five tests go red.** Without that test this whole section is a path trick.

Four snapshots on the §43/§48/§50 period boundaries, so the numbers are
comparable with the existing four-period family. The universe legitimately
**grows 9 → 11**: XLRE and XLC did not exist earlier, and `simulate_ensemble`
handles a symbol appearing mid-history natively. That growth *is* the
point-in-time property, not a defect in the data.

### Three things measured on the way, one of them a correction to myself

**1. Seven of the fifteen would never have traded.** `strategies.universe_for`
defaults to `cfg["symbols"]`, which lists only eight of them. XLI, XLY, XLP,
XLU, XLB, XLRE and XLC would be loaded, priced and marked to market, and never
entered — no error, no log line, visible only as a smaller signal denominator.
Every §57 arm therefore carries `replace: {symbols: [...]}`.

**2. `sector_concentration` is completely inert here.** No ETF appears in the
frozen `sectors:` map, so `sector_of` returns `None` and the rail skips. Zero
sector-concentration control on a universe organised *by sector*. Not fixed:
mapping ETFs into `sectors:` would re-arm the ETF-vs-constituent category error
that map was built to avoid, and would silently change `reclaim`'s universe.

**3. I predicted the correlation cap wrong, and the run said so.** Before
building, the pairwise correlation matrix over the repo's existing EIGHT ETFs
was measured across 76 rolling 60-bar windows: the 0.85 threshold sits above
almost every cross-sector pair (XLK–XLE 0.183, SPY–XLF 0.735), and greedy
admission let through a **median 7 of 8**. I wrote down that the cap would bind
*less* on fifteen names.

The actual runs say the opposite. On the full universe `correlation` is the
**dominant** block: 2,074 of 2,392 blocks in 2014-2019 and 1,042 of 1,304 in
2022-2026. Eight names give 28 pairs; fifteen give 105, and the rail needs only
two correlated holdings to refuse. Extrapolating a per-pair statistic to a
whole-book admission rule was the error, and the census is the measurement that
corrects it. **The threshold is not being re-picked** — choosing it after seeing
this would be fitting to the sample.

### A caveat that belongs on the front of any §57 reading

The original nine funds were mandated on the older S&P sector scheme and were
remapped onto GICS in the early 2000s — XLV was "Consumer Services" before it
was "Health Care". The FUND is continuous and no survivorship enters, but what
a ticker *represents* in 2000-2006 is not exactly what it represents in
2022-2026. A composition change, not a survivorship one, and a reason to read
the earliest period most cautiously.

### State

`data/pit/` holds four certified snapshots, 13/13/15/15 symbols. Nothing
enabled, no threshold moved, `mode: paper` unchanged.

## §57 — THE INCUMBENT ON DATA THAT IS NOT FLATTERING IT (DIAGNOSTIC, K=15, pre-registered 2026-08-09)

s57a-d written and frozen **together, before any was run**, then run once each.
**DIAGNOSTIC — this decides nothing and spends no EDGE budget.**

### The result

| period | return | PF | maxDD | trades | deploy | (a) trades | (b) vs SPY | (c) exposure-matched |
|---|---|---|---|---|---|---|---|---|
| 2000-2006 | **−8.20%** | 0.657 | 10.69% | 251 | 10.76% | PASS | **FAIL** −8.20 vs +10.01 | FAIL |
| 2007-2013 | **−6.44%** | 0.583 | 11.25% | 124 | 7.91% | PASS | **FAIL** −6.44 vs +51.30 | FAIL |
| 2014-2019 | +30.61% | 1.645 | 6.09% | 774 | 48.14% | PASS | **FAIL** +30.61 vs +97.39 | FAIL |
| 2022-2026 | +34.77% | 1.816 | 4.61% | 637 | 49.70% | PASS | **FAIL** +34.77 vs +67.06 | PASS |

**Clause (b) fails in four periods of four.**

The reading rule was committed in the registration before the run: *"fails
clause (b) in three or more periods — the ensemble does not beat a clean index
on this universe, and the ambiguity resolves against it."* Four of four is
outcome (1). It licenses **no further EDGE registration on this universe.**

### Why this one is different from the other fifteen

Every previous rejection was scored against a benchmark built from survivor-
selected membership, and therefore carried an excuse: the bot might have been
losing to an index that never existed. Here the benchmark is SPY, priced inside
a universe the probe certified has nothing missing. **That excuse is gone, in
both directions** — and the answer did not change.

### The counterweight, stated because it cuts the other way

The bot draws down **far** less than the index in every period: 10.69% against
SPY's 47.52%, 11.25% against 55.19%, 6.09% against 19.35%, 4.61% against
24.50%. On a risk-adjusted footing this table would read very differently.

**That clause was not registered, and it is not being added now.** §51 defines
`beats_benchmark_risk_adjusted` and it was available; it was not chosen, and
reaching for it after seeing that the absolute-return clause failed is exactly
the selection this apparatus exists to prevent. The drawdown numbers are
reported here because a reader deserves them, not because they change the
verdict. If risk-adjusted performance on this universe is worth testing, it is
worth testing as its own pre-registration, spending its own budget.

### What the census says the bot was actually doing

2000-2006 and 2007-2013: the drawdown latch dominates — 8,890 of 9,234 blocks
and 11,208 of 11,627 — with deployment at 10.76% and 7.91%. §40's finding,
reproduced on new data. 2014-2019 and 2022-2026: deployment is ~49% and
**correlation** becomes the dominant rail (see §56's correction).

### A runner defect found by this section, reported before the numbers

The first four runs computed every arm and then **crashed on the write**.
§55 added a sixth element to `run_arm`'s return — the trade keys
`compare_paired` needs — and updated three of the four places that consume the
record. Two sites still destructured positionally, and both were in the write
path, so the failure landed after all the compute. That is the §39 failure mode
the repo had already paid for once, at 388 seconds.

Nothing caught it because every test in `test_run_gate_reproduces.py` drives
`evaluate()`, which never sees those records. Fixed by giving the tuple named
field positions (`SUMMARY, PNLS, SECS, JUDGE_STATS, KEYS`) and replacing every
positional unpack with an index, plus two tests over `in_spec_order`'s real
output that fail if the arity and the constants drift apart.

### State

Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE stands at
**1 pass in 15; K stays 15.** The live forward record — ten closed trades,
realized −$339.16 — is untouched by anything here.

---

## §58 — THE COIL: A VOLATILITY-CONTRACTION PRECONDITION (DIAGNOSTIC, K=15, pre-registered 2026-08-10)

s58a-h and s59a-h — **sixteen specs written and frozen together, before any was
run**, then run once each. Eight periods across two universes per section.
**DIAGNOSTIC — these decide nothing and spend no EDGE budget. K stays 15.**

### Why a DIAGNOSTIC, when Phase 10 was built to make an EDGE claim possible

Both venues are closed, and the second one closed itself:

| venue | status |
|---|---|
| `data/snapshots/` — 38-name book | EDGE frozen by §52 (survivorship, up to +130pp) |
| `data/pit/` — 15 certified ETFs | EDGE closed by **§57's own pre-committed reading rule** |

§57 registered, before running, that failing `beats_benchmark_symbol` in three
or more of four periods "licenses no further EDGE registration on this
universe." It then failed in four of four. Honouring that only when its outcome
is convenient is the selection this apparatus exists to prevent — so the rule is
now **executed** by `register_gate.freeze_violation` rather than remembered, with
the same audited `--override-freeze` escape §52 has. Prose in a markdown file is
what §23's monotonicity lesson was for thirty-one sections.

### What was tested

A **precondition**, not a trigger: block an entry unless the last 20 bars'
high-to-low range, as a fraction of price, sits at or below a given percentile
of its own trailing year. Four arms — baseline, 40, 25, 10 — and `family:`
rather than `candidate:`, so **every** coil arm must clear. That is §23's
failure made unpassable.

### The result — REJECTED in eight of eight

Certified ETF universe:

| period | baseline PF | coil40 | coil25 | coil10 | why rejected |
|---|---|---|---|---|---|
| 2000-2006 | 0.657 | 0.567 | 0.575 | **0.991** | (b) and (c) |
| 2007-2013 | 0.583 | 0.544 | 1.286 | **1.358** | (b) on coil40, (c) |
| 2014-2019 | 1.645 | **1.737** | 1.684 | 1.265 | **(e) INTERIOR** |
| 2022-2026 | **1.816** | 1.393 | 1.416 | 1.094 | (b) and (c) |

38-name book:

| period | baseline PF | coil40 | coil25 | coil10 | why rejected |
|---|---|---|---|---|---|
| 2000-2006 | 0.231 | 0.231 | 0.231 | 0.231 | (b) — nothing moved at all |
| 2007-2013 | 1.132 | **1.185** | 1.185 | 1.185 | **(e) INTERIOR** |
| 2014-2019 | 1.732 | 1.835 | **2.070** | 1.909 | **(e) INTERIOR**, (c) |
| 2022-2026 | **1.698** | 1.560 | 1.670 | 1.825 | (b) on two arms, (c) |

### §55's monotonicity rule earned its keep on its first production run

`no_interior_optimum` was built in §55 out of §23's own written conclusion and
had never been exercised on a real registration. It **failed three of the eight
specs**, and s58g is the case it was written for:

    profit factor  1.732 -> 1.835 -> 2.070 -> 1.909      peak at coil25, INTERIOR

All three coil arms cleared `min_trades`, cleared `pf_gt_baseline`, and cleared
`maxdd_within` — coil25 at **PF 2.070 against 1.732, with maxDD 6.32% against
12.64%**. Read without clause (e) that is "the filter raises profit factor by a
third and halves drawdown," which is §23's table almost line for line. The only
two things that refused it were the paired interval and the monotonicity rule.

A lesson that cannot fail a gate is a lesson that gets forgotten. This one can
now fail one, and did.

### The mechanism is NOT the one the registration assumed, and this is the finding

A filter should remove trades. In the two certified periods where the drawdown
latch dominated, it **added** them:

| spec / arm | drawdown blocks | trades | deployment | return |
|---|---|---|---|---|
| s58a baseline | 8,890 | 251 | 10.76% | −8.20% |
| s58a coil10 | **0** | **406** | 19.20% | −0.28% |
| s58b baseline | 11,208 | 124 | 7.91% | −6.44% |
| s58b coil25 | **78** | **464** | 23.48% | +9.81% |

The filter's dominant effect is not selecting better trades. It is **declining
the early losses that arm the drawdown latch**, after which the latch never
engages and the book is free to trade far more than the baseline ever was.
§40's finding — the latch dominates the census — governs here too, one layer up:
*any* entry filter measured on this ensemble is really being measured through
the latch.

**This invalidates a premise the registration stated in its own clause (c)
comment.** That comment argued for the paired estimator because "a filter's
trade set is a strict SUBSET of the baseline's, so the shared variance cancels."
It is not a subset. Common-trade overlap ran **13% to 58%**, and in s58b coil25
there were 387 candidate-only trades against 47 baseline-only. The estimator is
valid whether or not the sets are nested — `significance.py` says so explicitly —
so no verdict is affected and no number is wrong. What was wrong is the reason
given for choosing it, written before the mechanism was understood.

### And on the 38-name book in 2000-2006, nothing moved at all

s58e returns identical figures for all four arms — ret −7.91%, PF 0.231, 37
trades — while the contraction rail blocked between 9,411 and 16,879 signals.
Every entry it refused was one the drawdown latch (19,411 blocks) would have
refused anyway. A rail can be fully armed, visibly firing thousands of times,
and change nothing.

### State

Nothing enabled. `risk.max_contraction_pctile` stays **0**. No threshold moved.
`mode: paper` unchanged. Reading rule outcome for the certified family: the
family clause failed in four of four, which is outcome (1) — **the rail stays at
0 permanently unless a later section argues otherwise on new evidence.**

---

## §59 — 52-WEEK-HIGH PROXIMITY, GEORGE & HWANG (2004) (DIAGNOSTIC, K=15, pre-registered 2026-08-10)

Registered and frozen together with §58. `close / max(high, 252 bars)`, buy the
quarter nearest their own highs, exit below the top half, with an absolute floor
at 90% of the high so the rule cannot buy the least-broken name in a broken
universe.

**Not a momentum variant — the paper that says momentum's ranking is the wrong
one.** §35/§37 rejected cross-sectional momentum hard (−23% to −26% on
2007-2013, −$169.18 per trade). George & Hwang found nearness to the 52-week
high subsumes much of momentum's profit *without* the long-run reversal.
`buy_top_fraction` and `exit_below_fraction` are copied from xsmom rather than
chosen, so the two differ in the statistic and in nothing else.

### The result — REJECTED in eight of eight

| period | universe | baseline | hi52on | (b) PF | (c) vs SPY | (d) paired |
|---|---|---|---|---|---|---|
| 2000-2006 | certified | −8.20% | −8.33% | FAIL | **FAIL** −8.33 vs +10.01 | INCONCLUSIVE |
| 2007-2013 | certified | −6.44% | −5.54% | PASS | **FAIL** −5.54 vs +51.30 | INCONCLUSIVE |
| 2014-2019 | certified | +30.61% | +35.56% | PASS | **FAIL** +35.56 vs +97.39 | INCONCLUSIVE |
| 2022-2026 | certified | +34.77% | +33.76% | FAIL | **FAIL** +33.76 vs +67.06 | INCONCLUSIVE |
| 2000-2006 | 38-name | −7.91% | −7.91% | FAIL | FAIL | INCONCLUSIVE |
| 2007-2013 | 38-name | +2.85% | +2.96% | PASS | FAIL | INCONCLUSIVE |
| 2014-2019 | 38-name | +95.89% | **+105.12%** | PASS | **PASS** vs +97.39 | INCONCLUSIVE |
| 2022-2026 | 38-name | +105.84% | **+107.94%** | PASS | **PASS** vs +63.52 | INCONCLUSIVE |

**On the certified universe clause (c) fails four of four.** That is the reading
rule's outcome (1), committed before the run: `hi52` stays disabled.

### The two that passed their benchmark, and why they change nothing

s59g and s59h clear `min_trades`, `pf_gt_baseline` and `beats_benchmark_symbol`
— +105.12% against SPY's +97.39%, and +107.94% against +63.52%. Both are on
`data/snapshots/`, and §50's asymmetry was restated in the registration
precisely so this could not be read as a win afterwards: **the strategy trades a
survivor-only universe, so the comparison runs in its favour. A FAIL there is
decisive; a PASS is not.**

The paired interval refused both anyway — +$9.01/trade with a 99.67% CI of
[−$25.18, +$35.51], and +$4.43 with [−$19.94, +$28.12]. The point estimates are
positive and small; the intervals contain zero comfortably. §18's generalisation
holds: at these sample sizes this method cannot certify an edge, and Phase 9's
sharper instrument is a better measurement rather than a lower bar.

### A candidate that changed literally nothing

s59e returns byte-identical figures with `hi52` enabled and disabled — −7.91%,
PF 0.231, 37 trades. The drawdown latch blocked 19,411 signals for the baseline
and 19,950 with hi52 on: every additional signal the new strategy produced was
absorbed. Clause (b) correctly fails on equality, because "not worse" is not
"better".

### The pipeline reproduced §57 exactly

Every §59 baseline arm on `data/pit/` is the identical configuration §57 scored,
and it returned the identical numbers in all four periods — −8.20%/0.657,
−6.44%/0.583, +30.61%/1.645, +34.77%/1.816, matching trade counts and deployment
to the digit. §35's rule: *a referee that cannot reproduce a verdict it has
already seen is not a referee, it is a new source of error.* It can.

### State

Nothing enabled. `hi52` ships `enabled: false`. EDGE stands at **1 pass in 15;
K stays 15.** The live forward record — ten closed trades, realized −$339.16 —
is untouched by anything here.

## §60 — THE PROJECT SCORES ITS OWN PREDICTIONS (2026-08-11, tooling — not a claim)

Registers nothing, spends no Bonferroni budget, enables nothing. **K stays 15.**
No file the trading loop reads was touched: `mode: paper` unchanged, no
strategy, no rail, no threshold.

`prior` has been a mandatory field on every spec since the runner existed, and
`research/README.md` says why — it is *"the only thing that makes a surprise
legible afterwards."* Fifty-five of them were written, hashed before their runs,
and **never once read back**. Nothing in this repo had ever opened
`registrations.jsonl` for anything but a membership test, or `verdicts.jsonl`
for anything but an append.

### What was built

`src/recall.py` + `scripts/recall.py` join the three layers that hold this
project's history — 64 sections of record, 55 registrations, 59 verdict rows —
and quote them. The constraint is `gen_research_index.py`'s own: *"an index
that formed its own opinion of the record would be a second source of truth."*
So every string the tool emits is a byte-exact span of a named file, an
identifier or number, or a member of a fixed `LABELS` set;
`tests/test_recall_quotes.py` walks the output and fails on a fourth category.

### The finding

All 55 priors were read and scored. Each reading quotes its prior byte-exact
and is refused if it does not.

| | passed | failed |
|---|---|---|
| **expected_pass** | 0 | 0 |
| **expected_fail** | 10 | 41 |

mixed 3 · no expectation stated 1 · unread 0 · scored 51 of 55

```
hit rate    80.4%
base rate   80.4%   (a constant "it fails", same 51 specs)
```

**The two numbers are identical, and that is the result.** Not one prior in
this project's history predicted its own hypothesis would pass. The author's
forecasts and a rubber stamp reading "it fails" are arithmetically the same
strategy, so the 80.4% measures the base rate of failure in this record and
nothing about anyone's judgement.

The confounder is structural and is not corrected for: the author of a prior
also sets the pass mark. Printing the base rate beside the hit rate is what
makes a pessimist and a forecaster distinguishable, and here they are not.

This does not say the priors were dishonest — they were unusually specific, and
several (§43, §50) computed the failing number *before* the run from figures
already published, which is a stronger act than a forecast. It says the
aggregate carries no signal, and that a prior earns its place only when it says
something a constant pessimist would not.

### Three defects the record had, found by building the tool

1. **`research/INDEX.md` was missing 13 of 64 sections** — §1–§4 (written
   `## 1.`), the five METHOD NOTEs, PHASE 0, ALREADY-SEEN OBSERVATION, and §30
   (an H3). `## §14–§17` produced `| §14– | §17 | ... |`, the range split across
   two columns. Its guard re-implemented the generator's own regex, so it could
   not fail for any of them — the standard `ci.yml:55-69` names.

2. **§30's verdict was inverted.** `### §30 CANDIDATE — tighter down-regime
   gross cap. NOT ADOPTED.` scored **`adopted`**, because the matcher tested
   `"ADOPTED" in title` and dropped the negation. It had been wrong for as long
   as the generator existed and nobody saw it, because §30 was never indexed.
   Negations now come first in the match order.

3. **Nothing enforced that this file is append-only.** Its whole evidentiary
   value is that a claim written before its result cannot be quietly reworded
   afterwards, and that was an honour system. `research/anchors.json` now pins a
   sha256 per section. A retroactive edit turns the suite red; appending a new
   section does not.

### What the index says now

`heading says` and `gate result` sit side by side and are allowed to disagree.
§57, §58 and §59 read `pre-registered` in the first — that phrase is in their
headings — and `0/4`, `0/8`, `0/8` in the second. Neither column was corrected.
`tests/test_research_index.py` fails if `classify()` ever starts consulting
verdicts, which is the helpful change that would collapse the two.

A ratio is a count of rows and is **not** a section verdict: §48 reads `4/4`
and is the survivorship finding, §51 reads `3/3` and sized the inflation at
+200.28pp, §44 reads `3/4` and the claim failed on a conjunction nothing
machine-readable records.

### What it does not solve

Search is BM25 over literal tokens with no stemming and no synonyms, so a query
for "volatility squeeze" will not surface §17's Donchian breakout. False
negatives are the expensive direction and this tool cannot bound them; every
search says so. And `divergences --as-of` reports what the register *said*,
which this file has already documented being wrong about by nineteen days.

### State

Nothing enabled. No threshold moved. `mode: paper` unchanged. EDGE stands at
**1 pass in 15; K stays 15.** Both venues remain frozen — §52 on
`data/snapshots/`, §57's own reading rule on `data/pit/`.

<!-- recall: section=§60 specs= -->

## §61 — THE JUDGE HAD NOT BEEN SEEING ITS OWN SCOREBOARD (2026-08-11, tooling — not a claim)

Registers nothing, spends no Bonferroni budget, enables no strategy. **K stays
15.** Both EDGE venues remain frozen.

**This one DID change the live path**, unlike §60. `config.yaml`,
`src/memory.py`, `src/ledger.py`, `src/preflight.py` and `src/main.py` all
moved. Stated here rather than buried, because every other recent section could
claim an empty diff against the trading loop and this one cannot.

### What was asked, and what was actually wrong

The owner asked to "fix the principles.md budget so knowledge can actually
grow." Measurement moved the target twice.

**`principles.md` was not truncating.** It is 1001 bytes on disk, but
`knowledge_block()` calls `.strip()` before slicing, so it was **1000 chars
against a 1000-char budget** — at the boundary, nothing dropped, and the last
principle did reach the judge. A prior session reported "1001 against 1000" and
implied overflow. That was wrong, and the correction matters: the file had zero
headroom, which is a different and quieter problem than being over.

**The constraint that actually bound was one level up.** Measured against the
live memory files:

```
assembled judge context   5,613 chars
learning.max_context_chars 4,000
                          ------
dropped, every judge call  1,613   (29%)
```

| block | state in the judge prompt |
|---|---|
| VALIDATED LESSONS | present |
| KNOWLEDGE | present — all 10 principles survived |
| TODAY'S MARKET CONTEXT | **cut mid-word**, at `"Iran deal h"` |
| NEWS MEMORY | **absent** |
| YOUR LAST RESOLVED CALLS | **absent** |
| YOUR RECENT CALIBRATION | **absent** |
| CURRENT REGIME | **absent** |

The judge had not been seeing its own scoreboard, its own calibration, or the
regime label. `context_for_llm` ended in a bare `ctx[:4000]`: it returned
happily, nothing marked the prompt, nothing logged, and no test could fail.

### The arithmetic nobody had summed

Three sub-budgets were DERIVED SHARES of the total:

```
lessons          max_context_chars // 2 = 2,000
market context                    // 4 = 1,000
scoreboard                        // 4 = 1,000
                                        -------
                                          4,000   = 100% of the cap
```

Exactly 100%, before knowledge (1,066), news memory (600), the book, the trade
block, calibration and the regime label got anything. **The oversubscription
was scale-invariant** — doubling the total leaves it at exactly 100% — so
raising `max_context_chars` alone would have fixed nothing. That is why the fix
had to be named budgets rather than a bigger number.

The largest unbudgeted block was the book: `max_open_positions: 0` means
UNCAPPED (§29), so it can list every name in the universe.

### What changed

Every block now has a **named** budget (`memory.context_budgets`), the pattern
`news.memory.max_context_chars` and `llm.knowledge_max_context_chars` had
already established twice. `learning.max_context_chars` 4,000 → 12,000 covers
their sum (~10,966) with headroom. `llm.knowledge_max_context_chars` 1,000 →
1,500, which is the growth the owner asked for.

Three signals where there were none: `preflight.warnings()` reports an
oversubscription at startup — **non-blocking, deliberately**, since this makes
the bot worse rather than unsafe and a config edit must not be able to stop it
trading at 09:25; a `context_evicted` ledger record fires on any cycle where
the cap actually bites, naming the blocks lost; and
`tests/test_context_budget.py` fails on a shipped config that oversubscribes.

**The old config's advice was right and is kept, not deleted.** The comment at
`config.yaml` said "DO NOT RAISE IT casually… widening this buys principles by
losing the regime label." True at the time, and the regime label had already
lost. It now records what changed and why the warning was correct.

### Two guards that could not have caught this

`test_principles_do_not_shrink_the_lesson_block` and
`test_unset_knowledge_path_gives_byte_identical_context` both build a `Memory`
on an **empty `tmp_path`** with news memory disabled. The assembled context is a
few hundred chars against a 4,000 cap, so eviction is impossible in that fixture
at any budget. They were green throughout. Every eviction case in the new file
carries a vacuity guard, the shape `tests/test_news_memory.py:229` already used.

And `test_the_shipped_principles_file_fits_its_budget`, which calls itself THE
LOAD-BEARING ONE, read its budget from `tests/conftest.py`'s fixture — which has
no `knowledge_max_context_chars` and so derived 1000 — and **never read
`config.yaml` at all**. It now reads the shipped config, and a companion asserts
300 chars of headroom so "the file is full" is a red build.

### The costs, both accepted

1. **§47's decay monitor is at n=11 of a pre-registered 20.** Trades 12–20 run
   on a judge that sees four blocks trades 1–11 did not, so that sample is not
   homogeneous. A measurement-validity cost, not a safety one —
   `src/decaycheck.py` cannot halt trading and `test_monitor_cannot_halt_trading`
   pins it. Owner decision, taken with the number in front of them.
2. **The effect on trading is unmeasurable here.** Divergences #14 and #15 are
   open by construction; `backtest.py` never imports `llm` and `judge_model` has
   no prompt for text to attach to. No backtest can score this. The argument is
   not that it trades better — it is that these blocks were designed to be seen,
   budgets were set for them, and four were being dropped in silence.

### State

Nothing enabled. No risk rail moved. `mode: paper` unchanged. EDGE stands at
**1 pass in 15; K stays 15.** Rollback is removing `learning.context_budgets`
and restoring the two numbers, byte-identical and asserted.

<!-- recall: section=§61 specs= -->

## §62 — SWING_SECTORS ON THE CERTIFIED ETF UNIVERSE (DIAGNOSTIC, K=15, pre-registered 2026-08-11)

**Registered before running:** s62a–h, eight specs frozen together (registration
commit precedes the run in this branch's history). Two families so the
flattering question was removed along with the flattering period: a–d score
the strategy ALONE against SPY on `data/pit/`'s certified funds; e–h score the
ADDITION of the strategy to the shipped ensemble. DIAGNOSTIC — data/pit
refuses EDGE (§57), and the reading rule spends none of the licence.

**What ran:** the four certified periods (2000-2006, 2007-2013, 2014-2019,
2022-2026), judge model on, K=15 reported throughout.

### The strategy alone (s62a–d): REJECTED, four of four

| period | ret | PF | maxDD | trades | deploy | SPY |
|---|---|---|---|---|---|---|
| 2000-2006 | +0.58% | 1.094 | 3.75% | 42 | 2.60% | +10.01% |
| 2007-2013 | +1.09% | 1.134 | 4.56% | 50 | 2.56% | +51.30% |
| 2014-2019 | +2.57% | 1.979 | 1.40% | 22 | 2.26% | +97.39% |
| 2022-2026 | +1.32% | 2.101 | 0.85% | 15 | 1.23% | +67.06% |

Clause (b) `beats_benchmark_symbol` failed in ALL FOUR periods, and the
risk-adjusted form (§51) failed alongside it in all four — levered to SPY's
own drawdown the best period reaches +37.92% against SPY's +67.06%. min_trades
passed everywhere, so this is not an unreadable result: the strategy traded,
profitably, at trivial size, and lost to the index by a mile.

**The pre-committed reading rule, clause (1): failing (b) in three or more of
four resolves against the strategy — the recommendation to the owner is DO
NOT ENABLE, and this recommendation is not negotiable after the fact.** It
fired at four of four.

### The addition (s62e–h): three of four pass, and every pass is a shrug

| period | baseline PF | +swing PF | ΔmaxDD | paired diff/trade | shared |
|---|---|---|---|---|---|
| 2000-2006 | 0.657 | 0.671 | −0.03pp | +$0.62, CI [−15.03, +13.05] | 94% |
| 2007-2013 | 0.583 | 0.620 | −0.72pp | +$7.36, CI [−66.37, +47.40] | 92% |
| 2014-2019 | 1.645 | 1.650 | +0.13pp | +$0.31, CI [−2.71, +3.82] | 98% |
| 2022-2026 | 1.816 | 1.814 | +0.20pp | −$0.09, CI [−5.30, +4.58] | 97% |

s62h failed `pf_gt_baseline` by 0.002. Every `not_worse_paired` interval
straddles zero. The addition changes 2–6% of the book's trades and moves its
profit factor at the third decimal — **harmless because it is nearly inert**.

### Why it is inert, stated plainly

Deployment never exceeded 2.6%. The entry demands a ≥12% sector drawdown AND
a stabilized, no-longer-falling 20-day base AND a pullback into a 0.75·ATR
zone — three conditions that rarely hold at once — and stop-distance sizing
against a 3.5×ATR stop cuts each position further. The strategy as
parameterized is a small, well-behaved trickle of trades, not a book.

### What this licenses, per the frozen rule

- `enabled: false` stands; the recommendation is DO NOT ENABLE this
  parameterization.
- No EDGE claim on any venue (rule 4), and none of the e–h passes may be
  quoted as one.
- Re-parameterizing (looser floor, larger size, more positions) is a NEW
  registration — §63, specs frozen before running — not a tweak to this one.
  Choosing new parameters AFTER reading this table is exactly the in-sample
  selection the apparatus exists to prevent, so any §63 must argue its
  parameters from mechanism, not from these results.
- The live swingscan job stays useful regardless: it ledgers dry-run
  candidates (`swing_scan_candidate` events), which is forward evidence the
  next registration can cite without touching the frozen data.

### Divergence noted alongside (see docs/divergences.md #21)

The intraday trigger is unmodelled: live, swing_scan fills INSIDE the zone
intraday; the simulator fills at the next open after a close inside the zone.
Same conditions, one price feed apart. Direction ambiguous — intraday fills
catch deeper pullbacks and also catch knives the daily close would have
dodged. Open by construction until an hourly-resolution arm is registered on
§25's data.

<!-- recall: section=§62 specs=s62a,s62b,s62c,s62d,s62e,s62f,s62g,s62h -->

---
## §63 — TWO LOOKAHEAD PROBES: ALFRED VINTAGES AND EDGAR FILINGS

*(Numbering note: this section was written 2026-08-10 on branch
`feature/alfred-edgar-lookahead-probes` as §60, before §60–§62 merged to
main and took those numbers. Renumbered §63 at merge, 2026-08-12; content
otherwise unchanged.)*


**No claim type.** Nothing is registered here and **K stays 15**. These are
probes, not gates: they decide whether a claim on either source *may later be
written*, and they are deliberately run before any ingest code exists.

### Why now, and why this is not a new idea

§31 already decided this and wrote down the reasoning. Choosing cross-asset
prices over FRED, it said: *"FRED serves CURRENT values and most macro series
are revised. Backtesting against revised data is lookahead, and it would
quietly invalidate this gate rather than fail it loudly. ALFRED vintages are
the correct fix and are a separate project."*

This is that separate project. The value of recording it that way is that the
conclusion was reached before the work was wanted, not after — the reasoning
carries no hindsight.

The governing precedent for the *shape* is §46's FMP probe: data carrying
lookahead cannot be gated on, because a claim scored against it would be
inflated and indistinguishable from a genuine edge. §28 is the harder
precedent — a fully drafted gate that was **never registered** because a probe
refused it.

### What each probe decides

`scripts/probe_alfred_vintages.py` — three checks, each fatal alone:

1. **VINTAGE AVAILABILITY** — more than one vintage for a revised series, or it
   is FRED-current wearing ALFRED's name.
2. **REVISION DIVERGENCE** — vintage values must actually differ for a revised
   series, **and must not differ** for a never-revised market rate. The second
   half is a negative control, and it is the reason the check means anything:
   without it, a divergence could be noise, and the probe returns UNDETERMINED
   rather than PASS.
3. **RELEASE LAG** — the first vintage must postdate the period it describes.
   Payrolls for January are not public until February; a zero lag is the period
   date wearing another name.

`scripts/probe_edgar_pit.py` — three checks, each fatal alone:

1. **ACCEPTANCE TIMESTAMP** — `acceptanceDateTime` present on every row *and*
   carrying a real time of day. An 8-K accepted at 18:05 ET is not tradeable
   that session, and an all-midnight column is the filing date wearing a clock.
2. **RETROACTIVE EDIT** — amendments must ADD a filing, not replace the
   original. If history is rewritten in place, a backtest reads today's
   corrected story as though it were known then.
3. **SURVIVORSHIP** — filing histories retained for issuers that failed.

The restatement names (KHC, UAA) and failed issuers (SIVB, FRC) are
deliberately **the same names the FMP probe uses**, so the two are comparable
rather than each picking a convenient universe.

### Verdict semantics

All three pass → a claim on that source MAY be pre-registered, with the usual
discipline (canonical-hash the spec, count it against K, exposure-matched
benchmark). Any one fails → that source is **LIVE JUDGE CONTEXT ONLY** — the W7
shape: judge-only, so under invariant #2 it can veto or shrink and can never
create a trade — and no spec may reference it. Both probes exit non-zero on
refusal rather than leaving the reader to infer it.

**A refusal is a successful run.** It is the outcome the probes exist to
produce cheaply.

### What is NOT claimed

Passing says a test *would be meaningful*, not that macro or filings help.
Nothing in `src/` reads either source; no strategy, rail, threshold or
`config.yaml` value is touched; `mode: paper`. EDGE stands at **1 pass in 15**,
and that single pass (§51) its own write-up showed to be explicable by
survivorship.

One coverage limit worth stating in advance: EDGAR's `submissions` endpoint
returns a RECENT window, not all history. A claim reaching further back needs
the full index, and the retained window is a separate question this probe does
not settle.

<!-- recall: section=§63 specs= -->

---

## §64 — TREND_HOLD: THE LEVERED 200-DMA SWITCH, AND THE LATCH THAT ATE THE WAVE (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

First of the four beat-SPY-wave families (§64-§67, sixteen specs s64a-e,
s65a-d, s66a-d, s67a-c registered TOGETHER, design frozen in
`research/specs/drafts/s64-wave-design.md` at commit 0b0b08a before the
margin model or any strategy in the wave existed; dossier
`research/candidates_2026-08.md`; transcripts
`research/s6*_run_2026-08-12.txt`). Registers nothing further, spends no
Bonferroni budget, enables nothing. **K stays 15** for the whole wave.

New under all sixteen: the §64 simulator margin model — leverage via
`risk.margin.multiplier`, financing charged per bar at the ^IRX as-of rate
+150bps, FAIL-CLOSED to 6% flat (divergence #16 made structural) — so every
levered number in this wave pays for its borrow, unlike every published
figure the dossier reviewed.

**Verdict: clause (b) beats_benchmark_symbol 0 of 4 on the lev150
candidate → CLOSED by its own rule (3).** s64e's grid failed
no_interior_optimum: on 2014-2019 at 1.5x, total return peaks AT ma_days
200 (+0.71%) against −18.67% at 250 — an interior optimum, the signature
of a fitted parameter, so the promotion path was closed from both ends.

**The finding that outranks the verdict: a single-symbol, full-equity
strategy plus the portfolio drawdown latch is a one-way trapdoor.** s64a's
census: 869 of 876 signals blocked, 867 by `drawdown`. Deployment 2.5%
(2000-06) to 23% (2014-19) on a strategy meant to be ~80-100% invested.
Mechanism: the latch blocks entries once equity falls ~10% from its
high-water mark; with ONE symbol and the book forced to cash, equity
cannot recover to un-latch — the first ~10% drawdown is permanent cash.
The latch was built for a 38-name book where other positions keep equity
moving (§48 measured it MASKING measurement; §58 measured filters THROUGH
it). Here it does not mask the candidate — it replaces it.

These verdicts therefore measure "candidate inside the shipped rails",
which is exactly what the specs declared (the §62 convention), and the
frozen rules count them — but what they closed is the candidates AS
WIRED, not the mechanisms. A re-test with the latch configuration DECLARED
IN THE ARMS is a different experiment needing its own pre-registration
(§53's precedent: relief cannot be claimed by arguing the config would
have changed it). Whether to spend one is an owner decision.

`mode: paper` unchanged; every wave strategy ships `enabled: false`;
ensemble-unmoved proven in `tests/test_margin_model.py`.

<!-- recall: section=§64 specs=s64a,s64b,s64c,s64d,s64e -->

---

## §65 — TOM_TILT: THE TURN-OF-THE-MONTH WINDOW NEVER GOT TO TRADE (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

Second wave family; wave header and the latch-trapdoor finding are in §64.

**Verdict: every clause failed in all four periods → CLOSED by its own
rule (3).** min_trades 15 vs 40 even in the calm 2014-2019 window (the
unlevered baseline managed 49 entries; the levered candidate latched out
after its first losing window and stayed out). The McConnell-Xu window was
never actually measured here — what was measured is that a strategy which
is deliberately 80% in cash still trips a high-water-mark latch on the
20% it does trade, and then cannot recover it. The window itself remains
untested on this apparatus; the candidate as wired is closed.

<!-- recall: section=§65 specs=s65a,s65b,s65c,s65d -->

---

## §66 — VOL_LEVER: ONE SPECTACULAR PERIOD, TWO TRADES, AND THE RULE THAT ALREADY KNEW (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

Third wave family; wave header in §64. EDGE was PRE-EXCLUDED for this
candidate in its own frozen prior, whatever the outcome.

**Verdict: clause (b) 1 of 4 → CLOSED by its own rule (3).** The one pass
is s66a (2000-2006): **+68.44% vs SPY +10.01% at a quarter of SPY's
drawdown (10.89% vs 47.52%)** — and it sits in the one spec of the four
whose own min_trades clause FAILED: two trades in seven years. The
discrete vol switch sat out both halves of the dot-com bust and rode the
middle, twice. That is either the mechanism working exactly as Moreira-
Muir describe, or two lucky episodes — and on n=2 those are the same
observation, which is why the reading rule pre-committed that no outcome
here licenses anything. Periods b-d: min_trades passed, benchmark failed.
The b-d results carry the same latch suffocation as the rest of the wave
(deployment 24-31% in 2014-19).

<!-- recall: section=§66 specs=s66a,s66b,s66c,s66d -->

---

## §67 — GEM: THE FALSIFICATION LANDED; THE DUAL-MOMENTUM FAMILY IS CLOSED (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

Fourth wave family; wave header in §64. Registered explicitly as a
falsification candidate: one arm, the exact Antonacci book spec, NO grid
(ReSolve's 1,226-sibling study is why a grid here would be spec-shopping).

**Verdict: clause (b) 0 of 3 → the dual-momentum family is CLOSED by its
own rule (1).** In its structurally favorable window (2007-2013, the long
bear its reputation rests on) it returned −8.79% against SPY's +51.30%.
2022-2026 (+45.41% vs +67.06%) is its best showing and still loses. The
first run of s67 CRASHED on a units bug (bars passed where closes were
wanted — fixed with a regression test; the reachability fixture carries no
SPY spine and could not have caught it); the verdicts above are from the
fixed rerun, same frozen specs.

Future GEM-shaped proposals get this section as the one-paragraph answer.
**s68 (macro gate on ALFRED vintages) remains UNREGISTERED** — blocked on
a FRED API key and a passing `probe_alfred_vintages.py` (§63). That venue
stays open and unspent, and it is the only one in the wave that still is.

<!-- recall: section=§67 specs=s67a,s67b,s67c -->

---

## §68 — TREND_HOLD UNLATCHED: THE PROMOTION RULE FIRED, 3 OF 4 (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

The latch wave (s68-s71, fifteen specs frozen together, owner-directed
after §64-§67: "rerun with the latch declared per-arm"). Baseline = the
§64 candidate exactly (lev150, latch 10%); candidate = identical twin with
`risk.max_drawdown_pct: 0` — the latch's whole cost is the arm delta.

**Verdict: latch_off beats SPY total return in 3 of 4 periods** — 2000-06
+51.76% vs +10.01%, 2007-13 +55.08% vs +51.30%, 2022-26 +75.57% vs
+67.06%; FAILS 2014-19 (+69.97% vs +97.39%), exactly where the dossier
said it would. All financing paid at ^IRX+150bps. **The frozen promotion
rule FIRED: one EDGE registration at K=16 is licensed** via the audited
`--override-freeze` supersession argument (wave-design doc). It has NOT
been registered yet — spending it is the next act, recorded when it
happens. The latch's measured cost vs §64: deployment 2.5-23% → 71-106%.
Cautions stand: three bear-containing periods carried it; the strategy
lost to SPY in the one grind-up window; a latchless single-symbol config
has no drawdown brake and is not shippable as-is (failure_modes).

<!-- recall: section=§68 specs=s68a,s68b,s68c,s68d -->

---

## §69 — TOM_TILT UNLATCHED: THE WINDOW FINALLY TRADED, AND MOSTLY LOST (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

**Verdict: (b) 1 of 4 (2000-06 only) → CLOSED for good by its own rule.**
With the latch off it traded 40+ windows per period; the McConnell-Xu
window beat exposure-matched SPY in 3 of 4 (the window itself retains
some edge) but total-return lost everywhere post-2007. The 2026
international persistence does not survive on US large-caps here.

<!-- recall: section=§69 specs=s69a,s69b,s69c,s69d -->

---

## §70 — VOL_LEVER UNLATCHED: STILL ONE PERIOD, STILL CLOSED (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

**Verdict: (b) 1 of 4 → CLOSED per its rule; EDGE was pre-excluded both
waves.** The 2000-06 pass repeats (min_trades failing again — the discrete
switch makes single-digit decisions); 2007-26 all lose total return.
Matches Moreira-Muir's own Table V: the 1.5x cap keeps the defense, loses
the offense.

<!-- recall: section=§70 specs=s70a,s70b,s70c,s70d -->

---

## §71 — GEM UNLATCHED: 0 OF 3 — THE FAMILY IS PERMANENTLY CLOSED (DIAGNOSTIC, K=15, pre-registered 2026-08-12)

**Verdict: (b) 0 of 3, now measured both wired (§67) and unwired.** Per
the frozen rule: no further GEM registration of any kind in this repo.
The book spec loses to SPY in every window including its structurally
favorable long bear.

<!-- recall: section=§71 specs=s71a,s71b,s71c -->

---

## §72 — THE LICENSED EDGE SPEND: TREND_HOLD CONFIRMED AT K=16 (EDGE, pre-registered 2026-08-12)

> **SUPERSEDED BY §75 (2026-08-22).** This section's result was measured with
> `judge_model: false`. Re-measured with the judge on — the live bot's own
> haircut distribution — clause (b) survives in **1 of 4** periods, not 3.
> The numbers below stand as the record of what was measured; the headline
> does not. K is unchanged.

The one registration §68's promotion rule licensed, spent on owner
direction, registered with the audited `--override-freeze` (argument in
the registration rows) and run as a deterministic re-scoring of s68's
exact configuration. **Every number reproduced byte-identically**, so the
frozen confirmation rule resolves: **EDGE CONFIRMED — beats SPY total
return in 3 of 4 periods** (2000-06 +51.76% vs +10.01%; 2007-13 +55.08%
vs +51.30%; 2022-26 +75.57% vs +67.06%; FAILS 2014-19 +69.97% vs
+97.39%), at 1.5x with financing paid at ^IRX+150bps, on the
survivorship-certified universe.

**The EDGE tally is now 2 passes in 16 — and this is the first pass on
clean data.** (§51's pass was explained by +200pp of survivorship.) The
venue RE-FREEZES with this registration; the licence was for one spend.

What this is NOT: an enablement, or a config change. The caveats are part
of the claim: three bear-containing periods carry it; it LOST to SPY in
the grind-up window (regime dependence is the mechanism, not a flaw to
fix); and a latchless single-symbol book has no drawdown brake — running
it live requires a risk design (per-strategy latch? wider portfolio
latch? separate sleeve?) that does not exist and is its own future,
separately-registered work. `mode: paper` unchanged; every §64-wave
strategy still ships `enabled: false`.

<!-- recall: section=§72 specs=s72a,s72b,s72c,s72d -->

---

## §73 — MACRO ENTRY GATE (UNRATE): 0 OF 4 — THE FAMILY IS CLOSED (DIAGNOSTIC, K=16, pre-registered 2026-08-12)

The spend `probe_alfred_vintages.py` licensed. On 2026-08-12 the probe
passed all three checks against a real FRED key (vintage axis exists,
revisions genuinely diverge from a market-rate negative control, release
lag postdates the observation period — `research/alfred_vintages_2026-08-12.txt`),
closing the §31 deferral. `scripts/build_macro_aux.py` then built a
daily, forward-filled, first-print-only UNRATE series
(`data/aux/unrate_2000-01-01_2026-08-11.json.gz`) for `risk.macro_gate`
(built §64, shipped disabled) to read.

**What was tested.** Both arms are §72's CONFIRMED-EDGE configuration
(trend_hold, latch off, 1.5x financed leverage) on the same four periods.
`macro_on` additionally blocks an entry whenever UNRATE's latest as-of
value sits above its own 252-day trailing mean — a filter that can only
REMOVE trades the price signal already wanted, never add one. Clause (b),
`significantly_better_paired` (§55's paired estimator, built for exactly
this shape: the candidate's trade set is a subset of baseline's), is the
frozen primary question.

**Verdict: clause (b) failed in all four periods — 0 of 4.** Per the
frozen reading rule, "1 or fewer... REFUTES the mechanism for trend_hold
and closes the family, the same standard applied to GEM, tom_tilt and
vol_lever." No promotion. K stays at 16.

| Period | baseline TR | macro_on TR | vs SPY (macro_on) | clause (b) |
|---|---|---|---|---|
| 2000-06 | +51.76% | +64.21% | +64.21% vs +10.01% | FAIL (inconclusive) |
| 2007-13 | +55.08% | +62.98% | +62.98% vs +51.30% | FAIL (inconclusive) |
| 2014-19 | +69.97% | +70.25% | +70.25% vs +97.39% | FAIL (inconclusive) |
| 2022-26 | +75.57% | **-1.97%** | -1.97% vs +67.06% | FAIL (inconclusive) |

Two things worth carrying forward, neither of them the prior's own
sharpest prediction:

**The predicted failure mode did not fire where predicted.** s73b's
frozen prior named the 2007-2013 GFC window as the period most likely to
be HURT by the gate — labor lags the market, and blocking trend_hold's
2009 re-entry while unemployment was still peaking would have removed
the highest-value trade in the whole dataset. That specific mechanism did
not materialize: `macro_on` DESCRIPTIVELY beat baseline in 2007-2013
(+62.98% vs +55.08%), just not significantly (n=9, wide CI). The
significance test failed everywhere for the more ordinary reason —
underpowered n on an already-small trade count, the same s48/s49 pattern
this whole program keeps re-finding.

**The real damage landed in the one period with "no strong prior."**
2022-2026 was written up as genuinely open — a non-recessionary labor
drift the other three periods' arguments didn't transfer to. It produced
the single worst result of the wave: 380 of 397 signals blocked, deployment
cut from 100.7% to 55.0%, and the strategy fell from beating SPY by
+8.5pp to losing to it by 69pp. A soft-landing-shaped rise in UNRATE
locked the gate almost fully shut through a period stocks kept rising —
the mirror image of the 2007-2013 concern, and the sharper of the two in
practice.

What this is NOT: a claim that macro data is useless, or that UNRATE
specifically is the wrong series — only that THIS mechanism (an
entry-blocking veto keyed to trailing-mean unemployment) on THIS
candidate (trend_hold) failed its own pre-committed test. The venue
closure `probe_alfred_vintages.py` cleared stands; a different macro
signal or a different role for it (judge-context shrink rather than a
hard entry block, the shape invariant #2 originally scoped it for) is not
foreclosed and is not licensed by this section either.

<!-- recall: section=§73 specs=s73a,s73b,s73c,s73d -->

## §74 CANDIDATE (not registered) — factor tilts from the "Beating the S&P 500" review

**2026-08-18.** Nothing here was registered, no K was spent, no gate ran —
same shape as §52. Source review: `knowledge/source-review-beating-the-sp500.md`.

**The claim, in one sentence.** A quality tilt (QMJ/RMW), and secondarily a
value (HML) or F-Score screen, applied to the equity universe would beat
buy-and-hold SPY net of costs — the report scores quality as the *"most
implementable factor tilt"* of the set at RMW 2.87% ann., Sharpe 0.37, on low
turnover.

**What it would cost to test.**
- Snapshot: a **point-in-time** universe with fundamentals. `data/pit/` is the
  only candidate and is frozen for a spent licence; `data/snapshots/` cannot
  serve this, because a factor screen ranks names *within* a universe and a
  survivorship-contaminated universe silently rewrites the ranking.
- Rule: `beats_benchmark_symbol` with `symbol: SPY`, plus
  `beats_benchmark_risk_adjusted`. **Never `beats_exposure_matched`** — §48
  established that any long-biased strategy passes that clause.
- Arms: at minimum a `baseline` long-only SPY arm against a quality-ranked arm
  at matched deployment, across the standard period splits. Rebalance
  frequency, universe construction, and cost model are all **unspecified by
  the source** and would be our design decisions, not the report's — which is
  itself a reason the report's numbers would not transfer to the result.
- Cost: one full K slot per period arm, against a budget that is frozen.

**What would have to be true first.**
1. The §52 freeze lifts — i.e. a fundamentals-carrying, point-in-time source
   passes `scripts/probe_delisted_coverage.py`.
2. Hands-off ends: the decay monitor reaches n=20 closed trades (it has not).
3. A spec exists that is ours and is written down before the run, per
   `bot-pre-registration`.

**The honest prior — supplied by the source itself.** Low. The report's own
synthesis is that *"for the large majority of approaches surveyed here, the
evidence does not support a durable edge over the S&P 500 net of costs and
taxes."* Specifically: value has run **−0.39% ann. since 2015**; smart-beta
products carrying exactly these tilts fall from ~3% pre-listing to −0.50% to
−1% post-listing, with the edge *"virtually disappearing"* once live; only
~35% of published anomalies replicate value-weighted (Hou-Xue-Zhang); and
F-Score's sample ends in 1996 with a ~35% post-publication haircut expected.
Quality's own recent stretch in the table is **−8.95% ann. 2024–2026:06**.

The momentum family is **not** parked here — it is closed. §67/§71 returned
0 of 3 twice ("THE FAMILY IS PERMANENTLY CLOSED") and §53 rejected xsmom
130/30 3 of 4.

<!-- recall: section=§74 specs= -->

## §75 — §72 RE-MEASURED WITH THE JUDGE ON: 1 OF 4 SURVIVES (METHOD, K stays 16, pre-registered 2026-08-22)

**The project's first METHOD claim.** 102 registrations before this were
76 DIAGNOSTIC, 22 EDGE, 4 CAPACITY, 0 METHOD. DIAGNOSTIC could not do this
job: `run_gate.py` prints that a DIAGNOSTIC *"decides NOTHING … cannot
enable, reject or support anything"*, and this question had to be able to
**withdraw support** from §72. METHOD — *"is the way we are measuring
sound?"* — is the only fit. It spends no Bonferroni budget and needed no
`--override-freeze`: `freeze_violation()` returns `None` for a non-EDGE
claim before it ever reads the path.

**The question.** §72 is the project's only EDGE pass on
survivorship-certified data, and it was measured with two known gaps:
`judge_model: false` (divergence #8 — the live judge cuts most entries and
the simulator modelled none of that) and the flat 5 bps cost default. §75
re-executes §72's frozen pass mark, on §72's frozen snapshots, with §72's
arms **character for character**, changing only how the measurement is
taken. A third gap, the earnings blackout (#19), is provably inapplicable
here — the universe is ETFs, and the guard reads `earnings_blackout_days`
from the strategy, which `trend_hold` does not carry — and was deliberately
not supplied, because declaring an aux that changes nothing while implying
a gap was shut is theatre.

**Control first, because two inputs are outside the hash.** `config.yaml`
is read from the working directory at run time and
`knowledge/judge_calibration.json` is referenced by no spec and no
registration row — both are unhashed inputs to every verdict. So before
§75 ran, `s72a`–`d` were re-executed unchanged on `main @ 86f28f1`. **Zero
drifted fields across every arm** (`research/s72*_control_2026-08-22.txt`).
That control, not the apparatus, is what makes the comparison below hold.

**The calibration, pinned for the first time.** This is the first verdict in
the project that names the calibration it ran against:
`c19b2ab9adbfed7d…` — the **corrected** file from PR #133 (n=250,
mean scale 0.595, veto 4.4%, 51 fallback rows excluded). That is roughly a
**40% position haircut**, not the ~27% the pre-correction file implied,
because 22% of its sample had been fallback approvals at scale 1.0.

### The result

Clause (b), `beats_benchmark_symbol`, on the `latch_off` arm — the clause
§72's own promotion rule was written in:

| period | §72 (judge off) | §75 (judge on, 1.55 bps) | judge cut | (b) | (d) |
|---|---|---|---|---|---|
| 2000–2006 | +51.76% vs SPY +10.01% | **+49.60%** vs +10.08% | 37 of 48 | **PASS** | PASS |
| 2007–2013 | +55.08% vs +51.30% | **+23.55%** vs +51.43% | 39 of 46 | FAIL | PASS |
| 2014–2019 | +69.97% vs +97.39% | **+29.00%** vs +97.49% | 25 of 29 | FAIL | FAIL |
| 2022–2026 | +75.57% vs +67.06% | **+47.12%** vs +67.15% | 17 of 18 | FAIL | PASS |

**1 of 4.** Under the reading rule frozen in the spec before any run, that is
the bottom branch: *"§72's pass was IN SUBSTANTIAL PART an artifact of a
judge-less, flat-cost measurement. The §72 headline is SUPERSEDED rather than
footnoted. K does NOT move."*

**The prior held — and it committed to this exact pattern.** It named,
before running, *"clause (b) SURVIVES in 2000-2006 and FAILS in 2007-2013,
2014-2019 and 2022-2026."* §60 found this project's 55 earlier priors
carried no information because every one said "fail"; this one said which
period would not. Both of its named unknowns resolved as flagged:

- **Clause (d), the risk-matched one, survived 3 of 4.** The judge shrinks
  drawdown as it shrinks return — maxDD 16.95%→10.90%, 26.18%→19.75%,
  15.80%→8.73% — and the scale factor in (d) rewards that. The prior said
  *"(d) may survive where (b) dies; I cannot sign that in advance,"* and it
  did. That is not a back door for §72: (d) was never the clause §68's
  promotion rule turned on.
- **`s75d` cleared `min_trades` at exactly 8 vs 8**, again. Whole-share
  truncation under the haircut zeroed nothing. The arithmetic failure the
  prior flagged did not fire.

**Slippage decided nothing, as predicted.** 1.55 bps against 5 moves ~0.8pp
in §72's favour; no clause turned on it.

### What this does and does not mean

It means §72 **does not survive the judge as calibrated.** Three of its four
periods lose to SPY once the live bot's own haircut distribution is applied
to its entries. The "first pass on survivorship-certified data" is, with the
judge modelled, one period of four — and 2000–2006 is the period §48 showed
carries the most regime-switch benefit for a trend follower.

It does **not** mean §72 is refuted, and the spec's own failure modes say
why: the calibration is **off-distribution**. Its 250 judged buys came from
the 38-name megacap ensemble book over 34 days of 2026; this applies that
marginal distribution to a SPY-only 200-DMA switch at 1.5× over 2000–2026.
The judge never saw a signal of this shape. A replay could settle it;
`judge_model.py` is a distribution, not a replay, and says so. #14 and #15
stay open. Judge-on narrows #8 and does not close it.

### What this licenses

**Nothing.** No K movement — a withdrawn pass does not refund budget. No
enablement, no config change, no new EDGE registration; the venue stays
frozen under §57. The baseline arm held §72's comparison shape and is not
evidence that `latch_on` is better.

The EDGE tally line across the repo now reads: **2 passes in 16 on the
register, of which §72 does not survive re-measurement with the judge on
(§75) — one period of four stands.** README, GLOSSARY and the
pre-registration skill carry the same sentence.

**Trial accounting, first live use.** All 16 arm runs here — 8 control, 8
§75 — were written to `research/trials.jsonl` by the runner itself, not
backfilled. `trial_report.py` reads `delta (verdicts - log) : 0` at 241.
Each row carries the run's per-bar Sharpe, skew and kurtosis
(`s72a/latch_off`: SR 0.038, kurtosis 7.05 — fat tails), which is what a
Deflated Sharpe will need and which no verdict before 2026-08-22 has.

**A footnote on §60 that is not a footnote.** §60 scored all 55 priors the
project had written and found the author's hit rate and a constant "it
fails" were the *same strategy* — not one prior had predicted its own
hypothesis would pass. §72's three `expected_pass` readings did not change
that: they were a deterministic re-scoring where predicting pass was
arithmetic. **§75a is the first genuine first-run forecast in this project
to predict its own pass, and it was right.** `recall.calibrate()` moved:
hit rate **0.83** against an always-fail base of **0.79**, the first time
the two have separated. On a sample of one genuine prediction, which is
worth exactly that much — §60 is amended, not overturned. What changed is
the *form*: a prior that names a clause and a period is falsifiable, and a
falsifiable prior can be right.

Trial count through §75: 241 arm runs.

<!-- recall: section=§75 specs=s75a,s75b,s75c,s75d -->

## §76 — THE DECAY MONITOR REACHED ITS THRESHOLD: INDISTINGUISHABLE FROM RANDOM, ON A STRATEGY ALREADY RETIRED (DIAGNOSTIC, K stays 16, 2026-08-23)

Registers nothing, spends no Bonferroni budget, enables nothing. **K stays
16.** Both EDGE venues remain frozen. It records a measurement that was
pre-registered long before it ran, and that nothing had written down.

### Why this section exists at all

`src/decaycheck.py` is **alert-only by construction**: it cannot halt trading
and it cannot write, and `tests/test_decaycheck.py` walks the AST to keep it
that way. It runs Sundays 11:30 ET. On 2026-08-23 it crossed its threshold,
returned a negative verdict, and — because `alert` was false — left no trace
anywhere. No ledger event, no alert, no file. Read by hand, on a Sunday
afternoon, four hours after it ran.

The 2026-08-02 hands-off decision paused this project *pending this exact
measurement*, expecting it in early September. It arrived on 2026-08-23 and
would have gone unread.

### The numbers

```
verdict         : INDISTINGUISHABLE_FROM_RANDOM
n_trades        : 24        (min_trades 20 — threshold met)
actual_mean_pct : -0.32%
null_mean_pct   : +0.80%    (2000 random-entry samples, seed 20260801)
percentile      : 17.4
slippage_bps    : 5.0
```

Over 24 closed round-trips the live entries averaged **−0.32%** per trade.
Random entries over the same names and dates averaged **+0.80%**. The bot sits
at the **17.4th percentile** of its own null.

### THE COMPOSITION, WHICH LEADS RATHER THAN TRAILS

| strategy | closed trades | live status |
|---|---|---|
| `tsmom` | **19 of 24** | RETIRED 2026-08-20 by `live_kill`, PF 0.145 |
| `meanrev` | 4 | enabled |
| `ma_crossover` | 1 | enabled |

**This is overwhelmingly a verdict on a strategy that no longer trades.**
`live_kill` retired tsmom's entries three days before this monitor crossed its
threshold, on a completely independent criterion — realized profit factor
against a floor frozen on 2026-07-21.

So the honest headline is not "the bot is indistinguishable from random". It
is that **two independent instruments reached the same conclusion about the
same strategy, and the faster one had already acted.** §10 leaned on
`live_kill` in writing as the realized-trades fast path; this is the slower
path arriving at the same place and confirming it.

Writing this up as a verdict on the running bot would have been the more
dramatic sentence and the less true one.

### What this does and does not license

**It does not say the bot is worse than random.** INDISTINGUISHABLE means no
edge is detectable. At n=24, the 17.4th percentile is not a rejection, and
§34 already established there is no validated way to pick a winner on this
data — a procedure that cannot reliably select cannot reliably condemn either.

**It says almost nothing about what is running now.** The two enabled
strategies have **five closed trades between them**. That is the real state of
the live record and it should be stated plainly rather than dressed up: after
five weeks of paper trading, the currently-enabled configuration has no
measurable record at all.

**No verdict is re-scored.** §41's precedent holds: a monitor finding does not
retroactively re-score sections.

### The sample cannot grow right now

Entries have been blocked since **2026-08-21**: 53 signals refused by
`unavailable_block` after the Anthropic account exhausted its credits, with
`on_unavailable: block` doing exactly what it was configured to do. The book
has drained 22 → 16 as exits continued to run correctly, and the degradation
SLO is breached daily. Until credits return, this measurement is frozen at
n=24 and the enabled strategies stay at n=5.

### Why it did not alert — RESOLVED, and it is not a gap

**Corrected 2026-08-24, the day after this section was written.** The original
text left it open: *"either the alert condition is deliberately narrower than
the verdict set … or it is a gap."* It is the former, and both halves of the
answer were already written down before this section claimed otherwise.

`src/decaycheck.py:20-21` states the contract in the module docstring:

```
0  no alert  (BEATS_RANDOM, INDISTINGUISHABLE, or INSUFFICIENT_DATA)
1  alert     (WORSE_THAN_RANDOM)
```

and the 2026-08-02 hands-off decision recorded the escalation trigger as
`WORSE_THAN_RANDOM` at n≥20. The monitor behaved exactly as specified. A
monitor that paged on "cannot distinguish" would page constantly at low n,
which is how an alarm gets muted and then ignored — the cost divergence #22
records.

Leaving that question open was itself the error this project keeps naming:
the answer was in the code and in the decision record, and the section
speculated instead of reading them.

**What remains true is narrower and still worth fixing.** `decaycheck` is
alert-only by construction — it cannot write to the ledger, and
`tests/test_decaycheck.py` walks the AST to keep it that way — so a
NON-ALERTING verdict leaves no trace anywhere at all. This one ran Sunday
11:30 ET and was found four hours later only because someone went looking. A
verdict below the alert threshold is still a measurement, and this project has
no channel that carries one. That is a REPORTING gap, not an alerting bug, and
it is why this section exists.

Trial count through §75: 241 arm runs. §76 runs no arms; the count is
unchanged.

<!-- recall: section=§76 specs= -->

## §77 — THE EDGE CLAIM IS RETIRED; THE APPARATUS IS THE ASSET (DECISION, K closed at 16, 2026-08-23)

Registers nothing and scores nothing. It **closes a claim type**, which no
prior section has done, and it is an owner's decision rather than a
measurement — recorded here because the decisions are what a future reader
needs to reconstruct why the record stops where it does.

### What was decided

The programme stops making EDGE claims. `register_gate.freeze_violation` now
refuses `claim: EDGE` **on every venue**, not only the two that were spent.

### Why the venue freezes were not enough

§52 froze `data/snapshots/` and §57 froze `data/pit/`. Both were **exhaustion**
arguments — *this dataset has given what it can* — and both were escapable in
the same way: point a spec at a directory neither prefix matched and the check
returned `None`.

So the licence to claim an edge depended on **what had been bought rather than
on what had been shown.** The open decision before this one was whether to buy
a survivorship-free licence (~$630/yr); doing so would have reopened EDGE
without anyone deciding to reopen it. A freeze that a purchase can lift is a
statement about inventory, not about the programme.

### The evidence the decision rests on

| section | finding |
|---|---|
| K=16 | **16 EDGE registrations, 2 passes** — a rate consistent with chance |
| §51 | **+200.28pp** survivorship inflation on the first pass — large enough to explain it outright |
| §75 | the second pass re-measured with the judge on: **1 period of 4 survives**, not 3 |
| §34 | the **selection procedure itself** scores no better than random — a method that cannot reliably pick cannot reliably confirm |
| §76 | the live decay monitor returns **INDISTINGUISHABLE_FROM_RANDOM** at n=24 |

None of that is new. What is new is declining to keep spending against it.

### What this is NOT

**Not a claim that an edge is impossible**, here or anywhere. It is a claim
that *this programme* has not found one in sixteen attempts, that its own
selection procedure cannot be trusted to find one, and that continuing to
register EDGE claims against that record would be the selection this apparatus
exists to prevent — turned on itself.

**Not a shutdown.** The bot keeps running. DIAGNOSTIC, METHOD and CAPACITY
claims stay open, and those are the claim types that describe how the machinery
behaves — which is now the thing being built. The live record is what makes the
apparatus demonstrable rather than theoretical: divergences #13–#24, the live
kill, the decay monitor and §76 all exist because a real bot was running and
being measured.

**Not irreversible.** `--override-freeze` lifts this exactly as it lifts §52
and §57. That is deliberate, and the reasoning is the repo's own: a wall gets
climbed, a speed bump with an audit trail gets recorded. Resuming edge claims
should cost a pre-registration that argues *why*, written before its data is
seen — not a new folder name.

### What the apparatus is, stated plainly

Sixteen pre-registrations with hash-frozen specs that refuse to score a moved
pass mark and **name the field that moved**. A divergence register where closed
means *a named test fails if it reopens*. Mutation proof on every guard. A
survivorship audit that measured its own bias at +200pp and froze its own
budget in code rather than quoting the number. A research record that audits
clean across the prose, the registrations and the verdicts.

That machinery found and killed this project's own results. **That is the
demonstration, and it is worth more than a strategy that lost $1,092.74 over
fourteen closed trades.**

Trial count through §75: 241 arm runs. §77 runs no arms; K is closed at 16.

<!-- recall: section=§77 specs= -->

---

## §78 — the judge moved off Anthropic, and the slow part was not the model

**2026-08-28. Runs no arms. K stays closed at 16. Claims nothing about edge.**

### What was actually broken

Since 2026-08-21 the bot had entered nothing. 53 signals, every one refused by
`unavailable_block`, because the Anthropic account ran out of credits and
`on_unavailable: block` did exactly what it is set to do. Exits kept running, so
the book drained 22 → 15 without a single decision to reduce it. Degradation SLO
breached every day.

The apparatus behaved correctly throughout. Nothing was mismeasured, nothing
lied. It simply could not trade.

### The finding worth keeping

The obvious fix — point `llm.provider` at the two A100s already sitting idle on
the same host — **would not have worked, and would have looked like it had.**

vLLM serves qwen3 with `--reasoning-parser=qwen3`. Left at the server default,
the model generates a long reasoning trace before its JSON. Measured on the real
judge prompt (`llm._SYSTEM` + a live-shaped `review_user_message`), n=3 each:

| | latency | completion tokens | verdict |
|---|---|---|---|
| server default | 47.1s | 2,261 | `downsize` |
| `enable_thinking: false` | **4.8s** | 224 | `downsize` |

`llm.timeout_seconds` is 30. So every default-mode call raised, every raise
became a degradation, and entries stayed blocked — **the same symptom, a new
cause, and a config that now looked correct.** An earlier probe had already
measured 43–80s through the real client and read as "the local model is just
slow"; it was not slow, it was answering a question nobody asked.

The discarded tokens were also unauditable. `reasoning_content` came back empty,
`llm.py` slices `{`..`}` out of `content` and drops the surrounding prose, and
the ledger stores only the visible `reasoning` field. Ten-fold latency, bought
reasoning that no record retains and no audit can inspect.

### What was NOT established

**Nothing about quality.** Four probe calls returned valid schema and
`downsize` 4/4 at scale 0.3–0.5, against a fitted Claude distribution of
`mean_scale 0.595` / `downsize_rate 0.858` over 250 buys. That is enough to say
the disposition differs and the calibration is stale. It is **not** a
distribution, not an agreement rate, and not evidence that either judge is
better.

The measurement that would settle it — `src/llm_shadow.py` on live signals —
needs Anthropic to answer, which is the thing that ran out. The baseline is
unreachable from here. Registered as **divergence #25** rather than left to be
discovered.

### The guard that nearly did not fire

`backtest.judge_model.context_version` 2 → 3, so the calibration is refused and
no gate can be scored with `--judge-model`.

Its documented trigger was *"bump whenever what the judge READS changes"*. Read
literally, replacing the model requires **no** bump — the inputs are identical.
The guard would have kept describing a judge that no longer existed, and it
would have looked healthy doing it. Same shape as the 171 `noop` capture rows
and the mutation harness printing CAUGHT for a test file that did not exist: an
instrument that cannot express its own failure is not evidence. The trigger now
names *who* reads, decoding settings included, and a test fails if a keyless
provider ever ships against a calibration claiming to match it.

### The honest summary

A bot that could not trade can now trade, on a judge nobody has measured, with
the calibration correctly refusing and the divergence declared. That is an
availability fix. It is not a result.

Trial count through §78: 241 arm runs. §78 runs no arms; K is closed at 16.

<!-- recall: section=§78 specs= -->

---

## §79 — measuring the new judge, and the run that disagreed with itself

**2026-08-28. Runs no arms. K stays closed at 16. Claims nothing about edge.**

§78 switched the judge to a self-hosted model and said plainly that nothing
about quality had been established. This measures the one property that decides
whether §78 achieved anything: **does the new judge let trades through?**

`scripts/judge_disposition.py` replays real past buy signals from the ledger
through the live judge and reports the verdict distribution against the fitted
one in `knowledge/judge_calibration.json`. 20 distinct `(symbol, strategy)`
signals, five passes over the identical set, n=100, zero degraded.

| | pooled | across the 5 passes | fitted Claude (n=250) |
|---|---|---|---|
| `veto_rate` | 0.220 | **0.100 – 0.300** | **0.044** |
| `downsize_rate` | 0.760 | 0.700 – 0.900 | 0.858 |
| `approve_rate` (full size) | 0.020 | 0.000 – 0.050 | ~0.142 |
| mean scale, non-veto | 0.474 | 0.431 – 0.520 | 0.595 |
| exposure per signal | 0.370 | 0.320 – 0.440 | 0.569 |

**78 of 100 signals passed the judge.** §78's fix works: this is not a
differently-blocked bot. It is a materially more conservative one — roughly
**65%** of the prior exposure per signal, from a veto rate around five times the
fitted one and a judge that almost never approves at full size.

### The methodological finding, which is the part worth keeping

**The first two runs of this script, over identical inputs, returned
`veto_rate` 0.300 and 0.150. Both were reported as "the veto rate" before a
third pass made the problem obvious.**

The judge samples. A single pass is a draw, and at n=20 the draw moves further
than the effect being measured. The instrument now takes `--repeat` and prints
a range; a one-pass number from it should not be quoted.

Same family as §76's decay reading, the 171 `noop` capture rows, and the
mutation harness printing CAUGHT for a test file that did not exist — a
measurement that cannot express its own uncertainty reads as a result. Here the
uncertainty was 2× the headline number.

The script is committed rather than run ad hoc, and
`tests/test_judge_disposition.py` pins the three ways it could flatter the
judge: counting a degraded call as a non-veto (understates the veto rate),
reporting `0.0` where nothing was judged (makes "nothing got through"
indistinguishable from "nothing vetoed"), and comparing against constants pasted
into the script rather than read from the fit. That last test first fired on the
script's own docstring, which quoted one of the fitted values as an example.

### What this is NOT

**Not an agreement rate.** Different judge and different context vintage. No
local verdict here is paired with a Claude verdict on the same prompt.

And no such pairing can be reconstructed: the prompt sidecar
(`Ledger._store_prompt`) landed 2026-08-22, a day after the judge went down, so
it never captured a Claude decision — the `decision_prompts` stream does not
exist on the Bizon. Verified, not assumed. The contexts behind the 250-buy
calibration are gone.

What *is* now true is the reverse: with the judge working, every decision from
the next live cycle forward records the exact prompt it saw. When credits
return, Claude can be replayed against those. Divergence #25 carries it.

Trial count through §79: 241 arm runs. §79 runs no arms; K is closed at 16.

<!-- recall: section=§79 specs= -->

## §80 — GATES, BACKTESTS AND RAILS READ AFTER-COST RESULTS FIRST (DECISION, K stays closed at 16, 2026-10-28)

**Registers nothing and records no verdict.** Like §77 it is an owner's
decision, written down because a future reader needs it to understand why the
rails and the gates stopped measuring the same thing they measured before.

### The decision, in the owner's words

Connor, 2026-10-28:

> "Yes: make gates, backtests and the drawdown/retire rails use after-cost results first (repete4's override included). Entry cost filters later, bot by bot."

The cost is the operator's simulated cost per order (`src/operator_settings.py`,
models in `src/cost_models.py`, the console's `costs.py` verbatim). Until today
it only lowered the equity the console *showed*. Nothing that decides anything
saw it. For this bot (equities) the research values are US$0.005 a share, at
least US$1.00 an order, at most 1% of the order's value.

### What changed

**The live rails.** RAIL EQUITY = broker equity − every operator cost charged so
far, computed once per cycle right after the broker read (`main._rail_equity`)
and read from `memory/operator_fees.jsonl` by a STRICT reader
(`operator_settings.read_strict`): a missing file is 0; an unreadable file, a
torn line or a line in another currency is never 0 — the rails fail CLOSED
(every entry refused under `rail_equity_unknown`, an alert, a ledger event,
health DEGRADED). It feeds:

- **the drawdown breaker** — measured on rail equity against a peak kept on the
  same basis. The stored GROSS peak is moved ONCE, on the first run of this
  code, to `peak − cost charged up to now`; the file is stamped
  `"basis": "after_cost"` with the gross figure and the amount beside it, so a
  rerun is a no-op (`risk.update_high_water`).
- **the daily-loss kill switch** — today's rail equity against the broker's
  `last_equity` less the cost charged before today (New York day boundary).
- **the live kill** and **the decay monitor** — each closed trade's P&L less its
  OWN cost (entry fill + close, joined on the fee line's `ref`). The decay
  monitor's random null is charged the same model on both of its legs, so the
  two sides pay alike.

The cycle charges the entry fills it confirms BEFORE the rails read equity, and
refreshes rail equity after reconcile/adoption charge their closes, so no rail
sees a cost one run late. Broker equity is unchanged everywhere else: sizing,
the caps, and the `equity` on `cycle_complete` the console reads (it nets the
cost off itself; `rail_equity` now sits beside it). Health shows the drawdown
before and after cost.

**The backtester and the gates.** Every simulated fill pays
`cost_models.order_cost` on its shares and notional, topped up over any venue
fee rather than added to it; both benchmarks pay it too. The cost is kept OUT of
the sim's cash — live sizes on broker equity, which never includes it — and
comes off every trade's P&L, the equity curve (so maxDD and Sharpe), total
return, profit factor, the significance P&Ls, the sim's own drawdown rail and
therefore every clause. The block is PINNED in the gate spec (`operator_costs:`
— the cost-models block written out in full, or `false` for deliberately gross),
never read from the console's settings file, so a run is reproducible from its
spec. `register_gate.py` refuses a NEW spec that does not declare it (after the
already-registered and verdict checks, so every existing registration stays
idempotent); `run_gate.py` stamps the block, its sha256 and what each arm paid
on the verdict row.

### What did not change

- **No threshold.** `risk.live_kill` (15 trades, PF 0.8), `daily_loss_limit_pct`,
  `max_drawdown_pct`, the decay monitor's 5th/95th percentiles and every gate
  clause are exactly as registered. Only the P&L they read moved.
- **No frozen record.** Absent block = no operator cost, and `config.yaml` (which
  no spec hashes) is stripped of any block before a frozen spec runs. Four
  frozen specs were re-executed on this code and on the code before it
  (`origin/cost-models-v2`, b0b1387): `s39`, `s72a`, `s64a`, `s73a` — 8 arms,
  every arm summary, every clause and every trial row byte-identical; the
  verdict row gains only the three `operator_cost*` stamps (null / 0.0). (`s72a`
  also still matches its row in `research/verdicts.jsonl` exactly; `s39`,
  `s64a` and `s73a` drifted from theirs long before this change, identically on
  both codes.)
- **K.** Closed at 16. Nothing registered.
- **Sizing and entry filters.** "Entry cost filters later, bot by bot."

### The re-score: §43's pass mark, after cost

The live strategy is the shipped ensemble (`ma_crossover`, `tsmom`, `meanrev`),
and the gate it answers to is its own: §43, `s43a`–`d`. §43 ran with the judge
modelled; the judge calibration now REFUSES (`context_version` 3, refit
pending, §78), so a judge-on re-score cannot be run. What could be run was §43's
four clauses verbatim, judge OFF, once gross and once at the research cost
block, as a **scratch reproduce**: eight single-arm METHOD-shaped specs
registered only into a scratch registrations file, scored by `run_gate.py` with
`--registrations/--verdicts/--trials` pointed outside `research/`. Nothing here
was appended to the record.

| period | return gross → after cost | PF | maxDD | trades | cost paid | exposure-matched bar | clauses |
|---|---|---|---|---|---|---|---|
| 2000-2006 | −7.19% → **−7.46%** | 0.433 → 0.421 | 11.52 → 11.76% | 37 → 37 | $274.49 | +4.78% → +4.75% | 1/4 → 1/4 |
| 2007-2013 | +3.49% → **+2.62%** | 1.165 → 1.121 | 12.14 → 12.64% | 104 → 104 | $875.47 | +7.82% → +7.74% | 1/4 → 1/4 |
| 2014-2019 | +23.59% → **+20.37%** | 1.557 → 1.471 | 10.83 → 11.50% | 375 → 372 | $3,280.25 | +24.39% → +24.05% | 1/4 → 1/4 |
| 2022-2026 | +107.77% → **+105.84%** | 1.652 → 1.636 | 10.32 → 10.44% | 969 → 969 | $1,922.17 | +33.91% → +33.37% | 4/4 → 4/4 |

Block sha256 `97147170b54b8105…` (the research values). Cash $100,000.

**No clause flips in either direction.** The cost drag is 0.27pp to 3.22pp of
return, and the margins it eats are larger in every period. 2014-2019 is where
it bites: 375 round trips at roughly US$1 a side, and the period's
exposure-matched FAIL widens from −0.80pp to −3.68pp. 2014-2019 also shows the
after-cost drawdown rail at work in the simulator — three fewer trades, because
the sim's breaker now measures after cost, as live's does.

**Read the 2022-2026 row with care.** The gross control clears all four of
§43's clauses there. That is **not** a verdict and licenses nothing: the judge
is OFF (live, it cuts most buys and §43's judge-on run of this very period
failed by −9.84pp), the universe is survivor-selected (§48, §51), the shipped
config has moved since §43 was frozen, and this ran in scratch without a prior
written before the data. It is reported because leaving a passing cell out of a
table is how a record starts flattering itself. The after-cost cell is the
point of this section: it moves by −1.93pp and stays where the gross one is.

Both benchmarks pay the cost too, so the exposure-matched bar falls slightly
(by ~1pp × deployment — the universe buy-and-hold is 500 equal $200 slices,
each paying the US$1 minimum on the way in and out). That makes the after-cost
bar a little EASIER, never harder; no clause sat close enough for it to matter.

### Accounting

24 arm runs executed outside the record: the 8 re-score arms above and the 16
reproduction arms (8 per code). None was appended to `research/trials.jsonl`:
the brief forbade spending trial budget, none tests a hypothesis, and none
selects anything. They are counted here so the number is recoverable — a
reader who counts them reaches 241 + 24 = 265.

Divergence #26 records what still differs: the gates charge a pinned research
block while live charges whatever the console says that cycle, the simulator
charges a short's opening order while live charges only `buy` entries, and
live's once-per-cycle rail equity can sit a cycle's close costs behind.

Trial count through §80: 241 arm runs. §80 records no arms; K is closed at 16.

<!-- recall: section=§80 specs= -->
