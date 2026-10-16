#!/usr/bin/env python3

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests


INPUT_FILE = Path("novelsound_full.json")
OUTPUT_FILE = Path("novelsound_full_with_authors.json")
CACHE_FILE = Path("authors_cache.json")

GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"

REQUEST_DELAY = 0.15


def normalize(text):
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def google_books_lookup(title):
    try:
        r = requests.get(
            GOOGLE_URL,
            params={
                "q": f'intitle:"{title}"',
                "maxResults": 10
            },
            timeout=20
        )

        r.raise_for_status()
        data = r.json()

    except Exception as e:
        print(f"  Google Books error: {e}")
        return None

    candidates = []

    for item in data.get("items", []):
        info = item.get("volumeInfo", {})

        result_title = info.get("title")
        authors = info.get("authors")

        if not result_title or not authors:
            continue

        score = similarity(
            title,
            result_title
        )

        candidates.append({
            "title": result_title,
            "authors": authors,
            "score": score,
            "source": "google_books"
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates[0]


def openlibrary_lookup(title):
    try:
        r = requests.get(
            OPENLIBRARY_URL,
            params={
                "title": title,
                "limit": 10,
                "fields": "title,author_name,key"
            },
            timeout=20,
            headers={
                "User-Agent":
                    "NovelSoundCatalog/1.0"
            }
        )

        r.raise_for_status()
        data = r.json()

    except Exception as e:
        print(f"  Open Library error: {e}")
        return None

    candidates = []

    for item in data.get("docs", []):
        result_title = item.get("title")
        authors = item.get("author_name")

        if not result_title or not authors:
            continue

        score = similarity(
            title,
            result_title
        )

        candidates.append({
            "title": result_title,
            "authors": authors,
            "score": score,
            "source": "open_library"
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates[0]


def find_author(title):
    google = google_books_lookup(title)

    # Strong Google match
    if google and google["score"] >= 0.85:
        return google

    time.sleep(REQUEST_DELAY)

    openlib = openlibrary_lookup(title)

    # Pick whichever has the stronger title match
    choices = [
        x for x in [google, openlib]
        if x is not None
    ]

    if not choices:
        return None

    choices.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = choices[0]

    # Avoid obviously bad matches
    if best["score"] < 0.65:
        return None

    return best


def main():
    catalog = load_json(
        INPUT_FILE,
        []
    )

    if isinstance(catalog, dict):
        catalog = catalog.get(
            "items",
            []
        )

    cache = load_json(
        CACHE_FILE,
        {}
    )

    print(
        f"Loaded {len(catalog)} NovelSound series."
    )

    for index, series in enumerate(
        catalog,
        start=1
    ):
        sid = str(series.get("id"))
        title = series.get(
            "title",
            ""
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
            continue

        # Cached result
        if sid in cache:
            result = cache[sid]

            if result:
                series["author"] = result[
                    "author"
                ]
                series["author_source"] = result[
                    "source"
                ]
                series[
                    "author_match_score"
                ] = result[
                    "score"
                ]

                print(
                    f"  cached: "
                    f"{result['author']}"
                )

            else:
                print(
                    "  cached: no match"
                )

            continue

        result = find_author(title)

        if result:
            authors = result["authors"]

            # Usually one author, but preserve multiple
            author_string = " & ".join(
                authors
            )

            series["author"] = author_string
            series[
                "author_source"
            ] = result["source"]

            series[
                "author_match_score"
            ] = round(
                result["score"],
                3
            )

            cache[sid] = {
                "author": author_string,
                "source": result["source"],
                "score": round(
                    result["score"],
                    3
                ),
                "matched_title":
                    result["title"]
            }

            print(
                f"  -> {author_string}"
                f" [{result['source']}, "
                f"{result['score']:.2f}]"
            )

        else:
            cache[sid] = None

            print(
                "  -> no reliable match"
            )

        # Save continuously
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
        OUTPUT_FILE,
        catalog
    )

    save_json(
        CACHE_FILE,
        cache
    )

    found = sum(
        1
        for series in catalog
        if series.get("author")
    )

    print()
    print(
        f"Authors found: {found}/"
        f"{len(catalog)}"
    )

    print(
        f"Saved: {OUTPUT_FILE}"
    )

    print(
        f"Cache: {CACHE_FILE}"
    )


if __name__ == "__main__":
    main()
