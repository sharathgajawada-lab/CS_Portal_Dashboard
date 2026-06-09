# CS Portal Analytics Dashboard

FastAPI + Vanilla JS analytics dashboard for the hear.com Customer Support Portal.

**Live:** https://cs-portal-dashboard.onrender.com  
**Stack:** FastAPI (Python 3.11) · Vanilla JS · Chart.js 4.4 · Supabase · Render

---

## Pages

| URL | Description |
|-----|-------------|
| `/` | Portal Activity — logins, articles, searches, videos, sessions, heatmap |
| `/csat` | Call Quality & CSAT — ratings, FCR, team/consultant leaderboard |

---

## Setup

### Local development

```bash
git clone https://github.com/sharathgajawada-lab/cs-portal-dashboard
cd cs-portal-dashboard
pip install -r requirements.txt

export CMS_API_KEY=your-cms-api-key
export SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co   # optional
export SUPABASE_KEY=your-service-role-jwt              # optional
export CSAT_UPLOAD_PASSWORD=hearcom2024                # optional
# Domo datasets (pre-configured for Audibene; override if using different org)
export DOMO_DATASET_ID=bc75b418-1308-468d-8856-07488a4b57d8          # CSAT: CALL_ID, ratings, etc.
export DOMO_REASONS_DATASET_ID=b96f9a8a-8082-48f1-8f02-107197f177f4  # Call details: CALL_SID__C, reasons

uvicorn main:app --reload
# Open http://localhost:8000
```

### Deploy to Render

1. Push to GitHub
2. Create new **Web Service** on render.com → connect repo
3. Set environment variables (see `render.yaml` for the full list):
   - `CMS_API_KEY` — hear.com CMS API key (required)
   - `SUPABASE_URL` — Supabase project URL (optional, enables full session analytics)
   - `SUPABASE_KEY` — Supabase service_role JWT (optional)
   - `CSAT_UPLOAD_PASSWORD` — password for CSAT upload endpoint (default: `hearcom2024`)
   - `DOMO_DATASET_ID` — Domo dataset for CSAT data: CALL_ID, ratings, consultant info (pre-set: audibene)
   - `DOMO_REASONS_DATASET_ID` — Domo dataset for call details: CALL_SID__C, call reasons (pre-set: audibene)
4. Deploy

UptimeRobot pings `/health` every 5 minutes to keep the free-tier instance warm.

---

## Architecture

```
Browser
  ├── GET /              → index.html  (Portal Activity)
  ├── GET /csat          → csat.html   (Call Quality)
  ├── GET /api/metrics/batch   → KPI time-series (cached 2h)
  ├── GET /api/articles        → Article performance
  ├── GET /api/search          → Search query intelligence
  ├── GET /api/sessions/full   → Session analytics (Supabase)
  ├── GET /api/csat/raw        → Full CSAT index (client-side filtered)
  └── POST /upload/csat        → Upload new call_quality CSV/XLSX
```

**Cache:** 2h fresh / 24h stale. Persisted to `/tmp` so it survives Render restarts.  
**Refresh:** Background loop every 2 hours — 15 fixed CMS calls + up to 12 timeline calls.  
**CSAT data:** Loaded from `call_quality.csv` at startup; re-uploadable via dashboard UI.

---

## CSAT Data

`call_quality.csv` — required columns:

| Column | Type | Example |
|--------|------|---------|
| `RATING` | float | `4.0` |
| `CONSULTANT_ID` | string | `user_abc` |
| `CONSULTANT_NAME` | string | `Jane Smith` |
| `CONSULTANT_TEAM` | string | `Team Amplifiers` |
| `DATETIME` | datetime | `2026-05-01 09:30:00` |
| `SOLVED` | boolean | `True` |

Upload a new file via the **Update CSAT** button on the `/csat` page (password-protected).

---

## Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (UptimeRobot) |
| `GET` | `/api/refresh` | Trigger manual data refresh |
| `GET` | `/api/metrics/batch` | All KPI time-series |
| `GET` | `/api/articles` | Article performance + health scores |
| `GET` | `/api/sessions/full` | Full-team session analytics |
| `GET` | `/api/csat/raw` | CSAT index for client-side filtering |
| `POST` | `/upload/csat` | Upload new CSAT CSV/XLSX |
| `GET` | `/debug/csat` | Verify CSAT data loaded |
| `GET` | `/debug/cms` | Test CMS connectivity |

---

## CSAT views & per-call drill-down (Call ID → UCJ)

The `/csat` page has three synchronized subpages, switched via the Overview / By Team / By Member toggle:

- **Overview** — unscoped: all teams and consultants.
- **By Team** — a team selector at the top. Picking a team re-scopes the *entire page* (KPIs, rating distribution, CSAT trend, FCR trend, leaderboards) to that team, and shows a per-team breakdown with its consultants. The CS Teams comparison cards always stay unscoped so they keep comparing all teams.
- **By Member** — team filter + search + a consultant selector. Picking a consultant re-scopes the whole page to that member and renders an **individual-calls table**: each row shows the **Call ID** (a clickable deep link into the Unified Comm Journal, filtered to that call via `globalFilter`), `OPPORTUNITY_ID`, linked call reason (when configured), the date, the star rating, and solved/unsolved.

### Required CSV column

`call_quality.csv` now includes a `CALL_ID` column. It is captured by the startup CSV loader, the `.xlsx` parser, and the upload endpoint, and stored per-call under `days[date].cn[cid].c` as `{i: call_id, r: rating, s: solved, o: opp_id, rs: reason}`. Rows without a `CALL_ID` are still counted in the aggregates but produce no per-call row.

| Column | Type | Example |
|--------|------|---------|
| `CALL_ID` | string | `CAa9b4e29c07c03415649b92bdabb227e8` |

UCJ base: `https://comm-journal.audibene.net/table` — the Call ID is passed as the `globalFilter` query param.
