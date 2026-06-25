# CS Portal Analytics Dashboard

FastAPI + vanilla JavaScript analytics dashboard for the hear.com Customer Support Portal.

**Stack:** FastAPI · Chart.js · optional Supabase session storage · Domo-backed CSAT · Render-ready deployment

---

## Pages

| URL | Description |
|-----|-------------|
| `/` | Portal Activity: logins, logouts, article views, searches, videos, sessions, heatmap, content health, executive command center |
| `/csat` | Domo-backed Call Quality & CSAT: ratings, solved rate, true FCR, teams, consultants, reason drivers, protected raw drilldowns |
| `/starter-guides` | Starter Guide journeys, guide instances, answers, slides, completion metrics, metrics command center |

---

## Important CSAT source model

CSAT is **Domo-first**.

The normal dashboard workflow is:

```text
Domo dataset -> backend CSAT index -> public aggregate dashboard view
                                    -> protected raw drilldowns for admins
```

There is no user-facing Excel upload flow on the CSAT page. The visible admin flow lets an authorized user:

1. unlock protected call-level drilldowns for the current browser session, and
2. manually trigger a Domo refresh through `/api/refresh/csat`.

`POST /upload/csat` is still present only as a protected break-glass backend path for emergency recovery/backfill. It is not part of the normal CSAT user experience.

---

## What this version fixes from the critique

- Replaces the old CSAT upload-oriented UX with a Domo-source/admin-access flow.
- Adds `/api/csat/view`, a public sanitized aggregate CSAT endpoint for dashboard rendering.
- Keeps `/api/csat/raw` protected for call IDs, summaries, opportunity/customer identifiers, and other sensitive fields.
- Adds a CSAT source strip showing source, refresh status, row coverage, data range, and access state.
- Adds an executive CSAT brief that calls out primary risk, team to inspect, and data confidence.
- Adds Portal Activity and Starter Guide command centers so users see the decision readout before the chart wall.
- Makes rating distribution, call-reason, and starter-guide slide drop-off charts clickable for analysis drilldowns/insights.
- Shows protected-drilldown notices instead of silently empty detail drawers when the viewer lacks admin access.
- Protects raw CSAT, Domo refresh, break-glass ingestion, cache clearing, and debug endpoints with `DASHBOARD_ADMIN_TOKEN`.
- Removes the insecure default password pattern and stops sending secrets in query strings.
- Makes CSAT team filtering configurable instead of hard-coded.
- Separates survey **Solved rate** from true operational **FCR**.
- Adds schema metadata, metric definitions, data-quality metadata, and a metrics registry.
- Escapes high-risk CMS, Domo, Supabase, and API-rendered values in key frontend rendering paths.
- Keeps the original critique inside `docs/ORIGINAL_CRITIQUE.md` and adds a resolution matrix in `docs/CRITIQUE_RESOLUTION_MATRIX.md`.

---

## Local development

```bash
pip install -r requirements.txt

export CMS_API_KEY=your-cms-api-key
export DASHBOARD_ADMIN_TOKEN=choose-a-long-random-token
export DATA_START=2026-04-24

# CSAT Domo source
export DOMO_CLIENT_ID=your-domo-client-id
export DOMO_CLIENT_SECRET=your-domo-client-secret
export DOMO_DATASET_ID=your-domo-dataset-id

# Optional
export SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
export SUPABASE_KEY=your-service-role-jwt
export ALLOWED_ORIGINS=http://localhost:8000

uvicorn main:app --reload
```

Open `http://localhost:8000`.

For local-only experiments, you may set `DISABLE_ADMIN_AUTH=true`. Never use that in production.

---

## Required production environment variables

| Variable | Required | Description |
|---|---:|---|
| `CMS_API_KEY` | Yes | CMS metrics API key |
| `DASHBOARD_ADMIN_TOKEN` | Yes | Admin token required for raw CSAT, Domo refresh, break-glass ingestion, cache, and debug endpoints |
| `APP_ENV` | Recommended | Use `production` for deployments |
| `DATA_START` | Recommended | Earliest portal data date, default `2026-04-24` |
| `DOMO_CLIENT_ID` | Required for live CSAT | Enables direct CSAT refresh from Domo |
| `DOMO_CLIENT_SECRET` | Required for live CSAT | Enables direct CSAT refresh from Domo |
| `DOMO_DATASET_ID` | Required for live CSAT | CSAT dataset to pull from Domo |
| `ALLOWED_ORIGINS` | Optional | Comma-separated origins for CORS. Leave blank for same-origin dashboard use |
| `SUPABASE_URL` | Optional | Enables stored session timeline analytics |
| `SUPABASE_KEY` | Optional | Supabase service-role key |
| `CSAT_TEAMS` | Optional | Comma-separated allow-list. Leave blank to include all teams |
| `CSAT_INCLUDE_BOT_CONSULTANTS` | Optional | Defaults to `false` |

`CSAT_UPLOAD_PASSWORD` is still accepted as a backwards-compatible alias for `DASHBOARD_ADMIN_TOKEN`, but new deployments should use `DASHBOARD_ADMIN_TOKEN`.

---

## Public and protected endpoints

Public CSAT endpoints:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/csat/view` | Sanitized aggregate CSAT payload for the dashboard UI |
| `GET` | `/api/csat/status` | Non-sensitive CSAT source, availability, refresh, and quality summary |

Protected endpoints requiring `X-Admin-Token: <token>` or `Authorization: Bearer <token>`:

| Method | Path | Reason |
|---|---|---|
| `GET` | `/api/csat/raw` | Contains call-level CSAT data and sensitive drilldown fields |
| `GET` | `/api/csat` | Backwards-compatible protected raw CSAT alias |
| `GET` | `/api/refresh/csat` | Pulls the latest CSAT data from Domo |
| `POST` | `/upload/csat` | Break-glass ingestion only; not exposed in the browser UI |
| `GET` | `/api/refresh` | Starts upstream refresh jobs |
| `GET` | `/cache/clear` | Can affect served data |
| `GET` | `/debug/*` | Operational/debug information |

---

## CSAT data contract

The CSAT index includes:

- `schema_version`
- `key_legend`
- `metric_definitions`
- `quality`
- `source`
- `date_min` / `date_max`
- indexed day, team, consultant, and reason structures

The sanitized dashboard endpoint strips call-level sensitive fields from the public payload. Admin users can unlock protected drilldowns from the CSAT page by entering `DASHBOARD_ADMIN_TOKEN`, which is stored only in `sessionStorage` for the current browser session.

---

## Running tests

```bash
pytest -q
python3 -m py_compile main.py
node --check /tmp/index.html.js
node --check /tmp/csat.html.js
node --check /tmp/starter_guides.html.js
```

Current baseline after this pass:

```text
67 passed
```

---

## Future-proofing rules for new data

When adding a new metric or source, update these in order:

1. Add or revise the metric definition in `metrics_registry.json`.
2. Add backend parsing/aggregation in one place.
3. Expose a typed API response with schema/version/source/quality metadata.
4. Add tests for normal, empty, malformed, missing-field, and new-dimension cases.
5. Render the metric with a visible definition, data freshness, and sample-size caveat.
6. Escape all frontend values that originate from APIs, CMS content, Domo, Supabase, or break-glass files.
7. Avoid hard-coding teams, event names, and metric formulas in both backend and frontend.

See `docs/METRICS_REGISTRY.md`, `docs/ORIGINAL_CRITIQUE.md`, and `docs/CRITIQUE_RESOLUTION_MATRIX.md` before adding new dashboard surfaces.

## World-class UI layer

The dashboard includes a shared premium UX layer:

- `assets/dashboard_ux.css`
- `assets/dashboard_ux.js`

These assets are linked from Portal Activity, Call Quality/CSAT, and Starter Guides. They add the executive hero, command palette, dark/compact/presentation modes, universal chart actions, chart focus studio, CSV/PNG export, data table view, local insights, and click-to-inspect behavior for every Chart.js canvas.

Run the full validation suite with:

```bash
python3 -m py_compile main.py
node --check assets/dashboard_ux.js
pytest -q
```

Expected result for this pass:

```text
73 passed
```
