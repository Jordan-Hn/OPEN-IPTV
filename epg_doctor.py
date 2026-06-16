"""
EPG diagnostic for OPEN-IPTV.

A standalone tool that reports what your source's XMLTV guide actually contains
and how well it lines up with your channel catalog. Reach for it when "now
playing" looks empty or wrong: it prints the guide URL in use, how many
programmes and channels the guide carries, and the match rate between the guide's
channel ids and the ``tvg-id``s on your live channels.

Run from the project folder, after the app has built its catalog at least once:

    python epg_doctor.py             # use the cached guide, fetch it once if empty
    python epg_doctor.py --refresh   # re-download the guide first
    python epg_doctor.py --no-refresh

It reads ``config.json`` and the existing catalog / guide databases and changes
nothing, except that a refresh updates the local guide cache. Credentials are
never printed.

stdlib only (plus the project's own modules).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from catalog import Catalog
from config_store import Config
from epg_guide import EpgGuide

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "iptv_catalog.db")
EPG_DB_PATH = os.path.join(BASE_DIR, "iptv_epg.db")

_SECRET_KEYS = {"username", "password", "user", "pass", "pwd", "token"}


def redact(url: str) -> str:
    """Return ``url`` with credentials masked, safe to print."""
    if not url:
        return "(none)"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        masked = []
        for pair in parts.query.split("&"):
            if not pair:
                continue
            key, _sep, _value = pair.partition("=")
            masked.append(f"{key}=***" if key.lower() in _SECRET_KEYS else pair)
        return urlunsplit((parts.scheme, host, parts.path, "&".join(masked), ""))
    except Exception:
        return "(unprintable url)"


def human_age(seconds: float) -> str:
    """A rough '3h ago' style age for a number of seconds."""
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def diagnose(refresh: str = "auto") -> int:
    """Print the EPG report. ``refresh`` is 'auto', 'always', or 'never'.

    Returns a process exit code (0 on a usable guide, 1 otherwise).
    """
    config = Config(CONFIG_FILE)
    config.load()

    if not os.path.exists(DB_PATH):
        print("No catalog database found next to this script.")
        print("Run the app once (python iptv_launcher.py) to build it, then retry.")
        return 1

    catalog = Catalog(DB_PATH)
    total = catalog.total_channels()
    live_total, live_tagged = catalog.live_counts()

    src = config.get("source") or {}
    epg_url = (src.get("epg_url") or "").strip() or catalog.epg_url()

    print("OPEN-IPTV EPG doctor")
    print("=" * 60)
    print(f"Catalog        : {total:,} entries, {live_total:,} live "
          f"({live_tagged:,} carry a tvg-id)")
    print(f"Guide URL      : {redact(epg_url)}")
    if not epg_url:
        print("\nNo guide URL is configured, so there is no EPG to check.")
        print("Add an XMLTV URL in Settings, or use an Xtream source (its guide")
        print("endpoint is derived automatically).")
        return 1

    guide = EpgGuide(EPG_DB_PATH, epg_url=epg_url)
    have = guide.programme_count()
    do_refresh = refresh == "always" or (refresh == "auto" and have == 0)
    if do_refresh:
        print("\nDownloading and indexing the guide (this can take a minute)...")

        def progress(phase, value):
            if phase == "download":
                sys.stdout.write(f"\r  downloaded {value // 1_000_000} MB ")
            else:
                sys.stdout.write(f"\r  indexed {value:,} programmes ")
            sys.stdout.flush()

        try:
            guide.refresh(progress=progress)
            print()
        except Exception as exc:
            print(f"\n  refresh failed: {exc}")
            if have == 0:
                return 1
            print("  continuing with the cached guide.")

    programmes = guide.programme_count()
    if programmes == 0:
        print("\nThe guide is empty. The URL may be wrong, need credentials, or be")
        print("temporarily unreachable. Try '--refresh', or open the URL in a browser.")
        return 1

    age = human_age(max(0.0, time.time() - guide.last_fetch()))
    print()
    print(f"Guide          : {programmes:,} programmes across "
          f"{guide.channel_count():,} channels, fetched {age}")
    print(f"Freshness      : {'fresh' if guide.is_fresh() else 'stale (the app will refresh it)'}")

    # Match the catalog's live channels to the guide by tvg-id.
    live_ids = set(catalog.live_tvg_ids())
    guide_ids = guide.channel_ids()
    matched = live_ids & guide_ids
    if live_ids:
        pct = 100 * len(matched) / len(live_ids)
        print(f"Match          : {len(matched):,} of {len(live_ids):,} distinct live "
              f"tvg-ids are in the guide ({pct:.0f}%)")
    else:
        print("Match          : no live channels carry a tvg-id, so none can be matched")

    # A few matched channels with their current programme, as a sanity check.
    sample_ids = list(matched)[:5]
    if sample_ids:
        chans = catalog.live_channels_by_tvg_ids(sample_ids)
        print("\nNow playing on a few matched channels:")
        for tvg in sample_ids:
            entries = chans.get(tvg) or []
            name = entries[0]["name"] if entries else tvg
            current, _nxt = guide.now_next(tvg)
            show = current.title if current and current.title else "(nothing on now)"
            print(f"  - {name[:38]:38}  {show}")

    # Surface mismatches both ways, the usual cause of empty 'now playing'.
    unmatched = list(live_ids - guide_ids)[:5]
    if unmatched:
        print("\nLive tvg-ids with no guide entry (sample):")
        for tvg in unmatched:
            print(f"  - {tvg}")
    extra = list(guide_ids - live_ids)[:5]
    if extra:
        print("\nGuide channel ids not used by any live channel (sample):")
        for cid in extra:
            print(f"  - {cid}")

    print()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose OPEN-IPTV's EPG guide and channel matching.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_const", const="always", dest="refresh",
                       help="re-download the guide before reporting")
    group.add_argument("--no-refresh", action="store_const", const="never", dest="refresh",
                       help="never download, report only the cached guide")
    parser.set_defaults(refresh="auto")
    args = parser.parse_args(argv)
    try:
        return diagnose(args.refresh)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
