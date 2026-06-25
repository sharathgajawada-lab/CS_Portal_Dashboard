> Current status: superseded by `docs/FINAL_UNLOCKED_WORLD_CLASS_UI_REPORT.md`. Some earlier protected/admin wording below describes an intermediate version.

# Dashboard implementation report

## Executive summary

This version converts the critique into a stronger product, analytics, UX, security, and test pass while preserving the current FastAPI + vanilla JavaScript deployment model.

The most important correction from the second pass: **CSAT is not an Excel-upload workflow. It is Domo-backed.** The CSAT page now treats Domo as the source of truth, renders a public sanitized aggregate view by default, and uses an admin token only for Domo refresh and protected raw drilldowns.

Validation result after changes:

```text
67 passed
```

Additional static checks completed:

```text
python3 -m py_compile main.py
node --check extracted index.html script
node --check extracted csat.html script
node --check extracted starter_guides.html script
```

## Files changed

| File | Main changes |
|---|---|
| `main.py` | Domo-source tracking, public sanitized `/api/csat/view`, protected raw `/api/csat/raw`, protected Domo refresh, admin-token auth, source/status metadata, configurable CSAT teams, data-quality summary, safer CORS |
| `csat.html` | Removed user-facing Excel upload UX, added Domo admin-access modal, source strip, executive brief, clickable rating/reason charts, protected drilldown notices, safer dynamic strings |
| `index.html` | Portal executive command center, dominant-behavior/depth/readout cards, filter/action affordances, safer CMS rendering, aligned data start, article-estimate disclosure, keyboard/focus improvements |
| `starter_guides.html` | Metrics command center, top-slide/freshness/readout cards, clickable slide-dropoff insight, safer dynamic onclick arguments, keyboard-accessible rows/cards, focus styles |
| `test_main.py` | Added coverage for public sanitized CSAT view and CSAT source metadata; security tests remain active |
| `README.md` | Rewritten to explain Domo-first CSAT, public/protected endpoints, testing, and future-proofing rules |
| `SECURITY.md` | Updated around Domo-backed CSAT, sanitized aggregate view, protected raw view, and break-glass ingestion |
| `docs/ORIGINAL_CRITIQUE.md` | Preserves the original hard critique inside the package |
| `docs/CRITIQUE_RESOLUTION_MATRIX.md` | Maps critique items to concrete code/product changes |
| `metrics_registry.json` / `docs/METRICS_REGISTRY.md` | Governed metric definitions and caveats |

## Product and UX changes

### 1. CSAT now tells users where the data comes from

The CSAT page has a source strip that shows:

- active source, normally Domo dataset;
- Domo configuration/refresh cadence;
- index generation time;
- covered date range;
- indexed row count;
- team/consultant coverage;
- whether the viewer has aggregate or admin raw access.

This prevents the page from feeling like a mystery data dump.

### 2. Excel upload is no longer part of the CSAT user experience

The old upload modal has been replaced with a Domo admin-access modal.

The modal explains that CSAT data is pulled from Domo and gives authorized users two actions:

1. unlock protected raw drilldowns for the session;
2. manually trigger a Domo refresh.

The backend `POST /upload/csat` endpoint still exists as a protected break-glass path for emergency recovery/backfill, but it is intentionally not exposed in the browser UI.

### 3. Executive brief added to CSAT

The CSAT page now summarizes:

- executive readout;
- primary risk reason;
- team to inspect;
- data confidence.

This moves the page closer to Power BI/Tableau-style analytic storytelling instead of forcing users to inspect every chart manually.

### 4. Charts are now more actionable

The rating distribution chart supports clicking a rating bucket to open a detail drawer. The call-reason chart supports clicking a reason to inspect related summaries when the viewer has admin access.

Without admin access, detail drawers show a protected-drilldown explanation instead of looking broken or empty.

### 5. Portal and Starter Guide UX now have decision layers

The Portal Activity page now includes an executive command center above the KPI grid. It summarizes total views, trend direction, dominant behavior, depth per login, active filter state, and article-estimate caveats.

The Starter Guides metrics page now includes a metrics command center. It summarizes guide opens, answer submission rate, top slide signal, active-day coverage, CMS project source, and an analyst note that separates aggregate CMS events from instance-level Customer GID drilldowns. The slide drop-off chart now supports click-to-insight behavior.

### 6. Metric language is clearer

The misleading “First-call resolution” label was replaced with **Solved rate** where the metric is survey-solved based. True FCR remains separate and is shown only when opportunity/call grouping supports it.

## Backend and data contract changes

### 1. Public sanitized CSAT endpoint

`GET /api/csat/view` returns the dashboard-safe aggregate view. It removes sensitive nested fields such as reason summaries and consultant call caches.

### 2. Protected raw CSAT endpoint

`GET /api/csat/raw` returns full raw drilldown data only with a valid admin token.

### 3. CSAT source tracking

The backend tracks CSAT source state including:

- active source;
- whether Domo is configured;
- whether dataset ID is set;
- last successful load;
- latest error;
- refresh cadence.

This metadata is included in `/api/csat/status`.

### 4. Configurable teams

CSAT team filtering is configurable through `CSAT_TEAMS`. Leaving it blank includes all teams, which prevents future team additions from being silently dropped.

## Testing and QA changes

New coverage includes:

- public CSAT view strips sensitive call-level data;
- public CSAT view does not leak secret call text;
- CSAT status exposes source metadata without exposing raw payloads;
- protected raw CSAT still requires admin auth;
- protected refresh/cache/debug controls still require admin auth;
- existing CSAT aggregation tests pass with configurable teams.

## Remaining architecture recommendations

This pass substantially improves the current codebase, but the dashboard is still a large vanilla JS/FastAPI application. The next larger investment should be:

1. split `main.py` into routers, services, models, repositories, and security modules;
2. move frontend code into modules or a component framework;
3. add typed Pydantic response models for every endpoint;
4. add Playwright tests for page load, filters, chart clicks, modals, and protected states;
5. move cache/state out of `/tmp` into Redis, Postgres, Supabase, or object storage;
6. add true role-based access control instead of one admin token.
