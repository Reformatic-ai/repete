"""Dead-man watchdog — the bot must fail LOUDLY when it runs unattended.

Scheduled ~30 min after the daily cycle (see scripts/
com.repete.watchdog.plist). Checks FIVE things and alerts via an
operator notification + log + ledger event when any of them fails:

  1. HEARTBEAT — did the PROCESS run? memory/heartbeat (written on every
     run_cycle exit path) must be from today (local). Missing/old on a weekday
     means the cycle never started — launchd broke, the venv broke, or the
     machine slept through it.
  2. CYCLE_COMPLETE — did the cycle FINISH? A `cycle_complete` record dated
     today must exist in the ledger.
  3. MARKET_CONTEXT — did it KNOW anything? Added 2026-07-29 (W6-A3), after the
     10:34 ET news refresh lost all eleven RSS feeds and the Alpaca wire to DNS
     failure and said so in one INFO line nobody reads. Checked only once the
     cycle completed, so a broken cycle reports its root cause rather than two
     symptoms. ALERT ONLY — news is fail-soft and never gates trading.
  4. HALT: if the kill-switch HALT file exists, keep reminding the owner
     every day until they deal with it.
  5. DRAWDOWN LATCH: if the drawdown circuit breaker is engaged, say so every
     day until it is cleared. Added 2026-08-03. §40 showed the rail is a
     ONE-WAY LATCH — the equity peak ratchets up and never down, so once
     entries are blocked the book goes to cash, equity stops moving, the peak
     never falls, and the bot never buys again. It blocked 99.43% of buy
     signals in 2022-2026 and has never tripped in production, which is
     exactly why it would engage in silence. ALERT ONLY — nothing here blocks
     or unblocks a cycle, and §41/§44 both REJECTED candidate fixes, so the
     latch itself is untouched.

Check 2 exists because check 1 cannot see a cycle that started and died.
`write_heartbeat()` runs in a `finally:`, so a crashed cycle stamps a FRESH
heartbeat on its way out and reads as healthy. That is what happened on Friday
2026-07-24: the 15:45 cycle died about six seconds in, this watchdog said
nothing, `catchup` used the same test and so silenced its own recovery, and the
EOD post read exactly like a quiet market. It cost a full trading day and went
unnoticed for two.

Added 2026-07-26. `check()`'s own docstring has described all three since; THIS
docstring said "two things" until 2026-07-29 (W5-5) — the file contradicted
itself, and the stale half was the one you read first.

Alerts degrade gracefully (a notification failure still logs); the watchdog
itself never raises.
"""
import logging
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("watchdog")


def configure_logging() -> None:
    """Attach file handlers. Called from __main__ only — see the note in
    main.configure_logging() for why import-time setup polluted the real
    production logs during every test run."""
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("logs/agent.log", mode="a")],
        force=True,
    )
    import log as structlog
    structlog.redact_existing_handlers()

import statepaths  # noqa: E402

# Defaults only — main() resolves the live values from config. The names stay
# because check() takes them as parameter defaults and ten tests inject through
# that seam; the literals now have one definition, in src/statepaths.py.
HEARTBEAT_FILE = statepaths.DEFAULT_HEARTBEAT_PATH
HALT_FILE = statepaths.DEFAULT_HALT_PATH


def notify(title: str, message: str):
    """Raise an operator alert through whatever channel exists on this host.

    Was an `osascript` banner — which meant that once the agent moved off the
    laptop, every alert went to a log file nobody reads. Now delegates to
    `alerting.send()`: webhook when `ALERT_WEBHOOK_URL` is set, desktop banner
    otherwise, so laptop behaviour is unchanged. Never raises."""
    try:
        import alerting
        return alerting.send(title, message)
    except Exception as e:  # noqa: BLE001 — alerting must not crash the alerter
        log.warning("notification failed: %s", e)
        return "log-only"


def heartbeat_date(path: str = HEARTBEAT_FILE) -> date | None:
    """Local calendar date of the last heartbeat, or None if unreadable."""
    try:
        with open(path) as f:
            ts = f.read().strip()
        return datetime.fromisoformat(ts).astimezone().date()
    except (OSError, ValueError):
        return None


_UNREADABLE = object()          # distinct from "read it, found nothing"


def _all_records(records=None):
    """The ledger, or None when it cannot be read at all. Shared by every check
    so `_UNREADABLE` and a genuine read failure behave identically."""
    if records is _UNREADABLE:
        return None
    if records is None:
        try:
            import yaml
            from ledger import Ledger
            with open("config.yaml") as f:
                cfg = yaml.safe_load(f)
            return Ledger(cfg["memory"]["ledger_path"]).all_records()
        except Exception:  # noqa: BLE001 — reported by the caller, not swallowed
            return None
    return records


def _event_on(day: date, event: str, records=None) -> bool | None:
    """Did `event` occur on `day`? True / False / None (ledger unreadable)."""
    records = _all_records(records)
    if records is None:
        return None
    for r in records:
        if r.get("event") != event:
            continue
        ts = r.get("ts") or r.get("timestamp") or ""
        try:
            if datetime.fromisoformat(ts).astimezone().date() == day:
                return True
        except ValueError:
            continue
    return False


def completed_on(day: date, records=None) -> bool | None:
    """Did a cycle reach `cycle_complete` on `day`?

    True / False / None, where None means the ledger could not be read at all.
    The three-way answer matters: a monitor that cannot see its input must say
    so rather than report all-clear, which is the failure this whole module is
    being corrected for.
    """
    return _event_on(day, "cycle_complete", records)


def news_on(day: date, records=None) -> bool | None:
    """Did the news brain produce a market context on `day`? Same three-way
    answer, same reason."""
    return _event_on(day, "market_context", records)


def check(today: date | None = None,
          heartbeat_path: str = HEARTBEAT_FILE,
          halt_path: str = HALT_FILE,
          records=None, cfg: dict | None = None) -> list[str]:
    """Return the list of problems found (empty = all clear).

    Six different questions, deliberately kept apart:

      * did the PROCESS run?     -> the heartbeat file
      * did the CYCLE finish?    -> a `cycle_complete` record in the ledger
      * did it KNOW anything?    -> a `market_context` record in the ledger
      * is trading halted?       -> the HALT file
      * is the book locked out?  -> the drawdown latch (§40), via
                                    `drawdown_latch()`
      * can the JUDGE answer?    -> llm_client.probe() (2026-08-29, only when
                                    cfg is passed and the provider is keyless)

    The halt and the latch cannot clear themselves, and both re-alert daily
    for that reason. The judge check re-alerts daily too, deliberately: the
    2026-08-21→28 outage refused 53 entries over seven days precisely because
    no repeated signal existed — a once-only alert for a persistent fault is
    how that outage stayed invisible.

    Until 2026-07-26 only the first was asked, and `write_heartbeat()` runs in
    a `finally:` — so a cycle that crashed six seconds in still stamped a
    fresh heartbeat and read as healthy. That is exactly what happened on
    2026-07-24: no decisions, no abort record, no alert. `docs/slo.md` had
    claimed completion was measured all along; it never was.

    The third question was added 2026-07-29 (W6-A3). On that day the 10:34 ET
    news refresh lost all eleven RSS feeds AND the Alpaca wire to DNS failure
    and reported it as a single INFO line, because news is fail-soft by design.
    Fail-soft is right — news must never gate trading — but "the bot traded
    today with no market awareness at all" is worth one alert. This check only
    ever ALERTS; nothing here can block a cycle.
    """
    today = today or date.today()
    problems = []
    if today.weekday() < 5:  # Mon-Fri: a cycle should have run
        hb = heartbeat_date(heartbeat_path)
        if hb is None:
            problems.append("no heartbeat file — the trading cycle has "
                            "never run or the file is unreadable")
        elif hb < today:
            problems.append(f"last heartbeat {hb} — today's trading cycle "
                            "did NOT run")
        else:
            # The process ran today. Did it get anywhere?
            done = completed_on(today, records)
            if done is None:
                problems.append("cannot read the ledger to confirm today's "
                                "cycle completed — treating as a failure")
            elif not done:
                problems.append("the cycle process ran today but never "
                                "reached cycle_complete — it died or aborted "
                                "part-way; check for a cycle_crashed record")
            else:
                # It ran and finished. Did it know anything while doing so?
                # Only asked when the cycle completed, so a broken cycle
                # reports one root cause instead of two symptoms.
                if news_on(today, records) is False:
                    problems.append(
                        "no market_context today — the bot traded with no "
                        "news awareness; check the news_sources ledger event "
                        "for which feeds failed (news is fail-soft, so this "
                        "never blocked the cycle)")
    if os.path.exists(halt_path):
        problems.append("HALT file present — trading is disabled until you "
                        "review and delete it")

    dd = drawdown_latch(records)
    if dd.get("engaged"):
        problems.append(f"drawdown circuit breaker ENGAGED — {_latch_detail(dd)}"
                        f". Entries are blocked; exits still run. "
                        f"{_reset_hint(dd)}")

    # The judge's endpoint, only when a config was passed (main() passes the
    # real one; tests that predate this parameter keep their no-network
    # contract). This is the check that actually PAGES — health.py flips the
    # container unhealthy and host_warnings ledgers it, but only the watchdog
    # calls alerting.send(). probe() never raises and answers None for a
    # keyed provider, where None means "not applicable", not "healthy".
    if cfg is not None and (cfg.get("llm") or {}).get("enabled"):
        try:
            import llm_client
            reason = llm_client.probe(cfg)
        except Exception as e:  # noqa: BLE001 — the alerter must not crash
            reason = f"judge probe itself failed: {e}"
        if reason:
            problems.append(
                f"{reason} — judged entries are being refused "
                f"(on_unavailable) while it stays down; exits are unaffected")
    return problems


def _latch_detail(dd: dict) -> str:
    if dd.get("drawdown_pct") is None:
        return dd.get("note") or "state unknown"
    return (f"{dd['drawdown_pct']:.2f}% from peak ${dd['peak_equity']:,.0f} "
            f"(limit {dd['limit_pct']:.1f}%)")


def _reset_hint(dd: dict | None = None) -> str:
    """risk.drawdown_hint: the peak reset for a real drawdown, the fee-log
    runbook when rail equity is unknown (2026-10-28, PR #168)."""
    try:
        import risk
        return risk.drawdown_hint(dd or {})
    except Exception:  # noqa: BLE001 — a missing hint must not break the check
        return "the peak only ratchets UP, so this cannot clear itself."


def drawdown_latch(records=None) -> dict:
    """State of the drawdown circuit breaker, read-only. Never raises.

    THE FIFTH CHECK (2026-08-03). §40 established that this rail is a ONE-WAY
    LATCH: `update_high_water` ratchets the peak up and never down, so once a
    drawdown blocks entries the book goes to cash, equity stops moving, the
    peak never falls, and **the bot never buys again**. In 2022-2026 it blocked
    99.43% of every buy signal. Production has not tripped it — §40 measured
    9.85pp of headroom — which is exactly why nobody has noticed that a
    permanent kill switch would engage in complete silence.

    §41 and §44 both tested candidate FIXES and both were REJECTED, so the
    latch stays and nothing here touches it. This belongs beside the HALT check
    rather than the heartbeat ones because it has the same shape: a state that
    cannot clear itself, so the watchdog keeps reminding the owner every day
    until they deal with it. A once-only alert would be wrong here — the
    condition is permanent, and the repetition is the point.

    Alerting only. Nothing here can block or unblock a cycle.
    """
    try:
        import yaml
        import health
        import risk
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f) or {}
        limit = (cfg.get("risk") or {}).get("max_drawdown_pct", 0)
        recs = _all_records(records)
        equity = health.last_known_equity(recs)
        # After the operator cost (2026-10-28), as the rail measures it; an
        # unreadable fee log reports ENGAGED, as the live rail fails closed.
        rail_eq, problem = (health.last_known_rail_equity(recs, cfg)
                            if equity is not None else (None, ""))
        return risk.drawdown_state(equity, limit, rail_equity=rail_eq,
                                   rail_problem=problem)
    except Exception as e:  # noqa: BLE001 — the watchdog itself never raises
        log.warning("drawdown latch state unreadable: %s", e)
        return {"engaged": False}


def catchup(now: datetime | None = None, records=None) -> str:
    """Late catch-up (2026-07-21): scheduled ~15:55 ET weekdays. If today's
    cycle hasn't run (machine was asleep at 15:45) and the market is still
    open, run it NOW instead of losing the trading day. Safe to double-fire:
    a same-day rerun is idempotent (client_order_ids + broker-fresh state).
    Returns what it did (for logs/tests)."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return "weekend — no action"
    if not (9 <= now.hour < 16 and (now.hour, now.minute) >= (9, 30)):
        return "market closed — no action"
    # Completion, NOT the heartbeat. This used to test `hb >= now.date()`,
    # which meant a cycle that crashed at 15:45 stamped a fresh heartbeat on
    # its way out and thereby suppressed the very catch-up that exists to
    # rescue it. On 2026-07-24 that cost a whole trading day: the crash
    # silenced its own recovery. Asking "did a cycle finish today?" makes the
    # catch-up fire on a crash, which is exactly when it is wanted.
    if completed_on(now.date(), records):
        return "cycle already ran today — no action"
    log.warning("catch-up: no completed cycle today at %s ET — running it late",
                now.strftime("%H:%M"))
    notify("Trading agent: late catch-up",
           "3:45 cycle was missed; running it now before the close")
    import main as main_mod
    main_mod.run_cycle()
    return "ran late cycle"


def _cfg_or_empty() -> dict:
    """config.yaml, or {} if it cannot be read for any reason.

    Total, and load-bearing that it is: the watchdog is the thing that tells a
    human the bot is dead, so it has to keep working when the config is what
    died. tests/test_alert_delivery.py runs this file as a real subprocess in a
    temp cwd holding only .env and HALT — no config.yaml at all — and that test
    is the only end-to-end proof the alert path works.
    """
    try:
        import yaml
        with open("config.yaml") as f:
            return yaml.safe_load(f) or {}
    except Exception:                       # noqa: BLE001 — see the docstring
        return {}


def main():
    if "--catchup" in sys.argv:
        log.info("watchdog catch-up: %s", catchup())
        return
    # Loaded ONCE, above check(), so the paths the watchdog watches and the
    # ledger it writes the ops_alert to come from the same config. check()
    # used to run before any config existed and take both paths from module
    # defaults, which is how the monitor could end up watching a different
    # file than the one health reported on.
    cfg = _cfg_or_empty()
    problems = check(heartbeat_path=statepaths.heartbeat_path(cfg),
                     halt_path=statepaths.halt_path(cfg), cfg=cfg)
    if not problems:
        log.info("watchdog: all clear")
        return
    for p in problems:
        log.critical("watchdog: %s", p)
        notify("Trading agent needs attention", p)
    try:  # ledger event is best-effort — ops alerts must not depend on it
        from ledger import Ledger
        Ledger(cfg["memory"]["ledger_path"]).log_event(
            "ops_alert", "; ".join(problems))
    except Exception as e:  # noqa: BLE001
        log.warning("ledger ops_alert write failed: %s", e)


def load_env() -> str | None:
    """Read .env. Called from __main__ only, for the same reason as
    configure_logging().

    Added 2026-07-30. This file was the ONLY launchd entrypoint without it, and
    launchd hands a process a bare environment — no shell profile, no .env. So
    every `os.environ.get()` in this process tree returned nothing, and two
    things depended on that:

      * `alerting.send()` never saw ALERT_WEBHOOK_URL, so the watchdog — the
        PRIMARY alerter, owner of all four checks above — would have kept
        firing macOS banners no matter what the .env file said. Setting the
        variable would have looked like it worked.
      * `catchup()` calls `main.run_cycle()` in-process, which builds a Broker
        from ALPACA_API_KEY / ALPACA_SECRET_KEY. Under launchd that raised
        "keys missing". The catch-up has fired twice (2026-07-28, -07-29) and
        both times returned early at "cycle already ran today", so the broken
        branch was never exercised — the rescue path for a missed trading day
        was latently dead.

    Proved with `env -i HOME=$HOME PATH=/usr/bin:/bin`, not by reading the
    code. `tests/test_alert_delivery.py` asserts it the same way: a subprocess
    in a cleared environment, because a grep for `load_dotenv` would pass on a
    call that never runs.
    """
    from dotenv import load_dotenv
    # cwd ONLY. 2026-08-22: the repo-root fallback was removed. It let a
    # process started from any other directory pick up the operator's real
    # `.env` — and the negative control in tests/test_alert_delivery.py (run
    # from a temp cwd with no .env) did exactly that: every suite run posted
    # false "Trading agent needs attention" alerts to the real ntfy topic while the test stayed green,
    # because it only watched its local receiver. Every launcher already cds
    # to the repo root, so production never used the fallback.
    candidate = os.path.join(os.getcwd(), ".env")
    if os.path.isfile(candidate):
        load_dotenv(candidate)
        return candidate
    return None


if __name__ == "__main__":
    configure_logging()
    _loaded = load_env()
    log.info("watchdog: .env %s", _loaded if _loaded else "not found in cwd — no webhook configured")
    main()
