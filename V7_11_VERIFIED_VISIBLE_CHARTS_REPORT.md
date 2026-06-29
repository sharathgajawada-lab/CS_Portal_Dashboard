# v7.11 Verified Visible Charts Fix

## Root cause fixed
Portal Activity was still blank because chart canvases could be created after the UX layer had already run, and the previous resize/polish function was effectively broken by a duplicated nested function. Expand worked because it created a fresh modal chart, while the original dashboard canvas remained stale.

## Engineering changes
- Replaced the broken duplicated `polishChartLayout()` block with one real function.
- Made the UX engine continuously detect late-created Chart.js instances.
- Forced safe resize/update passes for every dashboard chart instance.
- Locked every dashboard canvas into a stable absolute render box inside its chart card.
- Preserved the Chart Studio expand/close restore behavior.
- Kept the hero/banner removed.
- Bumped cache/versioning to `v7.11`.

## Browser verification performed
A Playwright browser harness created all real dashboard chart IDs from the three pages and verified visible canvas pixels before and after expand/close.

Validated chart IDs:
- Portal Activity: 9 charts
- CSAT: 9 charts
- Starter Guides: 6 charts
- Total: 24 charts

Result:
- 24 / 24 dashboard charts visible before expand
- 24 / 24 expand buttons available
- 0 expand/close failures
- 24 / 24 dashboard charts still visible after expand/close

## Static/runtime checks
- `node --check portal-system.js`
- `node --check dashboard_ux.js`
- `node --check assets/dashboard_ux.js`
- `node --check sw.js`
- inline script syntax checks for `index.html`, `csat.html`, and `starter_guides.html`
- `pytest -q` -> 83 passed
