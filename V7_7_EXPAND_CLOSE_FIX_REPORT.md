# v7.7 Expand close chart restore fix

## Fixed

- Fixed the bug where dashboard charts could disappear after opening an expanded chart and then closing it.
- The Chart Studio close action now destroys only the expanded studio chart, unlocks the page, forces browser resize events, re-stages every dashboard canvas, and resizes/updates each live Chart.js instance.
- The page scroll position is preserved after closing the expanded chart.
- The expand button remains available on dashboard charts, but the expanded modal canvas is not re-enhanced with dashboard controls.
- Service-worker cache bumped to `cs-portal-v7-7-expand-close-restore`.

## Files changed

- `portal-system.js`
- `dashboard_ux.js`
- `assets/dashboard_ux.js`
- `portal-overrides.css`
- `sw.js`
