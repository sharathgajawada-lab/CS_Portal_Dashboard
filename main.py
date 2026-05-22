from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import asyncio
import time
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CMS_BASE = "https://cms.audibene.net/api/metrics"
API_KEY = os.environ.get("CMS_API_KEY", "")
CACHE_TTL = 300  # 5 minutes cache

# In-memory cache: key -> (timestamp, data)
cache = {}

def cache_key(project, event, since, from_date, to_date):
    return f"{project}:{event}:{since}:{from_date}:{to_date}"

def get_cached(key):
    if key in cache:
        ts, data = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None

def set_cached(key, data):
    cache[key] = (time.time(), data)

async def fetch_with_retry(url, params, retries=3, delay=1.0):
    """Fetch from CMS with retry logic on 503/502 errors."""
    last_error = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    url,
                    params=params,
                    headers={"API-Key": API_KEY, "Accept": "application/json"}
                )
                print(f"CMS [{attempt+1}/{retries}]: {r.status_code} {url.split('/')[-3]}")

                if r.status_code in (502, 503, 504):
                    last_error = f"CMS returned {r.status_code}"
                    await asyncio.sleep(delay * (attempt + 1))
                    continue

                if r.status_code != 200:
                    raise HTTPException(status_code=r.status_code, detail=f"CMS error {r.status_code}")

                text = r.text.strip()
                if not text:
                    return {"series": []}

                return r.json()

        except httpx.TimeoutException:
            last_error = "CMS request timed out"
            await asyncio.sleep(delay)
        except httpx.RequestError as e:
            last_error = f"CMS connection error: {str(e)}"
            await asyncio.sleep(delay)

    raise HTTPException(status_code=503, detail=f"CMS unavailable after {retries} retries: {last_error}")


@app.get("/api/metrics/{project}/query/time-series")
async def proxy_metrics(
    project: str,
    event: str = Query(...),
    since: Optional[str] = None,
    bucket: str = "day",
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    # Check cache first
    key = cache_key(project, event, since, from_date, to_date)
    cached = get_cached(key)
    if cached is not None:
        print(f"Cache hit: {key}")
        return cached

    # Build params
    params = {"event": event, "bucket": bucket}
    if since:
        params["since"] = since
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    url = f"{CMS_BASE}/{project}/query/time-series"

    try:
        data = await fetch_with_retry(url, params)
        set_cached(key, data)
        return data
    except HTTPException as e:
        # If we have stale cache, return it with a warning rather than failing
        if key in cache:
            print(f"CMS down, serving stale cache for {key}")
            _, stale_data = cache[key]
            return stale_data
        raise e


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "cache_entries": len(cache),
        "cached_keys": list(cache.keys())[:5]
    }

@app.get("/cache/clear")
async def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()
