# AGENTS.md

## Project Overview

DigitalOcean Instance Availability Dashboard — a Python script that queries the DO API for droplet size availability across all regions, stores timestamped JSON snapshots, and generates a self-contained static HTML dashboard.

## Architecture

```
check_availability.py  →  data/*.json  →  dashboard.html
     (fetcher)           (snapshots)     (static output)
```

Single-file Python script. No framework, no server at runtime. The generated `dashboard.html` is fully self-contained (inline CSS + JS) and opens directly in a browser.

## Key Files

| File | Purpose |
|------|---------|
| `check_availability.py` | Main script: API fetching, matrix building, diff computation, JSON storage, HTML generation |
| `.env` | `DIGITAL_OCEAN_TOKEN` — DO API bearer token |
| `data/latest.json` | Most recent snapshot (auto-generated) |
| `data/<timestamp>.json` | Historical snapshots (auto-generated) |
| `dashboard.html` | Generated static dashboard (auto-generated) |

## Data Flow

1. **Fetch**: `GET /v2/sizes?per_page=200` and `GET /v2/regions?per_page=50` (2 API calls per run, free, no cost)
2. **Matrix**: Cross-reference both endpoints — a size is available in a region only if the region appears in the size's `regions[]` AND the size appears in the region's `sizes[]`
3. **Diff**: Compare current matrix against `data/latest.json` to detect changes (became_available, became_unavailable, new_size, removed_size)
4. **Save**: Timestamped JSON snapshot + overwrite `latest.json`
5. **Generate**: Self-contained HTML with embedded JSON data, inline CSS (~130 lines), inline JS (~220 lines)

## Script Structure (`check_availability.py`)

- `load_config()` — reads token from `.env`
- `fetch_paginated(url, token, key)` — generic paginated DO API fetcher
- `build_matrix(sizes, regions)` — cross-references endpoints, groups by category, sorts by price
- `compute_diff(current, previous)` — cell-by-cell comparison against previous snapshot
- `save_snapshot(snapshot)` — writes JSON to `data/`
- `generate_dashboard(snapshot)` — builds HTML string with embedded data + CSS + JS
- `CSS_CONTENT` / `JS_CONTENT` — string constants for the inline dashboard assets

## Dashboard Features

- Summary cards (sizes, regions, available count, changes)
- Changes panel with per-cell diff details
- Collapsible category groups with availability percentages
- Filters: category, slug search, availability status, region toggles, "changes only" mode
- Color-coded matrix cells (green = available, red = unavailable, amber border = changed)
- Sticky header and first column

## API Details

- Base: `https://api.digitalocean.com/v2`
- Auth: `Authorization: Bearer {token}`
- Endpoints: `/sizes` (172 items), `/regions` (15 items, 13 active)
- Rate limit: 5,000 req/hr (script uses 2 per run)
- Cost: $0 (read-only listing endpoints)

## Running

```bash
pip install -r requirements.txt
python check_availability.py
# Open dashboard.html in a browser
```

For periodic monitoring, add to cron:
```bash
*/30 * * * * cd /path/to/project && python check_availability.py
```

## Conventions

- All timestamps in UTC with `Z` suffix
- Snapshot filenames use `-` instead of `:` for filesystem compatibility
- Categories sorted in a fixed logical order (Basic → General Purpose → CPU → Memory → Storage → GPU)
- Regions sorted geographically west-to-east
