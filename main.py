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
    # Session stats use 1-hour fresh TTL
    fresh_ttl = 3600 if key.startswith("session:") else CACHE_TTL
    stale_ttl = 7200 if key.startswith("session:") else STALE_TTL
    if age < fresh_ttl:
        return entry["data"], True
    if age < stale_ttl:
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

# ─── CMS top-n fetch ──────────────────────────────────────────────────────────
async def cms_fetch_topn(project: str, event: str, group_by: str = "itemId",
                         n: int = 20, from_date: str = None, to_date: str = None) -> list:
    """Calls /query/top-n and returns [{id, count}] list."""
    params = {"event": event, "groupBy": group_by, "n": n}
    if from_date: params["from"] = from_date
    if to_date:   params["to"]   = to_date

    url = f"{CMS_BASE}/{project}/query/top-n"
    client = get_http_client()

    async with semaphore:
        for attempt in range(4):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    return []
                text = r.text.strip()
                if not text:
                    return []
                data = json.loads(text)
                # CMS returns either a list or {"items": [...]}
                if isinstance(data, list):
                    return data
                return data.get("items", data.get("results", []))
            except Exception:
                await asyncio.sleep(2 ** attempt)
    return []


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

# ─── Articles endpoint ───────────────────────────────────────────────────────
@app.get("/api/articles")
async def articles_metrics(n: int = 20):
    """
    Returns top articles merging views (content-events) and feedback (feedback-events).
    [{id, label, views, feedback, feedbackRate}]
    """
    cache_key = f"articles:top:{n}"
    data, fresh = cache_get(cache_key)
    if fresh:
        return data
    if data is not None and not fresh:
        asyncio.create_task(_refresh_articles(n))
        return data

    result = await _fetch_articles(n)
    cache_set(cache_key, result)
    return result

async def _refresh_articles(n: int):
    result = await _fetch_articles(n)
    cache_set(f"articles:top:{n}", result)

async def _fetch_articles(n: int) -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    views_raw, feedback_raw = await asyncio.gather(
        cms_fetch_topn("cs-portal-content-events", "article.viewed",
                       group_by="itemId", n=n,
                       from_date=DATA_START_DATE, to_date=today),
        cms_fetch_topn("cs-portal-feedback-events", "article.feedback",
                       group_by="itemId", n=n,
                       from_date=DATA_START_DATE, to_date=today),
    )

    # Normalise: CMS may return {id/itemId/key, count/value}
    def normalise(rows):
        # Unwrap {"top": [...]} envelope from CMS top-n response
        if isinstance(rows, dict):
            rows = rows.get("top") or rows.get("items") or rows.get("results") or []
        out = {}
        for row in rows:
            aid = row.get("itemId") or row.get("id") or row.get("key") or row.get("item_id") or ""
            cnt = int(row.get("count") or row.get("value") or 0)
            if aid:
                out[aid] = cnt
        return out

    views    = normalise(views_raw)
    feedback = normalise(feedback_raw)

    # Merge — use views as the source of truth for article list
    articles = []
    for aid, view_count in sorted(views.items(), key=lambda x: -x[1]):
        fb = feedback.get(aid, 0)
        rate = round(fb / view_count * 100, 1) if view_count > 0 else 0
        label = aid.replace("-", " ").replace("_", " ").title()
        articles.append({
            "id":           aid,
            "label":        label,
            "views":        view_count,
            "feedback":     fb,
            "feedbackRate": rate,
        })

    return {"articles": articles, "fetched_at": datetime.utcnow().isoformat()}


# ─── Session stats endpoint ───────────────────────────────────────────────────
SESSION_DAYS = 30

ALL_PROJECTS = [
    "cs-portal-auth-events",
    "cs-portal-content-events",
    "cs-portal-feedback-events",
    "cs-portal-items-events",
    "cs-portal-profile-events",
    "cs-portal-scheduling-events",
]

ACTIVITY_MAP = {
    "auth.login":             "auth",
    "auth.logout":            "auth",
    "article.viewed":         "articles",
    "video.watched":          "videos",
    "search.performed":       "search",
    "article.feedback":       "feedback",
    "profile.viewed":         "profile",
    "order_supplies.visited": "supplies",
    "scheduling.started":     "scheduling",
    "scheduling.completed":   "scheduling",
    "chat.started":           "chat",
    "chat.message_sent":      "chat",
    "asset.download":         "content",
    "category.viewed":        "content",
    "returns.viewed":         "supplies",
}

ACTIVITY_LABELS = {
    "articles":   "Articles",
    "videos":     "Videos",
    "search":     "Search",
    "profile":    "Profile views",
    "scheduling": "Scheduling",
    "supplies":   "Supplies / Returns",
    "feedback":   "Feedback",
    "chat":       "Chat",
    "content":    "Other content",
    "auth":       "Auth",
}

# Separate semaphore for timeline calls — higher concurrency is fine
timeline_sem = asyncio.Semaphore(30)

async def _get_project_timeline(project: str, user_id: str) -> list:
    params = {"userId": user_id, "since": f"{SESSION_DAYS}d", "limit": 500}
    url    = f"{CMS_BASE}/{project}/query/user-timeline"
    client = get_http_client()
    async with timeline_sem:
        for attempt in range(3):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    return []
                text = r.text.strip()
                if not text:
                    return []
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                return data.get("events", data.get("items", []))
            except Exception:
                await asyncio.sleep(2 ** attempt)
    return []

async def _fetch_user_all_projects(user_id: str) -> list:
    """Fetch and merge events across all 6 projects for one user."""
    results = await asyncio.gather(
        *[_get_project_timeline(p, user_id) for p in ALL_PROJECTS],
        return_exceptions=True
    )
    events = []
    for r in results:
        if isinstance(r, list):
            events.extend(r)
    return sorted(events, key=lambda e: e.get("timestamp", 0))

def _compute_sessions(events: list) -> list:
    """
    Group events by session_id, compute duration and activity breakdown.
    Returns list of session dicts.
    """
    from collections import defaultdict
    sessions = defaultdict(list)
    for e in events:
        sid = e.get("session_id") or e.get("sessionId") or e.get("session") or "unknown"
        sessions[sid].append(e)

    result = []
    for sid, evts in sessions.items():
        if sid == "unknown":
            continue
        evts_sorted = sorted(evts, key=lambda e: e.get("timestamp", 0))
        ts_list     = [e.get("timestamp", 0) for e in evts_sorted if e.get("timestamp")]
        if len(ts_list) < 2:
            continue

        t_start    = min(ts_list)
        t_end      = max(ts_list)
        duration_s = (t_end - t_start) / 1000  # ms → seconds

        # Cap unrealistic sessions (> 8 hours = likely idle/forgotten tab)
        if duration_s > 28800:
            duration_s = 28800

        has_logout = any((e.get("event_type") or e.get("eventType","")) == "auth.logout" for e in evts)
        date_str   = datetime.utcfromtimestamp(t_start / 1000).strftime("%Y-%m-%d")

        # Time per activity — gap between consecutive events, attributed to the first event's type
        activity_time = defaultdict(float)
        activity_events = defaultdict(int)
        for i in range(len(evts_sorted) - 1):
            gap_s    = (evts_sorted[i+1].get("timestamp",0) - evts_sorted[i].get("timestamp",0)) / 1000
            gap_s    = min(gap_s, 300)  # cap gaps at 5 min (idle time)
            etype    = evts_sorted[i].get("event_type") or evts_sorted[i].get("eventType") or ""
            bucket   = ACTIVITY_MAP.get(etype, "other")
            activity_time[bucket]   += gap_s
            activity_events[bucket] += 1

        # Also count last event
        last_etype  = evts_sorted[-1].get("event_type") or evts_sorted[-1].get("eventType") or ""
        last_bucket = ACTIVITY_MAP.get(last_etype, "other")
        activity_events[last_bucket] += 1

        # Extract search queries
        search_queries = [
            e.get("properties", {}).get("query", "")
            for e in evts_sorted
            if (e.get("event_type") or e.get("eventType","")) == "search.performed"
            and isinstance(e.get("properties"), dict)
            and e.get("properties", {}).get("query")
        ]

        result.append({
            "session_id":     sid,
            "date":           date_str,
            "duration_s":     round(duration_s),
            "has_logout":     has_logout,
            "event_count":    len(evts_sorted),
            "activity_time":  dict(activity_time),
            "activity_events":dict(activity_events),
            "search_queries": search_queries,
        })

    return result

async def _compute_all_session_stats() -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Step 1 — get ALL real users (exclude anonymous)
    all_users_raw = await cms_fetch_topn(
        "cs-portal-auth-events", "auth.login",
        group_by="userId", n=5000,
        from_date=DATA_START_DATE, to_date=today
    )
    # top-n returns {"top": [{itemId: userId, count: N}]} — unwrap
    if isinstance(all_users_raw, dict):
        all_users_raw = all_users_raw.get("top", [])
    user_ids = [
        r.get("itemId") or r.get("id") or r.get("key") or ""
        for r in all_users_raw
        if (r.get("itemId") or r.get("id") or r.get("key") or "") not in ("anonymous", "")
    ]
    print(f"Session stats: fetching timelines for {len(user_ids)} users")

    # Step 2 — fetch all users in batches of 20
    BATCH = 20
    all_sessions = []
    for i in range(0, len(user_ids), BATCH):
        batch   = user_ids[i:i+BATCH]
        results = await asyncio.gather(
            *[_fetch_user_all_projects(uid) for uid in batch],
            return_exceptions=True
        )
        for events in results:
            if isinstance(events, list):
                all_sessions.extend(_compute_sessions(events))
        await asyncio.sleep(0.1)  # brief pause between batches

    if not all_sessions:
        return {
            "total_sessions": 0, "avg_seconds": 0, "median_seconds": 0,
            "p90_seconds": 0, "pct_with_logout": 0,
            "activity_breakdown": [], "daily_avg": [],
            "top_searches": [], "computed_at": datetime.utcnow().isoformat()
        }

    # Step 3 — aggregate
    durations   = sorted([s["duration_s"] for s in all_sessions])
    n           = len(durations)
    avg_s       = round(sum(durations) / n)
    median_s    = durations[n // 2]
    p90_s       = durations[int(n * 0.9)]
    pct_logout  = round(sum(1 for s in all_sessions if s["has_logout"]) / n * 100, 1)

    # Activity breakdown — aggregate across all sessions
    from collections import defaultdict, Counter
    act_time_total  = defaultdict(float)
    act_event_total = defaultdict(int)
    act_users       = defaultdict(set)
    search_counter  = Counter()

    for s in all_sessions:
        for bucket, t in s["activity_time"].items():
            act_time_total[bucket]  += t
        for bucket, cnt in s["activity_events"].items():
            act_event_total[bucket] += cnt
        # user identity — use session_id prefix as proxy
        uid_proxy = s["session_id"][:8]
        for bucket in s["activity_time"]:
            act_users[bucket].add(uid_proxy)
        for q in s["search_queries"]:
            if q.strip():
                search_counter[q.strip().lower()] += 1

    total_time = sum(act_time_total.values()) or 1
    activity_breakdown = sorted([
        {
            "bucket":       bucket,
            "label":        ACTIVITY_LABELS.get(bucket, bucket.title()),
            "avg_seconds":  round(act_time_total[bucket] / n),
            "pct_time":     round(act_time_total[bucket] / total_time * 100, 1),
            "unique_users": len(act_users[bucket]),
            "avg_events":   round(act_event_total[bucket] / n, 1),
        }
        for bucket in act_time_total
        if bucket != "auth"
    ], key=lambda x: -x["avg_seconds"])

    # Daily avg duration
    from collections import defaultdict as dd2
    daily = dd2(list)
    for s in all_sessions:
        daily[s["date"]].append(s["duration_s"])
    daily_avg = [
        {"date": d, "avg_seconds": round(sum(v)/len(v)), "count": len(v)}
        for d, v in sorted(daily.items())
    ]

    # Top searches
    top_searches = [
        {"query": q, "count": c}
        for q, c in search_counter.most_common(20)
    ]

    return {
        "total_sessions":      n,
        "avg_seconds":         avg_s,
        "median_seconds":      median_s,
        "p90_seconds":         p90_s,
        "pct_with_logout":     pct_logout,
        "activity_breakdown":  activity_breakdown,
        "daily_avg":           daily_avg,
        "top_searches":        top_searches,
        "total_users_sampled": len(user_ids),
        "computed_at":         datetime.utcnow().isoformat(),
    }

@app.get("/api/session-stats")
async def session_stats():
    cache_key = "session:stats"
    data, fresh = cache_get(cache_key)
    if fresh:
        return data
    if data is not None and not fresh:
        asyncio.create_task(_refresh_session_stats())
        return data
    # First call — compute synchronously
    result = await _compute_all_session_stats()
    cache_set(cache_key, result)
    return result

async def _refresh_session_stats():
    try:
        result = await _compute_all_session_stats()
        cache_set("session:stats", result)
    except Exception as e:
        print(f"Session stats refresh error: {e}")

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
