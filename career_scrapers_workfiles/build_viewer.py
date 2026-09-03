"""
Builds a single self-contained HTML file (crawler_viewer.html) so you can pick
a company, search its scraped jobs, and see the exact URL for each one (as
visible text, not just a clickable link) — all client-side, no server needed.

Usage:
    python build_viewer.py
    open crawler_viewer.html
"""
import glob
import json

FILES = sorted(glob.glob("*_jobs*.json"))

LABELS = {
    "coca_cola_hbc_jobs.json": "Coca-Cola HBC",
    "hugoboss_jobs.json": "HUGO BOSS",
    "lenovo_jobs.json": "Lenovo",
    "lenovo_jobs_sample_verified.json": "Lenovo (sample)",
    "loreal_jobs.json": "L'Oréal",
    "nbc_jobs.json": "National Bank of Canada",
    "siemens_jobs.json": "Siemens",
}

INITIALS = {
    "coca_cola_hbc_jobs.json": "CC",
    "hugoboss_jobs.json": "HB",
    "lenovo_jobs.json": "LN",
    "lenovo_jobs_sample_verified.json": "LN",
    "loreal_jobs.json": "LO",
    "nbc_jobs.json": "NBC",
    "siemens_jobs.json": "SI",
}

EXTRA_FIELDS = ["country", "job_function", "division", "employment_type", "position_type"]
EXTRA_LABELS = {
    "country": "Country",
    "job_function": "Function",
    "division": "Division",
    "employment_type": "Type",
    "position_type": "Contract",
}


def load_datasets():
    datasets = []
    for path in FILES:
        with open(path, encoding="utf-8") as f:
            raw_jobs = json.load(f)

        present_extras = [f for f in EXTRA_FIELDS if any(f in j for j in raw_jobs)]

        jobs = []
        for j in raw_jobs:
            job = {
                "title": j.get("title") or "",
                "location": j.get("location") or "",
                "url": j.get("url") or "",
            }
            for field in present_extras:
                job[field] = j.get(field) or ""
            jobs.append(job)

        datasets.append({
            "file": path,
            "label": LABELS.get(path, path),
            "initials": INITIALS.get(path, path[:2].upper()),
            "jobs": jobs,
            "extraFields": present_extras,
        })
    return datasets


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Crawl Results</title>
<style>
  :root {
    --bg: #f6f7fb;
    --surface: #ffffff;
    --surface-2: #f0f1f7;
    --border: #e2e4ee;
    --text: #1c1e2b;
    --muted: #6b6f85;
    --accent: #5b52f6;
    --accent-soft: #eeecff;
    --ok: #1a9c6b;
    --ok-soft: #e5f7ef;
    --warn: #b6790a;
    --warn-soft: #fbf1de;
    --shadow: 0 1px 2px rgba(20, 21, 40, 0.04), 0 8px 24px -12px rgba(20, 21, 40, 0.12);
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1016;
      --surface: #17181f;
      --surface-2: #1d1f29;
      --border: #2a2c3a;
      --text: #e9e9f2;
      --muted: #9395a8;
      --accent: #8b83ff;
      --accent-soft: #24223f;
      --ok: #3ddc97;
      --ok-soft: #12271f;
      --warn: #f0b429;
      --warn-soft: #2c2312;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 8px 24px -12px rgba(0, 0, 0, 0.5);
      color-scheme: dark;
    }
  }

  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  .page {
    max-width: 1180px;
    margin: 0 auto;
    padding: 36px 24px 60px;
  }

  header.top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 28px;
    flex-wrap: wrap;
  }
  header.top h1 {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.01em;
    margin: 0;
  }
  header.top .grand-total {
    color: var(--muted);
    font-size: 13px;
  }
  header.top .grand-total b { color: var(--text); font-weight: 600; }

  /* Company tabs */
  .tabs {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .tab {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 14px 9px 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 999px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    box-shadow: var(--shadow);
    transition: transform 0.12s ease, border-color 0.12s ease;
  }
  .tab:hover { transform: translateY(-1px); }
  .tab.active {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
  }
  .tab .avatar {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 800;
    flex-shrink: 0;
  }
  .tab.active .avatar { background: rgba(255,255,255,0.25); color: white; }
  .tab .count { opacity: 0.7; font-weight: 500; }
  .tab .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--ok);
    flex-shrink: 0;
  }
  .tab .dot.warn { background: var(--warn); }
  .tab.active .dot { background: white; opacity: 0.9; }

  /* Toolbar */
  .toolbar {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
  }
  .search-box {
    position: relative;
    flex: 1;
  }
  .search-box svg {
    position: absolute;
    left: 13px;
    top: 50%;
    transform: translateY(-50%);
    width: 15px;
    height: 15px;
    stroke: var(--muted);
  }
  .search-box input {
    width: 100%;
    padding: 11px 14px 11px 36px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 14px;
    box-shadow: var(--shadow);
  }
  .search-box input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .search-box input::placeholder { color: var(--muted); }

  /* Stat chips */
  .stat-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .chip {
    font-size: 12px;
    padding: 5px 10px;
    border-radius: 7px;
    background: var(--surface-2);
    color: var(--muted);
    border: 1px solid var(--border);
  }
  .chip b { color: var(--text); font-weight: 700; }
  .chip.ok { background: var(--ok-soft); color: var(--ok); border-color: transparent; }
  .chip.warn { background: var(--warn-soft); color: var(--warn); border-color: transparent; }

  /* Table */
  .table-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .table-scroll { overflow: auto; max-height: 68vh; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  thead th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--surface);
    text-align: left;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  tbody td {
    padding: 11px 16px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: var(--surface-2); }

  td.title { max-width: 340px; }
  td.title a {
    color: var(--text);
    text-decoration: none;
    font-weight: 600;
  }
  td.title a:hover { color: var(--accent); text-decoration: underline; }
  td.location { white-space: nowrap; }
  td.extra { white-space: nowrap; color: var(--muted); }
  td.url {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 11.5px;
    color: var(--muted);
    max-width: 320px;
    word-break: break-all;
    user-select: all;
  }
  .empty-cell { color: var(--warn); font-style: italic; }

  .no-results {
    padding: 48px 24px;
    text-align: center;
    color: var(--muted);
    font-size: 14px;
  }

  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 6px; }
</style>
</head>
<body>

<div class="page">
  <header class="top">
    <h1>AI Crawl Results</h1>
    <div class="grand-total"><b id="grand-total">0</b> jobs across <b id="grand-companies">0</b> companies</div>
  </header>

  <div class="tabs" id="tabs"></div>

  <div class="toolbar">
    <div class="search-box">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input id="search" type="text" placeholder="Search by title, location, or url...">
    </div>
  </div>

  <div class="stat-row" id="stat-row"></div>

  <div class="table-card">
    <div class="table-scroll">
      <table>
        <thead id="thead"></thead>
        <tbody id="rows"></tbody>
      </table>
      <div class="no-results" id="no-results" style="display:none">No jobs match this search.</div>
    </div>
  </div>
</div>

<script>
  const DATA = __DATA_JSON__;
  const EXTRA_LABELS = __EXTRA_LABELS_JSON__;

  let activeIndex = 0;

  const tabsEl = document.getElementById('tabs');
  const searchInput = document.getElementById('search');
  const theadEl = document.getElementById('thead');
  const rowsBody = document.getElementById('rows');
  const noResults = document.getElementById('no-results');
  const statRow = document.getElementById('stat-row');

  document.getElementById('grand-total').textContent =
    DATA.reduce((sum, d) => sum + d.jobs.length, 0).toLocaleString();
  document.getElementById('grand-companies').textContent = DATA.length;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function buildTabs() {
    tabsEl.innerHTML = DATA.map((d, i) => {
      const emptyLoc = d.jobs.filter(j => !j.location).length;
      const dotClass = emptyLoc > 0 ? 'warn' : '';
      return `<button class="tab ${i === activeIndex ? 'active' : ''}" data-index="${i}">
        <span class="avatar">${escapeHtml(d.initials)}</span>
        ${escapeHtml(d.label)}
        <span class="count">${d.jobs.length.toLocaleString()}</span>
        <span class="dot ${dotClass}"></span>
      </button>`;
    }).join('');

    tabsEl.querySelectorAll('.tab').forEach(btn => {
      btn.addEventListener('click', () => {
        activeIndex = Number(btn.dataset.index);
        buildTabs();
        render();
      });
    });
  }

  function render() {
    const dataset = DATA[activeIndex];
    const query = searchInput.value.trim().toLowerCase();
    const extras = dataset.extraFields || [];

    const jobs = dataset.jobs.filter(j =>
      !query ||
      j.title.toLowerCase().includes(query) ||
      j.location.toLowerCase().includes(query) ||
      j.url.toLowerCase().includes(query)
    );

    const emptyLocationCount = dataset.jobs.filter(j => !j.location).length;
    const emptyTitleCount = dataset.jobs.filter(j => !j.title).length;
    const enrichedCount = extras.length ? dataset.jobs.filter(j => j[extras[0]]).length : 0;

    const chips = [];
    chips.push(`<span class="chip"><b>${jobs.length.toLocaleString()}</b>&nbsp;shown of ${dataset.jobs.length.toLocaleString()}</span>`);
    chips.push(emptyLocationCount === 0
      ? `<span class="chip ok">0 missing locations</span>`
      : `<span class="chip warn">${emptyLocationCount} missing location${emptyLocationCount === 1 ? '' : 's'}</span>`);
    if (emptyTitleCount > 0) {
      chips.push(`<span class="chip warn">${emptyTitleCount} missing title${emptyTitleCount === 1 ? '' : 's'}</span>`);
    }
    if (extras.length) {
      chips.push(`<span class="chip">${enrichedCount.toLocaleString()}/${dataset.jobs.length.toLocaleString()} enriched with ${EXTRA_LABELS[extras[0]].toLowerCase()}+metadata</span>`);
    }
    statRow.innerHTML = chips.join('');

    theadEl.innerHTML = `<tr>
      <th>Title</th>
      <th>Location</th>
      ${extras.map(f => `<th>${escapeHtml(EXTRA_LABELS[f] || f)}</th>`).join('')}
      <th>URL</th>
    </tr>`;

    if (jobs.length === 0) {
      rowsBody.innerHTML = '';
      noResults.style.display = 'block';
      return;
    }
    noResults.style.display = 'none';

    rowsBody.innerHTML = jobs.map(j => {
      const title = j.title
        ? `<a href="${escapeHtml(j.url)}" target="_blank" rel="noopener">${escapeHtml(j.title)}</a>`
        : '<span class="empty-cell">(no title)</span>';
      const location = j.location
        ? escapeHtml(j.location)
        : '<span class="empty-cell">—</span>';
      const extraCells = extras.map(f =>
        `<td class="extra">${j[f] ? escapeHtml(j[f]) : '<span class="empty-cell">—</span>'}</td>`
      ).join('');
      const url = escapeHtml(j.url);
      return `<tr><td class="title">${title}</td><td class="location">${location}</td>${extraCells}<td class="url">${url}</td></tr>`;
    }).join('');
  }

  searchInput.addEventListener('input', render);

  buildTabs();
  render();
</script>

</body>
</html>
"""


if __name__ == "__main__":
    datasets = load_datasets()
    out = (
        TEMPLATE
        .replace("__DATA_JSON__", json.dumps(datasets, ensure_ascii=False))
        .replace("__EXTRA_LABELS_JSON__", json.dumps(EXTRA_LABELS, ensure_ascii=False))
    )
    with open("crawler_viewer.html", "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote crawler_viewer.html — {len(datasets)} companies, "
          f"{sum(len(d['jobs']) for d in datasets)} total jobs")
