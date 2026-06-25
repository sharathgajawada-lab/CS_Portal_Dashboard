# v7.3 Clean UI Patch

Applied from the latest user feedback:

- Removed the Portal executive command-center card strip:
  - Executive Readout
  - Dominant Behavior
  - Depth Signal
  - Analyst Note
- Removed the dedicated CSAT Voice AI lens card and the red Domo team pill.
- Kept Voice AI as a normal CSAT team in dropdowns, filters, comparison tables, and drilldowns.
- Removed the Theme control from the dashboard UI and command palette.
- Reworked chart action controls into one clean in-flow toolbar per chart.
- Removed duplicate floating/legacy expand controls.
- Confirmed every detected chart still has Expand, Insight, Data, CSV, and PNG actions.
- Bumped asset/cache versions to force the browser/Render to load the corrected UI.

Validation:

```text
python3 -m py_compile main.py scripts/ui_click_smoke.py
node --check portal-system.js
node --check dashboard_ux.js
node --check assets/dashboard_ux.js
node --check sw.js
node --check extracted inline scripts from index.html
node --check extracted inline scripts from csat.html
node --check extracted inline scripts from starter_guides.html
pytest -q -> 78 passed
python3 scripts/ui_click_smoke.py -> passed, 23 enhanced charts, 23 expand clicks tested
```

Deployment:

Upload/replace the ZIP contents in the GitHub root, then use Render:

Manual Deploy -> Clear build cache & deploy

After deployment, hard-refresh once:

Cmd/Ctrl + Shift + R
