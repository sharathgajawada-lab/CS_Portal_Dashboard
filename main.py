from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import httpx
import os
import asyncio
import time
import json
from typing import Optional
from datetime import datetime, timezone
from collections import defaultdict
from contextlib import asynccontextmanager

# ─── Config ───────────────────────────────────────────────────────────────────
CMS_BASE          = "https://cms.audibene.net/api/metrics"
API_KEY           = os.environ.get("CMS_API_KEY", "")
CACHE_TTL         = 300    # 5 min fresh
STALE_TTL         = 3600   # 1 hr stale
PREFETCH_INTERVAL = 300    # background refresh every 5 min
DATA_START_DATE   = "2026-04-24"  # first day CMS tracking started

EVENTS = [
    {"key": "profile.viewed",         "project": "cs-portal-profile-events"},
    {"key": "auth.login",             "project": "cs-portal-auth-events"},
    {"key": "article.viewed",         "project": "cs-portal-content-events"},
    {"key": "search.performed",       "project": "cs-portal-content-events"},
    {"key": "video.watched",          "project": "cs-portal-content-events"},
    {"key": "article.feedback",       "project": "cs-portal-feedback-events"},
    {"key": "order_supplies.visited", "project": "cs-portal-items-events"},
    {"key": "auth.logout",            "project": "cs-portal-auth-events"},
    {"key": "scheduling.started",     "project": "cs-portal-scheduling-events"},
    {"key": "chat.started",           "project": "cs-portal-chat-events"},
    {"key": "chat.message_sent",      "project": "cs-portal-chat-events"},
    {"key": "asset.download",         "project": "cs-portal-content-events"},
    {"key": "category.viewed",        "project": "cs-portal-content-events"},
    {"key": "returns.viewed",         "project": "cs-portal-items-events"},
    {"key": "scheduling.completed",   "project": "cs-portal-scheduling-events"},
]

# ─── Cache ────────────────────────────────────────────────────────────────────
cache = {}
cms_status = {"healthy": True, "last_checked": None, "last_error": None}

def cache_get(key):
    entry = cache.get(key)
    if not entry:
        return None, False
    age = time.time() - entry["ts"]
    if age < CACHE_TTL:
        return entry["data"], True
    if age < STALE_TTL:
        return entry["data"], False
    return None, False

def cache_set(key, data):
    cache[key] = {"ts": time.time(), "data": data}

# ─── Aggregation ──────────────────────────────────────────────────────────────
def aggregate_to_daily(series: list, event_key: str = "") -> list:
    daily = defaultdict(int)
    for point in series:
        count = int(point.get("count", 0) or 0)
        if "ts" in point:
            try:
                dt = datetime.utcfromtimestamp(int(point["ts"]) / 1000)
                day_str = dt.strftime("%Y-%m-%d")
            except:
                continue
        elif "date" in point:
            day_str = str(point["date"])[:10]
        else:
            continue
        daily[day_str] += count
    if not daily:
        return []
    return [{"date": d, "count": daily[d], "event": event_key}
            for d in sorted(daily.keys())]

# ─── CMS Fetch (correct OpenAPI params: since/until not from/to) ──────────────
semaphore = asyncio.Semaphore(5)   # 5 concurrent CMS requests for speed

async def cms_fetch(project: str, event: str,
                    since: str = None, until: str = None) -> dict:
    """
    Fetch time-series from CMS.
    - since: lower bound (e.g. "2026-04-24" ISO date or "-30d" relative)
    - until: upper bound — NOTE: causes empty results, do not use
    """
    params = {"event": event, "bucket": "day"}
    if since:
        params["since"] = since
    # DO NOT pass until — CMS returns empty series when until is set

    url = f"{CMS_BASE}/{project}/query/time-series"

    async with semaphore:
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(
                        url, params=params,
                        headers={"api-key": API_KEY, "Accept": "application/json"}
                    )
                    cms_status["last_checked"] = datetime.utcnow().isoformat()

                    if r.status_code in (429, 502, 503, 504):
                        cms_status["healthy"] = False
                        cms_status["last_error"] = f"HTTP {r.status_code}"
                        await asyncio.sleep(2 ** attempt)
                        continue

                    if r.status_code != 200:
                        cms_status["healthy"] = False
                        cms_status["last_error"] = f"HTTP {r.status_code}"
                        return {"series": []}

                    text = r.text.strip()
                    if not text:
                        return {"series": []}

                    cms_status["healthy"] = True
                    cms_status["last_error"] = None
                    return json.loads(text)

            except Exception as e:
                cms_status["healthy"] = False
                cms_status["last_error"] = str(e)
                await asyncio.sleep(2 ** attempt)

        return {"series": []}

async def check_cms_health():
    """Quick health check against CMS."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://cms.audibene.net/api/metrics/cs-portal-profile-events/query/exists",
                params={"event": "profile.viewed"},
                headers={"api-key": API_KEY}
            )
            cms_status["healthy"] = r.status_code == 200
            cms_status["last_checked"] = datetime.utcnow().isoformat()
            if r.status_code != 200:
                cms_status["last_error"] = f"HTTP {r.status_code}"
    except Exception as e:
        cms_status["healthy"] = False
        cms_status["last_error"] = str(e)

# ─── Prefetch ─────────────────────────────────────────────────────────────────
async def prefetch_all():
    print("Prefetching all events in parallel...")
    await check_cms_health()
    if not cms_status["healthy"]:
        print(f"CMS unhealthy, skipping prefetch: {cms_status['last_error']}")
        return cache.get("batch:all", {}).get("data", {})

    today = datetime.utcnow().strftime("%Y-%m-%d")  # kept for logging

    # Fetch all events in parallel — semaphore limits concurrent CMS calls
    async def fetch_one(e):
        raw = await cms_fetch(e["project"], e["key"],
                              since=DATA_START_DATE)
        aggregated = aggregate_to_daily(raw.get("series", []), e["key"])
        return e["key"], {"series": aggregated}

    results = await asyncio.gather(*[fetch_one(e) for e in EVENTS],
                                   return_exceptions=True)

    result = {}
    for r in results:
        if isinstance(r, Exception):
            print(f"Fetch error: {r}")
            continue
        key, data = r
        result[key] = data

    cache_set("batch:all", result)
    total_rows = sum(len(v["series"]) for v in result.values())
    print(f"Batch cached — {len(result)} events, {total_rows} total rows")
    return result

async def background_prefetch():
    await asyncio.sleep(3)
    while True:
        try:
            await prefetch_all()
        except Exception as e:
            print(f"Prefetch error: {e}")
        await asyncio.sleep(PREFETCH_INTERVAL)

# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_prefetch())
    yield
    task.cancel()

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan, title="CS Portal Analytics")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ─── Batch endpoint ───────────────────────────────────────────────────────────
@app.get("/api/metrics/batch")
async def batch_metrics(request: Request):
    # Clear cache on hard reload (Cache-Control: no-cache)
    cache_control = request.headers.get("cache-control", "")
    pragma = request.headers.get("pragma", "")
    if "no-cache" in cache_control or "no-cache" in pragma:
        cache.pop("batch:all", None)

    data, fresh = cache_get("batch:all")
    if data is None:
        data = await prefetch_all()
    elif not fresh:
        asyncio.create_task(prefetch_all())
    return data

# ─── CMS Status endpoint ──────────────────────────────────────────────────────
@app.get("/api/cms-status")
async def get_cms_status():
    await check_cms_health()
    return cms_status

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    data, fresh = cache_get("batch:all")
    total_rows = 0
    if data:
        total_rows = sum(len(v.get("series", [])) for v in data.values())
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "batch_cached": data is not None,
        "batch_fresh": fresh,
        "total_rows": total_rows,
        "data_start_date": DATA_START_DATE,
        "cache_entries": len(cache),
        "cms": cms_status,
    }

@app.get("/cache/clear")
async def clear_cache():
    cache.clear()
    asyncio.create_task(prefetch_all())
    return {"status": "cleared, client reset, prefetching..."}

# ─── Dashboard ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f:
        return Response(content=f.read(), media_type="application/javascript")
