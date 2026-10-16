#!/usr/bin/env python3
"""
Download all audio files listed in a novelsound_full.json (full API dump) or
novelsound_catalog.json (if you only have simplified data, prefer the full json).

Usage:
  python3 download_all.py --input novelsound_full.json --dest /mnt/audiobooks8/AudiobookshelfLib/Downloads/NovelSound/ --workers 6

Notes:
- Skips files that already exist with a non-zero size.
- Writes to a .part temporary file and renames when complete.
- Sanitizes titles to create filesystem-safe folder and file names.
- Retries simple transient network errors a few times.
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def sanitize(s: str) -> str:
    # Keep unicode letters/numbers/spaces and a few punctuation characters
    s = s.strip()
    # Replace path separators and other problematic characters
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    # Trim length
    return s[:200]


def requests_session(retries=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504)):
    s = requests.Session()
    r = Retry(total=retries, backoff_factor=backoff_factor, status_forcelist=status_forcelist)
    adapter = HTTPAdapter(max_retries=r)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    # sensible default headers
    s.headers.update({"User-Agent": "novelsound-downloader/1.0"})
    return s


def download_url(session, url, dest_path, timeout=60):
    dest_path = Path(dest_path)
    tmp = dest_path.with_suffix(dest_path.suffix + ".part")

    # If a completed file already exists, do not fetch it again.
    if dest_path.exists():
        if dest_path.stat().st_size > 0:
            return "exists"
        # Empty final file is stale; remove it so the download can be retried.
        try:
            dest_path.unlink()
        except OSError:
            pass

    # A stale temporary file from a prior partial run should not block a new download.
    if tmp.exists():
        try:
            if tmp.stat().st_size > 0 and not dest_path.exists():
                tmp.unlink()
        except OSError:
            pass

    try:
        with session.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
        tmp.replace(dest_path)
        return "downloaded"
    except Exception as e:
        # Clean up partial on failure
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return f"error: {e}"


def build_filename(index: int, chapter_title: str, url: str) -> str:
    # Determine extension from URL
    ext = ".mp3"
    m = re.search(r"(\.[a-zA-Z0-9]{2,6})(?:[?#]|$)", url)
    if m:
        ext = m.group(1)
    safe_title = sanitize(chapter_title) or "chapter"
    return f"{index:03d} - {safe_title}{ext}"


def download_series(series, dest_root, session, retries=1):
    series_id = series.get("id")
    title = series.get("title") or f"series-{series_id}"
    chapters = series.get("chapters", [])
    safe_dirname = f"{series_id} - {sanitize(title)}"
    series_dir = Path(dest_root) / safe_dirname
    series_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for idx, ch in enumerate(chapters, start=1):
        url = ch.get("audio_url") or ch.get("url")
        if not url:
            results.append((series_id, idx, ch.get("title"), "no-url"))
            continue
        fname = build_filename(idx, ch.get("title") or f"chapter-{idx}", url)
        dest_path = series_dir / fname

        # Try with retries (simple loop)
        status = None
        for attempt in range(retries + 1):
            status = download_url(session, url, dest_path)
            if status == "downloaded" or status == "exists":
                break
            # backoff before retry
            time.sleep(1 + attempt * 2)
        results.append((series_id, idx, ch.get("title"), status))
    return results


def main():
    p = argparse.ArgumentParser(description="Download all novelsound audio from a full JSON dump")
    p.add_argument("--input", "-i", default="novelsound_full.json", help="Input JSON file (full API dump)")
    p.add_argument("--dest", "-d", required=True, help="Destination root folder for downloads")
    p.add_argument("--workers", "-w", type=int, default=6, help="Concurrent download workers")
    p.add_argument("--series-workers", type=int, default=3, help="How many series to process concurrently (each series downloads serially to keep ordering)" )
    p.add_argument("--skip-if-status", choices=["all", "complete", "ongoing"], default="all", help="Skip series by status (useful to only download complete series)")
    args = p.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print("Input JSON not found:", input_path, file=sys.stderr)
        sys.exit(2)

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # data is expected to be a list of series
    dest_root = Path(args.dest)
    dest_root.mkdir(parents=True, exist_ok=True)

    # filter by status
    series_list = []
    for s in data:
        st = (s.get("status") or "").lower()
        if args.skip_if_status == "complete" and st != "complete":
            continue
        if args.skip_if_status == "ongoing" and st != "ongoing":
            continue
        series_list.append(s)

    print(f"Found {len(series_list)} series to download (from {len(data)} total).")

    # Create a shared requests session for downloads
    session = requests_session(retries=3)

    # Use ThreadPoolExecutor to process series concurrently. Each series processed serially to preserve chapter order in filenames.
    series_workers = max(1, min(args.series_workers, args.workers))

    total_tasks = len(series_list)
    with ThreadPoolExecutor(max_workers=series_workers) as ex:
        futures = {ex.submit(download_series, s, dest_root, session, 2): s for s in series_list}

        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f"Series {s.get('id')} - {s.get('title')} failed: {e}")
                continue
            downloaded = sum(1 for r in res if r[3] == "downloaded")
            exists = sum(1 for r in res if r[3] == "exists")
            errors = [r for r in res if r[3] and str(r[3]).startswith("error")]
            print(f"Series {s.get('id')} - {s.get('title')}: downloaded={downloaded} exists={exists} errors={len(errors)}")
            for e in errors:
                print("  ", e)

    print("All series submitted/completed.")


if __name__ == "__main__":
    main()
