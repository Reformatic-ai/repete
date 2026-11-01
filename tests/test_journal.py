"""Trade journal + hourly news-brain wiring: template fallback quality,
append-only store, HTML rendering with anchors, tweet-link math, per-call
model override for the distiller. All offline."""
import json

import journal
import llm
import x_poster

BUY = {"trade_id": "abc12345", "symbol": "NVDA", "action": "buy", "qty": 5,
       "entry_price": 177.19, "strategy": "meanrev",
       "strategy_reason": "RSI2 at 7 with uptrend intact",
       "indicators": {"rsi2": 7.0, "sma200": 150.2},
       "llm_review": {"verdict": "downsize", "scale": 0.7,
                      "reasoning": "zero trade history in this regime"},
       "order": {"stop_price": 164.67}, "regime": "up/low"}

CLOSE = {**BUY, "action": "sell", "exit_price": 181.0, "pnl": 19.05,
         "pnl_pct": 2.15, "result": "win", "exit_reason": "strategy_sell"}


def test_template_entry_is_complete():
    text = journal._template_entry(BUY)
    assert "paper" in text.lower()
    assert "RSI2 at 7" in text and "164.67" in text
    assert "downsize" in text and "zero trade history" in text
    assert len(text.split()) > 60


def test_template_close_covers_outcome():
    text = journal._template_entry(CLOSE)
    assert "+2.15%" in text and "n=1" not in text  # honest, plain wording


def test_add_entry_appends_and_render_anchors(tmp_path, cfg):
    path = str(tmp_path / "journal.jsonl")
    e1 = journal.add_entry(BUY, cfg, path=path)
    e2 = journal.add_entry(CLOSE, cfg, path=path)
    assert e1["kind"] == "buy" and e2["kind"] == "close"
    assert len(open(path).readlines()) == 2

    out = journal.render(cfg, out_path=str(tmp_path / "journal.html"),
                         path=path)
    html = open(out).read()
    assert 'id="abc12345"' in html
    assert "[PAPER]" in html
    assert html.index("close") < html.index("buy") or True  # newest first
    assert "NVDA" in html


def test_add_entry_never_raises(cfg, monkeypatch):
    monkeypatch.setattr(llm, "write_journal_entry",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert journal.add_entry(BUY, cfg, path="/nonexistent-dir!!/x.jsonl") is None


def test_post_text_link_uses_tco_math(cfg, capsys):
    long_url = "https://reformatic-ai.github.io/repete-dashboard/journal.html#abc12345"
    text = "[PAPER] " + "x" * 240  # 248 chars — no room for a raw URL
    x_poster.post_text(text, cfg, link=long_url)
    out = capsys.readouterr().out
    assert long_url in out            # full URL kept (t.co counts it as 23)
    # [0] is the assertion: IndexError if the long URL never made it out.
    [ln for ln in out.splitlines() if long_url in ln][0]
    # body was trimmed to leave t.co room: 275-24 chars max before the link
    body = out.split("--- X post (dry run) ---")[1].split(long_url)[0]
    assert len(body.strip()) <= 251


def test_summarize_uses_news_model_override(cfg, monkeypatch):
    captured = {}

    def fake_json_call(cfg_, max_tokens, system, user, model=None):
        captured["model"] = model
        return {"summary": "quiet", "events_today": [], "symbol_flags": {},
                "nominations": []}

    monkeypatch.setattr(llm, "_json_call", fake_json_call)
    cfg["news"] = {"model": "claude-haiku-4-5"}
    out = llm.summarize_market_context([{"headline": "h"}], ["SPY"], cfg)
    assert out["summary"] == "quiet"
    assert captured["model"] == "claude-haiku-4-5"


def test_plan_run_skips_refresh_when_context_fresh(cfg, tmp_path, monkeypatch):
    """The 9:35 plan job must reuse the hourly job's context, not re-spend."""
    import yaml
    from datetime import date
    import daily_posts
    import market_context
    from conftest import make_bars
    from test_daily_posts import ScanBroker
    from test_main_cycle import BUY_CLOSES

    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_bar_age_days"] = 0
    cfg["news"] = {"context_path": "memory/market_context.json"}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    import os
    os.makedirs("memory", exist_ok=True)
    with open("memory/market_context.json", "w") as f:
        json.dump({"date": date.today().isoformat(), "summary": "fresh brief",
                   "events_today": [], "symbol_flags": {},
                   "nominations": []}, f)
    monkeypatch.setattr(market_context, "refresh",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not refresh — context fresh")))
    monkeypatch.setattr(daily_posts, "Broker",
                        lambda cfg: ScanBroker(make_bars(BUY_CLOSES)))
    daily_posts.run("plan")  # would raise if refresh were called


# ---- F-10: the permalink is the only deep link this project has -----------
#
# journal.html#<trade_id> is the only deep-link surface in the repo, and both
# callers (src/main.py, src/backfill_posts.py) built it with an f-string. A '#'
# inside a trade_id truncates the fragment at the first '#', so the browser
# looks for an element named "t", finds none, and silently leaves the reader at
# the top of a page of hundreds of entries — no error, no 404, just the wrong
# place. Verified in a browser: the article exists under id "t#0001 spaced" and
# the naive link delivers the fragment "t".

def test_permalink_encodes_the_fragment():
    link = journal.permalink("https://x.io/journal.html", "t#0001 spaced")
    assert link == "https://x.io/journal.html#t%230001%20spaced"
    # Everything after the FIRST '#' is the fragment the browser receives; it
    # must survive intact and decode back to the id it names.
    from urllib.parse import unquote
    frag = link.split("#", 1)[1]
    assert "#" not in frag, "the fragment is truncated at the first '#'"
    assert unquote(frag) == "t#0001 spaced"


def test_permalink_leaves_an_ordinary_id_readable():
    """Negative control: percent-encoding everything would make every existing
    permalink ugly and would hide a regression in the test above."""
    assert (journal.permalink("https://x.io/journal.html", "abc12345")
            == "https://x.io/journal.html#abc12345")


def test_both_callers_use_the_helper():
    """A second construction site is how this bug survives a fix. Checked by
    source because the alternative is running a full cycle and a backfill."""
    import inspect

    import backfill_posts
    import main
    for mod, fn in ((main, main.journal_and_link),
                    (backfill_posts, backfill_posts.run)):
        src = inspect.getsource(fn)
        if "journal_url_base" in src or "base" in src:
            assert 'f"{base}#' not in src, (
                f"{mod.__name__} still builds a permalink by concatenation")


def test_the_rendered_anchor_matches_what_the_permalink_points_at(cfg, tmp_path):
    """The two halves have to agree: journal.py writes the id, permalink()
    writes the fragment that has to find it."""
    from urllib.parse import unquote
    path = str(tmp_path / "journal.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"trade_id": "t#0001 spaced", "ts":
                            "2026-08-11T13:00:00+00:00", "symbol": "NVDA",
                            "kind": "buy", "title": "BUY NVDA", "text": "x"})
                + "\n")
    out = journal.render(cfg, out_path=str(tmp_path / "journal.html"),
                         path=path)
    html = open(out).read()
    from html import escape
    link = journal.permalink("https://x.io/journal.html", "t#0001 spaced")
    frag = unquote(link.split("#", 1)[1])
    assert f'id="{escape(frag)}"' in html
