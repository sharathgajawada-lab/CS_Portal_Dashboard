# Critique resolution matrix

This file keeps the original critique alive inside the code package and maps the highest-impact critique points to the second-pass changes.

| Critique area | Original issue | Second-pass resolution | Where to inspect |
|---|---|---|---|
| CSAT source model | The prior pass treated CSAT like an Excel-upload dashboard, but the real source is Domo. | Removed the user-facing Excel upload UI. CSAT is Domo-first, uses open internal raw drilldowns, and refreshes Domo without an admin modal by default. | `csat.html`, `/api/csat/view`, `/api/refresh/csat` |
| Sensitive CSAT data | Raw call summaries, call IDs, opportunity/customer identifiers, and response details were too easy to expose. | Kept sanitized `/api/csat/view` and made `/api/csat/raw` directly available for internal drilldowns per product requirement. Break-glass upload/debug/cache endpoints remain protected. | `main.py` `_sanitize_csat_index_for_dashboard`, tests |
| UX hierarchy | Dashboard had many charts but weak story and weak decision hierarchy. | Added CSAT source strip and executive brief: readout, primary risk, team to inspect, data confidence. | `csat.html` `renderCsatSourceStrip`, `renderCsatExecutiveBrief` |
| Chart interactions | Charts were not sufficiently testable/actionable as analysis surfaces. | Rating distribution, call-reason, and Starter Guide slide-dropoff charts now support click-to-drill/click-to-insight workflows. | `csat.html`, `starter_guides.html` chart `onClick` handlers |
| Portal UX | The dashboard did not give an executive readout before detailed charts. | Added Portal Activity command center with readout, dominant behavior, depth signal, filter actions, and content caveat. | `index.html` `renderPortalCommandCenter` |
| Starter Guide UX | Starter Guide metrics were a chart dump without a decision layer. | Added Starter Guide metrics command center and clickable slide-dropoff insight. | `starter_guides.html` `renderMetricsCommandCenter` |
| Empty/protected states | Missing admin access could make drilldowns appear empty or broken. | Removed protected/locked drilldown states from the CSAT UI; users can inspect call-level details directly in the internal dashboard. | `csat.html` `openCsatDetail`, consultant focus tables |
| Misleading FCR | Survey solved percentage was labeled like first-call resolution. | Renamed to Solved rate and kept true FCR separate. Data quality warns when opportunity IDs are missing. | `csat.html`, `metrics_registry.json` |
| Future teams | Hard-coded team allow-list could silently drop new teams. | `CSAT_TEAMS` is optional; blank means include all teams. Quality metadata reports dropped rows if allow-list is used. | `main.py`, `README.md` |
| Security defaults | Default password, open CORS, and sensitive operational endpoints were unsafe. | Admin token required for raw CSAT, Domo refresh, cache/debug, and break-glass ingestion. CORS is explicit. | `main.py`, `SECURITY.md`, `render.yaml` |
| Secrets in URLs | Admin credentials were previously sent through query strings. | Browser sends `X-Admin-Token`; token is stored only in sessionStorage. | `csat.html`, `SECURITY.md` |
| Data quality visibility | Users could not see coverage, drops, missing fields, or source freshness clearly. | Added data quality banner, source strip, and `/api/csat/status` source metadata. | `csat.html`, `main.py` |
| Metric governance | Metric definitions were scattered. | Added `metrics_registry.json` and `docs/METRICS_REGISTRY.md`. | registry/docs |
| Test baseline | Previous critique found failing tests. | Current suite passes and adds sanitized-public-view tests. | `test_main.py`, `pytest -q` |

## Remaining open items

These require a larger architectural/product investment beyond this pass:

1. Move the frontend from large inline scripts to modules or a component system.
2. Split `main.py` into routers/services/models/repositories/security.
3. Keep expanding Playwright browser tests for filters, charts, keyboard navigation, responsive layouts, and Domo error states.
4. Add role-based access control rather than a single admin token.
5. Persist cache/index state in a durable store instead of relying on local filesystem behavior.
6. Replace directional article-period estimates with exact article-by-day facts if the upstream source can provide them.

## Third-pass UI/product resolution addendum

| Critique area | Remaining issue after second pass | Third-pass resolution | Where to inspect |
|---|---|---|---|
| Frontend visual quality | The dashboard was improved analytically, but still did not feel like a premium product interface. | Added a shared visual design layer with executive hero sections, improved card hierarchy, dark mode, compact mode, presentation mode, and refined responsive behavior. | `assets/dashboard_ux.css`, linked from all HTML pages |
| Inconsistent chart affordances | Some charts had controls while others did not. Users could not expect the same behavior everywhere. | Added a universal chart engine that automatically gives every detected Chart.js canvas Insight, Data, CSV, PNG, Focus, and click-inspection tools. | `assets/dashboard_ux.js` `enhanceCharts`, `openStudio` |
| Weak tester coverage for UI behavior | Backend tests passed, but the UI contract was not protected. | Added static UI contract tests for shared assets, chart actions, CSAT Domo-first workflow, accessibility controls, and asset serving. | `test_ui_experience.py` |
| Future chart additions | New charts would require repeated hand-coded toolbars and export behavior. | Future Chart.js canvases with an `id` are upgraded automatically through a MutationObserver. | `assets/dashboard_ux.js` `observeDom`, `enhanceCharts` |
| Repeated frontend code | Large inline additions would become difficult to maintain across three pages. | Moved the premium UX layer into shared `/assets/dashboard_ux.css` and `/assets/dashboard_ux.js` served by FastAPI. | `main.py` `/assets/{asset_name}` route |
| Keyboard and power-user workflow | Dashboard navigation relied too much on mouse/manual scrolling. | Added Cmd/Ctrl+K command palette, on-page section map, keyboard-accessible chart focus, and skip link. | `assets/dashboard_ux.js`, `assets/dashboard_ux.css` |
| Automated test stability | Manual refresh endpoints could spawn network work during tests. | Test mode now returns no-op refresh responses instead of spawning background network jobs. | `main.py` `_is_test_mode`, `/api/refresh`, `/cache/clear` |
