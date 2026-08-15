# NovelSound Archiver

A Python toolkit for cataloging, enriching, downloading, and organizing audiobooks from a NovelSound subscription.

NovelSound Archiver uses NovelSound's backend API to build a local catalog, can enrich series with Audible metadata through an existing Audiobookshelf server, and downloads audio into either a simple directory structure or an Audiobookshelf-friendly `Author/Series/Book` layout.

> This project is intended for use with content you are authorized to access through your own NovelSound subscription.

## Features

- Catalog NovelSound series and their audio entries
- Store catalog information locally as JSON/CSV
- Enrich NovelSound series with Audible metadata through Audiobookshelf
- Match authors and series names using Audiobookshelf's Audible provider
- Download:
  - the entire catalog
  - a single series
  - multiple specified series
  - a list of series from a text file
- Concurrent downloads
- Resume interrupted downloads using `.part` files
- Skip files that have already been downloaded
- Dry-run mode
- Select custom output directories
- Audiobookshelf-friendly directory layout
- Fallback to `Unknown Author` when author metadata is unavailable

## Environment Variables

Authentication is handled through environment variables so credentials aren't stored in the scripts.

```bash
# NovelSound API authentication
export NOVELSOUND_TOKEN='Bearer YOUR_TOKEN'

# Audiobookshelf metadata authentication
export ABS_TOKEN='YOUR_ABS_API_TOKEN'

# Optional — defaults to http://localhost:13378
export ABS_URL='http://localhost:13378'
```

The NovelSound token can be found in your browser's **DevTools → Network → Request Headers → Authorization** while logged in.

The Audiobookshelf API token can be obtained from your ABS user settings.

To make these persistent, add the `export` commands to `~/.bashrc` or `~/.zshrc`.

**Never commit tokens or API keys to Git.**

## Requirements

- Python 3
- `requests`
- A NovelSound subscription/account
- Audiobookshelf *(optional, only required for Audible metadata enrichment)*

Install the Python dependency:

```bash
pip install requests
