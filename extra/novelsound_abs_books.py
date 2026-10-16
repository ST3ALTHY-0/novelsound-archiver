#!/usr/bin/env python3

import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests


INPUT_FILE = Path("novelsound_full_with_metadata.json")
OUTPUT_FILE = Path("novelsound_full_with_book_metadata.json")
CACHE_FILE = Path("abs_book_metadata_cache.json")

ABS_URL = os.environ.get(
    "ABS_URL",
    "http://localhost:13378"
).rstrip("/")

ABS_TOKEN = os.environ.get("ABS_TOKEN")

REQUEST_DELAY = 0.15
MIN_SERIES_SCORE = 0.70


def normalize(text):
    if not text:
        return ""

    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


def load_json(path, default):
    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    temp = Path(str(path) + ".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp.replace(path)


def search_abs(title):
    try:
        r = requests.get(
            f"{ABS_URL}/api/search/books",
            params={
                "provider": "audible",
                "title": title,
            },
            headers={
                "Authorization": f"Bearer {ABS_TOKEN}"
            },
            timeout=30,
        )

        r.raise_for_status()
        data = r.json()

        if isinstance(data, list):
            return data

    except Exception as e:
        print(f"  ABS error: {e}")

    return None


def parse_sequence(value):
    """
    Audible sequences are usually:
        "1"
        "2"
        "3.5"
    """

    if value is None:
        return None

    match = re.search(r"\d+(?:\.\d+)?", str(value))

    if not match:
        return None

    return float(match.group())


def chapter_number(chapter):
    """
    Extract:
        Book 1       -> 1
        Book 02      -> 2
        Volume 4     -> 4
        Vol. 7       -> 7

    Intentionally ignores titles such as:
        Chapter 1-67
    """

    title = chapter.get("title", "")

    match = re.search(
        r"\b(?:book|volume|vol\.?)\s*0*(\d+)\b",
        title,
        re.IGNORECASE
    )

    if not match:
        return None

    return float(match.group(1))


def audible_series_info(result):
    entries = result.get("series") or []

    if not entries:
        return None, None

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        name = entry.get("series")
        sequence = parse_sequence(
            entry.get("sequence")
        )

        if name:
            return name, sequence

    return None, None


def filter_series_results(series_title, results):
    """
    Keep Audible results that appear to belong to
    the same series.
    """

    candidates = []

    for result in results:
        audible_series, sequence = audible_series_info(
            result
        )

        if not audible_series:
            continue

        score = similarity(
            series_title,
            audible_series
        )

        if score < MIN_SERIES_SCORE:
            continue

        candidates.append({
            "result": result,
            "series": audible_series,
            "sequence": sequence,
            "score": score,
        })

    return candidates


def build_sequence_map(candidates):
    """
    sequence number -> best Audible result
    """

    sequence_map = {}

    for candidate in candidates:
        sequence = candidate["sequence"]

        if sequence is None:
            continue

        existing = sequence_map.get(sequence)

        if (
            existing is None
            or candidate["score"] > existing["score"]
        ):
            sequence_map[sequence] = candidate

    return sequence_map


def book_metadata(candidate):
    result = candidate["result"]

    return {
        "audible_title": result.get("title"),
        "audible_asin": result.get("asin"),
        "audible_author": result.get("author"),
        "audible_narrator": result.get("narrator"),
        "audible_publisher": result.get("publisher"),
        "audible_cover": result.get("cover"),
        "audible_sequence": candidate.get("sequence"),
        "audible_series": candidate.get("series"),
        "audible_published_year":
            result.get("publishedYear"),
        "audible_duration":
            result.get("duration"),
    }


def main():
    if not ABS_TOKEN:
        raise SystemExit(
            "ABS_TOKEN is not set.\n\n"
            "export ABS_TOKEN='YOUR_API_TOKEN'"
        )

    catalog = load_json(
        INPUT_FILE,
        []
    )

    if isinstance(catalog, dict):
        catalog = catalog.get("items", [])

    cache = load_json(
        CACHE_FILE,
        {}
    )

    print(
        f"Loaded {len(catalog)} NovelSound series."
    )

    matched_books = 0
    unmatched_books = 0

    for i, series in enumerate(
        catalog,
        start=1
    ):
        sid = str(series.get("id"))
        title = series.get("title", "").strip()
        chapters = series.get("chapters", [])

        print(
            f"[{i}/{len(catalog)}] "
            f"{title} "
            f"({len(chapters)} entries)"
        )

        # Search once per series
        if sid in cache:
            results = cache[sid]

            print(
                f"  cached Audible search "
                f"({len(results)} results)"
            )

        else:
            results = search_abs(title)

            if results is None:
                print(
                    "  request failed; skipping"
                )
                continue

            cache[sid] = results

            save_json(
                CACHE_FILE,
                cache
            )

            time.sleep(REQUEST_DELAY)

        candidates = filter_series_results(
            title,
            results
        )

        sequence_map = build_sequence_map(
            candidates
        )

        local_matches = 0

        for chapter in chapters:
            number = chapter_number(chapter)

            if number is None:
                unmatched_books += 1
                continue

            match = sequence_map.get(number)

            if not match:
                unmatched_books += 1
                continue

            chapter.update(
                book_metadata(match)
            )

            local_matches += 1
            matched_books += 1

            print(
                f"    {chapter.get('title')}"
                f" -> "
                f"{chapter.get('audible_title')}"
            )

        print(
            f"  matched "
            f"{local_matches}/{len(chapters)}"
        )

        save_json(
            OUTPUT_FILE,
            catalog
        )

    save_json(
        OUTPUT_FILE,
        catalog
    )

    save_json(
        CACHE_FILE,
        cache
    )

    print()
    print("Finished.")
    print(
        f"Matched books:   {matched_books}"
    )
    print(
        f"Unmatched books: {unmatched_books}"
    )
    print(
        f"Saved: {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
