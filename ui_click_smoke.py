# GitHub root UI fix report

This pass addresses the latest screenshots showing the deployed dashboard still rendering with the dense starter UI.

## Root cause fixed

The previous package linked the premium UI layer from:

```text
/assets/dashboard_ux.css
/assets/dashboard_ux.js
```

Your GitHub screenshot shows the repository is being maintained as a flat root upload and does not include an `assets/` directory. That means the premium CSS/JS layer could 404 or remain stale, leaving the dashboard with the older dense layout.

This package is GitHub-root ready:

```text
portal-overrides.css
portal-system.js
```

The three dashboard HTML files now load those root files directly.

## UI changes in this pass

- Root-level premium CSS/JS so the deployed dashboard matches the intended UI.
- Larger executive shell, improved spacing, larger typography, softer card hierarchy, and a more readable chart canvas layout.
- Visible top-level dashboard hero for Portal, CSAT, and Starter Guides.
- Persistent section map after the hero.
- CSAT remains Domo-first and open, with no Admin access / unlock / protected-drilldown UI.
- Voice AI remains first-class and visible near the top of CSAT.
- Every active Chart.js chart gets visible Expand plus Insight, Data, CSV, and PNG actions.
- The backend now serves both root assets and old `/assets/dashboard_ux.*` aliases to prevent broken deploys.
- Service worker cache bumped to force clients off the stale UI.

## Files that must be uploaded to GitHub root

```text
index.html
csat.html
starter_guides.html
main.py
sw.js
portal-overrides.css
portal-system.js
```

The package still includes `/assets/dashboard_ux.css` and `/assets/dashboard_ux.js` as compatibility aliases, but the root files above are the primary ones.

## Validation

```text
python3 -m py_compile main.py scripts/ui_click_smoke.py
node --check portal-system.js
node --check assets/dashboard_ux.js
node --check sw.js
node --check extracted inline scripts from index/csat/starter_guides
pytest -q
python3 scripts/ui_click_smoke.py
```

Result:

```text
78 passed
Browser click QA passed
24 enhanced charts
24 expand clicks tested
```

## Browser/deploy note

After deploying this version, hard refresh once or unregister the old service worker if the browser still shows the old UI:

```text
Cmd/Ctrl + Shift + R
```

The service worker name is now:

```text
cs-portal-v5-github-root-pro-ui
```
