#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEBUG = False


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


def log_debug(message):
    if DEBUG:
        print(message, flush=True)


def log_error(message):
    print(message, flush=True)

def sanitize(s: str) -> str:
    if not s:
        return ""

    s = s.strip()
    s = re.sub(r'[\\/:*?"<>|]+', "-", s)
    s = re.sub(r"\s+", " ", s)

    return s[:200]


def normalize_url(url: str) -> str:
    """
    Remove query string / fragment when creating an identity.

    This helps if NovelSound changes temporary query parameters while
    keeping the underlying audio URL path the same.
    """
    if not url:
        return ""

    try:
        parts = urlsplit(url)
        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            "",
            "",
        ))
    except Exception:
        return url


def chapter_key(series_id, chapter_index, chapter, url):
    """
    Stable local identifier.

    We intentionally do NOT rely on the filesystem path.

    Priority:
      series ID
      chapter ID if provided by NovelSound
      chapter index
      normalized URL
    """

    chapter_id = (
        chapter.get("id")
        or chapter.get("chapter_id")
        or chapter.get("_id")
        or ""
    )

    raw = "|".join([
        str(series_id),
        str(chapter_id),
        str(chapter_index),
        normalize_url(url),
    ])

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_filename(index: int, chapter_title: str, url: str) -> str:
    ext = ".mp3"

    m = re.search(r"(\.[a-zA-Z0-9]{2,6})(?:[?#]|$)", url or "")
    if m:
        ext = m.group(1)

    safe_title = sanitize(chapter_title) or "chapter"

    return f"{index:03d} - {safe_title}{ext}"


# ------------------------------------------------------------
# SQLite manifest
# ------------------------------------------------------------

class Manifest:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

        self.conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )

        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                chapter_key TEXT PRIMARY KEY,

                series_id TEXT,
                series_title TEXT,

                chapter_index INTEGER,
                chapter_id TEXT,
                chapter_title TEXT,

                audio_url TEXT,
                normalized_url TEXT,

                original_path TEXT,

                file_size INTEGER,

                status TEXT NOT NULL,

                downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_series
            ON downloads(series_id)
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_url
            ON downloads(normalized_url)
        """)

        self.conn.commit()

    def is_downloaded(self, key):
        with self.lock:
            row = self.conn.execute(
                """
                SELECT status
                FROM downloads
                WHERE chapter_key = ?
                """,
                (key,),
            ).fetchone()

        return row is not None and row[0] == "complete"

    def record_complete(
        self,
        key,
        series,
        chapter_index,
        chapter,
        url,
        path,
        file_size,
    ):
        series_id = series.get("id")
        series_title = series.get("title") or ""

        chapter_id = (
            chapter.get("id")
            or chapter.get("chapter_id")
            or chapter.get("_id")
            or ""
        )

        chapter_title = chapter.get("title") or ""

        with self.lock:
            self.conn.execute(
                """
                INSERT INTO downloads (
                    chapter_key,
                    series_id,
                    series_title,
                    chapter_index,
                    chapter_id,
                    chapter_title,
                    audio_url,
                    normalized_url,
                    original_path,
                    file_size,
                    status,
                    downloaded_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'complete',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)

                ON CONFLICT(chapter_key) DO UPDATE SET
                    series_title = excluded.series_title,
                    chapter_title = excluded.chapter_title,
                    audio_url = excluded.audio_url,
                    normalized_url = excluded.normalized_url,
                    original_path = excluded.original_path,
                    file_size = excluded.file_size,
                    status = 'complete',
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (
                    key,
                    str(series_id),
                    series_title,
                    chapter_index,
                    str(chapter_id),
                    chapter_title,
                    url,
                    normalize_url(url),
                    str(path),
                    file_size,
                ),
            )

            self.conn.commit()

    def stats(self):
        with self.lock:
            total = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM downloads
                WHERE status = 'complete'
                """
            ).fetchone()[0]

            series = self.conn.execute(
                """
                SELECT COUNT(DISTINCT series_id)
                FROM downloads
                WHERE status = 'complete'
                """
            ).fetchone()[0]

        return total, series

    def close(self):
        with self.lock:
            self.conn.close()


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

def requests_session(
    retries=3,
    backoff_factor=0.5,
    status_forcelist=(500, 502, 503, 504),
):
    session = requests.Session()

    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": "novelsound-downloader/1.0"
    })

    return session


def log_failure(log_path, series, chapter_index, chapter_title, url, status):
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "series_id": series.get("id"),
        "series_title": series.get("title") or "",
        "chapter_index": chapter_index,
        "chapter_title": chapter_title,
        "audio_url": url,
        "status": str(status),
    }

    log_error(
        "[FAIL] "
        f"series={series.get('id')} "
        f"chapter={chapter_index} "
        f"title={chapter_title} "
        f"status={status}"
    )

    if not log_path:
        return

    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ------------------------------------------------------------
# Existing library bootstrap
# ------------------------------------------------------------

def bootstrap_existing(data, dest_root, manifest):
    """
    Examine files using the OLD downloader's exact directory/filename
    convention.

    If a non-empty expected file exists, record it as complete.

    This lets an existing half-downloaded library become the starting
    state of the manifest without downloading anything.
    """

    dest_root = Path(dest_root)

    found = 0
    already_recorded = 0
    missing = 0

    print()
    print("Scanning existing NovelSound downloads...")
    print()

    for series in data:
        series_id = series.get("id")
        title = series.get("title") or f"series-{series_id}"

        safe_dirname = f"{series_id} - {sanitize(title)}"
        series_dir = dest_root / safe_dirname

        chapters = series.get("chapters", [])

        for idx, chapter in enumerate(chapters, start=1):
            url = chapter.get("audio_url") or chapter.get("url")

            if not url:
                continue

            key = chapter_key(
                series_id,
                idx,
                chapter,
                url,
            )

            if manifest.is_downloaded(key):
                already_recorded += 1
                continue

            filename = build_filename(
                idx,
                chapter.get("title") or f"chapter-{idx}",
                url,
            )

            path = series_dir / filename

            if path.exists() and path.is_file():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0

                if size > 0:
                    manifest.record_complete(
                        key,
                        series,
                        idx,
                        chapter,
                        url,
                        path,
                        size,
                    )

                    found += 1

                    if found % 1000 == 0:
                        print(
                            f"Recorded {found:,} existing files..."
                        )

                    continue

            missing += 1

    print()
    print("Bootstrap finished.")
    print(f"New existing files recorded : {found:,}")
    print(f"Already in manifest         : {already_recorded:,}")
    print(f"Not downloaded yet          : {missing:,}")
    print()


# ------------------------------------------------------------
# Downloading
# ------------------------------------------------------------

def download_url(session, url, dest_path, timeout=60):
    dest_path = Path(dest_path)
    tmp = dest_path.with_suffix(dest_path.suffix + ".part")

    if dest_path.exists():
        try:
            size = dest_path.stat().st_size
            if size > 0:
                return "exists", size
        except OSError:
            pass

        try:
            dest_path.unlink()
        except OSError:
            pass

    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass

    try:
        dest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        log_debug(f"[GET] {url}")

        with session.get(
            url,
            stream=True,
            timeout=timeout,
        ) as response:
            log_debug(
                f"[HTTP {response.status_code}] {url}"
            )

            response.raise_for_status()

            total = 0
            last_print = time.monotonic()

            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)

                    now = time.monotonic()
                    if now - last_print >= 10:
                        try:
                            f.flush()
                            actual_size = tmp.stat().st_size
                        except OSError:
                            actual_size = total

                        log_debug(
                            f"[PROGRESS] {dest_path.name}: "
                            f"received={total / 1024 / 1024:.1f} MiB "
                            f"on-disk={actual_size / 1024 / 1024:.1f} MiB "
                            f"file={tmp}"
                        )
                        last_print = now

        tmp.replace(dest_path)
        size = dest_path.stat().st_size

        if size <= 0:
            try:
                dest_path.unlink()
            except OSError:
                pass
            return "error: downloaded file is empty", 0

        return "downloaded", size

    except Exception as e:
        log_error(
            f"[REQUEST ERROR] {url} -> {type(e).__name__}: {e}"
        )

        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

        return f"error: {type(e).__name__}: {e}", 0


def download_series(
    series,
    dest_root,
    manifest,
    session,
    retries=2,
    log_path=None,
):
    series_id = series.get("id")

    title = (
        series.get("title")
        or f"series-{series_id}"
    )

    chapters = series.get("chapters", [])

    safe_dirname = (
        f"{series_id} - {sanitize(title)}"
    )

    series_dir = (
        Path(dest_root) / safe_dirname
    )

    series_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_debug(
        f"[START] Series {series_id} - {title} "
        f"({len(chapters)} chapters)"
    )

    results = []

    for idx, chapter in enumerate(
        chapters,
        start=1,
    ):
        url = (
            chapter.get("audio_url")
            or chapter.get("url")
        )

        chapter_title = (
            chapter.get("title")
            or f"chapter-{idx}"
        )

        if not url:
            results.append(
                (
                    series_id,
                    idx,
                    chapter_title,
                    "no-url",
                )
            )

            continue

        key = chapter_key(
            series_id,
            idx,
            chapter,
            url,
        )

        # ------------------------------------------------
        # IMPORTANT:
        # Manifest is checked BEFORE filesystem location.
        # ------------------------------------------------

        if manifest.is_downloaded(key):
            results.append(
                (
                    series_id,
                    idx,
                    chapter_title,
                    "manifest",
                )
            )

            continue

        filename = build_filename(
            idx,
            chapter_title,
            url,
        )

        dest_path = (
            series_dir / filename
        )

        status = None
        size = 0

        for attempt in range(
            retries + 1
        ):
            status, size = download_url(
                session,
                url,
                dest_path,
            )

            if status in ("downloaded", "exists"):
                manifest.record_complete(
                    key,
                    series,
                    idx,
                    chapter,
                    url,
                    dest_path,
                    size,
                )
                break

            permanent_error = False
            if isinstance(status, str):
                permanent_error = (
                    "404 Client Error" in status
                    or "403 Client Error" in status
                    or "400 Client Error" in status
                    or "410 Client Error" in status
                )

            if permanent_error:
                log_failure(
                    log_path,
                    series,
                    idx,
                    chapter_title,
                    url,
                    status,
                )
                break

            if attempt < retries:
                time.sleep(1 + attempt * 2)
                continue

            log_failure(
                log_path,
                series,
                idx,
                chapter_title,
                url,
                status,
            )
            break

        results.append(
            (
                series_id,
                idx,
                chapter_title,
                status,
            )
        )

    log_debug(
        f"[COMPLETE] Series {series_id} - {title}"
    )

    return results


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    global DEBUG

    parser = argparse.ArgumentParser(
        description=(
            "NovelSound downloader with persistent "
            "SQLite download manifest"
        )
    )

    parser.add_argument(
        "--input",
        "-i",
        default="novelsound_full.json",
        help="NovelSound full JSON dump",
    )

    parser.add_argument(
        "--dest",
        "-d",
        required=True,
        help="NovelSound destination directory",
    )

    parser.add_argument(
        "--manifest",
        default="novelsound_downloads.db",
        help=(
            "SQLite manifest database "
            "(default: novelsound_downloads.db)"
        ),
    )

    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=6,
    )

    parser.add_argument(
        "--series-workers",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--skip-if-status",
        choices=[
            "all",
            "complete",
            "ongoing",
        ],
        default="all",
    )

    parser.add_argument(
        "--log-file",
        default="download_failures.log",
        help=(
            "Append failed chapter downloads to this JSONL log file. "
            "Each line is one failed record."
        ),
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable verbose HTTP/debug output for request start, status, "
            "progress, and retries."
        ),
    )

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Scan existing downloads and populate "
            "the manifest without downloading"
        ),
    )

    args = parser.parse_args()
    DEBUG = args.debug

    input_path = Path(args.input)

    if not input_path.exists():
        print(
            "Input JSON not found:",
            input_path,
            file=sys.stderr,
        )
        sys.exit(2)

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    if not isinstance(data, list):
        print(
            "Expected the JSON root to be a list of series.",
            file=sys.stderr,
        )
        sys.exit(2)

    dest_root = Path(args.dest)

    dest_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest = Manifest(
        args.manifest
    )

    try:
        # ------------------------------------------------
        # Bootstrap mode
        # ------------------------------------------------

        if args.bootstrap:
            bootstrap_existing(
                data,
                dest_root,
                manifest,
            )

            chapters, series = (
                manifest.stats()
            )

            print(
                f"Manifest now contains "
                f"{chapters:,} chapters "
                f"across {series:,} series."
            )

            return

        # ------------------------------------------------
        # Normal download mode
        # ------------------------------------------------

        series_list = []

        for series in data:
            status = (
                series.get("status")
                or ""
            ).lower()

            if (
                args.skip_if_status == "complete"
                and status != "complete"
            ):
                continue

            if (
                args.skip_if_status == "ongoing"
                and status != "ongoing"
            ):
                continue

            series_list.append(series)

        print(
            f"Found {len(series_list):,} series "
            f"to process "
            f"(from {len(data):,} total)."
        )

        completed, completed_series = (
            manifest.stats()
        )

        print(
            f"Manifest already contains "
            f"{completed:,} downloaded chapters "
            f"across {completed_series:,} series."
        )

        session = requests_session(
            retries=3
        )

        series_workers = max(
            1,
            min(
                args.series_workers,
                args.workers,
            ),
        )

        with ThreadPoolExecutor(
            max_workers=series_workers
        ) as executor:

            futures = {
                executor.submit(
                    download_series,
                    series,
                    dest_root,
                    manifest,
                    session,
                    2,
                    args.log_file,
                ): series

                for series in series_list
            }

            for future in as_completed(
                futures
            ):
                series = futures[future]

                try:
                    results = future.result()

                except Exception as exc:
                    print(
                        f"Series "
                        f"{series.get('id')} - "
                        f"{series.get('title')} "
                        f"failed: {exc}"
                    )

                    continue

                downloaded = sum(
                    1
                    for result in results
                    if result[3] == "downloaded"
                )

                existing = sum(
                    1
                    for result in results
                    if result[3] == "exists"
                )

                manifest_skips = sum(
                    1
                    for result in results
                    if result[3] == "manifest"
                )

                errors = [
                    result
                    for result in results
                    if result[3]
                    and str(
                        result[3]
                    ).startswith("error")
                ]

                print(
                    f"Series "
                    f"{series.get('id')} - "
                    f"{series.get('title')}: "
                    f"downloaded={downloaded} "
                    f"existing={existing} "
                    f"manifest-skip={manifest_skips} "
                    f"errors={len(errors)}",
                    flush=True,
                )

                for error in errors:
                    print(
                        "   ",
                        error,
                        flush=True,
                    )

        completed, completed_series = (
            manifest.stats()
        )

        print()
        print("All series completed.")
        print(
            f"Manifest contains "
            f"{completed:,} downloaded chapters "
            f"across {completed_series:,} series."
        )

    finally:
        manifest.close()


if __name__ == "__main__":
    main()