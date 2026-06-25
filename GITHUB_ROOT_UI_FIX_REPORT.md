# Final unlocked world-class UI pass

This pass responds directly to the latest QA feedback:

- CSAT must be Domo-first, not Excel-upload-first.
- Voice AI must be first-class in CSAT.
- Nothing in CSAT should be locked behind an admin/access modal.
- The old diagnostic cards shown in the screenshot must be removed.
- Every active chart must have an Expand path.
- The shipped dashboard must match the preview instead of being hidden by old cached assets.

## Product/UI changes

### CSAT

- Removed the old top diagnostic card stack:
  - `Data quality:` banner
  - CSAT source strip
  - last index build card
  - rows/coverage card
  - protected drilldown card
  - executive readout card
  - primary-risk card
  - team-to-inspect card
  - data-confidence card
- Removed the Domo/admin unlock modal from the CSAT UI.
- CSAT now opens directly into filters, KPIs, charts, reasons, consultants, and drilldowns.
- Added a dedicated **Voice AI lens** near the top of CSAT:
  - Voice AI surveys
  - Voice AI average CSAT
  - Voice AI solved rate
  - Voice AI low-rating percentage
  - Voice AI trend chart
  - one-click `Focus Voice AI`
- Added canonical team normalization for Domo labels such as `VoiceAI`, `voice-ai`, and `teamvoiceai` so they all render as `Voice AI`.
- Kept the Domo refresh action visible and direct.

### Universal chart experience

Every active Chart.js chart is now handled by the shared UX engine:

- Insight
- Data table
- CSV export
- PNG export
- Expand chart studio
- click-to-inspect point behavior
- keyboard-accessible chart surfaces

The engine now enhances every canvas with an id, including charts rendered after API calls, and it prunes stale chart toolbars when charts are replaced by empty states.

### Cache / preview mismatch fix

The asset URLs were versioned, and static dashboard assets plus the service worker were updated with no-cache/network-first behavior for scripts and styles. This prevents the browser from silently showing an older dashboard after deployment.

## Backend/data changes

- `/api/csat/raw` is open by default for internal dashboard drilldowns.
- `/api/refresh/csat` is open by default unless `CSAT_REFRESH_REQUIRES_ADMIN=true` is explicitly set.
- Break-glass `POST /upload/csat`, debug endpoints, cache clearing, and legacy protected endpoints remain admin-token protected.
- `/api/config` exposes the effective CSAT drilldown/refresh behavior.
- CSAT no longer drops future/new Domo teams unless `CSAT_TEAMS` is explicitly configured.

## QA results

Static and unit validation:

```text
python3 -m py_compile main.py scripts/ui_click_smoke.py
node --check inline script extracted from index.html
node --check inline script extracted from csat.html
node --check inline script extracted from starter_guides.html
node --check assets/dashboard_ux.js
node --check sw.js
pytest -q
```

Result:

```text
78 passed
```

Browser click QA:

```text
python3 scripts/ui_click_smoke.py
```

Result:

```text
passed: true
total enhanced charts: 24
total expand clicks tested: 24
```

Chart coverage from the click QA:

| Page | Enhanced charts | Expand clicks tested |
|---|---:|---:|
| Portal Activity | 9 | 9 |
| CSAT / Call Quality | 10 | 10 |
| Starter Guides | 5 active charts | 5 |
| **Total** | **24** | **24** |

The CSAT browser QA also asserts that the removed/locked UI text does not appear and that `Voice AI` is visible in the loaded UI.

## Notes

This build intentionally favors the user requirement of open internal CSAT drilldowns. If the dashboard is ever exposed outside the intended internal environment, add a real permission layer or set:

```bash
CSAT_RAW_PUBLIC=false
CSAT_REFRESH_REQUIRES_ADMIN=true
```
