# Beginner's Guide: Building a Swing-Trading Agent From Zero

This guide walks you, step by step, from nothing to a running **paper**-trading
agent that documents every decision with its reasoning, learns from closed
trades, and publishes both to its own blog and trade journal.

The reasoning behind the choices is in `knowledge/backtest_candidates.md` (every
pre-registered claim and its verdict) and `knowledge/principles.md` (the rules
the project holds itself to). Where popular advice conflicted with what the
gates measured, this project follows the measurement.

> **Corrected 2026-07-29 (W5-4).** This heading said *"Enterprise-Grade"*, and
> this paragraph cited a `trading-bots-deep-research.md` that **exists nowhere in
> the repo** while claiming the bot "posts recaps to X" — X has been off since
> ~2026-07-24. All three were false. "Enterprise-Grade" is the one worth naming:
> §43 measured the shipped configuration against its own enablement gate on four
> periods spanning 26 years and it failed **all four**. See
> [README.md](README.md) for what this project has and has not shown.

**This is a swing-trading bot, not a day-trading bot — by design and by code.** It decides once per day on daily bars, and a hard "swing guard" in the risk rails blocks any exit before a position is at least `min_holding_days` old (default 2), so same-day round trips are structurally impossible. That's the right call: the research base rates on day trading are dismal (~97% of persistent day traders lose money), while the only strategy class with century-scale evidence net of costs is slow, multi-week trend-following. The one exception to the swing guard is the daily-loss kill switch — if the account breaches its loss limit, safety wins and everything is flattened regardless of holding period.

**The one rule above all others: this bot runs on paper money until §8 says otherwise.** Nothing in this guide is financial advice.

---

## How this bot is designed (read this first — 2 minutes)

The video's architecture puts the LLM in charge. The research says that's backwards: LLM-in-the-loop agents lose to deterministic code in fast decisions, hallucinate state, and get *worse* with naive memory. So this project uses the architecture the evidence supports:

```
                    ┌─────────────────────────────────────────┐
 Alpaca (paper) ──► │ 1. STATE  — positions/equity read from   │
                    │    the broker every cycle (never from    │
                    │    memory; the LLM can't corrupt it)     │
                    │ 2. SIGNAL — deterministic strategy code  │
                    │    (MA crossover example included)       │
                    │ 3. JUDGE  — Claude reviews the signal    │
                    │    vs. memory: approve / downsize / VETO │
                    │    only. It can never enlarge or invent  │
                    │    a trade.                              │
                    │ 4. RAILS  — hard risk checks in code:    │
                    │    sizing caps, trade-rate limit, daily  │
                    │    loss kill switch → HALT file          │
                    │ 5. EXECUTE → 6. LEDGER (every decision   │
                    │    + reasoning, append-only JSONL)       │
                    │ 7. LEARN  — lessons only from CLOSED     │
                    │    trades, losers force-included         │
                    │ 8. POST   — X recap (dry-run by default) │
                    └─────────────────────────────────────────┘
```

"Enterprise level" here means: full audit trail, deterministic state, hard limits the AI can't override, kill switch, graceful degradation (LLM down? bot still runs on rules; X down? trading continues), secrets out of code, and a documented go-live gate.

---

## Step 1 — Install the basics (~20 min)

1. **Install Python 3.11+**: from [python.org](https://www.python.org/downloads/) (check "Add to PATH" on Windows) or `brew install python` on Mac.
2. **Get the project onto your machine**: `git clone https://github.com/Reformatic-ai/repete.git ~/repete` (this said "unzip `trading-agent.zip`" until 2026-07-29 — it was a zip once and has been a public git repo since; the repo was named `trading-agent` until 2026-08-23, and GitHub redirects the old URL).
3. **Open a terminal in that folder** and create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Step 2 — Alpaca account + API keys (~15 min)

1. Sign up free at [alpaca.markets](https://alpaca.markets). No deposit needed for paper trading.
2. In the dashboard, make sure the toggle (top-left) says **Paper** — the paper account comes pre-loaded with $100k of fake money.
3. Go to **Home → API Keys → Generate**. Copy both the key and the secret immediately (the secret is shown once).
4. In the project folder: `cp .env.example .env`, then open `.env` and paste your keys into `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.

**Security habits that matter from day one** (this is where real people lost real money — see the 3Commas incident in the research report): never commit `.env` to git (the included `.gitignore` blocks it); when you eventually create *live* keys, disable withdrawal permissions and restrict by IP; treat keys like cash.

## Step 3 — Anthropic key for the judgment layer (~5 min, optional but recommended)

1. Create an API key at [console.anthropic.com](https://console.anthropic.com) (usage-based; this bot's usage is a few cents/day at one cycle daily).
2. Paste it into `ANTHROPIC_API_KEY` in `.env`.
3. Skip this entirely if you want: the bot detects a missing key and runs pure rule-based. That's the graceful-degradation design.

## Step 4 — First run (paper, X in dry-run)

```bash
python src/main.py
```

You should see: broker mode PAPER, your equity, then one line per symbol (most days: `HOLD — no crossover this bar`). When a crossover fires you'll see the LLM review, the risk-rail check, the order, and a printed **dry-run X post**.

Now look at your audit trail:
- `memory/ledger.jsonl` — one JSON line per decision, including holds, vetoes, and risk rejections, each with the strategy's reasoning and the LLM's review. This is your enterprise documentation layer.
- `logs/agent.log` — operational log.
- `memory/learnings.md` — will populate as trades close.

**Tip to see it trade sooner (without day trading):** crossovers on a 10/30 daily MA are rare — you may wait weeks for the first trade. To watch the full pipeline fire during your first days of paper testing, temporarily set `fast_period: 3`, `slow_period: 8` and add a few more volatile symbols. Keep `timeframe: 1Day` — this bot is a swing trader and the swing guard assumes daily decisions. Set the periods back before you start the real 2–3 month paper evaluation.

## Step 5 — Understand (then replace) the strategy

The included MA crossover is a **teaching device** — the same one from the video. Know what the research says: single-asset MA crossovers have no documented edge after costs, and the best-looking backtest rules failed out-of-sample once data-snooping was corrected. The plumbing (state → signal → judge → rails → ledger → learn → post) is the durable part of this project; the strategy is the part you're expected to replace.

When you design your own (edit `src/strategy.py` — anything that returns a `Signal` works):
- Log every variant you backtest; trying 50 variants and keeping the best one is how you manufacture a fake edge (the "False Strategy Theorem").
- Use walk-forward testing (optimize on window A, test on unseen window B, roll forward).
- Prefer parameter *plateaus* (works at 9/28 and 11/32) over sharp peaks (only works at 10/30).
- Model fees and slippage; compare everything against just buying and holding SPY.
- Free backtesting rigs to graduate into: [QuantConnect](https://www.quantconnect.com) (point-in-time data, avoids survivorship bias) or [vectorbt](https://vectorbt.dev) for fast parameter sweeps.

## Step 6 — Schedule it (make it autonomous)

One cycle per bar. For the default daily timeframe, run once per weekday before the close (e.g., 3:45 PM ET):

**Mac/Linux** — `crontab -e`, then:
```
45 15 * * 1-5  cd /path/to/repete && ./venv/bin/python src/main.py
```
**Windows** — Task Scheduler → Create Basic Task → Daily → Start a program: `C:\path\to\venv\Scripts\python.exe` with argument `src\main.py`, "Start in" = project folder.

For 24/7 reliability later, move it to a $5/month VPS (DigitalOcean/Hetzner) — same cron setup.

**Why 3:45 PM ET, and how it differs from the backtest.** Running just before
the 4:00 PM close means signals read today's *nearly complete* daily bar and
orders fill within minutes, same session. The backtest instead assumes the
signal fires on the *completed* close and fills at the *next open* — a more
conservative assumption (it eats the overnight gap). The live edge should
therefore be at least as good as backtested, but the two are not identical:
a large move in the final 15 minutes can produce a signal the completed bar
wouldn't have. Accepted trade-off; do not move the schedule earlier in the
day, where the partial bar is far less representative.

## Step 7 — Connect X (~20 min) — OPTIONAL, AND CURRENTLY SWITCHED OFF

> **X is off in the shipped config** (`x_posting.enabled: false`, owner decision
> 2026-07-28; `dry_run: true` since 2026-07-29). **You can skip this entire step
> and lose nothing** — the bot still composes every post and writes it to
> `memory/posts.jsonl` with status `x_disabled`, and `blog.html` and
> `journal.html` render from that archive. `src/x_poster.py` archives *before* it
> attempts delivery, so the public pages are unaffected by X being unreachable,
> unconfigured, or off. That decoupling was itself a bug fix: `blog.render` once
> filtered on `status == "posted"`, and the public blog sat frozen for three days
> while the bot placed ten trades.
>
> Until 2026-07-29 this step read as a working feature and step 5 told you to set
> `dry_run: false`. Both were wrong to leave standing. The instructions below
> still work — they are just not something you need.

1. Apply at [developer.x.com](https://developer.x.com) (Free tier: ~500 posts/month — plenty for recaps).
2. Create a Project + App. In app settings → **User authentication settings**: enable OAuth 1.0a, set app permissions to **Read and write**.
3. In **Keys and tokens**: copy the API Key/Secret and generate an Access Token/Secret **after** setting read-write (regenerate them if you set permissions later).
4. Paste all four `X_*` values into `.env`.
5. **Two flags, both deliberate.** Set `x_posting.enabled: true` to arm the sink
   at all, and leave `dry_run: true` while you read the printed posts for a few
   cycles. Only set `dry_run: false` once you have read what it would have
   published. Requiring both is the point: `enabled` also governs the blog and
   journal pipeline, so one flag alone must never be enough to start posting
   publicly.

Two things the bot enforces on purpose: every post discloses **[PAPER]** while paper trading (keep `disclose_paper: true` — posting fake trading results as real is exactly the behavior the FTC fined Warrior Trading $3M for), and an X outage never interrupts trading.

## Step 8 — The learning loop (and its guardrails)

What happens automatically: when a position closes, the P&L is written to the ledger (`outcome` record), Claude writes **one cautious, falsifiable observation** to `memory/learnings.md`, and before each new trade the judgment layer reads a **balanced sample** of recent closed trades — with losing trades force-included at ≥20%.

Those guardrails exist because the best controlled experiment on agent memory found naive memory made a profitable agent *unprofitable* — retrieval over-serves winning trades and the LLM gets overconfident. Hence: lessons only from closed trades (outcome embargo), losers always in the sample, lessons phrased as hypotheses ("n=1: ..."), and the LLM allowed only to veto or downsize — never to trade bigger because it "learned" something.

**Your weekly ritual (15 min, the human part of the loop):** open `memory/learnings.md` and `memory/ledger.jsonl`, and ask — is the bot vetoing good trades or approving bad ones? Are lessons overfitting to fewer than ~30 trades? Delete or annotate stale lessons (regimes change). You can paste both files into Claude and ask for a skeptical weekly review.

## Step 9 — Go-live gate (do not skip, do not shortcut)

Only after **all** of these:
- [ ] ≥ 2–3 months of paper trading (≥ 30 closed trades — below that, results are noise)
- [ ] Paper performance ≥ buy-and-hold SPY over the same period, after estimating fees
- [ ] You've watched the kill switch work (set `daily_loss_limit_pct: 0.1` for one day to test it)
- [ ] Live keys created with withdrawal disabled + IP allowlist; funded with only 1–3% of what you could afford to lose entirely
- [ ] `mode: live` in config **and** `LIVE_TRADING_CONFIRMED=YES` in `.env` (the double interlock)

Run paper and live in parallel for the first month and compare — the gap between them is your real slippage/fee cost. Expect live to underperform paper.

## Step 10 — Where to go next

In rough order of value: replace the strategy with something walk-forward tested (§5) → add bracket/stop-loss orders (`alpaca-py` supports them in `broker.py`) → move the ledger from JSONL to SQLite or Supabase once it's thousands of rows → add a morning "plan" post and evening "review" post → try the Alpaca MCP server with Claude Desktop for interactive research alongside the autonomous bot (that's the interactive workflow from the video; this project is the autonomous one).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `KeyError: ALPACA_API_KEY` | `.env` missing or not in the folder you're running from — run from the project root |
| `403/401` from Alpaca | Keys are from the live dashboard, not paper — regenerate under the **Paper** toggle |
| Every symbol says HOLD forever | Normal for daily bars; crossovers are rare. See the §4 tip |
| X post fails with 403 | Access tokens generated before permissions were read-write — regenerate them |
| Bot refuses to trade, mentions HALT | The kill switch fired. Read `HALT`, understand why, then delete the file |
| Sell rejected with "swing guard" | Working as intended — the position is younger than `min_holding_days`; the exit will be re-evaluated on a later cycle |
| LLM review always "disabled" | No `ANTHROPIC_API_KEY` set, or `llm.enabled: false` in config |
| Preflight refuses on the key | The key is present but malformed — usually several pasted end to end. `.env` must hold exactly ONE `ANTHROPIC_API_KEY=` line. Edit it somewhere you can see the text; an echo-disabled shell prompt is how the 2026-07-27 five-line mess happened |

## What this bot will NOT do

It will not print money. The research base rates: ~97% of persistent day traders lose; backtests barely predict live results; 4 of 6 frontier LLMs lost >30% trading real money in a public test. What this project gives you is the thing most retail bots lack — honest instrumentation. Every decision documented, every outcome measured against buy-and-hold, every lesson dated and falsifiable. If an edge exists in your strategy, this system will show it to you; if it doesn't, it will show you that too, on fake money, before it costs you anything.

## Licence

Apache-2.0 — see [LICENSE](LICENSE). `SPDX-License-Identifier: Apache-2.0`

Added 2026-07-27. Until then the repository was public with no licence file,
which under default copyright means nobody could legally use, copy or fork it —
including anyone it was being shown to.
