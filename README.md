# CS Portal Dashboard

An internal analytics dashboard for the hear.com Customer Support team. One FastAPI backend serves three single-page dashboards (plain HTML/CSS/JS + Chart.js — no frontend build step) that pull data from four external systems: **Cockpit CMS**, **Domo**, **Supabase**, and the **Starter Guide Service**.

This doc is written for whoever inherits this project next. If something below turns out to be wrong or stale, please fix it here rather than starting a new report file (see [Project history note](#project-history-note)).

---

## 1. The three dashboards

| URL | Page | What it shows | Primary data source |
|---|---|---|---|
| `/` | Portal Activity | Logins, article/video views, searches, sessions, content health, engagement trend, conversion rate — one long scroll, not tabbed | Cockpit CMS events (+ Supabase for long-term session history) |
| `/csat` | Call Quality & CSAT | Ratings, solved rate, true FCR, Voice AI lens, per-team/consultant breakdowns, call-reason drivers, call-level drilldowns | Domo (CSAT survey dataset) |
| `/starter-guides` | Starter Guide | 28-day hearing-aid onboarding programme: **Metrics** (engagement, drop-off funnel, cohort filter), **Customer Overview** (who's on an active journey), **Journey Details** (per-customer check-in history) | Starter Guide Service API (+ CMS for event counts) |

All three share one header component, one CSS file (`portal-overrides.css`), and one JS file (`portal-system.js`) — see [§5 Shared UI layer](#5-shared-ui-layer-chart-toolkit).

---

## 2. How a request flows through the system

```
Browser  ──►  FastAPI (main.py, on Render)  ──►  Cockpit CMS   (event analytics)
                                              ──►  Domo          (CSAT survey data)
                                              ──►  Supabase      (long-term event storage)
                                              ──►  Starter Guide Service (journeys API)
```

The backend is a **caching proxy + aggregator**, not a pass-through:

- Every page load costs **zero** external API calls — the dashboard reads from an in-memory cache (refreshed on a timer), so it stays fast even if an upstream is slow.
- A background loop refreshes the CMS/Domo cache **every 2 hours** automatically (`REFRESH_SEC`), independent of anyone visiting the site.
- The cache also survives a Render restart — it's written to disk (`/tmp/cs_portal_cache.json`) and reloaded on boot.
- If CMS is down, Portal Activity can partially rebuild itself from Supabase's stored history instead of failing.
- The Starter Guide tabs are more "live" — the Metrics tab calls the Starter Guide Service directly on each load (it pages through all active customers concurrently, ~5s), because that data isn't cached long-term yet.

---

## 3. The four external services (and why each exists)

### Render — hosting
- **Where:** [Render dashboard](https://dashboard.render.com/web/srv-d886qbp9rddc73b7d2n0)
- **What:** runs `main.py` as a single web service. Build = `pip install -r requirements.txt`. Start = `uvicorn main:app --host 0.0.0.0 --port $PORT` (see `render.yaml` / `Procfile` — both say the same thing; `Procfile` is a generic fallback for non-Render buildpacks, `render.yaml` is what Render actually reads).
- **Deploys automatically** (`autoDeploy: true`) on every push to `main` on GitHub. There is no separate staging environment or CI pipeline — pushing to `main` **is** the deploy.
- **Environment variables live here**, not in the repo. See [§4](#4-environment-variables) for the full list — go to the Render service → Environment tab to view/edit them.
- Render's free/low tiers spin down web services after inactivity. The code has two features that exist specifically to fight that:
  - `GET /health` (also responds to `HEAD`) is a cheap, no-auth endpoint that UptimeRobot polls.
  - A background task pings Supabase every 6 hours so *its* free tier doesn't pause either.

### GitHub — source of truth
- The repo `sharathgajawada-lab/CS_Portal_Dashboard`. `main` is the only branch that matters for deployment — Render watches it and redeploys on every push. There's no PR/review gate configured; treat `main` as production.

### Supabase — long-term event storage
- **Where:** [Supabase dashboard](https://supabase.com/dashboard/project/magdprmdaxgcwowgmzoy)
- **What it stores** — two tables, auto-created on startup if missing (`_sb_ensure_tables()`):
  - `cs_user_timelines` — one row per user event pulled from Cockpit CMS (`user_id`, `project`, `event_type`, `item_id`, `session_id`, `ts`, `event_date`, `properties` JSONB).
  - `cs_user_fetch_log` — one row per user (`user_id`, `last_fetched`, `event_count`) tracking when that user's timeline was last pulled, so the refresh loop spreads the "fetch every known user" workload across cycles instead of doing it all at once.
- **Why it exists:** Cockpit CMS metrics only return recent/sampled data. Supabase is the durable backing store that lets Portal Activity answer "session analytics over a custom date range" (`/api/sessions/full`) and survive a CMS outage.
- **Optional by design:** if `SUPABASE_URL`/`SUPABASE_KEY` are unset, the dashboard runs fine on cache-only data — you'll see `[supabase] not configured — using local timeline only` in the logs.

### UptimeRobot — monitoring
- **Where:** [UptimeRobot monitor](https://dashboard.uptimerobot.com/monitors/803158856)
- **What it does:** polls `GET/HEAD /health` on the Render URL on an interval. Two jobs at once: (1) alerts if the dashboard goes down, (2) keeps the Render service from idling out on a free/low tier.
- `/health` reports cache freshness, whether a refresh is currently running, and whether the CMS API key / admin token are configured — useful as a first stop when something looks wrong.

### Cockpit CMS — event analytics
- **Base URL:** `CMS_BASE_URL` (default `https://cms.audibene.net/api/metrics`)
- **Auth:** `CMS_API_KEY`, sent as both `api-key` and `x-api-key` headers.
- **What:** time-series and top-N event counts, and per-user event timelines, queried per "project" (`cs-portal-auth-events`, `cs-portal-content-events`, `cs-portal-feedback-events`, `cs-portal-items-events`, `cs-portal-scheduling-events`, and `starter-guide-events`). Drives Portal Activity and the Starter Guide event-count charts.

### Domo — CSAT survey data
- **Auth:** OAuth2 client-credentials — `DOMO_CLIENT_ID` + `DOMO_CLIENT_SECRET` → bearer token, against `DOMO_API_BASE`.
- **What:** exports the CSAT dataset (`DOMO_DATASET_ID`) as CSV, which the backend parses into an in-memory + on-disk (`data/csat_index.json`) index. This is the **only** source for `/csat` — there's no separate manual-upload path in normal use (see [§6](#6-csat-is-domo-first)).

### Starter Guide Service — onboarding journeys
- **Base URL:** `STARTER_GUIDE_BASE_URL` (default `https://starter-guide-service.audibene.net`)
- **Auth:** `STARTER_GUIDE_API_TOKEN` (bearer) for `/api/v1/*`; `/public/v1/*` needs no token.
- **What:** a 28-day, 7-check-in SMS onboarding programme for new hearing-aid wearers (Day 1, 4, 6, 14, 18, 24, 28). The dashboard proxies this service under `/api/sg/*` and separately computes engagement/drop-off metrics by aggregating every active customer's journey data live. Full domain background: [Confluence — CS Starter Guide](https://audibene.atlassian.net/wiki/spaces/CST/pages/6389269096/CS+Starter+Guide).
- **Known gap:** SMS delivery data (sent / received / opted-out / not-received) is **not** exposed by this API — it lives in a separate messaging provider. `GET /api/sg/metrics/delivery` is a placeholder today (`available: false`); wiring it once that endpoint is known is a self-contained change (see the docstring on that function in `main.py`).

---

## 4. Environment variables

Set these in **Render → your service → Environment**, not in the repo. Copy `render.yaml`'s list as a starting point.

### Required for full functionality
| Variable | Powers | Notes |
|---|---|---|
| `CMS_API_KEY` | Cockpit CMS calls (Portal Activity, Starter Guide event counts) | Without it, those charts stay empty |
| `DOMO_CLIENT_ID`, `DOMO_CLIENT_SECRET`, `DOMO_DATASET_ID` | CSAT page | All three needed together; without them the CSAT page shows "no data" |
| `DASHBOARD_ADMIN_TOKEN` | Protects break-glass/debug endpoints (see [§7](#7-security-model)) | Pick a long random string |

### Optional
| Variable | Default | Purpose |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | unset | Enables long-term session storage; app works without it |
| `STARTER_GUIDE_BASE_URL` | `https://starter-guide-service.audibene.net` | Only change if the service moves |
| `STARTER_GUIDE_API_TOKEN` | unset | Enables authenticated Starter Guide endpoints (full answers); public endpoints work without it |
| `STARTER_GUIDE_CMS_PROJECT` | `starter-guide-events` | CMS project name for Starter Guide event charts |
| `CMS_BASE_URL` | `https://cms.audibene.net/api/metrics` | Only change if CMS moves |
| `DOMO_API_BASE` | `https://api.domo.com` | Only change if Domo's API host changes |
| `ALLOWED_ORIGINS` | unset (no CORS) | Comma-separated origins if you ever call this API from another domain. **If unset, no CORS middleware is added at all** — fine for same-origin browser use |
| `DATA_START` | `2026-04-24` | Informational "earliest data" date shown in the UI |
| `APP_ENV` | `development` | Set to `production` on Render |
| `DASHBOARD_VERSION` | `2026.06-secure-metrics` | Cosmetic, shown in `/health` and `/api/config` |
| `CSAT_TEAMS` | unset (all teams) | Comma-separated allow-list to filter which CS teams appear in CSAT |
| `CSAT_INCLUDE_BOT_CONSULTANTS` | `false` | Read into CSAT quality metadata; **note:** no filtering logic keys off it in the current code — treat as a stub until someone wires it up |
| `CSAT_RAW_PUBLIC` | `true` | Informational flag surfaced in `/api/config`; does **not** itself gate `/api/csat/raw` (see [§7](#7-security-model) for the real story) |
| `CSAT_REFRESH_REQUIRES_ADMIN` | `false` | If `true`, `/api/refresh/csat` requires the admin token |
| `CSAT_UPLOAD_PASSWORD` | unset | Legacy alias for `DASHBOARD_ADMIN_TOKEN`, used only if the latter is unset |
| `DISABLE_ADMIN_AUTH` | `false` | If `true`, **disables all admin-token checks**. Local dev only — never set this on Render |

---

## 5. Shared UI layer (chart toolkit)

All three pages load the same two files:
- `portal-overrides.css`
- `portal-system.js`

This layer adds, to every Chart.js canvas on every page automatically: a per-chart toolbar (Insight / Data table / CSV / PNG / Expand), a full-screen "chart studio" modal, dark/compact/presentation modes, and a command palette (`Ctrl/Cmd+K`-style). If a chart looks wrong (wrong scale, blank after closing Expand, hover not working, PNG export failing), the bug almost certainly lives in `portal-system.js`, not in the page-specific script — these were exactly the class of bugs fixed in the commits from `2d76e29` back through `bf7c78d` (see git log for the root-cause writeups; the short version: never mutate Chart.js's live `chart.options` object directly — mutate `chart.config.options` instead, or you corrupt the chart's internal state).

There used to be a second, older copy of this layer at `dashboard_ux.css`/`dashboard_ux.js`. Those files were dead code — `main.py`'s asset route always preferred `portal-overrides.css`/`portal-system.js` first — and have been deleted. `/assets/dashboard_ux.css` and `/assets/dashboard_ux.js` still resolve correctly as URLs (the backend serves the real files under those legacy paths for backward compatibility), it's only the redundant on-disk copies that are gone.

---

## 6. CSAT is Domo-first

There is no user-facing upload flow. The flow is:

```
Domo dataset → backend pulls + indexes it → /csat renders it
```

`POST /upload/csat` still exists as an **admin-token-protected break-glass path** (manual CSV/Excel backfill if Domo is ever unreachable for a while) — it is intentionally not wired into any button in the UI.

- `GET /api/csat/view` — sanitized aggregate (call summaries/IDs stripped)
- `GET /api/csat/raw` — full index including call-level fields; this is what the `/csat` page actually renders, by product decision, so there's no "unlock" step for internal users

---

## 7. Security model

- **Admin token** (`DASHBOARD_ADMIN_TOKEN`): required via `X-Admin-Token: <token>` or `Authorization: Bearer <token>` header, checked with a constant-time comparison. Protects: `POST /upload/csat`, `GET /api/csat` (legacy alias), `GET /api/refresh`, `GET /cache/clear`, and every `/debug/*` route.
- **`GET /api/csat/raw` is intentionally public, not admin-gated in code.** This is a deliberate product decision (no locked-drilldown UX for internal staff), documented in the route's own docstring — but it means access control for that endpoint is **network-level only** (i.e., don't expose this Render service to the public internet without adding a layer in front of it, like an SSO proxy or IP allowlist). `CSAT_RAW_PUBLIC` is metadata only; flipping it to `false` does not add enforcement by itself today.
- **`GET /health`** is public by design (UptimeRobot needs to hit it without a token).
- **CORS** is off unless you set `ALLOWED_ORIGINS`.
- **No rate limiting** exists anywhere in the app.
- Full checklist: [SECURITY.md](SECURITY.md).

---

## 8. Local development

```bash
pip install -r requirements.txt

export CMS_API_KEY=...
export DASHBOARD_ADMIN_TOKEN=choose-a-long-random-value
export DOMO_CLIENT_ID=...
export DOMO_CLIENT_SECRET=...
export DOMO_DATASET_ID=...
# optional
export SUPABASE_URL=...
export SUPABASE_KEY=...
export STARTER_GUIDE_API_TOKEN=...

uvicorn main:app --reload
```

Open `http://localhost:8000`. For quick local experiments only, `DISABLE_ADMIN_AUTH=true` skips the admin-token check — never set this in a deployed environment.

### Running tests

Test dependencies (`pytest`, `playwright`, `beautifulsoup4`) are **not** in `requirements.txt` — that file is production-only, matching what Render actually installs. Install them separately for local test runs:

```bash
pip install pytest playwright beautifulsoup4
playwright install chromium   # only needed for ui_click_smoke.py

pytest -q                     # test_main.py + test_ui_experience.py
python3 -m py_compile main.py
python3 ui_click_smoke.py     # headless browser click-through smoke test
```

- `test_main.py` — backend unit tests (CSAT indexing, caching, API endpoint responses, config).
- `test_ui_experience.py` — static HTML/asset contract tests (asset linking, required UI markers).
- `ui_click_smoke.py` — renders the real HTML with API calls stubbed, clicks every chart's Expand action, checks nothing errors.

**Known issue:** as of this writing, 5 tests in `test_ui_experience.py` fail — 3 assert exact leftover marketing-comment strings (e.g. `"v7.4 axis visibility"`) that no longer exist in `portal-system.js`/`portal-overrides.css` after later fixes, and 2 fail only on Windows because they call `.read_text()` on `index.html` without `encoding="utf-8"` (the file contains a non-ASCII character; Windows' default codec can't decode it, Linux/Render is unaffected). All are test-file bugs, not application bugs — `main.py`'s own file reads were already fixed to specify UTF-8 explicitly. Worth cleaning up, but out of scope for this pass.

---

## 9. Project structure

```
main.py                  FastAPI backend — routes, caching, all external API integration
app.py                   Intentionally empty (Render/uvicorn entry point convention; real app is main.py)
index.html               Portal Activity page (self-contained HTML/CSS/JS)
csat.html                Call Quality & CSAT page
starter_guides.html      Starter Guide page (Metrics / Customer Overview / Journey Details)
portal-overrides.css     Shared chart toolkit + dashboard styling (see §5)
portal-system.js         Shared chart toolkit + dashboard behavior (see §5)
sw.js                    Service worker (asset caching in the browser)
metrics_registry.json    Human-authored metric definitions for Portal Activity metrics.
                          Documentation only — main.py does not read this file, so keeping
                          it in sync with the code is a manual habit, not enforced.
requirements.txt         Production Python dependencies (what Render installs)
runtime.txt              Python version pin (3.11.11)
Procfile                 Generic process declaration (Heroku-style buildpacks)
render.yaml              Render-specific service definition (the one Render actually reads)
test_main.py             Backend unit tests
test_ui_experience.py    Frontend/asset contract tests
ui_click_smoke.py        Headless browser smoke test
SECURITY.md              Security checklist
README.md                This file
```

---

## 10. Project history note

Earlier versions of this repo accumulated a long series of one-off AI-assisted session reports at the root (critique writeups, "V7_x" fix reports, packaging instructions for a manual zip-upload workflow). Those were removed because the repo is now driven by a normal `git push` → Render auto-deploy flow, and none of them were referenced by any code or test. If you want the history behind a specific past fix, `git log` is the source of truth going forward — please don't recreate standalone report files; put context in commit messages instead.
