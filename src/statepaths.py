"""Where the agent's runtime STATE files live — one answer, read from config.

Sibling to `src/sitepaths.py`, which fixed this same bug class for the published
artifacts on 2026-07-28. State was never migrated, so on 2026-08-11 a QA sweep
found `/healthz` answering about the wrong machine:

    $ python scripts/qa_sweep.py --fixture /tmp/qa/full     # ./memory/heartbeat exists
    58 passed, 0 FAILED
    $ rm -rf memory && python scripts/qa_sweep.py --fixture /tmp/qa/full
    57 passed, 1 FAILED          # PUB-04, HTTP 503, heartbeat_age_hours: null

Same fixture, same command, different answer — because `health.py` held
`HEARTBEAT_FILE = "memory/heartbeat"` and `HALT_FILE = "HALT"` as module
constants that no config could redirect. The sweep rewired every store to a
fixture and health kept reading whatever sat next to the process. `PUB-04` had
never measured the fixture; it measured the host, and passed only because the
sweep happened to run from a live checkout.

Worse than a wrong test result: THREE modules held their own copy of
`"memory/heartbeat"` — the writer in `main.py` and readers in `health.py` and
`watchdog.py` — with nothing making them agree. Three constants can drift where
one cannot, and no test would have noticed.

TOTAL BY CONTRACT. `heartbeat_path` runs inside `run_cycle`'s `finally:`, where
an exception may already be in flight, and inside `/healthz`. A resolver that
raised there would replace a real crash with a path error, or take down the
health endpoint that exists to report the crash. So: never raises, and never
touches the filesystem. `sitepaths.resolve` deliberately mkdirs — a cosmetic
render must not break a cycle — but a resolver used by a READ-ONLY health check
must not create the thing it is inspecting (invariant #9). That difference in
policy is why this is a separate module rather than two more functions there.
`ensure_parent` is the writer's half, and only `main.write_heartbeat` calls it.

THE DEFAULTS ARE LOAD-BEARING AND MUST STAY CWD-RELATIVE. `deploy/fly.toml`
symlinks `memory/` onto `/app/state`; `docker-compose.yml` mounts
`./memory:/app/memory:ro` for the publisher; `HEARTBEAT.md`, `docs/runbooks.md`,
`deploy/README.md` and `deploy/deploy.sh` all name the literal paths to
operators. An absolute default anchored on `__file__` would also make
`tests/test_publisher.py`'s "no heartbeat has ever been written in this fixture"
find the developer's REAL heartbeat and report HEALTHY on one machine and not
another.

HALT IS DELIBERATELY HALF-MIGRATED, and this is the important part. The
MONITORS (`health.status`, `watchdog.check`) resolve the halt path through here.
The TRADING RAIL does not: `risk.check_halt()` keeps its own cwd-relative
constant, because a config key that can move the kill switch is a way to disable
it, and `scripts/halt.py:80` already settled that question — "a HALT engaged into
some other directory is a stop button wired to nothing." `src/preflight.py`
refuses to start a cycle when either key is set to anything but its default, so
the keys are QA-only by enforcement and the monitor and the rail cannot disagree
in a process that trades.
"""
from __future__ import annotations

import os

DEFAULT_HEARTBEAT_PATH = "memory/heartbeat"
DEFAULT_HALT_PATH = "HALT"


def _memory(cfg: dict | None) -> dict:
    """The `memory:` block, whatever shape the config is in.

    `(cfg or {}).get("memory") or {}` rather than `.get("memory", {})`: a
    `memory:` key with an empty body parses to None, not to {}, and `.get` with
    a default does not help you there.
    """
    try:
        return (cfg or {}).get("memory") or {}
    except AttributeError:      # cfg is not a mapping at all
        return {}


def heartbeat_path(cfg: dict | None) -> str:
    """Where proof-of-life is written and read. Never raises."""
    return _memory(cfg).get("heartbeat_path") or DEFAULT_HEARTBEAT_PATH


def halt_path(cfg: dict | None) -> str:
    """Where the MONITORS look for the kill switch. Never raises.

    Not used by `risk.py` — see the module docstring. The rail's copy is the
    authority; this one exists so a health check can be pointed at a fixture.
    """
    return _memory(cfg).get("halt_path") or DEFAULT_HALT_PATH


DEFAULT_OPERATOR_DIR = "memory"


def operator_dir(cfg: dict | None = None) -> str:
    """The directory holding the operator's settings and the simulated-fee log
    (`operator_settings.json`, `operator_fees.jsonl` — see operator_settings.py).

    The console host writes the settings into this bot's `memory/` (mounted at
    /app/memory in the container; config.yaml is baked into the image, so the
    settings cannot live there). `REPETE_OPERATOR_DIR` overrides it, and the test
    suite sets it so no test can read the live settings or append a fake fee to
    the live fee log. Never raises, never touches the filesystem.
    """
    return (os.environ.get("REPETE_OPERATOR_DIR")
            or _memory(cfg).get("operator_dir") or DEFAULT_OPERATOR_DIR)


def ensure_parent(path: str) -> None:
    """mkdir the parent, for WRITERS ONLY.

    `os.path.dirname("heartbeat")` is `""` and `os.makedirs("")` raises
    FileNotFoundError — which the writer catches and logs, so a configured bare
    filename would silently cost proof-of-life. Hence the `if d:`.
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
