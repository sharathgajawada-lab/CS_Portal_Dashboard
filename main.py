from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import httpx
import os
import asyncio
import time
import json
from typing import Optional
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager

# ─── Config ───────────────────────────────────────────────────────────────────
CMS_BASE          = "https://cms.audibene.net/api/metrics"
API_KEY           = os.environ.get("CMS_API_KEY", "")
CACHE_TTL         = 300
STALE_TTL         = 3600
PREFETCH_INTERVAL = 300
DATA_START_DATE   = "2026-04-24"

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

# ─── CMS HTTP helpers ─────────────────────────────────────────────────────────
HEADERS = {"api-key": API_KEY, "Accept": "application/json"}
semaphore = asyncio.Semaphore(5)

async def cms_get(url: str, params: dict) -> dict:
    """Raw GET with retry. Returns parsed JSON or {}."""
    async with semaphore:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(url, params=params, headers=HEADERS)
                    cms_status["last_checked"] = datetime.utcnow().isoformat()
                    if r.status_code in (429, 502, 503, 504):
                        cms_status["healthy"] = False
                        cms_status["last_error"] = f"HTTP {r.status_code}"
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if r.status_code != 200:
                        cms_status["healthy"] = False
                        cms_status["last_error"] = f"HTTP {r.status_code}"
                        return {}
                    text = r.text.strip()
                    if not text:
                        return {}
                    cms_status["healthy"] = True
                    cms_status["last_error"] = None
                    return json.loads(text)
            except Exception as e:
                cms_status["healthy"] = False
                cms_status["last_error"] = str(e)
                await asyncio.sleep(2 ** attempt)
        return {}

async def fetch_time_series(project: str, event: str, since: str) -> list:
    """Returns aggregated daily series. since=DATA_START_DATE for all history."""
    data = await cms_get(
        f"{CMS_BASE}/{project}/query/time-series",
        {"event": event, "since": since, "bucket": "day"}
    )
    return aggregate_to_daily(data.get("series", []), event)

async def fetch_unique_count(project: str, event: str, since: str = "-30d") -> int:
    """Returns unique user count for an event."""
    data = await cms_get(
        f"{CMS_BASE}/{project}/query/unique-count",
        {"event": event, "since": since, "by": "userId"}
    )
    return data.get("count", 0)

async def fetch_top_articles(since: str = "-30d", n: int = 10) -> list:
    """Returns top N most viewed articles."""
    data = await cms_get(
        f"{CMS_BASE}/cs-portal-content-events/query/top-n",
        {"event": "article.viewed", "groupBy": "itemId", "n": n, "since": since}
    )
    return data.get("top", [])

async def check_cms_health():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{CMS_BASE}/cs-portal-profile-events/query/exists",
                params={"event": "profile.viewed"},
                headers=HEADERS
            )
            cms_status["healthy"] = r.status_code == 200
            cms_status["last_checked"] = datetime.utcnow().isoformat()
            if r.status_code != 200:
                cms_status["last_error"] = f"HTTP {r.status_code}"
            else:
                cms_status["last_error"] = None
    except Exception as e:
        cms_status["healthy"] = False
        cms_status["last_error"] = str(e)

# ─── Prefetch ─────────────────────────────────────────────────────────────────
async def prefetch_all():
    print("Prefetching all events in parallel...")
    await check_cms_health()
    if not cms_status["healthy"]:
        print(f"CMS unhealthy: {cms_status['last_error']}")
        existing = cache.get("batch:all")
        return existing["data"] if existing else {}

    # Fetch time-series for all events in parallel
    async def fetch_one(e):
        series = await fetch_time_series(e["project"], e["key"], DATA_START_DATE)
        return e["key"], {"series": series}

    # Also fetch unique counts and top articles in parallel
    ts_tasks = [fetch_one(e) for e in EVENTS]
    unique_tasks = [fetch_unique_count(e["project"], e["key"], DATA_START_DATE)
                    for e in EVENTS]
    top_articles_task = fetch_top_articles(DATA_START_DATE, 10)

    ts_results, unique_results, top_articles = await asyncio.gather(
        asyncio.gather(*ts_tasks, return_exceptions=True),
        asyncio.gather(*unique_tasks, return_exceptions=True),
        top_articles_task
    )

    result = {}
    for i, r in enumerate(ts_results):
        if isinstance(r, Exception):
            print(f"Fetch error {EVENTS[i]['key']}: {r}")
            result[EVENTS[i]["key"]] = {"series": []}
            continue
        key, data = r
        unique = unique_results[i] if not isinstance(unique_results[i], Exception) else 0
        result[key] = {**data, "unique_users": unique}

    result["__top_articles__"] = top_articles
    result["__fetched_at__"] = datetime.utcnow().isoformat()

    cache_set("batch:all", result)
    total_rows = sum(len(v.get("series", [])) for k, v in result.items()
                     if not k.startswith("__"))
    print(f"Batch cached — {len(EVENTS)} events, {total_rows} rows, "
          f"{len(top_articles)} top articles")
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
    cc = request.headers.get("cache-control", "")
    pragma = request.headers.get("pragma", "")
    if "no-cache" in cc or "no-cache" in pragma:
        cache.pop("batch:all", None)

    data, fresh = cache_get("batch:all")
    if data is None:
        data = await prefetch_all()
    elif not fresh:
        asyncio.create_task(prefetch_all())
    return data

# ─── CMS status ───────────────────────────────────────────────────────────────
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
        total_rows = sum(len(v.get("series", [])) for k, v in data.items()
                         if not k.startswith("__"))
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
    return {"status": "cleared, prefetching..."}

# ─── Dashboard ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f:
        return Response(content=f.read(), media_type="application/javascript")
