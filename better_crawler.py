#!/usr/bin/env python3

import os
import csv
import json
import requests

API = "https://audiobookbackend-production-d675.up.railway.app/books"

token = os.environ["NOVELSOUND_TOKEN"]

headers = {
    "Authorization": token,
}

all_series = []
page = 1
limit = 100

while True:
    print(f"Fetching page {page}...")

    r = requests.get(
        API,
        params={
            "limit": limit,
            "page": page,
        },
        headers=headers,
        timeout=30,
    )

    r.raise_for_status()
    data = r.json()

    items = data.get("items", [])

    if not items:
        break

    all_series.extend(items)

    print(f"  received {len(items)} series")

    if len(items) < limit:
        break

    page += 1


catalog = []

for series in all_series:
    chapters = series.get("chapters", [])
    tags = series.get("tags", [])

    catalog.append({
        "id": series.get("id"),
        "title": series.get("title"),
        "status": series.get("status"),
        "book_count": len(chapters),
        "main_tags": ", ".join(
            t.get("main_tag", "") for t in tags
        ),
        "sub_tags": ", ".join(
            t.get("sub_tag", "") for t in tags
        ),
        "views": series.get("total_views"),
        "cover": series.get("cover_image"),
        "synopsis": series.get("synopsis"),
        "url": f"https://novelsound.net/synopsis/{series.get('id')}",
    })


# Full raw API data — useful for downloader later
with open("novelsound_full.json", "w", encoding="utf-8") as f:
    json.dump(all_series, f, indent=2, ensure_ascii=False)


# Simplified catalog
with open("novelsound_catalog.json", "w", encoding="utf-8") as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)


with open("novelsound_catalog.csv", "w", newline="", encoding="utf-8") as f:
    fields = [
        "id",
        "title",
        "status",
        "book_count",
        "main_tags",
        "sub_tags",
        "views",
        "cover",
        "synopsis",
        "url",
    ]

    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(catalog)


print()
print(f"Series: {len(catalog)}")
print(
    "Audio entries:",
    sum(x["book_count"] for x in catalog),
)
print("Saved:")
print("  novelsound_catalog.csv")
print("  novelsound_catalog.json")
print("  novelsound_full.json")