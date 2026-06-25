# Upload this package to GitHub root

Replace the existing root files with the files in this package. Do not only add the CSS/JS files.

Minimum files that must be replaced:

- `main.py`
- `index.html`
- `csat.html`
- `starter_guides.html`
- `portal-overrides.css`
- `portal-system.js`
- `dashboard_ux.js`
- `sw.js`
- `requirements.txt`
- `runtime.txt`
- `render.yaml`
- `Procfile`

What this package fixes:

- CSAT team dropdowns always include `Voice AI`.
- Voice AI is preserved even if Render has an older `CSAT_TEAMS` allow-list.
- All Chart.js charts get a visible `Expand` control, plus the shared chart studio controls.
- Root-level GitHub assets are used, so the flat repository structure works.
- Service-worker cache is bumped to `cs-portal-v6-voice-ai-expand-root`.
- Render uses Python `python-3.11.11`, avoiding the pandas/Python 3.14 build failure.

After pushing, redeploy on Render with `Clear build cache & deploy`.
Then hard refresh the browser with Cmd/Ctrl+Shift+R.
