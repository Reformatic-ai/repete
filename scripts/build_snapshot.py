#!/usr/bin/env python3
"""Build a FROZEN bars snapshot for gate reproducibility.

Why this exists
---------------
Gate sections §1–§13 in `knowledge/backtest_candidates.md` were all scored on
one frozen file, `memory/bars_snapshot_2020_2026-07-10.json`. `memory/` is in
`.gitignore`, so that file was never committed — and it is now gone. Every
number those sections quote is, today, **unreproducible**. That is a process
bug, not bad luck: a snapshot that a gate depends on must live in version
control next to the verdicts it supports.

So snapshots now land in `data/snapshots/` (committed) and carry a SHA256 in
`data/snapshots/MANIFEST.json`. `--verify` re-hashes and fails loudly on drift,
which is what makes "re-running §14 reproduces §14" a checkable claim rather
than a hope.

Source: yfinance. No API keys, so any future session can rebuild without
credentials — that is the whole point.

    python scripts/build_snapshot.py                 # build the default snapshot
    python scripts/build_snapshot.py --verify        # check hashes, touch nothing

NOT interchangeable with the old file
-------------------------------------
yfinance serves split/dividend-ADJUSTED closes; the retired Alpaca snapshot
served raw prices. Same tickers, different numbers. Any strategy comparison
must therefore re-measure its baseline *inside this snapshot* — carrying a
figure across the two is comparing two different datasets. Sections written
against this file say so in their header.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots")
MANIFEST = os.path.join(SNAP_DIR, "MANIFEST.json")

DEFAULT_START = "2020-01-01"
DEFAULT_END = "2026-07-10"


def _config_symbols() -> list:
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(cfg_path) as f:
        return list(yaml.safe_load(f)["symbols"])


# ---------------------------------------------------------------- §32 universe

# S&P 1500 = 500 large + 400 mid + 600 small. The MID and SMALL lists are the
# point: a large-cap-only universe silently deletes every name that fell out of
# the index, and momentum strategies exploit that deletion far more than
# buy-and-hold does. Including mid/small recovers part of the loser
# distribution. It does NOT fix survivorship — see the §32 registration, which
# states the bias and its direction rather than pretending it away.
_INDEX_PAGES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
_UA = {"User-Agent": "repete-research/1.0 "
                     "(github.com/Reformatic-ai/repete)"}


def index_constituents(which: str = "sp1500") -> list:
    """Current index membership, sorted. Network, and deliberately not cached:
    the caller freezes the RESULT into a hashed snapshot, which is the artifact
    that has to be reproducible — not this lookup."""
    import io
    import urllib.request

    import pandas as pd

    names = (["sp500", "sp400", "sp600"] if which == "sp1500" else [which])
    out: set = set()
    for name in names:
        req = urllib.request.Request(_INDEX_PAGES[name], headers=_UA)
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", "replace")
        picked = None
        for table in pd.read_html(io.StringIO(html)):
            cols = [str(c) for c in table.columns]
            hit = [c for c in cols if c in ("Symbol", "Ticker", "Ticker symbol")]
            if hit and len(table) > 100:
                picked = [str(x).strip() for x in table[hit[0]]]
                break
        if picked is None:
            raise SystemExit(f"{name}: could not find a constituent table — "
                             f"the page layout changed")
        print(f"  {name}: {len(picked)} constituents")
        out |= set(picked)
    # yfinance wants BRK-B, Wikipedia writes BRK.B
    return sorted(s.replace(".", "-") for s in out if s and s != "nan")


def liquidity_screen(bars: dict, top_n: int, screen_year: str) -> list:
    """Top `top_n` symbols by MEDIAN DOLLAR VOLUME during `screen_year` only.

    The year matters more than the metric. Screening on liquidity measured over
    the whole file would select using data from the out-of-sample window — a
    subtle lookahead that would flatter every arm equally and be almost
    invisible in the result. Restricting the screen to the first calendar year
    means the universe is decided with information available before the test
    period starts.

    Symbols with no bars in that year are dropped: a name that was not trading
    at the start cannot be part of a universe chosen at the start.
    """
    scored = []
    for sym, rows in bars.items():
        vals = [r["close"] * r["volume"] for r in rows
                if r["ts"][:4] == screen_year and r.get("volume")]
        if len(vals) < 100:            # <~40% of a trading year: not established
            continue
        vals.sort()
        scored.append((vals[len(vals) // 2], sym))
    scored.sort(reverse=True)
    kept = sorted(s for _, s in scored[:top_n])
    print(f"  liquidity screen ({screen_year} median $ volume): "
          f"{len(bars)} fetched -> {len(scored)} established -> {len(kept)} kept")
    return kept


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(symbols: list, start: str, end: str, chunk: int = 100) -> dict:
    """{symbol: [{ts, open, high, low, close, volume}, ...]} in broker.bars shape.

    Chunked since 2026-07-27: one `yf.download` of 1,500 tickers returns a
    partial frame or times out, and the failure is silent — missing symbols just
    don't appear. A chunk that fails wholesale is reported and skipped rather
    than aborting a 15-minute download, and `sanity_check` refuses to write a
    snapshot that lost symbols it was asked for.
    """
    out: dict = {}
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        print(f"  batch {i // chunk + 1}/{-(-len(symbols) // chunk)} "
              f"({len(batch)} symbols)…", flush=True)
        try:
            out.update(_fetch_batch(batch, start, end))
        except Exception as e:  # noqa: BLE001 — reported, then the rest continues
            print(f"  !! batch starting {batch[0]} FAILED ({e}) — skipped",
                  file=sys.stderr)
    return out


def _fetch_batch(symbols: list, start: str, end: str) -> dict:
    import yfinance as yf

    # end is inclusive for us, exclusive for yfinance.
    end_excl = (datetime.fromisoformat(end).date().toordinal() + 1)
    end_excl = datetime.fromordinal(end_excl).date().isoformat()

    raw = yf.download(symbols, start=start, end=end_excl, interval="1d",
                      auto_adjust=True, progress=False, group_by="ticker",
                      threads=True)
    if raw is None or raw.empty:
        raise SystemExit("yfinance returned nothing — no network, or bad symbols")

    out: dict = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all")
        except KeyError:
            print(f"  !! {sym}: absent from the response — SKIPPED", file=sys.stderr)
            continue
        rows = []
        for ts, r in df.iterrows():
            if any(r.get(c) is None or r.get(c) != r.get(c)      # NaN check
                   for c in ("Open", "High", "Low", "Close")):
                continue
            rows.append({
                "ts": ts.tz_localize("UTC").isoformat() if ts.tzinfo is None
                      else ts.tz_convert("UTC").isoformat(),
                "open": round(float(r["Open"]), 6),
                "high": round(float(r["High"]), 6),
                "low": round(float(r["Low"]), 6),
                "close": round(float(r["Close"]), 6),
                "volume": float(r.get("Volume", 0) or 0),
            })
        if not rows:
            print(f"  !! {sym}: zero usable bars — SKIPPED", file=sys.stderr)
            continue
        out[sym] = rows
    return out


def sanity_check(bars: dict, symbols: list, start: str, end: str,
                 require_all: bool = True) -> list:
    """Return a list of problems. A snapshot that silently lost half its
    universe would quietly change every gate verdict downstream, so these are
    reported and — for the fatal ones — refuse to write.

    `require_all=False` for the §32 wide build: asking yfinance for 1,500
    current index members and getting 1,470 back is expected (renames, recent
    additions with no 2020 history), and the liquidity screen then picks the top
    N from whatever arrived. A missing name there is attrition, not corruption.
    For the 38-symbol config universe every name is load-bearing, so it stays
    fatal."""
    problems = []
    missing = [s for s in symbols if s not in bars]
    if missing:
        shown = missing if len(missing) <= 20 else missing[:20] + ["…"]
        problems.append(f"{'FATAL' if require_all else 'WARN'}: "
                        f"{len(missing)} symbols missing: {shown}")

    lengths = {s: len(v) for s, v in bars.items()}
    if lengths:
        longest = max(lengths.values())
        # A 2020-2026 daily series is ~1630 bars. Anything under 80% of the
        # longest series is a listing gap or a truncated download, not a bar
        # count — and it would bias any cross-sectional strategy.
        short = {s: n for s, n in lengths.items() if n < 0.8 * longest}
        if short:
            problems.append(f"WARN: short series (<80% of {longest} bars): {short}")

    for sym, rows in bars.items():
        for r in rows[:1] + rows[-1:]:
            if not (r["low"] <= r["open"] <= r["high"]
                    and r["low"] <= r["close"] <= r["high"]):
                problems.append(f"FATAL: {sym} {r['ts']} OHLC bounds violated")
        if any(r["close"] <= 0 for r in rows):
            problems.append(f"FATAL: {sym} has a non-positive close")
        ts = [r["ts"] for r in rows]
        if ts != sorted(ts):
            problems.append(f"FATAL: {sym} bars are not chronologically sorted")
        if len(set(ts)) != len(ts):
            problems.append(f"FATAL: {sym} has duplicate timestamps")
    return problems


def manifest_path(snap_dir: str) -> str:
    return os.path.join(snap_dir, "MANIFEST.json")


def load_manifest(snap_dir: str = SNAP_DIR) -> dict:
    mpath = manifest_path(snap_dir)
    if os.path.exists(mpath):
        with open(mpath) as f:
            return json.load(f)
    return {"snapshots": {}}


def cmd_verify(snap_dir: str = SNAP_DIR) -> int:
    man = load_manifest(snap_dir)
    if not man["snapshots"]:
        print(f"no snapshots recorded in {manifest_path(snap_dir)}")
        return 1
    bad = 0
    for name, meta in sorted(man["snapshots"].items()):
        path = os.path.join(snap_dir, name)
        if not os.path.exists(path):
            print(f"MISSING  {name}")
            bad += 1
            continue
        actual = sha256_file(path)
        ok = actual == meta["sha256"]
        print(f"{'OK      ' if ok else 'DRIFTED '} {name}  "
              f"{meta['n_symbols']} symbols  {meta['n_bars']} bars")
        if not ok:
            print(f"         expected {meta['sha256'][:16]}… got {actual[:16]}…")
            bad += 1
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END, help="inclusive")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="default: every symbol in config.yaml")
    p.add_argument("--out", default=None, help="default: bars_<start>_<end>.json.gz")
    p.add_argument("--dir", default="data/snapshots",
                   help="where the snapshot and its MANIFEST live. §56 writes "
                        "the survivorship-CERTIFIED universe to data/pit/, "
                        "which is a different place on purpose: register_gate's "
                        "EDGE freeze is keyed on the data/snapshots/ prefix, "
                        "and everything under it is survivor-selected by "
                        "construction. Writing elsewhere is only legitimate "
                        "with a passing probe record — "
                        "tests/test_pit_snapshot_requires_probe.py enforces that.")
    p.add_argument("--verify", action="store_true",
                   help="re-hash committed snapshots and exit")
    p.add_argument("--universe", default=None,
                   choices=sorted(_INDEX_PAGES) + ["sp1500"],
                   help="§32: fetch current index membership instead of "
                        "config.yaml's symbols")
    p.add_argument("--top", type=int, default=0,
                   help="§32: keep the top N by median dollar volume in the "
                        "screen year (0 = keep everything fetched)")
    p.add_argument("--screen-year", default=None,
                   help="§32: year the liquidity screen measures "
                        "(default: the start year — point-in-time)")
    p.add_argument("--always-include", nargs="+", default=None,
                   help="symbols kept regardless of the liquidity screen. SPY "
                        "is not optional: simulate_ensemble reads it for the "
                        "regime label and vol bucket (backtest.py:640), and "
                        "risk.regime_exposure is ENABLED — a universe without "
                        "SPY silently stops that rail binding, which is a "
                        "sim/live divergence, not a smaller universe.")
    args = p.parse_args()
    snap_dir = os.path.join(os.path.dirname(__file__), "..", *args.dir.split("/"))

    if args.verify:
        return cmd_verify(snap_dir)

    if args.universe:
        print(f"resolving universe {args.universe}…")
        symbols = index_constituents(args.universe)
        if args.always_include:
            extra = [s for s in args.always_include if s not in symbols]
            symbols = sorted(symbols + extra)
            print(f"  force-included: {extra}")
        print(f"  union: {len(symbols)} tickers")
    else:
        symbols = args.symbols or _config_symbols()
    name = args.out or f"bars_{args.start}_{args.end}.json.gz"
    if "/" in name or os.sep in name:
        # `--out` is a FILENAME; it is joined with SNAP_DIR below. Passing a
        # path silently produced `data/snapshots/data/snapshots/…` and raised
        # FileNotFoundError on write — AFTER a full 1,181-symbol fetch had
        # already run (2026-07-28). Fail here, before the network work, and say
        # exactly what to pass instead.
        raise SystemExit(
            f"--out takes a filename, not a path: {name!r}\n"
            f"It is written into {args.dir}/ automatically.\n"
            f"  use:  --out {os.path.basename(name)}")
    os.makedirs(snap_dir, exist_ok=True)
    path = os.path.join(snap_dir, name)

    print(f"fetching {len(symbols)} symbols {args.start} → {args.end} (yfinance)…")
    bars = fetch(symbols, args.start, args.end)

    if args.top:
        keep = liquidity_screen(bars, args.top,
                                args.screen_year or args.start[:4])
        bars = {s: bars[s] for s in keep}
        symbols = keep

    problems = sanity_check(bars, symbols, args.start, args.end,
                            require_all=not args.universe)
    for msg in problems:
        print("  " + msg, file=sys.stderr)
    if any(m.startswith("FATAL") for m in problems):
        print("refusing to write a snapshot that failed sanity checks",
              file=sys.stderr)
        return 2

    # gzip: 7.8 MB -> 1.8 MB, and these files live in git forever.
    # mtime=0 so rebuilding identical data yields an identical hash.
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as gz:
        gz.write(json.dumps(bars, separators=(",", ":"),
                            sort_keys=True).encode())

    n_bars = sum(len(v) for v in bars.values())
    man = load_manifest(snap_dir)
    man["snapshots"][name] = {
        "sha256": sha256_file(path),
        "source": "yfinance (auto_adjust=True — split/dividend adjusted)",
        "start": args.start,
        "end": args.end,
        "n_symbols": len(bars),
        "n_bars": n_bars,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "note": "Adjusted prices. NOT comparable with the retired Alpaca raw-price "
                "snapshot used by §1–§13; re-baseline inside this file.",
    }
    with open(manifest_path(snap_dir), "w") as f:
        json.dump(man, f, indent=2, sort_keys=True)

    size_mb = os.path.getsize(path) / 1e6
    print(f"wrote {path}  ({len(bars)} symbols, {n_bars} bars, {size_mb:.1f} MB)")
    print(f"sha256 {man['snapshots'][name]['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
