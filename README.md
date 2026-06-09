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
export DOMO_CLIENT_ID=your-domo-client-id              # optional
export DOMO_CLIENT_SECRET=your-domo-client-secret      # optional
export DOMO_DATASET_ID=e1dc0e03-bb12-48fc-9908-937b7a5b91d2

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
    - `DOMO_CLIENT_ID` — Domo OAuth client id (optional, enables auto CSAT pull)
    - `DOMO_CLIENT_SECRET` — Domo OAuth client secret (optional)
    - `DOMO_DATASET_ID` — Domo dataset id (`e1dc0e03-bb12-48fc-9908-937b7a5b91d2`)
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
**CSAT data:** Loaded from Domo when configured (`DOMO_CLIENT_ID`, `DOMO_CLIENT_SECRET`, `DOMO_DATASET_ID`), otherwise from `call_quality.csv`; re-uploadable via dashboard UI.

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
- **By Member** — team filter + search + a consultant selector. Picking a consultant re-scopes the whole page to that member and renders an **individual-calls table**: each row shows the **Call ID** (a clickable deep link into the Unified Comm Journal, filtered to that call via `globalFilter`), the date, the star rating, and solved/unsolved.

### Required CSV column

`call_quality.csv` now includes a `CALL_ID` column. It is captured by the startup CSV loader, the `.xlsx` parser, and the upload endpoint, and stored per-call under `days[date].cn[cid].c` as `{i: call_id, r: rating, s: solved}`. Rows without a `CALL_ID` are still counted in the aggregates but produce no per-call row.

| Column | Type | Example |
|--------|------|---------|
| `CALL_ID` | string | `CAa9b4e29c07c03415649b92bdabb227e8` |

UCJ base: `https://comm-journal.audibene.net/table` — the Call ID is passed as the `globalFilter` query param.
