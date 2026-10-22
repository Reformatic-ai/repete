# Security

This is a research project that trades **paper money only**. It manages no
client funds and holds no user data. The realistic threat is not an attacker
profiting — it is a leaked broker or model credential, and a bot that trades
when it should not.

## Reporting a vulnerability

Open a [GitHub security advisory](https://github.com/Reformatic-ai/repete/security/advisories/new).
Please do not open a public issue for anything credential-related.

Sole maintainer, best-effort response. There is no SLA and it would be dishonest
to publish one.

## What protects credentials

| Control | Where | Evidence it works |
|---|---|---|
| Secrets never in git | `.gitignore:12` (`.env`, no trailing slash) | gitleaks over all 196 commits: **zero findings** (2026-08-06) |
| Repo secret scan | `.github/workflows/ci.yml` | Blocking, `git` mode so deleted-but-committed secrets are still caught. Proven to fail on a planted fake |
| Push protection | GitHub setting | Blocks a push containing a recognised credential |
| Dependency CVEs | CI, `pip-audit` ×2 | Advisory pass for all severities; **blocking** on HIGH/CRITICAL |
| Log redaction | `src/log.py:25` | Redacts by env-var *name* pattern incl. `WEBHOOK`/`PING_URL` — a capability URL **is** the credential |
| No-echo secret entry | `scripts/set_*.sh` | `stty -echo`, `chmod 600`, value never echoed even on failure |
| Local plaintext audit | `scripts/check_secret_exposure.py` | Weekly launchd job. Deliberately **not** in CI — it reads `.env` and local transcripts, neither of which exists on a runner, so it would be a check that can never fail |
| Rotation record | `docs/secrets_rotation.md` | Per-key inventory and a dated rotation log |

**Never paste a credential into an issue, a PR, or an assistant session.** Two
real leaks on 2026-07-30 — an OANDA token pasted into a chat, and an ntfy topic
captured in a screenshot — are why `scripts/set_*.sh` exist in their current
form. If a value is exposed, rotate it; do not edit the transcript, because
editing a session log destroys an audit record and leaves the value in every
backup anyway.

## What protects the money

Paper today, but these are the controls that would matter:

- **Two independent switches to go live** — `mode: live` in `config.yaml` *and*
  `LIVE_TRADING_CONFIRMED=YES` in `.env` (`src/broker.py:66`). Either alone
  stays paper.
- **Kill switch**, two modes — `./scripts/halt.sh` (block entries, still exit
  positions) and `--freeze` (no cycle at all). Refuses to engage without a
  stated reason.
- **Circuit breakers** — 5% daily loss flattens and freezes; 10% drawdown
  blocks entries but never exits.
- **Flatten recovery** — success means *the broker reports the book flat*, not
  that no exception was raised.
- **Fail-safe preflight** — any config or environment failure aborts the cycle
  rather than trading through it.

## Not claimed

No SOC 2, no penetration test, no formal access review, and at one maintainer no
separation of duties. `docs/soc2_readiness.md` maps what exists to the Trust
Services Criteria and states the gaps plainly rather than implying coverage.

**No pull request in this repository has ever been reviewed by a second person,
and that is recorded rather than papered over.** A required-approval rule on a
single-author repository is satisfied by its own author, which is a control in
appearance only. `enforce_admins` is enabled instead, because that one actually
binds the maintainer.
