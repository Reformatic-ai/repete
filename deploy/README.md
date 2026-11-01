# Deploying Repete to an always-on host

A closed laptop lid is a missed trading day. This directory holds everything
needed to move the agent off the laptop; **you run the commands** (they need
your accounts and secrets — nothing here provisions infrastructure for you).

Two supported shapes. Pick one:

| | Fly.io | VPS (docker compose) |
|---|---|---|
| Setup | `flyctl launch` + secrets | ssh + `deploy.sh` |
| Cost | ~$2–5/mo (shared-cpu-1x) | ~$5/mo (any small droplet) |
| State | one Fly volume | bind-mounted dirs |
| Best when | you want zero server admin | you want plain, inspectable Docker |

Both run the **same image and the same `scripts/scheduler.py`** — the jobs, ET
times, and safety rails are identical to the laptop.

---

## Before you deploy — the honest checklist

1. **`mode: paper`.** Confirm `config.yaml` still says `paper` and that
   `LIVE_TRADING_CONFIRMED` is `NO`. Deploying does not change the go-live gate
   (`docs/go_live_checklist.md`); it only stops the bot missing days.
2. **Copy the existing state.** `memory/` is the track record. If you deploy
   with an empty `memory/`, you start the record over. See *Migrating state*.
3. **Secrets** — read `deploy/SECRETS.md`. Nothing goes in the image or git.

---

## Option A — Fly.io

```bash
flyctl auth login
flyctl launch --no-deploy --copy-config --config deploy/fly.toml
flyctl volumes create repete_state --size 1        # memory/, logs/, backups/
```

Set secrets (never `--build-arg`, never in the image):

```bash
flyctl secrets set \
  ALPACA_API_KEY=... ALPACA_SECRET_KEY=... \
  ANTHROPIC_API_KEY=... \
  X_API_KEY=... X_API_SECRET=... \
  X_ACCESS_TOKEN=... X_ACCESS_TOKEN_SECRET=... \
  ALERT_WEBHOOK_URL=... HEARTBEAT_PING_URL=... \
  LIVE_TRADING_CONFIRMED=NO
flyctl deploy
```

**Do not skip the last two.** On the laptop the agent alerted through a macOS
notification banner; on a Linux host that call does not exist, so without
`ALERT_WEBHOOK_URL` every alert lands in a log file nobody reads. And every
check the agent runs happens *on the host* — if the container is OOM-killed or
the machine is powered off, nothing is left to notice, and a dead bot looks
exactly like a quiet market. `HEARTBEAT_PING_URL` (a free healthchecks.io check
works) is pinged after each cycle so an **outside** service tells you when the
pings stop. It is the only monitor that survives the host dying.

Verify (see *Verifying* below):

```bash
flyctl logs                    # expect: "scheduler up — 17 jobs, timezone America/New_York"
flyctl ssh console -C "python src/health.py"
```

## Option B — VPS with docker compose

On any small Linux box with Docker installed:

```bash
git clone https://github.com/Reformatic-ai/repete.git
cd repete
cp .env.example .env && $EDITOR .env        # fill in keys; stays out of git
sh deploy/deploy.sh                          # build, start, verify
```

To survive reboots, install the systemd unit:

```bash
sudo cp deploy/repete.service /etc/systemd/system/
sudo sed -i "s|/opt/repete|$(pwd)|" /etc/systemd/system/repete.service
sudo systemctl enable --now repete
```

---

## Migrating your existing state

The track record lives in `memory/`. Copy it from the laptop **before** the
first cycle runs on the new host, or you restart the record from zero:

```bash
# from the laptop, in the repo root
tar -czf state.tar.gz memory/
# Fly:
flyctl ssh sftp shell <<< "put state.tar.gz /app/state.tar.gz"
flyctl ssh console -C "tar -xzf /app/state.tar.gz -C /app && rm /app/state.tar.gz"
# VPS:
scp state.tar.gz user@host:/opt/repete/ && ssh user@host \
  "cd /opt/repete && tar -xzf state.tar.gz && rm state.tar.gz"
```

Then confirm the counts survived:

```bash
python src/health.py            # open_positions / last_cycle should look right
python src/review.py            # closed-trade count matches the laptop
```

---

## Verifying the deploy

| Check | Command | Expect |
|---|---|---|
| Scheduler booted | `flyctl logs` / `docker compose logs agent` | `scheduler up — 17 jobs, timezone America/New_York` |
| Agent healthy | `python src/health.py` | `HEALTHY`, or `DEGRADED — no heartbeat` until the first cycle |
| One manual cycle | `docker compose run --rm agent python src/main.py` | writes a `cycle_complete` event |
| Public status | open `/status` if the publisher runs | operational panel |
| Backups persist | `ls backups/` after 17:00 ET | a dated `.tar.gz` |
| Restore works | `python scripts/restore_drill.py` | `PASS` |

The **heartbeat is the real test**: after the first 15:45 ET weekday cycle,
`memory/heartbeat` should be fresh. A stale heartbeat is what "silently dead"
looks like — the watchdog alerts on it (see `HEARTBEAT.md`).

## Publishing the dashboard from the container

Publishing needs a `.site/.git` checkout with push credentials mounted at
`/app/.site`. Without it `scripts/publish_dashboard.sh` is a **clean no-op**
(it exits 0), so the agent still trades — the public page just stops updating.
The container image does not include `git` credentials by design.

## Rollback

```bash
flyctl releases                 # then: flyctl deploy --image <previous>
# VPS:
git checkout <previous-sha> && sh deploy/deploy.sh
```

State is on a volume, so a rollback never touches the ledger. If a deploy looks
wrong mid-session, the safest immediate stop is the kill switch, not a redeploy:

```bash
touch HALT      # the next cycle refuses to trade and says why
```
