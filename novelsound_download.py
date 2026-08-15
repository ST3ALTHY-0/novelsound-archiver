#!/usr/bin/env python3

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests


DEFAULT_CATALOG = "novelsound_full.json"
DEFAULT_OUTPUT = "Audiobooks"


def sanitize(name):
    """Make a string safe for use as a Linux filename."""
    name = name.strip()

    # Replace path separators/control chars
    name = re.sub(r'[/\x00-\x1f]', "_", name)

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name)

    # Avoid weird trailing dots/spaces
    name = name.rstrip(". ")

    return name or "Unknown"


def load_catalog(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Supports both:
    #
    # [
    #   {...},
    #   {...}
    # ]
    #
    # and:
    #
    # {
    #   "items": [...]
    # }

    if isinstance(data, dict):
        data = data.get("items", [])

    if not isinstance(data, list):
        raise ValueError("Catalog is not a list and contains no 'items' list.")

    return data


def find_series(catalog, query):
    query = query.strip()

    # Allow numeric series ID
    if query.isdigit():
        sid = int(query)

        matches = [
            s for s in catalog
            if s.get("id") == sid
        ]

        if matches:
            return matches[0]

        raise ValueError(f"No series with ID {sid}")

    q = query.casefold()

    # Exact title first
    exact = [
        s for s in catalog
        if s.get("title", "").strip().casefold() == q
    ]

    if len(exact) == 1:
        return exact[0]

    # Then allow partial matching
    partial = [
        s for s in catalog
        if q in s.get("title", "").casefold()
    ]

    if len(partial) == 1:
        return partial[0]

    if not partial:
        raise ValueError(
            f'No series matching "{query}"'
        )

    titles = "\n".join(
        f'  {s.get("id")}: {s.get("title")}'
        for s in partial[:20]
    )

    raise ValueError(
        f'"{query}" matches multiple series:\n{titles}\n'
        f"Use the numeric series ID or a more specific title."
    )


def get_extension(url):
    path = unquote(urlparse(url).path)

    suffix = Path(path).suffix

    if suffix and len(suffix) <= 8:
        return suffix

    return ".mp3"


def chapter_path(output_dir, series, chapter, library_layout=False):
    series_title = sanitize(
        series.get("audible_series")
        or series.get("title")
        or f"Series {series.get('id')}"
    )

    chapter_title = sanitize(
        chapter.get("title")
        or f"Chapter {chapter.get('id')}"
    )

    extension = get_extension(
        chapter.get("audio_url", "")
    )

    if library_layout:
        author = sanitize(
            series.get("author")
            or "Unknown Author"
        )

        folder = (
            output_dir
            / author
            / series_title
            / chapter_title
        )

    else:
        folder = (
            output_dir
            / series_title
        )

    return folder / f"{chapter_title}{extension}"


def download_file( series,
    chapter,
    output_dir,
    timeout,
    library_layout=False):
    url = chapter.get("audio_url")

    if not url:
        return "error", series, chapter, "No audio_url"

    final_path = chapter_path(
    output_dir,
    series,
    chapter,
    library_layout
    )

    final_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Already downloaded
    if final_path.exists() and final_path.stat().st_size > 0:
        return "skip", series, chapter, final_path

    part_path = Path(str(final_path) + ".part")

    existing_size = 0

    if part_path.exists():
        existing_size = part_path.stat().st_size

    headers = {}

    if existing_size:
        headers["Range"] = f"bytes={existing_size}-"

    try:
        r = requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=timeout,
            allow_redirects=True,
        )

        # Server honored our resume request
        if existing_size and r.status_code == 206:
            mode = "ab"

        # Server ignored Range. Start over safely.
        elif r.status_code == 200:
            mode = "wb"
            existing_size = 0

        else:
            r.raise_for_status()
            mode = "wb"

        with open(part_path, mode) as f:
            for chunk in r.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    f.write(chunk)

        part_path.rename(final_path)

        return "done", series, chapter, final_path

    except Exception as e:
        return "error", series, chapter, str(e)


def collect_jobs(series_list, output_dir):
    jobs = []

    for series in series_list:
        chapters = series.get("chapters", [])

        for chapter in chapters:
            jobs.append(
                (
                    series,
                    chapter,
                    output_dir
                )
            )

    return jobs


def read_series_file(path):
    results = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            results.append(line)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Download audiobooks from a NovelSound catalog."
    )

    source = parser.add_mutually_exclusive_group(
        required=True
    )

    source.add_argument(
        "--all",
        action="store_true",
        help="Download every series in the catalog.",
    )

    source.add_argument(
        "--series",
        action="append",
        help=(
            "Download a series by title or ID. "
            "May be specified multiple times."
        ),
    )

    source.add_argument(
        "--series-file",
        help=(
            "Text file containing one series title "
            "or ID per line."
        ),
    )

    parser.add_argument(
        "--catalog",
        default=DEFAULT_CATALOG,
        help=f"Catalog JSON file (default: {DEFAULT_CATALOG})",
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Download directory (default: {DEFAULT_OUTPUT})",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent downloads (default: 4)",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading.",
    )
    
    parser.add_argument(
        "--library-layout",
        action="store_true",
        help=(
            "Use Author/Series/Book/audio.ext directory layout. "
            "Falls back to 'Unknown Author' when author metadata is missing."
        ),
    )

    args = parser.parse_args()

    catalog = load_catalog(args.catalog)

    print(f"Catalog contains {len(catalog)} series.")

    # Select series
    if args.all:
        selected = catalog

    else:
        queries = []

        if args.series:
            queries.extend(args.series)

        if args.series_file:
            queries.extend(
                read_series_file(args.series_file)
            )

        selected = []
        seen = set()

        for query in queries:
            try:
                series = find_series(
                    catalog,
                    query
                )

            except ValueError as e:
                print(f"\nERROR: {e}", file=sys.stderr)
                sys.exit(1)

            sid = series.get("id")

            if sid not in seen:
                selected.append(series)
                seen.add(sid)

    output_dir = Path(args.output).expanduser()

    jobs = collect_jobs(
        selected,
        output_dir
    )

    print(
        f"Selected {len(selected)} series "
        f"with {len(jobs)} audio files."
    )

    for series in selected:
        print(
            f"  [{series.get('id')}] "
            f"{series.get('title')} "
            f"({len(series.get('chapters', []))} files)"
        )

    if args.dry_run:
        print("\nDry run:")

        for series, chapter, _ in jobs:
            path = chapter_path(
            output_dir,
            series,
            chapter,
            args.library_layout
        )

            print(path)

        return

    print(
        f"\nDownloading with "
        f"{args.workers} workers...\n"
    )

    done = 0
    skipped = 0
    failed = 0

    with ThreadPoolExecutor(
        max_workers=args.workers
    ) as executor:

        futures = [
            executor.submit(
                download_file,
                series,
                chapter,
                output,
                args.timeout,
                args.library_layout,
            )
            for series, chapter, output in jobs
        ]

        for future in as_completed(futures):
            status, series, chapter, result = future.result()

            title = series.get("title", "Unknown")
            chapter_title = chapter.get(
                "title",
                "Unknown"
            )

            if status == "done":
                done += 1
                print(
                    f"[DONE] {title} / {chapter_title}"
                )

            elif status == "skip":
                skipped += 1
                print(
                    f"[SKIP] {title} / {chapter_title}"
                )

            else:
                failed += 1
                print(
                    f"[FAIL] {title} / {chapter_title}: "
                    f"{result}"
                )

    print("\nFinished.")
    print(f"Downloaded: {done}")
    print(f"Already existed: {skipped}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()