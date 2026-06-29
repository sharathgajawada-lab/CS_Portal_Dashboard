# V7.8 Portal Activity expand-close restore fix

## Issue
CSAT charts restored correctly after closing expanded charts, but Portal Activity charts could disappear after closing because Portal still had a legacy `#chartModal` path in `index.html`.

## Fix
- Added restore calls directly inside Portal Activity `closeChartModal()`.
- Exposed `window.DashboardUX.restoreCharts()` from the shared UX engine.
- Added a compatibility wrapper around any legacy `window.closeChartModal()` call.
- Preserved scroll position after close.
- Added multiple safe Chart.js resize/update passes after modal close.
- Bumped service-worker cache to `cs-portal-v7-8-portal-modal-close-restore`.

## Files changed
- `index.html`
- `portal-system.js`
- `dashboard_ux.js`
- `assets/dashboard_ux.js`
- `sw.js`
