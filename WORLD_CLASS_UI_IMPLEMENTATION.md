> Current status: superseded by `docs/FINAL_UNLOCKED_WORLD_CLASS_UI_REPORT.md`. Some earlier protected/admin wording below describes an intermediate version.

# Second-pass UX, Domo, and QA report

## Why this pass exists

The first implementation pass fixed several backend/security/data issues, but it did not go far enough on user experience and it incorrectly preserved an Excel-upload mental model for CSAT. This pass corrects that product mistake and makes the dashboard behave more like a Domo-backed analytics product.

## Cross-dashboard UX improvements

| Page | UX improvement | Status |
|---|---|---|
| Portal Activity | Executive command center above KPI grid with trend health, dominant behavior, depth signal, filter actions, and article-estimate caveat | Implemented |
| Call Quality / CSAT | Domo source strip, executive brief, protected drilldown state, chart click-to-drill | Implemented |
| Starter Guides | Metrics command center with guide-open readout, answer-rate signal, top-slide signal, source coverage, and journey-search handoff | Implemented |

## CSAT workflow QA

| Flow | Expected behavior | Status |
|---|---|---|
| Public page load | CSAT loads from `/api/csat/view` without requiring admin token. | Implemented |
| Source awareness | User sees data source, index build time, range, rows, and access state. | Implemented |
| Admin access | User enters token to unlock protected raw drilldowns. | Implemented |
| Domo refresh | Authorized user triggers `/api/refresh/csat`; no upload fallback appears. | Implemented |
| Missing token | Refresh/admin actions open the Domo admin-access modal. | Implemented |
| Wrong token | Raw fetch falls back to aggregate view rather than breaking the dashboard. | Implemented |
| Protected detail | Without raw access, call-level drawers explain the protected state. | Implemented |
| Break-glass upload | Backend endpoint remains protected but is not shown in the CSAT UI. | Implemented |

## Chart and click QA

| Surface | Interaction | Result |
|---|---|---|
| Rating distribution | Click a rating bucket | Opens rating-level detail drawer; shows protected notice if no raw summaries are available |
| Call reason bar chart | Click a reason | Opens reason detail drawer with summaries for admin users |
| Primary risk card | Inspect reason | Filters/focuses the call-reason analysis |
| Team to inspect card | Focus team | Applies global team scope and rerenders page |
| Starter Guide slide drop-off | Click a slide bar | Updates the metrics readout with the selected slide and view count |
| Portal command center | Click actions | Clears filters, focuses dominant event, scrolls to trend/content sections |

## Accessibility and UX notes

- Source and access state are visible instead of hidden in network calls.
- Protected data states are explicit.
- Chart helper text tells users that CSAT charts are clickable.
- Executive cards reduce dashboard scanning cost.
- Buttons use semantic `<button>` elements instead of clickable text where new actions were added.
- Dynamic strings inserted into inline chart/card actions use JSON string serialization.

## Security QA

- `/api/csat/view` strips sensitive nested call fields.
- `/api/csat/raw` requires admin token.
- `/api/refresh/csat` requires admin token.
- `/upload/csat` remains protected and hidden from normal UI.
- Browser uses `X-Admin-Token`, not query-string secrets.
- Admin token is only kept in `sessionStorage` for the browser session.

## Automated validation

```text
pytest -q                               -> 67 passed
python3 -m py_compile main.py            -> passed
node --check extracted index.html script -> passed
node --check extracted csat.html script  -> passed
node --check extracted starter script    -> passed
```

## Remaining high-value testing still recommended

Automated browser testing is the next gap. Add Playwright tests for:

1. `/csat` aggregate load;
2. Domo access modal open/close;
3. Domo refresh with missing/wrong token;
4. rating chart click;
5. reason chart click;
6. team focus from executive brief;
7. Portal command-center actions;
8. Starter Guide slide-dropoff click;
9. keyboard navigation through major controls;
10. mobile responsive layout;
11. public view does not render sensitive call text.
