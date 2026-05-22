from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import httpx
import os
import asyncio
import time
import json
import gzip
from typing import Optional
from contextlib import asynccontextmanager

# ─── Config ───────────────────────────────────────────────────────────────────
CMS_BASE   = "https://cms.audibene.net/api/metrics"
API_KEY    = os.environ.get("CMS_API_KEY", "")
CACHE_TTL  = 300          # 5 min fresh cache
STALE_TTL  = 3600         # 1 hr stale-while-revalidate
PREFETCH_INTERVAL = 300   # background refresh every 5 min

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
]

# ─── In-memory cache ──────────────────────────────────────────────────────────
cache      = {}   # key -> {"ts": float, "data": dict}
cache_lock = asyncio.Lock()

def cache_get(key):
    entry = cache.get(key)
    if not entry:
        return None, False
    age = time.time() - entry["ts"]
    if age < CACHE_TTL:
        return entry["data"], True   # fresh
    if age < STALE_TTL:
        return entry["data"], False  # stale but usable
    return None, False

def cache_set(key, data):
    cache[key] = {"ts": time.time(), "data": data}

# ─── CMS fetch with retry + exponential backoff ───────────────────────────────
semaphore = asyncio.Semaphore(3)   # max 3 concurrent CMS requests

async def cms_fetch(project: str, event: str, since: str = "180d",
                    from_date: str = None, to_date: str = None) -> dict:
    params = {"event": event, "bucket": "day"}
    if since and not from_date:
        params["since"] = since
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    url = f"{CMS_BASE}/{project}/query/time-series"

    async with semaphore:
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(url, params=params,
                                         headers={"API-Key": API_KEY,
                                                  "Accept": "application/json"})
                    if r.status_code in (429, 502, 503, 504):
                        wait = 2 ** attempt
                        print(f"  CMS {r.status_code} for {event}, retry in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    if r.status_code != 200:
                        return {"series": [], "error": r.status_code}
                    text = r.text.strip()
                    return json.loads(text) if text else {"series": []}
            except Exception as e:
                await asyncio.sleep(2 ** attempt)
        return {"series": [], "error": "max retries exceeded"}

# ─── Batch prefetch ───────────────────────────────────────────────────────────
async def prefetch_all():
    print("Prefetching 180d batch...")
    result = {}
    for e in EVENTS:
        data = await cms_fetch(e["project"], e["key"], since="180d")
        result[e["key"]] = data
        await asyncio.sleep(0.3)   # gentle pacing
    async with cache_lock:
        cache_set("batch:180d", result)
    print(f"Batch cached — {len(result)} events")
    return result

async def background_prefetch():
    """Background task: refresh cache every 5 minutes."""
    await asyncio.sleep(5)          # wait for server to fully start
    while True:
        try:
            await prefetch_all()
        except Exception as e:
            print(f"Prefetch error: {e}")
        await asyncio.sleep(PREFETCH_INTERVAL)

# ─── Lifespan (startup/shutdown) ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_prefetch())
    yield
    task.cancel()

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)   # gzip all responses >500 bytes
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ─── Batch endpoint ───────────────────────────────────────────────────────────
@app.get("/api/metrics/batch")
async def batch_metrics():
    """Returns all 9 events for 180 days in one gzipped response."""
    data, fresh = cache_get("batch:180d")

    if data is None:
        # Cache miss — fetch now
        print("Cache miss, fetching batch...")
        data = await prefetch_all()
        fresh = True

    if not fresh:
        # Stale — return stale data immediately, refresh in background
        asyncio.create_task(prefetch_all())
        print("Serving stale cache, refreshing in background")

    return data

# ─── Single metric endpoint (kept for compatibility) ──────────────────────────
@app.get("/api/metrics/{project}/query/time-series")
async def proxy_metrics(
    project: str,
    event: str = Query(...),
    since: Optional[str] = None,
    bucket: str = "day",
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    key = f"{project}:{event}:{since}:{from_date}:{to_date}"
    data, fresh = cache_get(key)
    if fresh:
        return data

    result = await cms_fetch(project, event, since, from_date, to_date)
    cache_set(key, result)
    return result

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    batch, fresh = cache_get("batch:180d")
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "batch_cached": batch is not None,
        "batch_fresh": fresh,
        "total_cache_entries": len(cache),
    }

@app.get("/cache/clear")
async def clear_cache():
    cache.clear()
    asyncio.create_task(prefetch_all())
    return {"status": "cleared, prefetching..."}

# ─── Dashboard HTML ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()
