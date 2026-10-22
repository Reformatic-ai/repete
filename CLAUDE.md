# CLAUDE.md — Instructions for Claude Code working on this project

## What this is
An autonomous **swing-trading** agent (stocks via Alpaca, **paper trading**) that:
documents every decision with reasoning in an append-only JSONL ledger, uses an
LLM only as a judgment layer (approve/downsize/veto), learns cautiously from
closed trades, and posts trade recaps to X. Built from an evidence-based design
(see `GUIDE.md` for the full walkthrough and the research rationale).

## Architecture (do not restructure without asking)
```
src/broker.py    Alpaca wrapper. Paper-mode double interlock lives here. Orders carry a
                 deterministic client_order_id (ta-SYM-side-YYYYMMDD, 2026-07-21) so a
                 crashed cycle rerun cannot double-submit. Socket timeout installed on
                 both alpaca-py clients (2026-08-06 — the SDK sets NONE anywhere, which
                 cost 246s on three dead sockets on 2026-08-05). READS retry on
                 connection-class errors inside a per-cycle budget; WRITES never do.
src/preflight.py Fail-SAFE startup validation (2026-07-21): risk params, interlock, env
                 keys, ledger-tail integrity, timeframe. Any failure = no trading this
                 cycle + ledger preflight_failure + macOS alert. Opposite polarity from
                 data outages (which degrade gracefully) — on purpose.
src/scorecard.py Monthly performance vs S&P from cycle_complete equity snapshots + SPY
                 bars (2026-07-21). Surfaces in review.py + dashboard. The benchmark
                 goal is measured every month and published — never promised, and never
                 wired into sizing/signals (goal-chasing is the documented failure mode
                 of every LLM-trader reviewed).
src/datacheck.py Second-vendor price cross-check (2026-07-21): SPY close Alpaca vs
                 yfinance each cycle; same-session divergence > cap => degradation
                 event + entries blocked that cycle (exits never). Vendor outage fails
                 open silently — fires only when both vendors report and DISAGREE.
src/deploycheck.py Is the RUNNING code the REVIEWED code? (2026-07-25, §26 divergence
                 #7: production traded 57 commits / 8,151 lines behind the repo for
                 three days, enforcing a max_open_positions §13 had already gated up,
                 and nothing noticed). Three read-only layers, ALL fail-open: running
                 sha (REPETE_GIT_SHA env first — the image ships without .git; the
                 Dockerfile takes a GIT_SHA build-arg and deploy.sh exports it), config
                 drift via `git status --porcelain config.yaml` (needs no network and
                 carries the real weight), and behind-upstream count. Wired in main.py
                 as degradation + alert, ONE per day — NOT through preflight, whose
                 polarity is fail-SAFE: stale gated params are wrong but not dangerous,
                 and halting a cycle over them costs a trading day to fix a reporting
                 problem. An "unknown" is deliberately NOT an alert (a container
                 legitimately has no .git; a daily false alarm mutes the channel). The
                 sha is stamped on every cycle_complete so drift stays reconstructable
                 from the ledger even when no alert fired.
src/revalidate.py Quarterly strategy re-validation, REPORT ONLY (2026-07-21): reruns
                 each enabled strategy's CURRENT live params (never a grid re-search)
                 through the walk-forward gate on recent data; prints + ledger
                 `revalidation` event. Never auto-disables — live_kill is the fast path.
src/strategies/  Strategy ensemble (deterministic ONLY; LLM never generates signals):
                 ma_crossover (baseline), tsmom, xsmom, meanrev. Registry in __init__.py;
                 config `strategies:` gates ENTRIES per strategy; exits always route to the
                 strategy that OWNS the position (tagged on its ledger record), even if since
                 disabled. Entries: priority order, first surviving buy takes ownership.
                 A strategy may be enabled live ONLY after passing backtest.enablement_gate
                 (walk-forward OOS: positive, PF>=1.3, >=15 trades, beats B&H raw /
                 risk-adjusted / exposure-matched).
src/strategy.py  Compatibility facade over strategies/ (legacy generate_signal + indicators).
src/llm.py       Judgment layer: approve / downsize / veto. Can NEVER enlarge or invent trades.
                 Runs on llm.model (claude-sonnet-5, right-sized 2026-07-18 — evals show no
                 thinking-model edge for judge roles); learning passes use learning.model (Fable).
                 Judge prompt includes a bull/bear debate step (2026-07-21, TradingAgents-inspired,
                 single call): both cases argued in the JSON before the verdict, stored on the
                 ledger's llm_review; calibration impact measured by the judgments scoreboard.
src/risk.py      Hard rails: sizing (meanrev: stop-distance risk sizing, gate 2026-07-19 §8,
                 superseded vol_target; others: 1% notional), caps, trade-rate limit,
                 daily-loss kill switch (HALT file), swing guard (min_holding_days blocks
                 early exits — no day trading), chandelier trail (tsmom only, §7 — stop
                 ratchets up, never down), re-entry cooldown (meanrev only, §9),
                 entry drift guard (2026-07-21: buy skipped when live quote drifts >
                 risk.max_entry_drift_bps from signal price; fail-open on quote outage;
                 entries only — would have blocked all six 2026-07-16 stale-bars fills),
                 correlation heat cap (2026-07-21, EastEquity review: buy blocked when
                 >= risk.correlation_cap.max_correlated open positions have >= threshold
                 return correlation with it — "co-moving names are one bet"; entries
                 only, fail-open without bars; fail-open guard skips are ledgered as
                 "degradation" events and counted by review.py — silence must stay
                 distinguishable from "checked and fine");
                 param-gated down-regime exposure cap exists but is OFF (cannot bind),
                 pre-registered live kill criteria (2026-07-21, risk.live_kill: a strategy
                 with >=15 live closed trades and PF<0.8 stops ENTERING, exits unaffected —
                 the live-side mirror of the enablement gate, registered before any strategy
                 was near the threshold). Degradation SLO: ops.max_degradations_per_day
                 fail-open events in one day -> slo_breach event + macOS alert (main.py).
src/ledger.py    Append-only audit trail. Outcomes written only after close (outcome embargo).
src/store.py     Event-store backend behind one 2-method interface (2026-07-22, backlog #4):
                 JsonlStore (DEFAULT, unchanged) | SqliteStore (container/server deploys).
                 store.configure(cfg) picks it ONCE at cycle start; a config typo falls
                 back to jsonl on purpose. Ledger/Lessons/Judgments/PostExit are all
                 backend-agnostic. Migrate + verify: scripts/migrate_jsonl_to_sqlite.py.
src/health.py    One status object (heartbeat age, HALT, degradations today, slo_breach,
                 backend, open positions) for the watchdog, container HEALTHCHECK and a
                 future status page. Read-only.
src/disclaimer.py Single source of the paper-trading/not-advice disclaimer (2026-07-22).
                 Dashboard, journal, blog footers render it; publisher/gates.py
                 re-exports it. Edit the text HERE only.
src/evidence.py  Audit pack exporter (Phase C, 2026-07-22): `python src/evidence.py`
                 writes a dated bundle (summary.md, performance.json, lineage.json,
                 invariants.json) — per-trade decision lineage + machine-checked
                 invariant spot checks. Offline + read-only by design (no broker,
                 no keys); a missing stream is noted, never a crash.
src/log.py       Structured JSON logging w/ secret redaction (Phase D, 2026-07-22):
                 attach_json_handler() mirrors logs to logs/agent.jsonl; env values
                 with secret-looking names are scrubbed to [REDACTED]. Additive —
                 the human-readable launchd logs are untouched.
src/memory.py    Retrieval layer: similar-setups trade sample for the judge (2026-07-21,
                 deterministic strategy/regime/symbol/indicator match — losers still
                 force-included; balanced random sample for signal-less callers),
                 ranked lessons, judge calibration + last-20-resolved-calls scoreboard,
                 regime — assembled into the review prompt.
src/lessons.py   Hypothesis book (memory/lessons.jsonl, append-only events + replay): falsifiable
                 lessons with a lifecycle candidate -> active | refuted | retired; staleness is
                 scope-tiered (symbol 21d / strategy 90d / regime 180d — learning.staleness_tiers). learnings.md is
                 a GENERATED view of this store — never hand-edit or treat it as source of truth.
src/judgments.py Judge calibration (memory/judgments.jsonl): every approve/downsize/veto logged,
                 later resolved (realized close or counterfactual) and scored; the judge sees its
                 own track record in the prompt. kind=llm and kind=rails bucketed separately.
                 Judgments also carry the judge's stated confidence (2026-07-21) — scored per
                 bucket vs realized win rate in review.py; measurement only for now (no caps).
                 The judge's context includes a deterministic CURRENT BOOK block (memory.py,
                 2026-07-21): open positions, unrealized P&L, gross exposure — broker-fresh.
                 risk.py also carries a portfolio heat cap (max_portfolio_heat_pct: total
                 open stop-risk + new entry risk <= 4% equity, entries only) and the
                 watchdog has a --catchup mode (launchd 15:55 ET: a missed 15:45 cycle
                 runs late while the market is still open instead of losing the day).
src/counterfactual.py  What a vetoed buy would have done (pessimistic stop-before-TP replay,
                 embargoed until min_holding_days + extra_days pass).
src/postexit.py  Post-exit runner tracking (memory/postexit.jsonl, append-only): every close
                 re-marked at 15/30/60d -> left_on_table | good_lock_in | mixed |
                 stopped_then_recovered | stop_confirmed. MEASUREMENT ONLY — feeds
                 review.py + future exit-rule gate evidence, never sizing/signals/judge.
src/modelver.py  Decision-surface fingerprint (config + strategy/risk/broker/llm code)
                 stamped on every ledger record; review.py segments the track record by
                 version. Stamp-only by design — no freeze; gates iterate the model.
src/regime.py    Deterministic market regime from SPY bars (trend x vol bucket); tags decisions,
                 judgments, and lesson scopes so off-regime evidence gets discounted.
src/market_context.py  Morning news awareness (Alpaca News API + free public RSS
                 from WSJ + CNBC + MarketWatch via news.rss_sources [headline/summary only, no
                 login/scraping/credentials — ToS-clean; full-text would need a
                 licensed API, not built] + LLM distill, 9:35 job):
                 today-only context for the judge/plan post + validated watchlist
                 NOMINATIONS. The LLM may summarize news and nominate scan symbols;
                 it still cannot generate, enlarge, or execute trades — a nominated
                 entry needs a deterministic strategy signal + judge review (told
                 "outside backtested universe, extra skepticism") + all rails, and
                 is capped at news.max_news_entries_per_cycle (1). Nominations are
                 validated deterministically (ticker shape, fresh bars, history).
                 Stale context is ignored; every failure = no context, normal trading.
                 Missed-run resilience (2026-07-21): if no context exists at cycle
                 start (all hourly fires missed), main.run_cycle self-heals with one
                 inline refresh before judging — fail-soft, never blocks the cycle.
src/learn.py     Learning engine: `python src/learn.py` (weekly, --meta for merge pass) +
                 learn.inline_pass() at every cycle end. Bounded LLM calls, never crashes a cycle.
src/x_poster.py  X recaps. dry_run default. Always disclose [PAPER]. Failures never block
                 trading. post_text = single choke point (disclosure + 275 cap + t.co link math).
src/journal.py   Public trade journal: ~500-word Fable-5 write-up per executed buy and per
                 close (template fallback) -> memory/journal.jsonl (append-only) -> journal.html
                 on the GitHub Pages site; recap tweets link to #<trade_id>. Cosmetic — never
                 blocks trading. Engagement metrics are NEVER read; learning stays outcome-based.
src/dashboard.py Self-contained dashboard.html — dark terminal theme, hero total-P/L banner
                 (equity - reporting.starting_equity), P/L-over-time + per-trade P/L charts,
                 equity curve, positions, decisions + judge reasoning (filter chips), lessons,
                 calibration, slippage; vanilla inline JS only (tooltips/count-up/filters).
                 Regenerated at every scheduled
                 touchpoint and published with journal.html via scripts/publish_dashboard.sh
                 to https://reformatic-ai.github.io/repete-dashboard/ (public repo).
                 Both the laptop (run_cycle.sh) and container (scheduler.py chains the
                 publish after the cycle/posts) paths push; publish is a clean no-op
                 without a .site/.git checkout + push creds.
                 The news brain (market_context) refreshes hourly 9:25-15:25 ET on
                 claude-haiku-4-5 (news.model) via com.repete.newsbrain.
publisher/       Phase B subscription publication (2026-07-22, PRODUCT.md): FastAPI
                 service reading agent state via ReadOnlyLedger, backed by a
                 genuinely read-only store (store.read_only_reader — read_all only,
                 no append; sqlite opened mode=ro) so invariant #9 is a structural
                 AttributeError, not just the compose :ro mount. /healthz reads
                 read-only too (health.status(read_only=True)) — it never runs
                 store.configure or issues DDL. Magic-link auth (token hashes only),
                 Stripe stub/live billing, tiered feed (free: 1-day delay, no judge
                 reasoning; paid: same-day + reasoning/debate/confidence + journal),
                 dry-run email outbox, DRAFT legal pages. The daily digest broadcast
                 (publisher/broadcast.py + scripts/send_digest.py) needs THREE
                 switches to mail anyone — digest.enabled AND email.dry_run:false AND
                 RESEND_API_KEY — and is not scheduled; its opt-out link is a
                 non-mutating GET confirm page because mail scanners prefetch links.
                 Checkout is refused in code
                 until gates.revenue_gate passes (invariant #10); the numeric
                 thresholds are HARDCODED constants (gates.MIN_CLOSED_TRADES /
                 MIN_HISTORY_DAYS), not config knobs, and the billing webhook refuses
                 to grant while the gate is closed (unsigned stub grants need an
                 explicit opt-in). Own mutable state in publisher_data/ (gitignored),
                 NEVER in memory/. Per-IP token-bucket rate limiting (Phase D,
                 publisher/ratelimit.py — strictest on /auth/request-link, the
                 email-sending endpoint; keys on X-Forwarded-For only behind
                 publisher.trust_proxy; knobs under publisher.rate_limit).
docs/            Ops docs (Phase D, 2026-07-22): runbooks.md, incident_response.md,
                 secrets_rotation.md, slo.md, soc2_readiness.md (readiness map, NOT a
                 compliance claim), go_live_checklist.md (the ordered laptop->live-product
                 list — gates are never waived by enthusiasm).
scripts/backup.sh + scripts/restore_drill.py  State backup (scheduler 17:00 ET, keeps
                 14 local + 30 in iCloud; .env never archived) + weekly restore drill
                 (Sat 10:00) — a backup that has never been restored is a hope, and a
                 backup on the same disk as the thing it protects is not off-site. The
                 drill also checks content HASHES (a corruption can preserve both line
                 count and JSON validity), that each archived stream is a byte-PREFIX of
                 live (append-only means live may only have GROWN — divergence means
                 history was rewritten), and that the newest archive exists off-host with
                 a matching hash. REPETE_OFFHOST_DIR overrides the mirror; the test
                 suite sets it so no test can write into real iCloud. In the container,
                 backups/ is a mounted volume so archives survive rebuild/redeploy.
scripts/rotate_logs.sh  Bounds logs/ (daily 17:05, 5 MB x 5 generations). COPYTRUNCATE,
                 not rename, and that is the whole design: FOUR processes write
                 logs/agent.log (main, watchdog, daily_posts, backfill) and renaming
                 would leave three of them appending into an orphaned inode. Truncating
                 in place keeps the inode, so no Python handler needed changing — and
                 it is the only thing that can bound cron.log (13 shell `>>` redirects)
                 and launchd.err.log (StandardErrorPath in 12 plists), which Python
                 cannot reach at all. Growth is ~17 KB/trading-day, so this is insurance
                 against a crash loop, not a volume problem.
scripts/install_launchd.sh  Renders the launchd plist templates ({{AGENT_ROOT}}
                 placeholder) for the current checkout, plutil-lints, installs to
                 ~/Library/LaunchAgents, optional --load. The plists ship path-agnostic
                 so a moved/cloned repo never runs a stale absolute path.
.github/workflows/ci.yml  Tests + pip-audit dependency CVE scan on every push/PR
                 (suite is offline; no secrets in CI ever).
src/main.py      Orchestrator: state -> signal -> judge -> rails -> execute -> ledger -> learn -> post.
config.yaml      All parameters. .env holds secrets (never commit).
```

## Safety invariants — NEVER weaken these, even if asked casually
1. **Paper by default.** Live trading requires BOTH `mode: live` in config.yaml AND
   `LIVE_TRADING_CONFIRMED=YES` in .env. Never remove or bypass this interlock.
2. **The LLM cannot override risk rails.** `risk.py` checks run after the LLM review,
   in deterministic code. Keep it that way.
3. **Swing-only.** The swing guard (`min_holding_days`) must keep blocking exits on
   young positions. Only the daily-loss kill switch and broker-side protective
   stop/take-profit bracket legs (set deterministically at entry, before any LLM
   involvement; the chandelier trail may deterministically RAISE a stop leg later,
   never lower or remove it) may exit before `min_holding_days`; the guard
   continues to block all strategy-signal exits on young positions. `timeframe`
   stays `1Day`.
4. **Positions/equity always read fresh from the broker** (`broker.account()`,
   `broker.positions()`) — never inferred from memory, the ledger, or prior LLM output.
5. **Outcome embargo.** Lessons/evidence are generated only from CLOSED trades;
   counterfactual resolutions wait out the full horizon (min_holding_days + extra).
   Memory samples must keep force-including losing trades, and lesson retrieval keeps
   force-including a refuted CAUTION when any exist.
6. **Learning is conservative-only and validated.** Lessons feed ONLY the veto/downsize
   review prompt (never sizing/signals) and must EARN `active` status from evidence
   (>=3 supports, 2x contradicts); refutation and staleness retire them. All lifecycle
   transitions are ledger-mirrored. `learnings.md` is a generated view of
   `memory/lessons.jsonl` — never hand-edit it.
7. **X posts disclose [PAPER]** while paper trading. Never remove the disclosure.
8. **Secrets stay in .env** and out of git. Never print keys to logs.
9. **The publisher layer is READ-ONLY.** Nothing in a subscriber/billing/email/web
   surface may write to the ledger or influence a trading decision — a cycle must
   behave identically whether the publisher runs or not (see PRODUCT.md). Publishing
   that could affect trading would stop the track record from being evidence.
10. **No money before the revenue gates.** PRODUCT.md: >=30 closed trades, >=3 months
   live, attorney review, legal pages published. Same discipline as the trading gates.
11. If the owner asks to go live, walk them through the go-live gate in GUIDE.md §9
   first (2–3 months paper, ≥30 closed trades, beats buy-and-hold) instead of just flipping it.

## Conventions
- Python 3.11+, no framework. Keep modules small and dependency-light.
- Every new decision path must write a ledger record (including skips/rejections).
- External-call failures (LLM, X, data) degrade gracefully; they never crash the cycle.
- Test logic without network: import modules with a dict config and fake bars
  (see GUIDE.md; a crossover fixture is `[10]*6 + [9,9,9,20]` with fast=3/slow=5).

## Prioritized backlog (good next tasks)
1. ~~**Bracket orders**~~ DONE: ATR-based stop-loss + take-profit legs
   (`risk.brackets` in config.yaml, off by default; GTC legs; broker-side exits
   reconciled into the ledger at cycle start by `main.reconcile_closed_positions`).
2. ~~**Backtest harness**~~ DONE: `python src/backtest.py` — offline replay through
   `strategy.generate_signal` + `risk` sizing with fee/slippage, walk-forward split,
   vs buy-and-hold; every variant logged to `research/trials.jsonl`.
3. ~~**Weekly review command**~~ DONE: `python src/review.py` — skeptical report
   (win rate, PF, vs SPY, lesson staleness) scored against the GUIDE.md §9 go-live gate.
4. **SQLite ledger backend** behind the same `Ledger` interface (JSONL stays default).
5. **Morning plan / evening review X posts** (respecting dry_run and [PAPER] disclosure).
6. ~~**Unit tests**~~ DONE: `tests/` (pytest, fully offline; run
   `.venv/bin/python -m pytest tests/ -q`).
7. ~~**Data-freshness guard**~~ DONE 2026-07-16: `risk.bars_fresh` +
   `risk.max_bar_age_days` — stale SPY aborts the cycle, stale symbols are
   dropped (response to the stale-bars API bug).
8. ~~**Heartbeat + watchdog**~~ DONE 2026-07-16: `memory/heartbeat` written on
   every cycle exit; `src/watchdog.py` (launchd 16:15 weekdays) alerts via
   macOS notification when a weekday cycle didn't run or HALT is engaged.
9. ~~**Fill-quality tracking**~~ DONE 2026-07-16: `main.record_fill_quality`
   appends measured slippage (signal vs fill, bps) per trade; surfaced in
   `review.py` for the go-live cost comparison.
10. ~~**tsmom index gate / meanrev entry cap / vol-regime stops**~~ DECIDED,
    final 2026-07-17 on a FROZEN data snapshot: ALL REJECTED (index gate was
    briefly adopted 07-16 on what turned out to be API re-fetch drift, then
    reverted). Param-gated code paths remain. See
    knowledge/backtest_candidates.md §2–4 + its METHOD NOTE: **all backtest
    variant comparisons must use one frozen `--bars-file` snapshot** — live
    re-fetches of the same window drift intraday.
11. ~~**Earnings-blackout entry filter**~~ DECIDED 2026-07-17 (frozen
    snapshot): ADOPTED for tsmom (N=3, `strategies.tsmom.earnings_blackout_days`),
    REJECTED for meanrev (hurts the dip edge) — per-strategy param, yfinance
    calendar via src/earnings.py (cached, fail-open). See
    knowledge/backtest_candidates.md §1.
12. **Dashboard + daily posts** DONE 2026-07-17: `src/dashboard.py` renders
    dashboard.html each cycle; `src/daily_posts.py` posts a 9:35 plan and
    4:20 review (launchd com.repete.dailypost), read-only scans,
    [PAPER] enforced.
13. **common-trade ports** DONE 2026-07-19 (research in
    knowledge/port_research_2026-07-19.md; gates in backtest_candidates.md
    §7–§9): post-exit runner tracking (src/postexit.py), model-version
    fingerprint (src/modelver.py), citation-graded lessons, secrets-hygiene
    test, gap-adjusted heat report (review.py), chandelier trail (ADOPTED
    tsmom), stop-distance risk sizing (ADOPTED meanrev, superseded
    vol_target), re-entry cooldown (ADOPTED meanrev).
