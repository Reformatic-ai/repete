"""Shared fixtures. All tests run offline: no Broker/anthropic/tweepy clients
are ever constructed, LLM review is disabled, and X posting stays dry_run.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest


@pytest.fixture(autouse=True)
def _fake_broker_env(monkeypatch):
    """Preflight requires broker keys in env; tests never construct a real
    Broker (it's always monkeypatched), so dummies keep the suite offline."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")


@pytest.fixture(autouse=True)
def _no_real_alerts(monkeypatch):
    """No test may deliver an alert to a human or to an external service.

    `alerting.send()` already refuses under `PYTEST_CURRENT_TEST`; this stubs the
    two DELIVERY primitives as well, so a path that bypasses `send()` — a direct
    `_macos_banner` call, a future channel, a module that grows its own poster —
    still cannot reach the screen or the network. Two independent layers, because
    on 2026-08-02 the suite delivered eight real notifications to the owner's
    phone and one layer is exactly what was missing.

    These raise rather than no-op: a test that tries to alert is a test with a
    bug in it, and silently swallowing the attempt would hide the next one.

    `tests/test_alerts_are_silent_in_tests.py` proves the guard;
    `tests/test_alerting.py` opts out of the ENV half so real channel selection
    stays covered — a suppression that quietly stops delivery being tested is
    how this comes back.
    """
    import alerting

    def _no_banner(title, message):
        raise AssertionError(
            f"a test tried to deliver a desktop notification: {title!r}. "
            f"Stub it — see conftest._no_real_alerts.")

    def _no_post(*a, **k):
        raise AssertionError(
            "a test tried to POST to an alert webhook. Stub it — see "
            "conftest._no_real_alerts.")

    monkeypatch.setattr(alerting, "_macos_banner", _no_banner)
    monkeypatch.setattr(alerting, "_post_json", _no_post)


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch):
    """No test may reach the LLM vendor. Raise, never no-op.

    Found 2026-08-22: a test stubbed `llm_client.complete()`; review_signal
    then moved to `complete_detailed()`; the stub was bypassed and four tests
    hit api.anthropic.com with a fake key and read the 401 as "vendor down".
    Green on a laptop with a network, and the suite's whole contract is
    "offline by design — no credentials, ever, in CI".

    This blocks the real SDK at the one seam every vendor call goes through:
    `llm_client._create` imports `anthropic` per call. Tests that install a
    FAKE via `monkeypatch.setitem(sys.modules, "anthropic", ...)` keep working
    — the guard only refuses when the module that would be imported is the
    genuine package. Tests that stub complete()/complete_detailed() never get
    here at all.
    """
    import builtins
    real_import = builtins.__import__

    def _guarded(name, *a, **k):
        if name == "anthropic":
            mod = sys.modules.get("anthropic")
            genuine = mod is None or getattr(mod, "__file__", None) is not None
            if genuine:
                raise AssertionError(
                    "a test tried to import the REAL anthropic SDK, i.e. reach "
                    "the vendor. Stub llm_client.complete_detailed, or install a "
                    "fake via monkeypatch.setitem(sys.modules, 'anthropic', ...). "
                    "See conftest._no_real_llm_calls.")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _guarded)


@pytest.fixture(autouse=True)
def _no_real_offhost_backups(monkeypatch, tmp_path_factory):
    """No test may write into the owner's real iCloud Drive.

    `scripts/backup.sh` mirrors every archive off-host, resolving iCloud by
    default. `tests/test_backup_restore.py` runs that script for real against an
    AGENT_ROOT fixture, so without this the suite would deposit a fixture
    archive into `~/…/CloudDocs/repete-backups` on every run and then
    prune the owner's genuine archives to the newest 30 alongside them.

    Same shape as `_no_real_alerts`: the destination is redirected for the whole
    suite, and a test that wants to exercise the mirror sets the variable
    itself. Pointing it at a tmp dir rather than "" keeps the mirror path
    EXERCISED — disabling it outright would leave the feature untested, which is
    how a backup control quietly stops being one.
    """
    monkeypatch.setenv("REPETE_OFFHOST_DIR",
                       str(tmp_path_factory.mktemp("offhost")))
    # The remote branch too (B2 went live 2026-09-24). Modules that call
    # load_dotenv() at import copy the host's real remote and passphrase into
    # os.environ, and backup.sh would then encrypt fixture archives and upload
    # them to the live bucket. Tests that exercise the remote set their own.
    for key in ("REPETE_MIRROR_REMOTE", "REPETE_BACKUP_PASSPHRASE"):
        monkeypatch.delenv(key, raising=False)
    for key in [k for k in os.environ if k.startswith("RCLONE_CONFIG_")]:
        monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _no_real_operator_state(monkeypatch, tmp_path_factory):
    """No test may read the live operator settings or append to the live fee log.

    The console writes `memory/operator_settings.json` on the host the bot runs
    on, and the suite is run from that same checkout: without this, a test that
    closes a trade would charge a fake fee into the real `operator_fees.jsonl`,
    which the console subtracts from the equity it shows. Tests that exercise
    the fee path point it at their own directory.
    """
    monkeypatch.setenv("REPETE_OPERATOR_DIR",
                       str(tmp_path_factory.mktemp("operator")))


@pytest.fixture
def cfg(tmp_path):
    """Minimal dict config mirroring config.yaml, network layers disabled.

    Every writable store defaults to a pytest tmp path so no test can ever
    touch the real memory/ files OR the real public artifacts, even without an
    explicit override.

    `publish.out_dir` is part of that promise and was missing until 2026-07-28.
    Until then the claim above was true of memory/ and false of the HTML:
    dashboard/blog/journal render defaulted to a CWD-relative constant, so
    tests/test_backfill_posts.py overwrote the repo-root blog.html and
    journal.html with fixtures whenever pytest ran from the repo root."""
    return {
        "publish": {"out_dir": str(tmp_path / "site")},
        "mode": "paper",
        "symbols": ["SPY"],
        "strategy": {
            "name": "ma_crossover",
            "fast_period": 3,
            "slow_period": 5,
            "timeframe": "1Day",
            "lookback_bars": 100,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 10.0,
            "max_order_value_usd": 2000,
            "max_trades_per_day": 2,
            "daily_loss_limit_pct": 3.0,
            "max_open_positions": 5,
            "max_bar_age_days": 0,  # fixture bars carry fixed Jan-2026 ts;
                                    # the freshness guard is tested explicitly
            "min_holding_days": 2,
            "brackets": {
                "enabled": True,
                "atr_period": 14,
                "stop_atr_mult": 2.0,
                "take_profit_atr_mult": 3.0,
                "time_in_force": "gtc",
            },
        },
        "llm": {"enabled": False, "model": "claude-sonnet-4-5", "max_tokens": 1000},
        "x_posting": {"enabled": True, "dry_run": True, "post_style": "recap",
                      "hashtags": "#papertrading #tradingbot", "disclose_paper": True,
                      "posts_log_path": str(tmp_path / "posts.jsonl"),
                      "journal_path": str(tmp_path / "journal.jsonl")},
        "memory": {
            "ledger_path": str(tmp_path / "ledger.jsonl"),
            "learnings_path": str(tmp_path / "learnings.md"),
            "negative_example_quota": 0.20,
            "review_lookback_trades": 25,
        },
        "learning": {
            "lessons_path": str(tmp_path / "lessons.jsonl"),
            "judgments_path": str(tmp_path / "judgments.jsonl"),
            "promotion_min_supports": 3,
            "promotion_support_ratio": 2.0,
            "refutation_min_evidence": 3,
            "staleness_days": 45,
            "counterfactual_extra_days": 5,
            "counterfactual_max_per_run": 10,
            "evaluator_batch_size": 5,
            "max_llm_calls_per_cycle": 2,
            "top_k_lessons": 8,
            "max_context_chars": 4000,
            "regime": {"sma_period": 50, "vol_period": 20,
                       "vol_low": 0.15, "vol_high": 0.25},
        },
        "backtest": {
            "slippage_bps": 5,
            "fee_per_trade_usd": 0.0,
            "walk_forward_split": 0.7,
            "trials_path": str(tmp_path / "backtest_trials.jsonl"),
        },
    }


@pytest.fixture
def account():
    return {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}


def make_bars(closes, start_day=1):
    """Build daily bar dicts from a list of closes (high=low=close unless spread)."""
    bars = []
    for i, c in enumerate(closes):
        day = start_day + i
        bars.append({
            "ts": f"2026-01-{day:02d}T21:00:00+00:00",
            "open": c, "high": c, "low": c, "close": c, "volume": 1000,
        })
    return bars


@pytest.fixture
def bars_up():
    """CLAUDE.md crossover fixture: SMA3 crosses above SMA5 on the last bar."""
    return make_bars([10] * 6 + [9, 9, 9, 20])
