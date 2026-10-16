# NovelSound Auto Downloader

Fetch the NovelSound catalog, download audio, track completed downloads across drives, and optionally enrich titles with author and Audible metadata.

## Quick start

Run commands from the project directory:

```bash
cd /home/luke/NovelSoundAutoDownloader
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install requests
```

In later terminals, run `source .venv/bin/activate`. Python's standard library provides SQLite; `requests` installs `urllib3`. The `-u` flag makes Python print output immediately.

### 1. Fetch the catalog

```bash
export NOVELSOUND_TOKEN='YOUR_AUTHORIZATION_HEADER_VALUE'
python3 -u fetch_novelsound_catalog.py
```

The token is sent **unchanged** as the `Authorization` header (no prefix is added). The script fetches up to 100 series per page until finished and overwrites:

| File | Purpose |
| --- | --- |
| `novelsound_full.json` | Complete records, chapters, and audio URLs; **use for downloads** |
| `novelsound_catalog.json` | Simplified series list |
| `novelsound_catalog.csv` | Simplified spreadsheet export |

The simplified exports do not contain audio URLs. Download scripts read URLs from the full catalog and do not need `NOVELSOUND_TOKEN`.

### 2. Download the catalog (recommended)

```bash
python3 -u download_all_with_manifest.py \
  --input novelsound_full.json \
  --dest /mnt/audiobooks8-2/AudiobookshelfLib/Books/NovelSound/ \
  --manifest novelsound_downloads.db \
  --log-file /mnt/audiobooks8/AudiobookshelfLib/Downloads/NovelSound/download_failures.log \
  --workers 3
```

The SQLite manifest remembers completed audio entries across runs and drives. Existing nonempty files at the expected destination are also recorded. Rerunning retries failures without redownloading recorded entries.

Files are stored as:

```text
NovelSound/
â””â”€â”€ 123 - Example Series/
    â”œâ”€â”€ 001 - Book 1.mp3
    â””â”€â”€ 002 - Book 2.mp3
```

Names are sanitized; the extension comes from the URL (default `.mp3`). A NovelSound chapter may represent a full book or another audio grouping.

## Using multiple drives

**Already used this manifest?** Keep the same `novelsound_downloads.db` and change `--dest` to the new drive. No additional setup is needed.

**Downloaded files before creating the manifest?** Register existing files on the old drive first:

```bash
python3 -u download_all_with_manifest.py \
  --input novelsound_full.json \
  --dest /mnt/audiobooks8/AudiobookshelfLib/Downloads/NovelSound/ \
  --manifest novelsound_downloads.db \
  --bootstrap
```

`--bootstrap` scans the bulk downloader's expected `ID - Series Title` folders, registers nonempty files, and exits **without downloading**. Repeat for other existing roots, using the same database, then download remaining entries to the new destination.

**Important limitations:**

- Completed manifest entries are trusted even if their original files have been moved or deleted.
- The selected-series downloader uses a different naming layout; its files generally cannot be bootstrapped this way.
- Entries are identified using series ID, chapter ID (when available), chapter index, and URL path without query/fragment. Changes to chapter order or URL path can result in new entries.
- The manifest downloader creates series folders before checking completion, so a second drive may contain empty folders; a partially downloaded series may be split between drives.

## Download individual series

The filename really is **`download_novelsound_series.py.py`** (double extension).

Preview a title match without downloading:

```bash
python3 download_novelsound_series.py.py \
  --series 'Example Series' \
  --output /mnt/audiobooks8-2/AudiobookshelfLib/Books/NovelSound/ \
  --dry-run
```

Remove `--dry-run` to download. Matching is case-insensitive: exact titles first, then unique partial matches. Use a numeric ID if a title is ambiguous.

Download several IDs:

```bash
python3 -u download_novelsound_series.py.py \
  --series 123 --series 456 \
  --output ./Audiobooks \
  --workers 4
```

Or supply a text file (`series.txt`) with one title or ID per line; blank lines and `#` comments are ignored:

```bash
python3 -u download_novelsound_series.py.py \
  --series-file series.txt \
  --output ./Audiobooks
```

Choose **one** selection mode: repeated `--series`, `--series-file`, or `--all`.

By default, output is `Series/Book.ext`. `--library-layout` instead uses `Author/Series/Book/audio.ext` (`Unknown Author` if absent). Book names prefer `audible_title`, then a descriptive URL filename, then chapter title; series names prefer `audible_series` when available.

This downloader skips nonempty destination files and attempts to resume `.part` files with HTTP Range; it restarts when the server ignores Range. **It neither reads nor updates the SQLite manifest**, so it cannot detect completed downloads on another drive.

## Other download options

### Simple bulk downloader (no manifest)

```bash
python3 -u download_all_simple.py \
  --input novelsound_full.json \
  --dest ./Audiobooks \
  --workers 3
```

Uses the same `ID - Series Title/001 - Chapter Title.ext` layout as the manifest downloader. Skips existing nonempty destination files, retries failed requests, and restarts partial files. It has no cross-drive history or persistent failure log; errors print to the terminal.

### Command reference

| Option | Manifest bulk | Simple bulk | Selected series |
| --- | --- | --- | --- |
| Catalog input | `--input`, `-i` (default `novelsound_full.json`) | Same | `--catalog` (default `novelsound_full.json`; also accepts an object with `items`) |
| Destination | `--dest`, `-d` (required) | Same | `--output` (default `Audiobooks`) |
| Workers | `--workers`, `-w` (default 6) | Same | `--workers` (default 4 concurrent audio downloads) |
| Series concurrency | `--series-workers` (default 3) | Same | â€” |
| Status filter | `--skip-if-status` (default `all`) | Same | â€” |
| Manifest database | `--manifest` (default `novelsound_downloads.db`) | â€” | â€” |
| Failure log | `--log-file` (default `download_failures.log`) | â€” | â€” |
| Existing-file import | `--bootstrap` | â€” | â€” |
| Verbose requests | `--debug` | â€” | â€” |
| Request timeout | â€” | â€” | `--timeout` (default 60 seconds) |
| Preview | â€” | â€” | `--dry-run` |
| Author layout | â€” | â€” | `--library-layout` |

For bulk downloaders, `--series-workers` limits concurrent series to at least 1 and at most `--workers`; entries within each series download sequentially. Increasing only `--workers` above 3 leaves the default series limit at 3. Status filters: `all` processes all; `complete` processes **only complete series**; `ongoing` processes **only ongoing series** (despite the option's name).

Manifest failures are appended as JSON Lines; old failure records remain after a successful retry. Bulk `.part` downloads restart rather than resume. Use `--debug` to see requests, HTTP status, and periodic progress.

## Optional metadata enrichment

These helpers run from the project root, have fixed input/output/cache filenames, and do **not** accept command-line options. They enrich JSON catalogs, not audio tags or Audiobookshelf library records. Review title-similarity matches before relying on them.

### Author lookup (Google Books / Open Library)

```bash
python3 -u Extra/get_author.py
```

Reads `novelsound_full.json`, writes `novelsound_full_with_authors.json`, and caches lookups in `authors_cache.json`. Existing authors are preserved; new matches include source and score. Cached misses are reused.

To use the enriched authors for downloads:

```bash
python3 -u download_novelsound_series.py.py \
  --catalog novelsound_full_with_authors.json \
  --series 123 \
  --output ./Audiobooks \
  --library-layout
```

### Audible metadata through Audiobookshelf

```bash
export ABS_URL='http://localhost:13378'
export ABS_TOKEN='YOUR_AUDIOBOOKSHELF_API_TOKEN'
python3 -u Extra/novelsound_abs_metadata.py
python3 -u Extra/novelsound_abs_books.py
```

`ABS_URL` defaults to `http://localhost:13378`; `ABS_TOKEN` is required. Both helpers query `/api/search/books` with the `audible` provider and a Bearer token.

| Script | Input | Output | Cache |
| --- | --- | --- | --- |
| `Extra/novelsound_abs_metadata.py` | `novelsound_full.json` | `novelsound_full_with_metadata.json` | `abs_metadata_cache.json` |
| `Extra/novelsound_abs_books.py` | `novelsound_full_with_metadata.json` | `novelsound_full_with_book_metadata.json` | `abs_book_metadata_cache.json` |

The first adds series-level author, narrator, publisher, Audible series information, and match scores; it skips series with existing authors. The second matches book/volume numbers to Audible sequence numbers and adds chapter-level `audible_*` fields. Chapter ranges such as `Chapter 1-67` are not treated as book numbers.

**These are separate enrichment paths:** the Audiobookshelf series helper reads the original full catalog, not the author-only output.

Preview enriched book naming:

```bash
python3 -u download_novelsound_series.py.py \
  --catalog novelsound_full_with_book_metadata.json \
  --series 123 \
  --output ./Audiobooks \
  --library-layout \
  --dry-run
```

Remove `--dry-run` after reviewing paths. Changing catalogs or layouts may change filenames and cause the selected-series downloader to download entries again.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `KeyError: 'NOVELSOUND_TOKEN'` | Export the authorization value before fetching. |
| `ModuleNotFoundError: requests` | Activate `.venv` and install `requests`. |
| No audio download jobs | Use `novelsound_full.json`, not the simplified exports. |
| New drive skips completed entries | Expected when using the same manifest. |
| Files on another drive download again | Use the original manifest; bootstrap older files that were not recorded. |
| Need more parallel series | Increase both `--workers` and `--series-workers`. |
| Need failure details | Add `--debug` or run `tail -f PATH_TO_DOWNLOAD_FAILURES_LOG`. |

For downloader flags, run `python3 SCRIPT_NAME --help`. The catalog fetcher and enrichment helpers do not implement `--help`.

> **Related:** `fullAudioBooks/` is a separate FullAudioBooks workflow. Older script names may appear in notes (`better_crawler.py`, `novelsound_download.py`, `download_all_manifest.py`); the names above are the current ones.
