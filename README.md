# CS Portal Analytics Dashboard

FastAPI + vanilla JavaScript analytics dashboard for the hear.com Customer Support Portal.

**Stack:** FastAPI · Chart.js · Supabase optional session storage · Render-ready deployment

---

## Pages

| URL | Description |
|-----|-------------|
| `/` | Portal Activity: logins, logouts, article views, searches, videos, sessions, heatmap, content health |
| `/csat` | Call Quality & CSAT: ratings, solved rate, true FCR, teams, consultants, call-level drill-down |
| `/starter-guides` | Starter Guide journeys, guide instances, answers, slides, and completion metrics |

---

## What changed in the hardened version

This version addresses the main findings from the dashboard critique:

- Admin-only controls are protected by `DASHBOARD_ADMIN_TOKEN` instead of a hard-coded default password.
- Raw CSAT, uploads, refresh, cache clearing, and debug endpoints now require an admin token.
- Uploads send the admin token in the `X-Admin-Token` header, not in the query string.
- CORS is same-origin by default and only opens to explicit `ALLOWED_ORIGINS`.
- CSAT team filtering is configurable instead of hard-coded, so new teams are not silently dropped.
- The CSAT metric previously labeled “First-call resolution” is now labeled “Solved rate.” True FCR is tracked separately when opportunity IDs are available.
- CSAT responses include schema version, key legend, metric definitions, and data-quality metadata.
- Frontend rendering now escapes high-risk CMS/upload fields in the portal, CSAT, and starter-guide pages.
- Tests have been updated around the new security model and pass locally.

---

## Local development

```bash
pip install -r requirements.txt

export CMS_API_KEY=your-cms-api-key
export DASHBOARD_ADMIN_TOKEN=choose-a-long-random-token
export DATA_START=2026-04-24

# Optional
export SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
export SUPABASE_KEY=your-service-role-jwt
export DOMO_CLIENT_ID=your-domo-client-id
export DOMO_CLIENT_SECRET=your-domo-client-secret
export DOMO_DATASET_ID=your-domo-dataset-id

uvicorn main:app --reload
```

Open `http://localhost:8000`.

For local-only experiments, you may set `DISABLE_ADMIN_AUTH=true`, but do not use that setting in production.

---

## Required production environment variables

| Variable | Required | Description |
|---|---:|---|
| `CMS_API_KEY` | Yes | CMS metrics API key |
| `DASHBOARD_ADMIN_TOKEN` | Yes | Admin token required for raw CSAT, uploads, refresh, cache, and debug endpoints |
| `APP_ENV` | Recommended | Use `production` for deployments |
| `DATA_START` | Recommended | Earliest portal data date, default `2026-04-24` |
| `ALLOWED_ORIGINS` | Optional | Comma-separated origins for CORS. Leave blank for same-origin dashboard use |
| `SUPABASE_URL` | Optional | Enables stored session timeline analytics |
| `SUPABASE_KEY` | Optional | Supabase service-role key |
| `DOMO_CLIENT_ID` | Optional | Enables direct CSAT refresh from Domo |
| `DOMO_CLIENT_SECRET` | Optional | Enables direct CSAT refresh from Domo |
| `DOMO_DATASET_ID` | Optional | CSAT dataset to pull from Domo |
| `CSAT_TEAMS` | Optional | Comma-separated allow-list. Leave blank to include all teams |
| `CSAT_INCLUDE_BOT_CONSULTANTS` | Optional | Defaults to `false` |

`CSAT_UPLOAD_PASSWORD` is still accepted as a backwards-compatible alias for `DASHBOARD_ADMIN_TOKEN`, but new deployments should use `DASHBOARD_ADMIN_TOKEN`.

---

## Protected endpoints

The following endpoints require `X-Admin-Token: <token>` or `Authorization: Bearer <token>`:

| Method | Path | Reason |
|---|---|---|
| `GET` | `/api/csat/raw` | Contains call-level CSAT data and summaries |
| `GET` | `/api/csat` | Backwards-compatible CSAT raw alias |
| `POST` | `/upload/csat` | Replaces CSAT source data |
| `GET` | `/api/refresh` | Starts upstream refresh jobs |
| `GET` | `/api/refresh/csat` | Pulls CSAT data from Domo |
| `GET` | `/cache/clear` | Can affect served data |
| `GET` | `/debug/*` | Operational/debug information |

Public alternatives:

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health and cache status without sensitive data |
| `GET` | `/api/config` | Public dashboard config and schema metadata |
| `GET` | `/api/csat/status` | Non-sensitive CSAT availability and quality summary |

---

## Architecture

```text
Browser
  ├── GET /                         -> index.html
  ├── GET /csat                     -> csat.html
  ├── GET /starter-guides           -> starter_guides.html
  ├── GET /api/metrics/batch        -> KPI time series
  ├── GET /api/articles             -> article performance and health scoring
  ├── GET /api/search               -> search query intelligence
  ├── GET /api/sessions/full        -> session analytics when Supabase is enabled
  ├── GET /api/csat/status          -> public CSAT health summary
  ├── GET /api/csat/raw             -> protected CSAT index
  └── POST /upload/csat             -> protected CSV/XLSX CSAT upload
```

The backend still intentionally serves a simple HTML/JS application, but the data contract is now more explicit. CSAT index payloads include:

- `schema_version`
- `key_legend`
- `metric_definitions`
- `quality`
- `date_min` / `date_max`
- indexed day, team, consultant, reason, and call-level structures

See `docs/METRICS_REGISTRY.md` and `metrics_registry.json` for metric definitions that should be kept in sync with future dashboard changes.

---

## CSAT source data

Accepted upload formats: `.csv`, `.xlsx`, `.xls`.

Recommended columns:

| Column | Type | Purpose |
|---|---|---|
| `RATING` | number | Survey rating, usually 1-5 |
| `SOLVED` | boolean/text | Survey solved flag used for solved rate |
| `CONSULTANT_ID` | string | Stable consultant key |
| `CONSULTANT_NAME` | string | Display name |
| `CONSULTANT_TEAM` | string | Team dimension |
| `DATETIME` | datetime | Survey/call timestamp |
| `CALL_ID` | string | Deep-link to UCJ call record |
| `OPPORTUNITY_ID` | string | Enables true FCR and repeat-call analysis |
| `CALL_SUMMARY` | text | Used in call-level drill-downs |
| `CALL_REASON` | text | Used in reason distribution and low-CSAT drivers |

Rows without optional fields are still counted where possible. The dashboard surfaces missing opportunity IDs and summaries in the CSAT data-quality banner.

---

## Running tests

```bash
pytest -q
```

Current hardened baseline: `65 passed`.

---

## Future-proofing rules for new data

When adding a new metric or source, update these in order:

1. Add or revise the metric definition in `metrics_registry.json`.
2. Add backend parsing/aggregation in one place.
3. Expose a typed API response with schema/version metadata.
4. Add tests for normal, empty, malformed, and missing-field cases.
5. Render the metric with a visible definition, data freshness, and sample-size caveat.
6. Escape all frontend values that originate from APIs, uploaded files, CMS content, Domo, or Supabase.

Do not hard-code new teams, event names, or business rules in multiple frontend and backend locations. Prefer environment configuration or a dimension table/registry.
