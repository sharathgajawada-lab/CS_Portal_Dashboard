from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import httpx
import os
import asyncio
import time
import json
import hashlib
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict
from contextlib import asynccontextmanager

# ─── Config ───────────────────────────────────────────────────────────────────
CMS_BASE          = "https://cms.audibene.net/api/metrics"
API_KEY           = os.environ.get("CMS_API_KEY", "")
CACHE_TTL         = 300   # 5 min — fresh
STALE_TTL         = 3600  # 1 hr  — stale-while-revalidate
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

def cache_get(key):
    entry = cache.get(key)
    if not entry:
        return None, False
    age = time.time() - entry["ts"]
    if age < CACHE_TTL:
        return entry["data"], True
    if age < STALE_TTL:
        return entry["data"], False   # stale but usable
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
    return [
        {"date": d, "count": daily[d], "event": event_key}
        for d in sorted(daily.keys())
    ]

# ─── Shared HTTP client (connection pooling) ──────────────────────────────────
_http_client: httpx.AsyncClient = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _http_client

# ─── CMS Fetch ────────────────────────────────────────────────────────────────
semaphore = asyncio.Semaphore(5)   # slightly higher — reusing connections is cheaper

async def cms_fetch(project: str, event: str, since: str = None,
                    from_date: str = None, to_date: str = None) -> dict:
    params = {"event": event, "bucket": "day"}
    if since:      params["since"]  = since
    if from_date:  params["from"]   = from_date
    if to_date:    params["to"]     = to_date

    url = f"{CMS_BASE}/{project}/query/time-series"
    client = get_http_client()

    async with semaphore:
        for attempt in range(4):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    return {"series": []}
                text = r.text.strip()
                if not text:
                    return {"series": []}
                return json.loads(text)
            except Exception:
                await asyncio.sleep(2 ** attempt)
        return {"series": []}

# ─── Prefetch ─────────────────────────────────────────────────────────────────
async def prefetch_all():
    print("Prefetching all events from start date...")
    result = {}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for e in EVENTS:
        raw = await cms_fetch(e["project"], e["key"],
                              from_date=DATA_START_DATE, to_date=today)
        aggregated = aggregate_to_daily(raw.get("series", []), e["key"])
        result[e["key"]] = {"series": aggregated}
        await asyncio.sleep(0.2)   # slightly tighter — connection reuse helps
    cache_set("batch:all", result)
    print(f"Batch cached — {len(result)} events")
    return result

async def background_prefetch():
    await asyncio.sleep(5)
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
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ─── Batch endpoint ───────────────────────────────────────────────────────────
@app.get("/api/metrics/batch")
async def batch_metrics(request_etag: Optional[str] = Query(None, alias="etag")):
    data, fresh = cache_get("batch:all")
    if data is None:
        data = await prefetch_all()
    elif not fresh:
        asyncio.create_task(prefetch_all())

    # Generate ETag from data hash so browser can skip re-parsing unchanged data
    payload   = json.dumps(data, separators=(",", ":"))
    etag_val  = hashlib.md5(payload.encode()).hexdigest()[:16]
    max_age   = CACHE_TTL if fresh else 60

    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Cache-Control": f"public, max-age={max_age}, stale-while-revalidate=3600",
            "ETag": f'"{etag_val}"',
            "Vary": "Accept-Encoding",
        },
    )

# ─── Domo endpoint ────────────────────────────────────────────────────────────
@app.get("/domo/{event_key}")
async def domo_endpoint(event_key: str):
    cache_key = f"domo:{event_key}"
    data, fresh = cache_get(cache_key)
    if fresh:
        return data

    event_conf = next((e for e in EVENTS if e["key"] == event_key), None)
    if not event_conf:
        raise HTTPException(status_code=404, detail=f"Unknown event: {event_key}")

    today = datetime.utcnow().strftime("%Y-%m-%d")
    raw   = await cms_fetch(event_conf["project"], event_key,
                            from_date=DATA_START_DATE, to_date=today)
    rows  = aggregate_to_daily(raw.get("series", []), event_key)
    result = {"rows": rows, "total": len(rows), "event": event_key}
    cache_set(cache_key, result)
    return result

# ─── Single metric proxy ──────────────────────────────────────────────────────
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

    effective_from = from_date or DATA_START_DATE
    effective_to   = to_date or datetime.utcnow().strftime("%Y-%m-%d")

    raw       = await cms_fetch(project, event, since=since,
                                from_date=effective_from if not since else None,
                                to_date=effective_to   if not since else None)
    aggregated = aggregate_to_daily(raw.get("series", []), event)
    result     = {"series": [{"date": r["date"], "count": r["count"]} for r in aggregated]}
    cache_set(key, result)
    return result

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    data, fresh = cache_get("batch:all")
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "batch_cached": data is not None,
        "batch_fresh": fresh,
        "data_start_date": DATA_START_DATE,
        "cache_entries": len(cache),
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
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"},
    )

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f:
        content = f.read()
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )
