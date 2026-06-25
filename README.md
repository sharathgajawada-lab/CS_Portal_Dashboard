# CS Portal Analytics Dashboard

FastAPI + vanilla JavaScript analytics dashboard for the hear.com Customer Support Portal.

**Stack:** FastAPI · Chart.js · optional Supabase session storage · Domo-backed CSAT · Render-ready deployment

---

## Pages

| URL | Description |
|-----|-------------|
| `/` | Portal Activity: logins, logouts, article views, searches, videos, sessions, heatmap, content health, executive command center |
| `/csat` | Domo-backed Call Quality Command Center: CSAT, solved rate, true FCR, teams, consultants, reason drivers, open drilldowns, and dedicated Voice AI analysis |
| `/starter-guides` | Starter Guide journeys, guide instances, answers, slides, completion metrics, metrics command center |

---

## Important CSAT source model

CSAT is **Domo-first**.

The normal dashboard workflow is:

```text
Domo dataset -> backend CSAT index -> open CSAT dashboard drilldowns
```

There is no user-facing Excel upload flow on the CSAT page. The dashboard directly loads the Domo-backed CSAT index, including call IDs/summaries when they are present in the index. The visible CSAT UI does **not** show admin locks, protected-drilldown cards, or Excel-upload controls.

`POST /upload/csat` is still present only as a backend break-glass recovery/backfill path. It is not part of the normal CSAT user experience.

For public/external deployments, set `CSAT_DRILLDOWNS_REQUIRE_TOKEN=true` if raw call details must be restricted again.

---

## What this final UI pass fixes

- Removes the CSAT data-quality/source/protected cards that created visual noise at the top of the page.
- Adds a premium **Call Quality Command Center** hero with executive metrics, Domo refresh, Voice AI focus, expand, and export actions.
- Adds a first-class **Voice AI performance** section with CSAT, volume, solved rate, low-rating risk, trend chart, and driver table.
- Opens CSAT drilldowns in the UI instead of showing locked/protected states.
- Makes `/api/csat/raw` open by default for the dashboard, with an opt-in environment flag for public deployments.
- Keeps CSAT Domo-first and removes the Excel-upload UX from the page.
- Adds a visible **Expand** control to every Chart.js chart through the shared UX engine, not only selected charts.
- Adds universal chart actions: Insight, Data, CSV, PNG, Expand, and click-to-inspect.
- Bumps the service-worker/static-asset cache version so deployed users actually see the redesigned UI instead of cached old assets.
- Keeps the original critique in `docs/ORIGINAL_CRITIQUE.md` and adds final resolution documentation.

---

## Local development

```bash
pip install -r requirements.txt

export CMS_API_KEY=your-cms-api-key
export DATA_START=2026-04-24

# CSAT Domo source
export DOMO_CLIENT_ID=your-domo-client-id
export DOMO_CLIENT_SECRET=your-domo-client-secret
export DOMO_DATASET_ID=your-domo-dataset-id

# Optional operational controls
export DASHBOARD_ADMIN_TOKEN=choose-a-long-random-token
export SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
export SUPABASE_KEY=your-service-role-jwt
export ALLOWED_ORIGINS=http://localhost:8000

uvicorn main:app --reload
```

Open `http://localhost:8000`.

---

## Required production environment variables

| Variable | Required | Description |
|---|---:|---|
| `CMS_API_KEY` | Yes | CMS metrics API key |
| `APP_ENV` | Recommended | Use `production` for deployments |
| `DATA_START` | Recommended | Earliest portal data date, default `2026-04-24` |
| `DOMO_CLIENT_ID` | Required for live CSAT | Enables direct CSAT refresh from Domo |
| `DOMO_CLIENT_SECRET` | Required for live CSAT | Enables direct CSAT refresh from Domo |
| `DOMO_DATASET_ID` | Required for live CSAT | CSAT dataset to pull from Domo |
| `DASHBOARD_ADMIN_TOKEN` | Recommended | Still protects admin/debug/cache/break-glass routes |
| `CSAT_DRILLDOWNS_REQUIRE_TOKEN` | Optional | Defaults to `false`. Set `true` only if raw CSAT drilldowns must require token auth |
| `ALLOWED_ORIGINS` | Optional | Comma-separated origins for CORS. Leave blank for same-origin dashboard use |
| `SUPABASE_URL` | Optional | Enables stored session timeline analytics |
| `SUPABASE_KEY` | Optional | Supabase service-role key |
| `CSAT_TEAMS` | Optional | Comma-separated allow-list. Leave blank to include all teams |
| `CSAT_INCLUDE_BOT_CONSULTANTS` | Optional | Defaults to `false` |

`CSAT_UPLOAD_PASSWORD` is still accepted as a backwards-compatible alias for `DASHBOARD_ADMIN_TOKEN`, but new deployments should use `DASHBOARD_ADMIN_TOKEN`.

---

## CSAT endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/csat/raw` | Domo-backed CSAT payload used by the open dashboard drilldowns. Requires token only when `CSAT_DRILLDOWNS_REQUIRE_TOKEN=true` |
| `GET` | `/api/csat/view` | Sanitized aggregate CSAT payload kept for compatibility |
| `GET` | `/api/csat/status` | Non-sensitive CSAT source, availability, refresh, and quality summary |
| `GET` | `/api/refresh/csat` | Pulls the latest CSAT data from Domo |
| `POST` | `/upload/csat` | Break-glass ingestion only; not exposed in the browser UI |

Other admin/debug/cache endpoints still use `DASHBOARD_ADMIN_TOKEN` unless admin auth is disabled for local development.

---

## Chart UX contract

Every Chart.js canvas with an `id` gets the shared dashboard toolbar and floating expand button automatically:

- **Insight**: generated readout from visible chart data
- **Data**: visible chart data table
- **CSV**: chart data export
- **PNG**: chart image export
- **Expand**: full-screen Chart Studio
- **Click inspect**: point-level popover on chart clicks

This applies to Portal Activity, CSAT, Voice AI, and Starter Guides charts.

---

## Running tests

```bash
python3 -m py_compile main.py
node --check assets/dashboard_ux.js
pytest -q
```

Current baseline after this pass:

```text
74 passed
```

---

## Future-proofing rules for new data

When adding a new metric or source, update these in order:

1. Add or revise the metric definition in `metrics_registry.json`.
2. Add backend parsing/aggregation in one place.
3. Expose a versioned API response with schema/source/quality metadata.
4. Add tests for normal, empty, malformed, missing-field, and new-dimension cases.
5. Render the metric with a visible definition, freshness, and sample-size caveat.
6. Escape all frontend values that originate from APIs, CMS content, Domo, Supabase, or break-glass files.
7. Avoid hard-coding teams, event names, and metric formulas in both backend and frontend.

See `docs/METRICS_REGISTRY.md`, `docs/ORIGINAL_CRITIQUE.md`, `docs/CRITIQUE_RESOLUTION_MATRIX.md`, and `docs/FINAL_BOSS_UI_FIX_REPORT.md` before adding new dashboard surfaces.
