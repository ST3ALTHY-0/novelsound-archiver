# NovelSound Auto Downloader

Fetch a NovelSound (NS) catalog, download audio, and optionally enrich the catalog with author and Audible metadata.

## Which script should I use?

| Script | Purpose |
| --- | --- |
| `fetch_novelsound_catalog.py` | Fetch the catalog from the NovelSound API and export full JSON plus simplified JSON/CSV. |
| `download_all_with_manifest.py` | Download the whole catalog with a persistent SQLite record of completed downloads. Recommended for continuing downloads across multiple drives. |
| `download_all_simple.py` | Download the whole catalog, skipping files already present at the destination. No database. |
| `download_novelsound_series.py.py` | Download selected series by title or ID, a list of series, or the entire catalog. Supports partial download resumption and an optional Author/Series/Book layout. |
| `Extra/get_author.py` | Look up authors using Google Books and Open Library. |
| `Extra/novelsound_abs_metadata.py` | Add series-level metadata using Audiobookshelf's Audible search. |
| `Extra/novelsound_abs_books.py` | Match individual book entries to Audible metadata through Audiobookshelf. |

The double `.py.py` extension above is the current filename; use it as written. Earlier names mentioned in commands or editor tabs include `better_crawler.py`, `novelsound_download.py`, and `download_all_manifest.py`. The examples here use the files currently on disk.

The `fullAudioBooks/` directory contains a separate workflow for FullAudioBooks, rather than NovelSound.

## Setup

Run commands from the project directory so relative catalog, cache, and database paths resolve consistently:

```bash
cd /home/luke/NovelSoundAutoDownloader
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install requests
```

The scripts use Python 3, `requests`, and Python's standard library. The bulk downloaders also import `urllib3`, which is installed with `requests`. SQLite support comes from Python's `sqlite3` module.

Activate the virtual environment again in each new terminal. Use `python3 -u` for immediately flushed terminal output.

## 1. Fetch or refresh the catalog

Supply your NovelSound authorization header value in an environment variable:

```bash
export NOVELSOUND_TOKEN='YOUR_AUTHORIZATION_HEADER_VALUE'
python3 -u fetch_novelsound_catalog.py
```

The script sends this value directly as the `Authorization` header; it does not add a prefix. It fetches pages of 100 series until the catalog ends. There are no command-line options, and running it fetches data immediately.

It overwrites these files in the current directory:

| File | Contents |
| --- | --- |
| `novelsound_full.json` | Full API records, including chapters and audio URLs. **Use this for downloading.** |
| `novelsound_catalog.json` | Simplified series summaries for browsing. |
| `novelsound_catalog.csv` | Simplified catalog for a spreadsheet. |

The simplified exports omit chapter records and audio URLs, so they cannot supply the download jobs. The downloaders read URLs from the full catalog and do not read `NOVELSOUND_TOKEN` themselves.

## 2. Download with the manifest

```bash
python3 -u download_all_with_manifest.py \
  --input novelsound_full.json \
  --dest /mnt/audiobooks8-2/AudiobookshelfLib/Books/NovelSound/ \
  --manifest novelsound_downloads.db \
  --log-file /mnt/audiobooks8/AudiobookshelfLib/Downloads/NovelSound/download_failures.log \
  --workers 3
```

The downloader records completed chapters in `novelsound_downloads.db`. On a later run, it checks this database before checking the destination and skips recorded chapters even when the destination changes. Nonempty files already at the expected destination are also recorded as complete.

Files use this layout, with titles sanitized for filenames:

```text
NovelSound/
  123 - Example Series/
    001 - Book 1.mp3
    002 - Book 2.mp3
```

The extension is inferred from the audio URL, with `.mp3` as the fallback. NovelSound's `chapters` entries may represent whole books or other audio groupings.

### Existing downloads and a second HDD

If the old downloads are already recorded in the same manifest, run the command above with the new destination. No bootstrap is needed.

If the original downloads were made without this manifest, register them first. Point `--dest` at the **old library root** containing the `ID - Series Title` folders:

```bash
python3 -u download_all_with_manifest.py \
  --input novelsound_full.json \
  --dest /mnt/audiobooks8/AudiobookshelfLib/Downloads/NovelSound/ \
  --manifest novelsound_downloads.db \
  --bootstrap
```

Replace the old drive path if your files are elsewhere. Bootstrap scans the expected folder and filename layout and records nonempty files; it does not download audio. It skips entries already marked complete. You can repeat it for additional library roots using the same database, then download the remainder to the new drive.

Important behavior:

- Keep using the same manifest across drives. A new database will not know about files on another drive.
- Completed manifest entries are trusted without checking whether their original files still exist. Moving files preserves the skip behavior; deleting files does not automatically make them download again.
- Bootstrap requires the bulk downloader's exact naming layout. The selected-series downloader uses a different layout and cannot generally be bootstrapped this way.
- The manifest identifies chapters using series ID, chapter ID when available, chapter index, and the audio URL with query string and fragment removed. Changes to chapter ordering or URL paths can cause an entry to be treated as new.
- The script currently creates every processed series folder before checking the manifest. A book already completed on the first drive can therefore leave an **empty folder on the second drive**. A partially completed series may be split between drives.

### Manifest downloader options

| Option | Default / behavior |
| --- | --- |
| `--input`, `-i` | `novelsound_full.json` |
| `--dest`, `-d` | Required destination root. |
| `--manifest` | `novelsound_downloads.db` |
| `--log-file` | `download_failures.log`; appends failed chapter records as JSON Lines. |
| `--workers`, `-w` | `6`; caps concurrent series processing. |
| `--series-workers` | `3`; actual concurrency is at least 1 and otherwise the smaller of this and `--workers`. Audio entries within each series download sequentially. |
| `--skip-if-status` | `all`; despite its name, `complete` **includes only complete series**, and `ongoing` includes only ongoing series. |
| `--bootstrap` | Register existing nonempty files and exit without downloading. |
| `--debug` | Show requests, HTTP status, and periodic download progress. |

Rerun the same command to retry failed entries; failures are not marked complete. Downloads use `.part` files, but interrupted partial files are restarted rather than resumed. The log is append-only, so an earlier failure remains in it after a successful retry.

## 3. Download selected series

Preview a selection by title before downloading:

```bash
python3 download_novelsound_series.py.py \
  --series 'Example Series' \
  --output /mnt/audiobooks8-2/AudiobookshelfLib/Books/NovelSound/ \
  --dry-run
```

Remove `--dry-run` to download. Titles are matched case-insensitively, using an exact match first and then a unique partial match. Use a numeric ID when a title is ambiguous:

```bash
python3 -u download_novelsound_series.py.py \
  --series 123 \
  --series 456 \
  --output ./Audiobooks \
  --workers 4
```

The IDs above are examples; use IDs from your catalog. To select from a text file, put one title or numeric ID on each line. Blank lines and lines starting with `#` are ignored:

```text
# series.txt
123
Example Series
```

```bash
python3 -u download_novelsound_series.py.py \
  --series-file series.txt \
  --output ./Audiobooks
```

Choose one selection mode: repeated `--series`, `--series-file`, or `--all`.

| Option | Default / behavior |
| --- | --- |
| `--catalog` | `novelsound_full.json`; also accepts an object containing an `items` list. |
| `--output` | `Audiobooks` |
| `--workers` | `4` concurrent audio downloads. |
| `--timeout` | `60` seconds for HTTP requests. |
| `--dry-run` | Print planned paths without downloading. |
| `--library-layout` | Use `Author/Series/Book/audio.ext`. Missing authors become `Unknown Author`. |

Normally files go under `Series/Book.ext`. Book names prefer chapter-level `audible_title`, then a descriptive audio URL filename, then the chapter title. Series names prefer `audible_series` when present.

This script skips nonempty destination files and attempts to resume existing `.part` files using HTTP Range requests. It restarts the file if the server ignores the Range request. **It does not consult or update the manifest**, so it does not know what you downloaded to a different drive.

## 4. Simple bulk downloader

```bash
python3 -u download_all_simple.py \
  --input novelsound_full.json \
  --dest ./Audiobooks \
  --workers 3
```

This uses the same `ID - Series Title/001 - Chapter Title.ext` layout as the manifest downloader. It skips nonempty files at that destination, retries failed requests, and restarts partial files. It has no cross-drive tracking or persistent failure log.

Options are `--input` / `-i` (default `novelsound_full.json`), required `--dest` / `-d`, `--workers` / `-w` (default `6`), `--series-workers` (default `3`), and `--skip-if-status` (`all`, `complete`, or `ongoing`). Concurrency and status filtering behave as described for the manifest downloader. Errors are printed to the terminal.

## Optional: enrich the catalog

Run these helpers from the project root. Their input, output, and cache filenames are constants in the scripts; they do not have command-line options. They write enriched JSON catalogs, not audio tags or Audiobookshelf library records. Matches are based on title similarity and should be reviewed.

### Authors from Google Books and Open Library

```bash
python3 -u Extra/get_author.py
```

Reads `novelsound_full.json`, writes `novelsound_full_with_authors.json`, and caches lookups in `authors_cache.json`. It preserves existing authors and stores match source and score for new matches. Cached misses are reused on later runs.

Use its output with the selected-series downloader:

```bash
python3 -u download_novelsound_series.py.py \
  --catalog novelsound_full_with_authors.json \
  --series 123 \
  --output ./Audiobooks \
  --library-layout
```

### Series and book metadata through Audiobookshelf

Set the URL and API token for your Audiobookshelf server:

```bash
export ABS_URL='http://localhost:13378'
export ABS_TOKEN='YOUR_AUDIOBOOKSHELF_API_TOKEN'
python3 -u Extra/novelsound_abs_metadata.py
python3 -u Extra/novelsound_abs_books.py
```

`ABS_URL` defaults to `http://localhost:13378`; `ABS_TOKEN` is required. These scripts call the server's `/api/search/books` endpoint with the `audible` provider and a Bearer token.

| Helper | Input | Output | Cache |
| --- | --- | --- | --- |
| `Extra/novelsound_abs_metadata.py` | `novelsound_full.json` | `novelsound_full_with_metadata.json` | `abs_metadata_cache.json` |
| `Extra/novelsound_abs_books.py` | `novelsound_full_with_metadata.json` | `novelsound_full_with_book_metadata.json` | `abs_book_metadata_cache.json` |

The first helper adds series-level author, narrator, publisher, Audible series information, and match scores. It skips series that already have an author. The second matches chapter titles containing book/volume numbers to Audible sequence numbers and adds chapter-level `audible_*` fields. Entries such as `Chapter 1-67` are not matched as book numbers.

The author-only helper and the Audiobookshelf helpers are separate enrichment paths: the Audiobookshelf series helper reads the original full catalog, not the author-only output.

Download with enriched book names and the library layout:

```bash
python3 -u download_novelsound_series.py.py \
  --catalog novelsound_full_with_book_metadata.json \
  --series 123 \
  --output ./Audiobooks \
  --library-layout \
  --dry-run
```

Review the planned paths, then remove `--dry-run` to download. Switching catalogs or layouts can change destination filenames, so the selected-series downloader may download files again if the new paths do not already exist.

## Help and troubleshooting

- For downloader usage, run `python3 SCRIPT_NAME --help`. The fetch and enrichment scripts do not implement `--help`.
- `KeyError: 'NOVELSOUND_TOKEN'`: set the environment variable before fetching the catalog.
- `ModuleNotFoundError: requests`: activate the virtual environment and install `requests` there.
- No audio jobs: use the full catalog rather than the simplified JSON/CSV exports.
- Already completed entries skipped on a new drive: this is expected when using the same manifest.
- Existing files on another drive downloaded again: confirm you are using the original database and have bootstrapped any older unrecorded files.
- More concurrency: increase both `--workers` and `--series-workers` for the bulk downloaders. Increasing only `--workers` above 3 leaves the default series limit at 3.
- To inspect manifest download failures live, add `--debug` or watch the log with `tail -f download_failures.log` (use your configured log path).
