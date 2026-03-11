#!/usr/bin/env python3
"""DigitalOcean Instance Availability Checker.

Fetches droplet size availability across all DO regions,
stores snapshots as JSON, and generates a static HTML dashboard.
"""

import json
import os
import shutil
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.digitalocean.com/v2"
SIZES_ENDPOINT = f"{BASE_URL}/sizes"
REGIONS_ENDPOINT = f"{BASE_URL}/regions"
DATA_DIR = Path(__file__).parent / "data"
DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

REGION_ORDER = [
    "sfo2", "sfo3", "nyc1", "nyc2", "nyc3",
    "tor1", "atl1", "ams3", "lon1", "fra1",
    "blr1", "sgp1", "syd1",
]

CATEGORY_SORT_ORDER = [
    "Basic",
    "Basic AMD",
    "Basic Intel",
    "General Purpose",
    "General Purpose 2x SSD",
    "General Purpose 6.5x SSD",
    "General Purpose — Premium Intel",
    "General Purpose — Premium Intel 2x SSD",
    "General Purpose - Premium Intel 5.5x SSD",
    "CPU-Optimized",
    "CPU-Optimized 2x SSD",
    "CPU Intensive 5x SSD",
    "CPU Optimized - Premium Intel 5x SSD",
    "Premium Intel",
    "Memory-Optimized",
    "Memory-Optimized 3x SSD",
    "Memory-Optimized 6x SSD",
    "Premium Memory-Optimized",
    "Premium Memory-Optimized 3x SSD",
    "Storage-Optimized",
    "Storage-Optimized 1.5x SSD",
    "Premium Storage-Optimized",
    "Premium Storage-Optimized 1.5x SSD",
]


def load_config():
    load_dotenv(Path(__file__).parent / ".env")
    token = os.getenv("DIGITAL_OCEAN_TOKEN", "").strip()
    if not token:
        print("Error: DIGITAL_OCEAN_TOKEN not found in .env")
        sys.exit(1)
    return token


def fetch_paginated(url, token, key):
    headers = {"Authorization": f"Bearer {token}"}
    items = []
    page_url = url
    while page_url:
        resp = requests.get(page_url, headers=headers, params={"per_page": 200}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get(key, []))
        page_url = data.get("links", {}).get("pages", {}).get("next")
    return items


def fetch_sizes(token):
    return fetch_paginated(SIZES_ENDPOINT, token, "sizes")


def fetch_regions(token):
    return fetch_paginated(REGIONS_ENDPOINT, token, "regions")


def build_matrix(sizes, regions):
    active_regions = [r for r in regions if r["available"]]
    active_region_slugs = sorted(
        [r["slug"] for r in active_regions],
        key=lambda s: REGION_ORDER.index(s) if s in REGION_ORDER else 999,
    )

    region_sizes_map = {}
    for r in active_regions:
        region_sizes_map[r["slug"]] = set(r["sizes"])

    categories = {}
    for size in sizes:
        cat = size["description"]
        if cat not in categories:
            categories[cat] = []

        availability = {}
        for region_slug in active_region_slugs:
            in_size_regions = region_slug in size["regions"]
            in_region_sizes = size["slug"] in region_sizes_map.get(region_slug, set())
            availability[region_slug] = in_size_regions and in_region_sizes

        categories[cat].append({
            "slug": size["slug"],
            "vcpus": size["vcpus"],
            "memory": size["memory"],
            "disk": size["disk"],
            "price_monthly": size["price_monthly"],
            "price_hourly": size["price_hourly"],
            "transfer": size["transfer"],
            "globally_available": size["available"],
            "availability": availability,
            "available_count": sum(1 for v in availability.values() if v),
        })

    for cat in categories:
        categories[cat].sort(key=lambda s: s["price_monthly"])

    sorted_cats = OrderedDict()
    known = [c for c in CATEGORY_SORT_ORDER if c in categories]
    unknown = sorted(c for c in categories if c not in CATEGORY_SORT_ORDER)
    for cat in known + unknown:
        sorted_cats[cat] = categories[cat]

    return {
        "categories": sorted_cats,
        "regions": active_region_slugs,
        "region_names": {r["slug"]: r["name"] for r in active_regions},
    }


def load_previous():
    latest = DATA_DIR / "latest.json"
    if not latest.exists():
        return None
    try:
        with open(latest) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def compute_diff(current_matrix, previous_data):
    if previous_data is None:
        return {
            "has_previous": False,
            "changes": [],
            "summary": {
                "became_available": 0,
                "became_unavailable": 0,
                "new_sizes": 0,
                "removed_sizes": 0,
            },
        }

    prev_matrix = previous_data["matrix"]

    current_flat = {}
    for cat_sizes in current_matrix["categories"].values():
        for size in cat_sizes:
            current_flat[size["slug"]] = size["availability"]

    prev_flat = {}
    for cat_sizes in prev_matrix["categories"].values():
        for size in cat_sizes:
            prev_flat[size["slug"]] = size["availability"]

    changes = []
    all_slugs = set(current_flat.keys()) | set(prev_flat.keys())
    for slug in sorted(all_slugs):
        if slug in current_flat and slug not in prev_flat:
            changes.append({"slug": slug, "type": "new_size"})
            continue
        if slug not in current_flat and slug in prev_flat:
            changes.append({"slug": slug, "type": "removed_size"})
            continue
        for region in current_matrix["regions"]:
            curr_val = current_flat[slug].get(region, False)
            prev_val = prev_flat[slug].get(region, False)
            if curr_val != prev_val:
                changes.append({
                    "slug": slug,
                    "region": region,
                    "from": prev_val,
                    "to": curr_val,
                    "type": "became_available" if curr_val else "became_unavailable",
                })

    return {
        "has_previous": True,
        "previous_timestamp": previous_data.get("timestamp", "unknown"),
        "changes": changes,
        "summary": {
            "became_available": sum(1 for c in changes if c["type"] == "became_available"),
            "became_unavailable": sum(1 for c in changes if c["type"] == "became_unavailable"),
            "new_sizes": sum(1 for c in changes if c["type"] == "new_size"),
            "removed_sizes": sum(1 for c in changes if c["type"] == "removed_size"),
        },
    }


def save_snapshot(snapshot):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = snapshot["timestamp"].replace(":", "-")
    filepath = DATA_DIR / f"{ts}.json"
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)
    shutil.copy2(filepath, DATA_DIR / "latest.json")
    return filepath


def format_memory(mb):
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:.0f} GB" if gb == int(gb) else f"{gb:.1f} GB"
    return f"{mb} MB"


def generate_dashboard(snapshot):
    matrix = snapshot["matrix"]
    diff = snapshot["diff"]

    total_sizes = sum(len(sizes) for sizes in matrix["categories"].values())
    total_regions = len(matrix["regions"])
    total_combos = total_sizes * total_regions
    available_count = sum(
        s["available_count"]
        for sizes in matrix["categories"].values()
        for s in sizes
    )
    unavailable_count = total_combos - available_count
    pct = round(available_count / total_combos * 100, 1) if total_combos else 0

    changed_cells = set()
    if diff["has_previous"]:
        for c in diff["changes"]:
            if c["type"] in ("became_available", "became_unavailable"):
                changed_cells.add((c["slug"], c["region"]))

    dashboard_data = {
        "timestamp": snapshot["timestamp"],
        "matrix": matrix,
        "diff": diff,
        "summary": {
            "total_sizes": total_sizes,
            "total_regions": total_regions,
            "total_combinations": total_combos,
            "available": available_count,
            "unavailable": unavailable_count,
            "pct_available": pct,
        },
    }

    data_json = json.dumps(dashboard_data, separators=(",", ":"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DO Instance Availability</title>
<style>
{CSS_CONTENT}
</style>
</head>
<body>
<header>
  <h1>DigitalOcean Instance Availability</h1>
  <span id="timestamp"></span>
</header>
<main>
  <section id="summary"></section>
  <section id="changes"></section>
  <section id="filters"></section>
  <div id="matrix-container">
    <table id="matrix"></table>
  </div>
</main>
<script>
const DATA = {data_json};
{JS_CONTENT}
</script>
</body>
</html>"""

    DASHBOARD_PATH.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Inline CSS
# ---------------------------------------------------------------------------
CSS_CONTENT = r"""
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: #f1f5f9; color: #1e293b; font-size: 14px;
}
header {
  background: #0f172a; color: #f8fafc; padding: 16px 24px;
  display: flex; align-items: center; justify-content: space-between;
}
header h1 { font-size: 18px; font-weight: 600; }
header #timestamp { font-size: 13px; color: #94a3b8; }
main { padding: 16px 24px; max-width: 100%; }

/* Summary cards */
#summary {
  display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap;
}
.card {
  background: #fff; border-radius: 8px; padding: 14px 18px;
  flex: 1; min-width: 160px; border-top: 3px solid #3b82f6;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.card.green { border-top-color: #22c55e; }
.card.red { border-top-color: #ef4444; }
.card.amber { border-top-color: #f59e0b; }
.card .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
.card .sub { font-size: 12px; color: #94a3b8; margin-top: 2px; }

/* Changes panel */
#changes {
  background: #fff; border-radius: 8px; padding: 14px 18px;
  margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
#changes.hidden { display: none; }
#changes .changes-header {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; user-select: none;
}
#changes .changes-header h3 { font-size: 14px; font-weight: 600; }
#changes .changes-list { margin-top: 10px; max-height: 200px; overflow-y: auto; }
#changes .changes-list.collapsed { display: none; }
.change-item { padding: 3px 0; font-size: 13px; font-family: monospace; }
.change-item.available { color: #16a34a; }
.change-item.unavailable { color: #dc2626; }

/* Filters */
#filters {
  display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; align-items: center;
}
#filters select, #filters input[type="text"] {
  padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px;
  font-size: 13px; background: #fff;
}
#filters input[type="text"] { width: 200px; }
#filters label { font-size: 13px; display: flex; align-items: center; gap: 4px; cursor: pointer; }
#filters button {
  padding: 6px 12px; border: 1px solid #cbd5e1; border-radius: 6px;
  background: #fff; font-size: 13px; cursor: pointer;
}
#filters button:hover { background: #f1f5f9; }

/* Region toggles */
#region-toggles {
  display: flex; gap: 4px; flex-wrap: wrap; margin-left: 8px;
}
.region-toggle {
  font-size: 11px; padding: 3px 7px; border-radius: 4px;
  border: 1px solid #cbd5e1; background: #fff; cursor: pointer; user-select: none;
}
.region-toggle.active { background: #dbeafe; border-color: #3b82f6; color: #1d4ed8; }

/* Matrix table */
#matrix-container { overflow-x: auto; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
#matrix {
  border-collapse: collapse; width: 100%; font-size: 13px;
}
#matrix thead { position: sticky; top: 0; z-index: 10; }
#matrix thead th {
  background: #f8fafc; border-bottom: 2px solid #e2e8f0;
  padding: 6px 8px; text-align: left; font-weight: 600; white-space: nowrap;
}
#matrix thead th.region-col {
  text-align: center; width: 58px; min-width: 58px; max-width: 58px;
  font-size: 11px; font-weight: 500;
  vertical-align: bottom; padding-bottom: 8px;
}
#matrix thead th.region-col .region-label {
  writing-mode: vertical-lr; transform: rotate(180deg);
  display: inline-block; max-height: 90px;
}
#matrix thead th.corner { position: sticky; left: 0; z-index: 15; background: #f8fafc; }

/* Category header rows */
.cat-header {
  background: #1e293b; color: #f8fafc; cursor: pointer; user-select: none;
}
.cat-header td {
  padding: 8px 12px; font-weight: 600; font-size: 13px;
}
.cat-header:hover { background: #334155; }
.cat-header .arrow { display: inline-block; width: 16px; transition: transform 0.15s; }
.cat-header.collapsed .arrow { transform: rotate(-90deg); }
.cat-header .cat-stats { font-weight: 400; color: #94a3b8; font-size: 12px; margin-left: 8px; }

/* Size rows */
.size-row td { padding: 5px 8px; border-bottom: 1px solid #f1f5f9; }
.size-row td.slug-col {
  position: sticky; left: 0; z-index: 5; background: #fff;
  font-family: monospace; font-size: 12px; white-space: nowrap;
}
.size-row td.info-col { text-align: right; white-space: nowrap; color: #64748b; }
.size-row td.avail-cell {
  text-align: center; width: 58px; min-width: 58px; max-width: 58px;
  font-size: 14px; padding: 4px;
}
.size-row td.avail-cell.yes { background: #dcfce7; color: #166534; }
.size-row td.avail-cell.no { background: #fee2e2; color: #991b1b; }
.size-row td.avail-cell.changed { box-shadow: inset 0 0 0 2px #f59e0b; }
.size-row td.count-col { text-align: center; font-weight: 600; font-size: 12px; }
.size-row td.count-col.full { color: #16a34a; }
.size-row td.count-col.partial { color: #d97706; }
.size-row td.count-col.none { color: #dc2626; }

/* Collapsed categories hide size rows */
.cat-group.collapsed .size-row { display: none; }
"""

# ---------------------------------------------------------------------------
# Inline JS
# ---------------------------------------------------------------------------
JS_CONTENT = r"""
document.addEventListener('DOMContentLoaded', () => {
    renderTimestamp();
    renderSummary();
    renderChanges();
    renderFilters();
    renderMatrix();
});

function renderTimestamp() {
    const d = new Date(DATA.timestamp);
    document.getElementById('timestamp').textContent = 'Last updated: ' + d.toLocaleString();
}

function renderSummary() {
    const s = DATA.summary;
    const d = DATA.diff;
    let changeText = 'First run';
    let changeClass = '';
    if (d.has_previous) {
        const up = d.summary.became_available;
        const down = d.summary.became_unavailable;
        changeText = `+${up} / -${down}`;
        changeClass = down > 0 ? 'red' : (up > 0 ? 'green' : '');
    }
    document.getElementById('summary').innerHTML = `
        <div class="card"><div class="label">Sizes</div><div class="value">${s.total_sizes}</div><div class="sub">across ${Object.keys(DATA.matrix.categories).length} categories</div></div>
        <div class="card"><div class="label">Regions</div><div class="value">${s.total_regions}</div><div class="sub">active datacenters</div></div>
        <div class="card green"><div class="label">Available</div><div class="value">${s.available}</div><div class="sub">${s.pct_available}% of ${s.total_combinations} combos</div></div>
        <div class="card ${changeClass || 'amber'}"><div class="label">Changes</div><div class="value">${changeText}</div><div class="sub">${d.has_previous ? 'since ' + new Date(d.previous_timestamp).toLocaleString() : 'no previous data'}</div></div>
    `;
}

function renderChanges() {
    const el = document.getElementById('changes');
    const d = DATA.diff;
    if (!d.has_previous || d.changes.length === 0) {
        el.classList.add('hidden');
        return;
    }
    const items = d.changes.map(c => {
        if (c.type === 'became_available') {
            return `<div class="change-item available">[+] ${c.slug} now available in ${c.region}</div>`;
        } else if (c.type === 'became_unavailable') {
            return `<div class="change-item unavailable">[-] ${c.slug} no longer available in ${c.region}</div>`;
        } else if (c.type === 'new_size') {
            return `<div class="change-item available">[NEW] ${c.slug} added</div>`;
        } else {
            return `<div class="change-item unavailable">[DEL] ${c.slug} removed</div>`;
        }
    }).join('');
    el.innerHTML = `
        <div class="changes-header" onclick="toggleChanges()">
            <h3>Changes (${d.changes.length})</h3><span id="changes-arrow">&#9660;</span>
        </div>
        <div class="changes-list" id="changes-list">${items}</div>
    `;
}

function toggleChanges() {
    const list = document.getElementById('changes-list');
    const arrow = document.getElementById('changes-arrow');
    list.classList.toggle('collapsed');
    arrow.textContent = list.classList.contains('collapsed') ? '\u25B6' : '\u25BC';
}

function renderFilters() {
    const cats = Object.keys(DATA.matrix.categories);
    const regions = DATA.matrix.regions;
    const regionNames = DATA.matrix.region_names;

    const catOptions = cats.map(c => `<option value="${c}">${c}</option>`).join('');
    const regionToggles = regions.map(r =>
        `<span class="region-toggle active" data-region="${r}" onclick="toggleRegion(this)">${r}</span>`
    ).join('');

    document.getElementById('filters').innerHTML = `
        <select id="cat-filter" onchange="applyFilters()">
            <option value="__all__">All Categories</option>
            ${catOptions}
        </select>
        <input type="text" id="search" placeholder="Search slug..." oninput="applyFilters()">
        <select id="avail-filter" onchange="applyFilters()">
            <option value="all">All</option>
            <option value="full">Fully available</option>
            <option value="partial">Partially available</option>
            <option value="none">Fully unavailable</option>
        </select>
        <label><input type="checkbox" id="changes-only" onchange="applyFilters()"> Changes only</label>
        <button onclick="expandAll()">Expand All</button>
        <button onclick="collapseAll()">Collapse All</button>
        <div id="region-toggles">${regionToggles}</div>
    `;
}

function toggleRegion(el) {
    el.classList.toggle('active');
    applyFilters();
}

function getActiveRegions() {
    return Array.from(document.querySelectorAll('.region-toggle.active')).map(el => el.dataset.region);
}

function expandAll() {
    document.querySelectorAll('.cat-group').forEach(g => g.classList.remove('collapsed'));
    document.querySelectorAll('.cat-header').forEach(h => h.classList.remove('collapsed'));
}

function collapseAll() {
    document.querySelectorAll('.cat-group').forEach(g => g.classList.add('collapsed'));
    document.querySelectorAll('.cat-header').forEach(h => h.classList.add('collapsed'));
}

function renderMatrix() {
    const regions = DATA.matrix.regions;
    const regionNames = DATA.matrix.region_names;
    const categories = DATA.matrix.categories;
    const changedCells = new Set();
    if (DATA.diff.has_previous) {
        DATA.diff.changes.forEach(c => {
            if (c.type === 'became_available' || c.type === 'became_unavailable') {
                changedCells.add(c.slug + '|' + c.region);
            }
        });
    }

    let thead = '<tr><th class="corner">Size</th><th>vCPU</th><th>RAM</th><th>$/mo</th><th>Avail</th>';
    regions.forEach(r => {
        thead += `<th class="region-col" data-region="${r}"><span class="region-label">${regionNames[r] || r}</span></th>`;
    });
    thead += '</tr>';

    let tbody = '';
    for (const [cat, sizes] of Object.entries(categories)) {
        const totalAvail = sizes.reduce((a, s) => a + s.available_count, 0);
        const totalCells = sizes.length * regions.length;
        const pct = totalCells > 0 ? Math.round(totalAvail / totalCells * 100) : 0;
        const catId = cat.replace(/[^a-zA-Z0-9]/g, '_');

        const hasChanges = sizes.some(s => regions.some(r => changedCells.has(s.slug + '|' + r)));

        tbody += `<tbody class="cat-group collapsed" data-category="${cat}" id="cat-${catId}">`;
        tbody += `<tr class="cat-header collapsed" onclick="toggleCat('${catId}')">`;
        tbody += `<td colspan="${5 + regions.length}">`;
        tbody += `<span class="arrow">&#9660;</span> ${cat}`;
        tbody += `<span class="cat-stats">${sizes.length} sizes &mdash; ${totalAvail}/${totalCells} available (${pct}%)`;
        if (hasChanges) tbody += ' &mdash; has changes';
        tbody += `</span></td></tr>`;

        sizes.forEach(s => {
            const mem = s.memory >= 1024 ? Math.round(s.memory / 1024) + ' GB' : s.memory + ' MB';
            const rowHasChanges = regions.some(r => changedCells.has(s.slug + '|' + r));
            const countClass = s.available_count === regions.length ? 'full' : (s.available_count === 0 ? 'none' : 'partial');

            tbody += `<tr class="size-row${rowHasChanges ? ' has-changes' : ''}" data-slug="${s.slug}" data-avail-count="${s.available_count}">`;
            tbody += `<td class="slug-col">${s.slug}</td>`;
            tbody += `<td class="info-col">${s.vcpus}</td>`;
            tbody += `<td class="info-col">${mem}</td>`;
            tbody += `<td class="info-col">$${s.price_monthly}</td>`;
            tbody += `<td class="count-col ${countClass}">${s.available_count}/${regions.length}</td>`;
            regions.forEach(r => {
                const avail = s.availability[r];
                const changed = changedCells.has(s.slug + '|' + r);
                const cls = (avail ? 'yes' : 'no') + (changed ? ' changed' : '');
                tbody += `<td class="avail-cell ${cls}" data-region="${r}">${avail ? '\u2713' : '\u2717'}</td>`;
            });
            tbody += '</tr>';
        });
        tbody += '</tbody>';
    }

    document.getElementById('matrix').innerHTML = `<thead>${thead}</thead>${tbody}`;
}

function toggleCat(catId) {
    const group = document.getElementById('cat-' + catId);
    const header = group.querySelector('.cat-header');
    group.classList.toggle('collapsed');
    header.classList.toggle('collapsed');
}

function applyFilters() {
    const catFilter = document.getElementById('cat-filter').value;
    const search = document.getElementById('search').value.toLowerCase();
    const availFilter = document.getElementById('avail-filter').value;
    const changesOnly = document.getElementById('changes-only').checked;
    const activeRegions = getActiveRegions();

    // Show/hide region columns
    document.querySelectorAll('th.region-col, td.avail-cell').forEach(el => {
        el.style.display = activeRegions.includes(el.dataset.region) ? '' : 'none';
    });

    // Filter categories and rows
    document.querySelectorAll('.cat-group').forEach(group => {
        const cat = group.dataset.category;
        if (catFilter !== '__all__' && cat !== catFilter) {
            group.style.display = 'none';
            return;
        }
        group.style.display = '';

        let visibleCount = 0;
        group.querySelectorAll('.size-row').forEach(row => {
            let visible = true;
            const slug = row.dataset.slug;
            const availCount = parseInt(row.dataset.availCount);

            if (search && !slug.includes(search)) visible = false;

            if (availFilter === 'full' && availCount !== activeRegions.length) visible = false;
            else if (availFilter === 'none' && availCount !== 0) visible = false;
            else if (availFilter === 'partial' && (availCount === 0 || availCount === activeRegions.length)) visible = false;

            if (changesOnly && !row.classList.contains('has-changes')) visible = false;

            row.style.display = visible ? '' : 'none';
            if (visible) visibleCount++;
        });
    });
}
"""


def main():
    print("DigitalOcean Instance Availability Checker")
    print("=" * 45)

    token = load_config()

    print("Fetching sizes...")
    sizes = fetch_sizes(token)
    print(f"  {len(sizes)} sizes")

    print("Fetching regions...")
    regions = fetch_regions(token)
    active = [r for r in regions if r["available"]]
    print(f"  {len(regions)} regions ({len(active)} active)")

    print("Building availability matrix...")
    matrix = build_matrix(sizes, regions)
    total_sizes = sum(len(s) for s in matrix["categories"].values())
    total_avail = sum(s["available_count"] for ss in matrix["categories"].values() for s in ss)
    total_combos = total_sizes * len(matrix["regions"])
    print(f"  {total_sizes} sizes x {len(matrix['regions'])} regions = {total_combos} combos")
    print(f"  {total_avail} available ({round(total_avail/total_combos*100, 1)}%)")

    print("Loading previous snapshot...")
    previous = load_previous()
    if previous:
        print(f"  Previous: {previous['timestamp']}")
    else:
        print("  No previous data (first run)")

    print("Computing diff...")
    diff = compute_diff(matrix, previous)
    if diff["has_previous"]:
        s = diff["summary"]
        print(f"  +{s['became_available']} available, -{s['became_unavailable']} unavailable, "
              f"{s['new_sizes']} new, {s['removed_sizes']} removed")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "timestamp": timestamp,
        "matrix": matrix,
        "diff": diff,
        "raw_api": {"sizes": sizes, "regions": regions},
    }

    print("Saving snapshot...")
    filepath = save_snapshot(snapshot)
    print(f"  {filepath}")

    print("Generating dashboard...")
    generate_dashboard(snapshot)
    print(f"  {DASHBOARD_PATH}")

    print(f"\nDone! Open {DASHBOARD_PATH} in your browser.")


if __name__ == "__main__":
    main()
