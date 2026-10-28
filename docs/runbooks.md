# Runbooks

One page per failure mode. Format: symptoms → diagnose → fix → verify.
General rule: the agent fails SAFE on config problems (no trading) and fails
SOFT on data/LLM/X problems (degrades, keeps going) — know which kind you're
looking at before touching anything.

Quick status from anywhere:

```bash
python src/health.py            # one status object; nonzero exit = unhealthy
python src/review.py            # full skeptical report
tail -5 logs/agent.jsonl        # structured log tail (secrets redacted)
```

**Stop it now:** `./scripts/halt.sh "why you are stopping it"`

---

## STOP TRADING NOW (operator-initiated halt)

**When:** you want new positions to stop opening — a data source looks wrong,
fills look wrong, news you do not want the bot trading into, or you simply want
it to stand still while you look at something.

**What it does:** blocks NEW ENTRIES, immediately. `risk.check_halt` is
consulted once per cycle *and again per order*, so this stops the very next
order, not merely the next cycle.

**Two modes. Pick by asking: do you still trust the bot and the broker?**

| | `./scripts/halt.sh "why"` (default) | `./scripts/halt.sh --freeze "why"` |
|---|---|---|
| new entries | blocked | blocked |
| the cycle | **runs** | does not run at all |
| your open positions | still managed — closed when their strategy says to | untouched by the agent; **only broker-side bracket legs protect them** |
| use it when | "stop adding risk" — the market has gone mad, you want the book wound down on its own terms | the BOT or the BROKER is what you distrust: bad fills, a suspect feed, a bug |

The distinction matters most in the case that reads backwards: if you halt
*because the broker is filling badly*, the default mode would keep sending it
sell orders. That is what `--freeze` is for.

**Neither mode sells anything by itself.** `exits` only acts when a strategy
signals an exit; `freeze` does not act at all. Neither is a substitute for
deciding what to do with an open book.

**If you actually want the book flat, close the positions yourself** — through
the broker, deliberately, having decided that is what you want. There is no
scripted flatten, on purpose.

**History (2026-08-02):** HALT used to be `freeze` always, while `halt.py`
claimed positions kept "running to their normal stops and exits". They did not
— the cycle returned before any signal was evaluated. PR #72 corrected the
wording; the mode split made the original promise available for real.

```bash
./scripts/halt.sh "the market has gone mad"              # entries blocked, exits still run
./scripts/halt.sh --freeze "fills look wrong on every order"   # stop everything
./scripts/halt.sh --status                               # is it on, which mode, and why
./scripts/halt.sh --clear                                # resume trading
```

**Verify:** `./scripts/halt.sh --status` reports engaged and names the mode.
The next cycle logs `halted_exits_only` (exits mode) or `halted_cycle_skipped`
(freeze), and blocked entries record the `halt` rail. The halt is also ledgered
(`halt_engaged`) and alerted, so a second operator sees it.

**Notes:**

- Halting twice does **not** overwrite the original reason **or its mode**.
  Re-running with `--freeze` on a live `exits` halt changes nothing — clear it
  and engage again. Silently escalating an engaged kill switch while reporting
  "already engaged" is exactly the surprise to avoid.
- A HALT file written before modes existed (no `mode:` line) reads as
  **freeze**. Whoever pulled it believed it stopped everything, and a missing
  line is not permission to resume trading.
- It refuses to halt without a reason — an unexplained HALT cannot be safely
  cleared later, because nobody can tell it from a stray file.
- A failing ledger write or a dead alert webhook does **not** prevent the halt.
  Those are the same things that tend to be broken during a real incident.
- **Rehearsing this? Set `REPETE_ALERTS_OFF=1`.** `halt.py` sends a real alert,
  so a drill pages you for an incident that is not happening. On 2026-08-02 a
  single evening's rehearsals and test runs delivered eight false notifications;
  an alert channel that cries wolf during development is one you learn to swipe
  away, and a muted channel is worse than none. The test suite is already
  covered — `alerting.send` refuses while `PYTEST_CURRENT_TEST` is set — so this
  is only for manual runs and drills.

  ```bash
  REPETE_ALERTS_OFF=1 ./scripts/halt.sh "rehearsal, not a real halt"
  ```

  It suppresses **delivery only**: the HALT file is still written, the ledger
  still records it, and the suppressed alert is still logged at INFO.

---

## HALT engaged (daily-loss kill switch)

**Symptoms:** `HALT` file exists in the repo root; cycles log "halted" and do
nothing; watchdog notification fired.

**Diagnose:**
```bash
cat HALT                                    # reason + timestamp written at trip
# The trip and the skipped cycles are DIFFERENT events, and neither is named
# "halt". Grepping for "halt" alone returns nothing, which during an incident
# reads as "the kill switch never fired".
grep '"event": "kill_switch"' memory/ledger.jsonl | tail -3
grep '"event": "kill_switch_flatten_failed"' memory/ledger.jsonl | tail -3
grep '"event": "halted_cycle_skipped"' memory/ledger.jsonl | tail -3
```

The kill switch always engages a **freeze**, never `exits`, and that is
load-bearing rather than stylistic: it engages HALT *before* flattening
specifically so the next cycle cannot re-enter the same path and re-call
`flatten_all()`. Under `exits` it would do exactly that, every cycle, for as
long as the daily loss stood — and return early before any exit ran anyway.

**CHECK THIS FIRST: did the flatten actually succeed?**
```bash
grep '"event": "kill_switch_flatten_failed"' memory/ledger.jsonl | tail -3
grep '"event": "kill_switch_flatten_retry"' memory/ledger.jsonl | tail -5
grep '"event": "kill_switch_flatten_recovered"' memory/ledger.jsonl | tail -3
grep '"event": "kill_switch_flatten_abandoned"' memory/ledger.jsonl | tail -3
```

Success is judged by **re-reading the book from the broker**, not by the absence
of an exception (`flatten_all` is `cancel_orders` + `close_all_positions`, and
the close-all reports per-position results rather than raising when only some of
them close). So `kill_switch_flatten_failed` means positions genuinely remain.

**Since 2026-08-02 this retries itself.** The kill switch tries three times
in-cycle, then writes a `flatten: pending` marker into the `HALT` file and the
`flatten-retry` job re-attempts every 15 minutes through the session
(09:30–16:00 ET), up to `risk.kill_switch.max_attempts` (8, ≈2 hours).

| you see | meaning | what to do |
|---|---|---|
| `kill_switch_flatten_recovered` | the book is flat | nothing — HALT stays on for you to clear deliberately |
| `kill_switch_flatten_retry` | still trying | watch; alerts fire per attempt |
| `kill_switch_flatten_abandoned` | **budget spent, positions still open** | close them at the broker yourself |

`cat HALT` shows the pending marker and the attempt count.

**Clearing HALT also cancels a pending flatten** — the marker lives inside the
HALT file precisely so `rm HALT` cannot leave an automatic liquidation armed
against a book you have just decided to keep. If you clear it and still want the
positions worked down, re-engage with `./scripts/halt.sh "post-kill-switch"` so
the agent manages exits while opening nothing.

To turn the automation off entirely, set `risk.kill_switch.auto_retry_flatten:
false`; the failure is then marked and alerted, and waits for you.

**Fix:** This is the one guard that *should* require a human. Read the ledger
around the trip, understand which positions caused the loss, decide whether
the day was an anomaly (gap, halt, fat print) or the strategy misbehaving.
Only then: `rm HALT`.

**Verify:** next cycle runs (`python src/main.py` manually or wait for the
schedule) and `python src/health.py` shows `halted: false`.

---

## Drawdown circuit breaker ENGAGED (the bot has stopped buying, permanently)

**Symptoms:** alert "drawdown circuit breaker ENGAGED"; `python src/health.py`
prints `DEGRADED` with a `dd=…%` field and no headroom left; the bot holds and
sells but never opens a new position.

**Read this first: it will NOT clear itself.** This is not a cooldown. The
equity high-water mark ratchets UP and never down (`risk.update_high_water`),
which is correct on its own — a peak that followed equity downward could never
fire. But combined with entries-blocked there is no recovery path:

> equity peaks at **P** → a ≥10% drawdown blocks every entry → open positions
> exit normally and the book goes to cash → **in cash, equity is flat** at
> ~0.9P → the peak stays **P** → the drawdown stays ≥10% → **the bot never buys
> again.**

§40 measured what this does historically: in 2022–2026 the rail blocked
**99.43%** of every buy signal, and the bot executed 29 trades out of 245,213.
§41 and §44 both tested candidate automatic fixes (peak decay, with and without
reduced sizing) and **both were REJECTED**, so the latch is deliberately still
here and clearing it is a human decision.

**Diagnose:**
```bash
python src/health.py --json | python -c "import json,sys; print(json.load(sys.stdin)['drawdown'])"
```
`engaged: true` with a real `drawdown_pct` is a genuine drawdown. `engaged:
true` with `drawdown_pct: null` and an "unreadable" note is a corrupt
high-water file instead — that one self-heals on the next cycle's write, so
wait one cycle before touching anything. `engaged: true` with
`drawdown_pct: null` and a note that the **operator fee ledger** is unreadable
(or that the last cycle could not read it) is not a drawdown either: it is
[`rail_equity_unknown`](#rail_equity_unknown-operator-fee-log-unreadable-entries-blocked),
below. Do not reset the peak for it.

**Fix — only after you have decided the drawdown is acceptable to resume from:**
```bash
rm memory/.equity_highwater.json
```
The next cycle reseeds the peak from current equity, so the drawdown resets to
0% and entries resume. **This forgives the drawdown rather than recovering
it** — you are choosing a new peak, and the rail will then measure from there.
If you are not willing to make that call, the correct action is to leave it
engaged; the book still exits normally while blocked.

**Verify:** `python src/health.py` shows headroom restored, and the next cycle
logs a new peak.

---

## rail_equity_unknown (operator fee log unreadable; entries blocked)

**Symptoms:** alert titled "Repete: operator fee ledger unreadable"; a
`rail_equity_unknown` event in the ledger; that cycle's entries written as
refused decisions with `rail: "rail_equity_unknown"`; `cycle_complete` carries
`"rail_equity": null`; a swing-scan pass writes `swing_scan_entries_blocked`
with the same reason. `python src/health.py` prints `DEGRADED` (and the
container goes `unhealthy` in `docker compose ps`) with two problems: "operator
fee ledger unreadable (…) — rail equity is unknown, so the rails fail CLOSED"
and "drawdown circuit breaker ENGAGED — operator fee ledger unreadable …".
**Exits still run**: strategy exits, bracket legs and reconcile all work, and
each close is still charged its fee. The weekly decay check exits 2 ("could
not run") while it lasts.

**Read this first: do NOT follow the `rm memory/.equity_highwater.json` hint**
that health prints after the ENGAGED line. That hint belongs to a real
drawdown. This one is not a drawdown and clears itself once the fee log reads;
removing the peak would forgive a drawdown nobody reviewed.

**Why it fails closed:** since 2026-10-28 (owner decision, §80) the drawdown,
daily-loss and live-kill rails read **rail equity** = broker equity minus every
operator cost in `memory/operator_fees.jsonl`. They read it with a STRICT
reader (`operator_settings.read_strict`), because a skipped line is a cost read
as $0, and $0 flatters every rail. A missing file is fine (nothing charged
yet). The error text names the first line it could not use:

| the problem says | likely cause |
|---|---|
| `line N is torn or invalid (…)`, N the last line | a crash or kill mid-write: the process died part-way through one append (`operator_settings.charge` writes one line per filled order) |
| the same, N an earlier line | a hand-edit, or a torn line that a later append was glued onto (below) |
| `line N is in 'CAD', not this bot's USD` | a line repete did not write (it only writes USD): a copied file, a misdirected writer, a hand-edit |
| `… unreadable: [Errno 13] Permission denied`, or another OS error | ownership or permissions, a directory where the file should be, a disk error |

**The glued line.** `charge()` appends `line + "\n"`. If a torn tail has no
newline, the next fee written (a close's fee — exits keep running) lands on
the same line: `{…torn fragment{…a whole record…}`. That record is a real cost
that neither the rails nor the console can read. Look for it before you
repair.

**Diagnose (read-only).** On the Bizon the bot runs in docker, and `./memory`
in the checkout is `/app/memory` in the container (`docker-compose.yml`), so
these run on the host from the checkout root and read the same file the bot
reads:
```bash
cd ~/bots/repete
grep '"event": "rail_equity_unknown"' memory/ledger.jsonl | tail -3
docker compose exec agent python src/health.py   # the container's own view
ls -l memory/operator_fees.jsonl                  # owner, mode, size
tail -c 1 memory/operator_fees.jsonl | od -c      # a whole file ends in \n
tail -n 3 memory/operator_fees.jsonl
# Every line's status, numbered as the error numbers them:
.venv/bin/python - <<'EOF'
import json
for n, line in enumerate(open("memory/operator_fees.jsonl"), 1):
    if not line.strip():
        continue
    try:
        rec = json.loads(line)
        cur = rec.get("currency")
        print(n, "ok" if cur == "USD" else f"CURRENCY {cur!r}", rec.get("ref"))
    except ValueError as e:
        print(n, "TORN", repr(line[:200]), e)
EOF
```

**Fix — the operator's call, never automatic.** This file is the record of
every simulated cost charged. The console nets it off displayed equity and the
rails net it off rail equity. **Never delete or edit a fee line silently, and
never "fix" it by emptying the file**: an empty file reads as $0 charged, which
is exactly the flattering answer the strict reader exists to refuse.

Do it outside 09:00–16:30 ET on a weekday (and not at 17:00, when the backup
reads `memory/`), or at the weekend. Every job that can charge a fee starts
inside that window (the 09:35 open cycle, the 15:45 cycle and 15:55 catch-up,
the :05/:35 swing scans, flatten-retry), and the margin covers a long-running
cycle, so nothing appends while you work. `tail -3 logs/agent.log` should show
nothing from the last few minutes.

1. **Unreadable file** (permissions, a directory, a disk error): fix the
   cause, not the content. The container runs as the host user
   (`HOST_UID`/`HOST_GID` in `docker-compose.yml`), so the file should belong
   to that user and be readable; a root-owned file usually means something
   was run as root against `memory/`. Change nothing inside the file.

2. **A torn or foreign line**: move it, verbatim, into a side file, and leave
   every other byte alone. `BAD` is the line number from the listing above (if
   there are several, do the highest number first, so the others keep theirs):
   ```bash
   cd ~/bots/repete
   D=$(date -u +%Y-%m-%d); BAD=<line number>
   cp -p memory/operator_fees.jsonl memory/operator_fees.jsonl.before-repair-$D
   sed -n "${BAD}p" memory/operator_fees.jsonl > memory/operator_fees.jsonl.torn-$D
   sed "${BAD}d" memory/operator_fees.jsonl > memory/operator_fees.jsonl.new
   diff memory/operator_fees.jsonl memory/operator_fees.jsonl.new   # that one line, nothing else
   mv memory/operator_fees.jsonl.new memory/operator_fees.jsonl
   ls -l memory/operator_fees.jsonl                                 # same owner as before
   ```
   For a line in another currency, call the side file `.foreign-$D` instead,
   find out where it came from, and do not convert it to USD by hand.

3. **Re-append what is recoverable, whole.** Print any whole record glued
   after the torn fragment, in the exact form the bot writes:
   ```bash
   .venv/bin/python - memory/operator_fees.jsonl.torn-$D <<'EOF'
   import json, sys
   line = open(sys.argv[1]).read().rstrip("\n")
   for i in range(1, len(line)):
       if line[i] != "{":
           continue
       try:
           print(json.dumps(json.loads(line[i:]), sort_keys=True))
           break
       except ValueError:
           continue
   else:
       print("no whole record after the fragment")
   EOF
   ```
   A whole record found that way is a real cost: append it. A bare fragment
   (`{… "ref": "close:abc", "fee": 1.0, …`) is recoverable only if its `ref`,
   `fee` and `ts` are all legible; write it out whole, in the shape of the
   lines around it, checking the order in the ledger
   (`grep abc memory/ledger.jsonl`). If the fee itself is cut off, do not
   guess it: leave it in the side file and say so in step 4. Before appending
   anything, check its ref is not already there. A duplicate ref is summed by
   the rails and dropped by the console, so the two would disagree:
   ```bash
   grep -c '"ref": "close:abc"' memory/operator_fees.jsonl          # must print 0
   printf '%s\n' '<the whole record, one line>' >> memory/operator_fees.jsonl
   ```

4. **Say what you did.** Leave the side files in `memory/`: `scripts/backup.sh`
   archives the whole directory, so they travel with the evidence. Name them
   `operator_fees.jsonl.<what>-<date>`, never `*.jsonl`. The restore drill
   parses every `memory/*.jsonl`, and the console reads only
   `operator_fees.jsonl`. Then write one event to the ledger (this deploy's
   store is `jsonl`):
   ```bash
   .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from ledger import Ledger; Ledger('memory/ledger.jsonl').log_event('operator_fee_log_repaired', 'line N moved to memory/operator_fees.jsonl.torn-YYYY-MM-DD; re-appended close:abc (fee 1.0); cause: ...')"
   ```

**Verify:**
```bash
.venv/bin/python -c "import sys; sys.path.insert(0, 'src'); import operator_settings as o; print(o.rail_totals('memory'))"
docker compose exec agent python src/health.py
```
The first prints `(total, total)` and no `FeeLedgerError`. Health stops
listing "operator fee ledger unreadable" at once. The ENGAGED line stays until
the next cycle, because health reads the drawdown from the last
`cycle_complete`, which still says `"rail_equity": null`. The next cycle (09:35
or 15:45 ET) writes no `rail_equity_unknown` event, records a number in
`rail_equity`, judges its entries normally, and health returns to `HEALTHY`
(the container to `healthy`) if nothing else is wrong.

If the newest backup was taken while the bad line was in place, the next
restore drill fails with `operator_fees.jsonl: archive is not a prefix of live`
(and unparseable lines). That is this repair, not a new corruption: the
`.before-repair-` copy and the ledger event explain it, and the next
`scripts/backup.sh` writes an archive that matches. Keep the older archive;
it is the evidence.

---

## slo_breach (too many fail-open degradations in one day)

**Symptoms:** macOS alert "degradation SLO breached"; `slo_breach` event in
the ledger.

**Diagnose:**
```bash
grep '"event": "degradation"' memory/ledger.jsonl | tail -10
```
Each degradation names its source (`correlation_cap: no bars`,
`drift_guard: no quote`, `market_context: stale`...). Three in one day means
some upstream (usually the data vendor) is flaky — the guards are skipping
fail-open, which silently widens risk.

**Fix:** Identify the common source. Vendor outage → usually resolves itself;
consider not trading manually that day. Key expired → rotate
(docs/secrets_rotation.md). Code bug → fix, run tests.

**Verify:** next cycle produces zero degradation events.

---

## Stale bars (data-freshness guard aborting cycles)

**Symptoms:** cycle logs "SPY bars stale — aborting"; no decisions ledgered.

**Diagnose:**
```bash
python -c "
import yaml, sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv()
from broker import Broker
cfg=yaml.safe_load(open('config.yaml')); b=Broker(cfg)
print(b.bars('SPY','1Day',3)[-1])"
```
Compare the last bar's date to today. Market holiday → not a bug, the guard
is doing its job. Weekday with markets open → Alpaca data problem.

**Fix:** Nothing to fix locally on a vendor outage — the guard exists so we
never repeat 2026-07-16 (six fills priced off stale bars). If Alpaca status
page confirms an incident, wait it out; the 15:55 catch-up run gets one more
chance the same day.

**Verify:** the catch-up cycle at 15:55 ET trades normally, or the next day's
cycle does.

---

## Vendor divergence (datacheck blocking entries)

**Symptoms:** ledger `degradation` event naming `datacheck`; "entries blocked"
in the cycle log; exits still processed.

**Diagnose:** the event detail carries both SPY closes (Alpaca vs yfinance)
and the divergence in bps. Confirm with any public quote source which vendor
is wrong.

**Fix:** none needed for a day — this fires only when both vendors report and
DISAGREE, and it already fails closed for entries only. Persistent (>2 days)
divergence → check whether one vendor changed its adjustment policy
(splits/dividends); update `datacheck` config cap if the world legitimately
changed.

**Verify:** next cycle has no datacheck event and entries flow.

---

## Universe truncated (fewer symbols than the cycle asked for)

**Symptoms:** ledger `universe_truncated` naming the missing symbols; if enough
were lost, `universe_floor_blocked` and "entries blocked, exits still run".
Decisions from that cycle carry `rail: "universe"`.

**Diagnose:**

```bash
grep -c universe_truncated memory/ledger.jsonl
grep data_error memory/ledger.jsonl | tail -5
```

`data_error` details carry the exception per symbol. Three shapes, and they
mean different things:

| detail contains | what it is | what to do |
|---|---|---|
| `NameResolutionError` | the laptop lost DNS | check wifi/VPN; usually self-heals |
| `RemoteDisconnected` / `Connection aborted` | Alpaca closed the socket | nothing; the read retries now |
| `subscription` / `403` | the data plan does not cover that symbol | remove it from `symbols:` or upgrade |

**Fix:** a truncation of one or two symbols needs no action — that tier exists
to be visible, not to be fixed. A blocked cycle means the feed lost a fifth of
the cross-section; the bot has already refused entries and kept exits running,
which is the correct posture. Do not widen `risk.min_universe_fraction` to make
the alert stop.

**Verify:** the next cycle logs no `universe_floor_blocked` and entries flow.

**Why entries and not the whole cycle:** every gate scored the full universe. A
book entered from a quarter of it is a different bot than the one that was
gated — that is divergence #17 seen from the live side. Exits are never blocked
by a data rail; a stranded position is worse than a missed entry.

---

## Broker call hangs / cycle runs long

**Symptoms:** minutes-long gaps in `logs/agent.log` between broker calls; a
`cycle_margin_low` alarm; `broker retry budget ... is spent` in the log.

**Diagnose:** the 2026-08-05 signature is a `RemoteDisconnected` at the END of
a 90-second gap — connection-level, so `alpaca-py`'s own 429/504 retry never
looked at it, and there was no socket timeout at all. Since 2026-08-06 every
SDK call carries `ops.broker_timeout_sec` (5s connect / 10s read) and reads
retry twice inside `ops.broker_retry_budget_sec` (60s per cycle).

**Fix:** if the budget message appears, the outage is broad rather than a
single flaky socket — check https://status.alpaca.markets. Do NOT raise the
budget to push through it: the budget is what keeps the cycle inside its
15-minute window, and overrunning it is divergence #18 (a late DAY order is
queued for the next open, not rejected).

**Verify:** `python -c "import broker" ` is not the check. The real one is
`pytest tests/test_broker_resilience.py`, whose black-hole-socket test points a
real client at a server that never answers and asserts it gives up.

---

## Missed cycle (nothing ran at 15:45 ET)

**Symptoms:** watchdog alert at 16:15; no `cycle_complete` event today;
heartbeat stale in `python src/health.py`.

**Diagnose:**
```bash
ls -la memory/heartbeat && cat memory/heartbeat
tail -50 logs/agent.log                  # or: docker compose logs --tail 50 agent
launchctl list | grep repete      # laptop path
docker compose ps                        # container path
```

**Fix by cause:**
- Laptop asleep → the 15:55 catch-up already retried while the market was
  open; if that also missed, the day is lost (by design — no after-hours
  trading). Long-term fix: run the container on an always-on host.
- Container down → `docker compose up -d`, then check `docker compose ps`
  health column.
- Crash mid-cycle → the deterministic client_order_id makes a rerun safe:
  `python src/main.py` (or `docker compose run --rm agent python src/main.py`).
- No jobs listed (`launchctl list | grep repete` empty) → the launchd
  jobs were never installed for this checkout. Install them with
  `sh scripts/install_launchd.sh --load` — it renders the plist templates with
  this checkout's real path (they ship a `{{AGENT_ROOT}}` placeholder so a moved
  or freshly-cloned repo can't run stale paths) and bootstraps them.

**Verify:** `python src/health.py` healthy; today's `cycle_complete` in the
ledger.

---

## Publisher down / misbehaving

**Symptoms:** site 5xx or unreachable; `/healthz` failing.

**Diagnose:**
```bash
curl -s localhost:8080/healthz | python -m json.tool
docker compose --profile publisher logs --tail 50 publisher
```

**Fix:** restart is always safe — the publisher holds no agent state and
cannot affect trading (invariant #9): `docker compose --profile publisher
restart publisher`. Missing `PUBLISHER_SESSION_SECRET` → set it in `.env`.
429s everywhere → someone is hammering an endpoint; that is the rate limiter
working, not an outage.

**Verify:** `/healthz` 200; `/feed` returns JSON.

**Remember:** trading does not care whether the publisher is up. Never delay
a trading fix to babysit the publisher.

---

## The digest didn't go out

**Symptoms:** subscribers report no email, or you expected one and saw none.

**Diagnose — read the audit trail before changing anything:**
```bash
tail -3 publisher_data/digest_runs.jsonl   # every run records why it stopped
tail -5 publisher_data/outbox.jsonl        # per-recipient delivery records
```

`skipped_reason` names the cause outright. The four you will actually see:

| `skipped_reason` | meaning |
|---|---|
| `publisher.digest.enabled is false` | working as designed — the digest is **off by default** |
| `already ran today (…)` | it already went out; check `outbox.jsonl` before re-sending |
| `no active subscribers` | the list is empty, or you are pointed at the wrong `data_dir` |
| `… exceeds max_recipients_per_run` | the blast-radius cap refused rather than truncating |

If `dry_run: true` on every outbox line, nothing was ever sent: a real send
needs **all three** of `publisher.digest.enabled`, `email.dry_run: false`, and
`RESEND_API_KEY`. Deliberately three — see PRODUCT.md.

**Rehearse before arming anything:**
```bash
python scripts/send_digest.py --dry-run
```

**Verify:** a run line with `skipped_reason: null` and `failed: 0`.

---

## The digest went out twice

**Symptoms:** subscribers received two copies.

**Cause:** almost always a human re-running the CLI. `send_daily_digest()`
refuses a second run the same **ET** day, but `--force` bypasses that guard —
which is what it is for, and why it should not live in a cron line.

**Diagnose:** `grep '"date_et": "YYYY-MM-DD"' publisher_data/digest_runs.jsonl`
— more than one line with `queued + sent > 0` is a double send.

**Fix:** nothing to roll back; mail is gone. Remove `--force` from whatever
invoked it. If two hosts are running the publisher, stop one — the same-day
guard reads a local file and cannot see the other machine.

---

## Backup / restore

**Take a backup now:** `scripts/backup.sh`
**Prove it restores:** `python scripts/restore_drill.py`

Since 2026-08-06 every archive is written **twice**: `backups/` locally (newest
14) and `~/Library/Mobile Documents/com~apple~CloudDocs/repete-backups`
(newest 30). The second is the one that survives this laptop.
`REPETE_OFFHOST_DIR` overrides the destination; `""` disables the mirror.

**If this disk is gone**, the archives are on any Mac signed into the same
account, or at icloud.com:
```bash
ls -1t ~/Library/Mobile\ Documents/com~apple~CloudDocs/repete-backups/
```

**Actual restore after data loss:**
```bash
tar -xzf backups/agent-backup-<newest>.tar.gz -C /tmp/restore
# inspect /tmp/restore/memory before touching anything live
cp -a /tmp/restore/memory/. memory/
python scripts/restore_drill.py     # then run the drill against live
python src/health.py
```
The streams are append-only JSONL — a restore is a file copy, no database
surgery. Anything traded between backup and restore is reconciled at the
next cycle start from the BROKER (source of truth for positions, invariant
#4); the ledger gap is honest history, note it in an `event` record.

**`off-host copy missing`** from the drill means the mirror stopped — iCloud
signed out, the folder moved, or the disk filled. The local archive is fine;
what has failed is the copy that survives the machine. Fix it and re-run
`scripts/backup.sh`, which re-mirrors the newest archive.

**`archive is not a prefix of live`** is a different animal, and worse. These
streams are append-only, so live should only ever have grown. Divergence means
some historical record changed — a corrupted write, a restore from the wrong
machine, or a hand-edit (`memory/lessons.jsonl` is generated; CLAUDE.md says
never to edit it by hand). Do **not** overwrite the archive by taking a fresh
backup: that would replace the evidence with the corruption. Diff the archive
against live first and find out which record moved.

---

## "thin margin to the close"

**Symptom:** an alert saying the cycle finished fewer than
`ops.min_close_margin_min` (default 5) minutes before the 16:00 ET close. At
most once a day.

**What it means.** The cycle fires at 15:45 against a 16:00 close, so the whole
budget is fifteen minutes. This says the book was being traded into the bell.
It is **not** a claim that anything was actually mis-filled.

**Why it matters — divergence #18.** Nothing compares the clock to the close
before submitting. Exits go out `TimeInForce.DAY`, and Alpaca does not reject a
DAY order placed after the bell — it **queues it for the next open**. So an
overrun never fails loudly; it silently converts a same-close fill into a
next-open one, across an overnight gap no gate ever modelled.

**Diagnose — the timing is already in the ledger:**
```bash
grep cycle_timing memory/ledger.jsonl | tail -5
```
Long `duration_s` with a normal start ⇒ the cycle got slower; look for broker
socket hangs (`Connection aborted`) in `logs/agent.log` — that is what cost
2026-08-05 its 7.72 minutes. Short `duration_s` but small `margin_min` ⇒ the
cycle STARTED late; check whether the laptop was asleep and launchd fired
backlogged jobs together, as on 2026-07-30.

**Fix.** None in code yet, deliberately. A hard cutoff that refuses to submit
near the bell is a behaviour change, and refusing an exit is not obviously safer
than filling it late. Treat a repeat as the signal to take Phase 4 (broker
retry/backoff) or to move the cycle earlier.

---

## "log rotation failed"

**Symptom:** an alert from `run_rotate_logs.sh`; `logs/` keeps growing.

**Run it by hand:**
```bash
sh scripts/rotate_logs.sh
```

**What it does.** Daily 17:05, every day. Any file over 5 MB is **copied** to
`.1` then **truncated in place**, keeping 5 generations. `copytruncate`, not
rename, and that is load-bearing: four separate processes write
`logs/agent.log`, so renaming would leave three of them appending into an
orphaned inode. Truncating in place keeps the inode and they carry on. It is
also the only mechanism that can bound `cron.log` and `launchd.err.log`, which
are written by shell redirects and by launchd rather than by Python.

**Not urgent unless it is.** Steady growth is ~17 KB/trading-day, so 10 MB is
~2.3 years out. What this defends against is a crash loop — `agent.jsonl` once
grew 144 KB in one day, 9× its median.

---

## Container restart / redeploy

```bash
docker compose build && docker compose up -d       # agent
docker compose --profile publisher up -d           # + publisher
docker compose run --rm agent python src/health.py # verify
```
State lives in the `./memory` volume mount, so rebuilds are always safe.
Never bake state into the image.

---

## Decay alert (strategy may have fallen to random-entry levels)

**Symptoms:** alert titled "Repete: strategy may have decayed";
`scripts/run_decay_check.sh` logged a `WORSE_THAN_RANDOM` verdict in
`logs/cron.log`. Trading is **still running** — this monitor is alert-only and
never halts (§47).

**Diagnose:**
```bash
.venv/bin/python src/decaycheck.py --json        # full verdict + percentile
.venv/bin/python src/decaycheck.py --strategy tsmom   # per-strategy breakdown
grep 'decaycheck' logs/cron.log | tail -10       # is this new, or a trend?
```

Read the percentile, not just the verdict. One reading below the 5th percentile
on ~20 trades is weak evidence; the same reading falling week over week is the
signal. The monitor is deliberately **lenient** (§47: the null carries no stops,
which flatters the strategy), so a fire is more meaningful than a pass.

**Fix:** This is a research trigger, not an incident. Nothing auto-changes.
Options, in the order the repo's own discipline prefers:
1. Confirm it isn't a data problem first — `python src/datacheck.py`, and check
   the trades in the window aren't dominated by one symbol or one week.
2. If the decay looks real, the response is a **new pre-registration**, not a
   parameter tweak. A strategy that stopped working is not fixed by re-tuning it
   on the data that showed it stopped — that is curve-fitting the failure.
3. To stop trading meanwhile, that is a human decision: set the strategy's
   `max_open_positions: 0` in `config.yaml`, or write a `HALT` file. The monitor
   will not do either for you.

**Verify:** `.venv/bin/python src/decaycheck.py` reports the expected verdict,
and `git diff config.yaml` shows exactly what you intended to change (probably
nothing).

**Note:** exit code 2 means the check **could not run** — a missing config, no
bars, a broker failure. That is *not* a pass. `INSUFFICIENT_DATA` (exit 0) is
also not a pass: it means there were fewer than 20 closed trades, so no claim
was made in either direction.
