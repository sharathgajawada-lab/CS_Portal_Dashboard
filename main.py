from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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
CACHE_TTL = 300  # 5 minutes

cache = {}
request_semaphore = asyncio.Semaphore(2)  # Max 2 concurrent CMS requests

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

async def fetch_with_retry(url, params, retries=3):
    async with request_semaphore:  # Only 2 requests at a time
        await asyncio.sleep(0.3)  # 300ms delay between requests
        last_error = None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(
                        url,
                        params=params,
                        headers={"API-Key": API_KEY, "Accept": "application/json"}
                    )
                    print(f"CMS [{attempt+1}]: {r.status_code} for {params.get('event')}")

                    if r.status_code in (429, 502, 503, 504):
                        wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                        print(f"Retrying in {wait}s...")
                        await asyncio.sleep(wait)
                        last_error = f"HTTP {r.status_code}"
                        continue

                    if r.status_code != 200:
                        return {"series": [], "error": f"CMS returned {r.status_code}"}

                    text = r.text.strip()
                    if not text:
                        return {"series": []}

                    return r.json()

            except httpx.TimeoutException:
                last_error = "timeout"
                await asyncio.sleep(2 ** attempt)
            except httpx.RequestError as e:
                last_error = str(e)
                await asyncio.sleep(1)

        return {"series": [], "error": f"Failed after {retries} retries: {last_error}"}


@app.get("/api/metrics/{project}/query/time-series")
async def proxy_metrics(
    project: str,
    event: str = Query(...),
    since: Optional[str] = None,
    bucket: str = "day",
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    key = cache_key(project, event, since, from_date, to_date)
    cached = get_cached(key)
    if cached is not None:
        print(f"Cache hit: {event}")
        return cached

    params = {"event": event, "bucket": bucket}
    if since:
        params["since"] = since
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    url = f"{CMS_BASE}/{project}/query/time-series"
    data = await fetch_with_retry(url, params)

    # Cache even on success (stale cache fallback)
    if "series" in data:
        set_cached(key, data)
    elif key in cache:
        print(f"CMS failed, serving stale cache for {event}")
        _, stale = cache[key]
        return stale

    return data


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "cache_entries": len(cache),
    }

@app.get("/cache/clear")
async def clear_cache():
    cache.clear()
    return {"status": "cleared"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()
