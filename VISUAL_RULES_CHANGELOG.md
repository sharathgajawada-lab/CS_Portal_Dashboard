# CS Portal Dashboard visual-rule update

## Scope
Updated the existing frontend only (`index.html` and `csat.html`) without changing backend routes, API contracts, cache logic, Domo/CMS/Supabase calls, upload endpoints, or Python code.

## What changed

### Mandatory visual rules now auto-apply
A global CSS and JavaScript rule layer was added to both dashboard pages. It automatically enhances current and future charts/cards that use the existing `.chart-wrap`, `.card`, table, and navigation conventions.

### Every chart gets dashboard controls
Each chart container now receives a Power BI / Domo-style toolbar with:
- Global filter / Last / All time / Current / Previous selector
- `vs prev period` comparison toggle
- `Graph by` selector: Day, Week, Month, Quarter, Year
- `Expand` action

Charts that already had native controls keep them. The new mandatory toolbar is added as a consistent visual and interaction layer so newly added chart blocks inherit the same feature set.

### Expand behavior is standardized
- Existing expand buttons were normalized to `Expand ↗`.
- Charts without an expand button receive one automatically.
- Double-clicking a chart canvas expands it.
- Keyboard users can tab to a chart and press Enter/Space to expand it.
- The expanded modal includes a range label and clearer helper text.

### Clickability and interaction affordances
- Chart canvases, table headers, rows, KPI cards, heatmap cells, and clickable elements now show clearer hover/focus states.
- Sortable table headers are keyboard-accessible.
- Hover states were strengthened to make the app feel more interactive and dashboard-like.

### Page-level UX improvements
- Page titles are set dynamically:
  - `Portal Activity — CS Portal`
  - `Call Quality / CSAT — CS Portal`
- Active nav state is reinforced with styling and `aria-current`.
- A visible page heading is injected under the sticky header.
- Resolved date range text appears in the date controls.

### CSAT-specific improvements
- Consultant focus now shows a clearer active pill when scoped.
- `Clear focus` stays hidden until a consultant is selected.
- A visible `Update data` button is added to the header when the CSAT upload modal exists.

## Validation
- JavaScript syntax check passed for scripts extracted from both HTML files using `node --check`.
- Existing pytest suite was run. It currently has pre-existing backend test failures around `_build_csat_index` team allow-list filtering sample teams; these are unrelated to the frontend visual-rule changes because backend/Python files were not modified.

## Files changed
- `index.html`
- `csat.html`
- `VISUAL_RULES_CHANGELOG.md`
