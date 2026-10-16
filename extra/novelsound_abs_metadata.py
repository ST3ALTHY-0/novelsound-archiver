#!/usr/bin/env python3

import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests


INPUT_FILE = Path("novelsound_full.json")
OUTPUT_FILE = Path("novelsound_full_with_metadata.json")
CACHE_FILE = Path("abs_metadata_cache.json")

ABS_URL = os.environ.get("ABS_URL", "http://localhost:13378").rstrip("/")
ABS_TOKEN = os.environ.get("ABS_TOKEN")

REQUEST_DELAY = 0.15
MIN_SCORE = 0.70


def normalize(text):
    if not text:
        return ""

    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


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


def get_series_names(result):
    names = []

    for entry in result.get("series") or []:
        if isinstance(entry, dict):
            name = entry.get("series")
            if name:
                names.append(name)

    return names


def score_result(novelsound_title, result):
    """
    Score an Audible result.

    A matching Audible series name is much more useful than
    comparing NovelSound's series title against "Series Name 14".
    """

    title_score = similarity(
        novelsound_title,
        result.get("title", "")
    )

    series_names = get_series_names(result)

    series_score = 0.0

    for series_name in series_names:
        series_score = max(
            series_score,
            similarity(
                novelsound_title,
                series_name
            )
        )

    # Strongly prefer actual Audible series-name matches.
    if series_score >= 0.95:
        final_score = max(
            series_score,
            title_score
        )

    elif series_score > 0:
        final_score = (
            series_score * 0.8
            + title_score * 0.2
        )

    else:
        final_score = title_score

    return {
        "score": final_score,
        "title_score": title_score,
        "series_score": series_score
    }


def search_abs(title):
    url = f"{ABS_URL}/api/search/books"

    headers = {
        "Authorization": f"Bearer {ABS_TOKEN}"
    }

    try:
        r = requests.get(
            url,
            params={
                "provider": "audible",
                "title": title
            },
            headers=headers,
            timeout=30
        )

        r.raise_for_status()

        data = r.json()

        if not isinstance(data, list):
            print(
                "  Unexpected ABS response type:",
                type(data).__name__
            )
            return None, False

        return data, True

    except requests.RequestException as e:
        print(f"  ABS request error: {e}")
        return None, False

    except ValueError as e:
        print(f"  Invalid JSON from ABS: {e}")
        return None, False


def choose_best_match(title, results):
    candidates = []

    for result in results:
        scores = score_result(
            title,
            result
        )

        candidates.append({
            "result": result,
            **scores
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = candidates[0]

    if best["score"] < MIN_SCORE:
        return None

    return best


def metadata_from_match(match):
    result = match["result"]

    audible_series = None
    sequence = None

    series_entries = result.get("series") or []

    if series_entries:
        first = series_entries[0]

        if isinstance(first, dict):
            audible_series = first.get("series")
            sequence = first.get("sequence")

    return {
        "author": result.get("author"),
        "narrator": result.get("narrator"),
        "publisher": result.get("publisher"),
        "audible_asin": result.get("asin"),
        "audible_series": audible_series,
        "audible_sequence": sequence,
        "audible_title": result.get("title"),
        "audible_cover": result.get("cover"),
        "published_year": result.get("publishedYear"),
        "language": result.get("language"),
        "duration": result.get("duration"),
        "genres": result.get("genres"),
        "audible_tags": result.get("tags"),
        "abridged": result.get("abridged"),
        "metadata_source": "audiobookshelf_audible",
        "metadata_match_score": round(
            match["score"],
            3
        ),
        "metadata_series_score": round(
            match["series_score"],
            3
        ),
        "metadata_title_score": round(
            match["title_score"],
            3
        )
    }


def main():
    if not ABS_TOKEN:
        raise SystemExit(
            "ABS_TOKEN is not set.\n\n"
            "Run:\n"
            "  export ABS_TOKEN='YOUR_API_TOKEN'\n\n"
            "Optionally:\n"
            "  export ABS_URL='http://localhost:13378'"
        )

    catalog = load_json(
        INPUT_FILE,
        []
    )

    if isinstance(catalog, dict):
        catalog = catalog.get(
            "items",
            []
        )

    if not isinstance(catalog, list):
        raise SystemExit(
            f"{INPUT_FILE} is not a valid catalog list."
        )

    cache = load_json(
        CACHE_FILE,
        {}
    )

    print(
        f"Loaded {len(catalog)} NovelSound series."
    )

    found = 0
    not_found = 0
    errors = 0

    for index, series in enumerate(
        catalog,
        start=1
    ):
        sid = str(
            series.get("id")
        )

        title = (
            series.get("title")
            or ""
        ).strip()

        if not title:
            continue

        print(
            f"[{index}/{len(catalog)}] "
            f"{title}"
        )

        # Already enriched
        if series.get("author"):
            print(
                f"  existing: "
                f"{series['author']}"
            )

            found += 1
            continue

        # Cached lookup
        if sid in cache:
            cached = cache[sid]

            if cached.get("status") == "found":
                metadata = cached[
                    "metadata"
                ]

                series.update(
                    metadata
                )

                print(
                    f"  cached: "
                    f"{metadata.get('author')}"
                )

                found += 1
                continue

            if cached.get("status") == "not_found":
                print(
                    "  cached: no reliable match"
                )

                not_found += 1
                continue

        results, request_ok = search_abs(
            title
        )

        # IMPORTANT:
        # Do not cache API/network failures as "not found".
        if not request_ok:
            errors += 1
            print(
                "  -> request failed; "
                "will retry next run"
            )

            time.sleep(
                REQUEST_DELAY
            )

            continue

        match = choose_best_match(
            title,
            results
        )

        if not match:
            print(
                "  -> no reliable Audible match"
            )

            cache[sid] = {
                "status": "not_found",
                "title": title
            }

            not_found += 1

        else:
            metadata = metadata_from_match(
                match
            )

            author = metadata.get(
                "author"
            )

            series.update(
                metadata
            )

            cache[sid] = {
                "status": "found",
                "title": title,
                "metadata": metadata
            }

            found += 1

            print(
                f"  -> {author or 'Unknown'}"
            )

            print(
                "     Audible: "
                f"{metadata.get('audible_title')}"
            )

            if metadata.get(
                "audible_series"
            ):
                print(
                    "     Series: "
                    f"{metadata['audible_series']}"
                )

            print(
                "     Score: "
                f"{metadata['metadata_match_score']:.3f}"
            )

        # Save after every series so Ctrl+C doesn't lose work.
        save_json(
            CACHE_FILE,
            cache
        )

        save_json(
            OUTPUT_FILE,
            catalog
        )

        time.sleep(
            REQUEST_DELAY
        )

    save_json(
        CACHE_FILE,
        cache
    )

    save_json(
        OUTPUT_FILE,
        catalog
    )

    print()
    print("Finished.")
    print(f"Matched:   {found}")
    print(f"No match:  {not_found}")
    print(f"Errors:    {errors}")
    print(f"Total:     {len(catalog)}")
    print()
    print(f"Saved: {OUTPUT_FILE}")
    print(f"Cache: {CACHE_FILE}")


if __name__ == "__main__":
    main()
