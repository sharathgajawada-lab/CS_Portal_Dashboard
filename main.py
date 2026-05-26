"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

Call budget:
  Startup / hourly intel refresh : 14 calls total
    - 9  time-series  (one per event, KPIs)
    - 1  top-n        articles by itemId
    - 1  top-n        feedback by itemId
    - 1  top-n        categories by itemId
    - 1  top-n        search users by userId
    - 1  user-timeline (1 user, content-events only, search+video)
  Every 5 min (batch only) : 9 calls
  Per page load             : 0 calls (all from cache)
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
from collections import defaultdict, Counter
import httpx, os, asyncio, time, json, hashlib

# ── Config ─────────────────────────────────────────────────────────────────────
CMS_BASE      = "https://cms.audibene.net/api/metrics"
API_KEY       = os.environ.get("CMS_API_KEY", "")
DATA_START    = "2026-04-24"
BATCH_TTL     = 300    # 5 min  — KPI time-series
INTEL_TTL     = 3600   # 1 hr   — articles/search/categories
STALE_TTL     = 7200   # 2 hrs  — serve stale if CMS is down
BATCH_SEC     = 300    # re-fetch KPIs every 5 min
INTEL_SEC     = 3600   # re-fetch intel every 1 hr
SEARCH_GAP_MS = 3000   # keystroke gap — filter partial queries

EVENTS = [
    {"key": "auth.login",             "project": "cs-portal-auth-events",       "label": "Logins",          "color": "#0e6e45"},
    {"key": "article.viewed",         "project": "cs-portal-content-events",    "label": "Articles viewed", "color": "#c47a0a"},
    {"key": "search.performed",       "project": "cs-portal-content-events",    "label": "Searches",        "color": "#5b3fbf"},
    {"key": "video.watched",          "project": "cs-portal-content-events",    "label": "Videos watched",  "color": "#c03030"},
    {"key": "category.viewed",        "project": "cs-portal-content-events",    "label": "Category views",  "color": "#1a4fdb"},
    {"key": "article.feedback",       "project": "cs-portal-feedback-events",   "label": "Feedback",        "color": "#0f6e56"},
    {"key": "order_supplies.visited", "project": "cs-portal-items-events",      "label": "Supplies visits", "color": "#b45309"},
    {"key": "auth.logout",            "project": "cs-portal-auth-events",       "label": "Logouts",         "color": "#6b7280"},
    {"key": "scheduling.started",     "project": "cs-portal-scheduling-events", "label": "Scheduling",      "color": "#7c3aed"},
]

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = {}

def cache_get(key):
    e = _cache.get(key)
    if not e:
        return None, False
    age   = time.time() - e["ts"]
    fresh = INTEL_TTL if key == "intel:all" else BATCH_TTL
    if age < fresh:    return e["data"], True
    if age < STALE_TTL: return e["data"], False
    return None, False

def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}

# ── HTTP client ────────────────────────────────────────────────────────────────
_client = None

def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _client

# ── Single semaphore — gentle on CMS ──────────────────────────────────────────
_sem = None

def get_sem():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(3)  # max 3 concurrent CMS calls
    return _sem

# ── Core HTTP ──────────────────────────────────────────────────────────────────
async def _get(url, params, retries=3):
    sem = get_sem()
    client = get_client()
    async with sem:
        for attempt in range(retries):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    wait = 2 ** attempt
                    print(f"[CMS] {r.status_code} — waiting {wait}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                if r.status_code != 200:
                    print(f"[CMS] {r.status_code} {url}", flush=True)
                    return None
                text = r.text.strip()
                return json.loads(text) if text else None
            except Exception as ex:
                print(f"[CMS] error: {ex}", flush=True)
                await asyncio.sleep(2 ** attempt)
    return None

async def _timeseries(project, event):
    data = await _get(f"{CMS_BASE}/{project}/query/time-series",
                      {"event": event, "bucket": "day"})
    if not data:
        return []
    daily = defaultdict(int)
    for p in data.get("series", []):
        ts = p.get("ts") or p.get("timestamp")
        if ts:
            try:
                d = datetime.utcfromtimestamp(int(ts)/1000).strftime("%Y-%m-%d")
                daily[d] += int(p.get("count", 0) or 0)
            except Exception:
                pass
    return [{"date": d, "count": daily[d]} for d in sorted(daily)]

async def _topn(project, event, group_by="itemId", n=10):
    data = await _get(f"{CMS_BASE}/{project}/query/top-n",
                      {"event": event, "groupBy": group_by, "n": n})
    if not data:
        return []
    rows = data.get("top", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []

async def _timeline(project, user_id, limit=200):
    data = await _get(f"{CMS_BASE}/{project}/query/user-timeline",
                      {"userId": user_id, "since": "30d", "limit": limit})
    if not data:
        return []
    events = data.get("events", []) if isinstance(data, dict) else data
    return sorted(events, key=lambda e: e.get("timestamp", 0)) if isinstance(events, list) else []

def _etype(e): return e.get("event_type") or e.get("eventType") or ""
def _props(e):
    p = e.get("properties")
    return p if isinstance(p, dict) else {}

# ── BATCH: 9 calls, every 5 min ───────────────────────────────────────────────
async def fetch_batch():
    print(f"[batch] fetching...", flush=True)
    result = {}
    # Fire all 9 in parallel — controlled by semaphore(3) so max 3 at once
    async def fetch_one(ev):
        s = await _timeseries(ev["project"], ev["key"])
        return ev["key"], {"series": s}
    pairs = await asyncio.gather(*[fetch_one(ev) for ev in EVENTS], return_exceptions=True)
    for p in pairs:
        if isinstance(p, tuple):
            result[p[0]] = p[1]
    cache_set("batch:all", result)
    total = sum(len(v.get("series",[])) for v in result.values())
    print(f"[batch] done — {total} data points across {len(result)} events", flush=True)
    return result

# ── INTEL: 5 calls, every 1 hr ────────────────────────────────────────────────
async def fetch_intel():
    print(f"[intel] fetching...", flush=True)

    # 4 top-n calls in parallel
    art_rows, fb_rows, cat_rows, user_rows = await asyncio.gather(
        _topn("cs-portal-content-events",  "article.viewed",   "itemId", 10),
        _topn("cs-portal-feedback-events", "article.feedback", "itemId", 10),
        _topn("cs-portal-content-events",  "category.viewed",  "itemId", 10),
        _topn("cs-portal-content-events",  "search.performed", "userId", 10),
    )
    print(f"[intel] top-n: articles={len(art_rows)} fb={len(fb_rows)} cats={len(cat_rows)} users={len(user_rows)}", flush=True)

    # 1 timeline call — pick first non-anonymous user
    timeline = []
    for r in user_rows:
        uid = r.get("userId") or r.get("itemId") or ""
        if uid and uid != "anonymous":
            timeline = await _timeline("cs-portal-content-events", uid, limit=200)
            print(f"[intel] timeline: {len(timeline)} events for {uid[:20]}", flush=True)
            break

    # ── Articles ──────────────────────────────────────────────────────────────
    fb_map = {}
    for r in fb_rows:
        aid = r.get("itemId") or r.get("item_id") or ""
        if aid:
            fb_map[aid] = int(r.get("count", 0))

    total_views = sum(int(r.get("count", 0)) for r in art_rows)
    articles = []
    for r in art_rows:
        aid   = r.get("itemId") or r.get("item_id") or ""
        views = int(r.get("count", 0))
        fb    = fb_map.get(aid, 0)
        hlp_pct = None  # sentiment needs timeline — dropped to save calls
        score = min(30, round(views/max(total_views,1)*300)) + 15 + 15
        articles.append({
            "id": aid,
            "label": aid.replace("-"," ").replace("_"," ").title(),
            "views": views,
            "share_pct": round(views/total_views*100, 1) if total_views else 0,
            "helpful": 0, "not_helpful": 0, "helpful_pct": hlp_pct,
            "total_feedback": fb,
            "avg_seconds": None, "min_seconds": None, "max_seconds": None,
            "time_sample": 0,
            "health_score": score,
            "is_dead_end": False, "next_articles": [],
        })

    # ── Search queries from 1 timeline ───────────────────────────────────────
    query_counter = Counter()
    zero_result_q = Counter()
    search_total  = 0
    search_conv   = 0
    video_counter = Counter()
    video_urls    = {}

    for i, ev in enumerate(timeline):
        et    = _etype(ev)
        props = _props(ev)

        if et == "search.performed":
            q = (props.get("query") or "").strip()
            if q:
                ts = ev.get("timestamp", 0)
                is_final = True
                if i+1 < len(timeline):
                    nev = timeline[i+1]
                    if _etype(nev) == "search.performed" and (nev.get("timestamp",0)-ts) < SEARCH_GAP_MS:
                        is_final = False
                if is_final:
                    search_total += 1
                    ql = q.lower()
                    query_counter[ql] += 1
                    if props.get("resultCount") == 0:
                        zero_result_q[ql] += 1
                    sid = ev.get("session_id") or ""
                    for j in range(i+1, len(timeline)):
                        nev = timeline[j]
                        if nev.get("session_id") != sid and sid: break
                        if _etype(nev) == "article.viewed": search_conv += 1; break

        if et == "video.watched":
            title = props.get("videoTitle", "").strip()
            url   = props.get("videoUrl", "")
            if title:
                video_counter[title] += 1
                if url and title not in video_urls:
                    video_urls[title] = url

    # ── Categories ────────────────────────────────────────────────────────────
    categories = [{
        "path":  r.get("itemId") or "",
        "label": (r.get("itemId") or "").replace("/category/","").replace("-"," ").title() or "Home",
        "count": int(r.get("count", 0)),
    } for r in cat_rows]

    # ── Content gap detection ─────────────────────────────────────────────────
    art_slugs = {(r.get("itemId") or "").lower().replace("-"," ") for r in art_rows}
    gaps = []
    for q, cnt in query_counter.most_common(20):
        q_words = set(q.lower().split())
        matched = any(len(q_words & set(s.split())) >= 1 for s in art_slugs)
        gaps.append({"query": q, "count": cnt,
                     "is_zero_result": q in zero_result_q,
                     "has_content": matched})

    result = {
        "articles": {
            "articles":    articles,
            "total_views": total_views,
            "computed_at": datetime.utcnow().isoformat(),
            "note": "Views from top-n. Feedback count from top-n. Time spent requires timeline (disabled to reduce API load).",
        },
        "search": {
            "top_queries":     [{"query": q, "count": c} for q,c in query_counter.most_common(20)],
            "zero_result":     [{"query": q, "count": c} for q,c in zero_result_q.most_common(10)],
            "content_gaps":    gaps,
            "total_searches":  search_total,
            "conversion_rate": round(search_conv/search_total*100,1) if search_total else 0,
            "computed_at":     datetime.utcnow().isoformat(),
            "note": "Based on 1 active user's timeline (200 events).",
        },
        "categories": {
            "categories":  categories,
            "computed_at": datetime.utcnow().isoformat(),
        },
        "videos": {
            "videos":      [{"title":t,"count":c,"url":video_urls.get(t,"")} for t,c in video_counter.most_common(20)],
            "computed_at": datetime.utcnow().isoformat(),
            "note": "From 1 user timeline. Full video analytics requires itemId on video events.",
        },
        "sessions": {
            "total_sessions": 0, "avg_seconds": 0, "median_seconds": 0,
            "p90_seconds": 0, "pct_with_logout": 0,
            "depth_distribution": {}, "activity_breakdown": [], "daily_avg": [],
            "computed_at": datetime.utcnow().isoformat(),
            "note": "Session analytics disabled — requires 60+ API calls. Enable when CMS rate limits are raised.",
        },
        "computed_at": datetime.utcnow().isoformat(),
    }
    cache_set("intel:all", result)
    print(f"[intel] done — {len(articles)} articles, {search_total} searches, {len(categories)} cats", flush=True)
    return result

async def get_batch():
    data, fresh = cache_get("batch:all")
    if data is None:   return await fetch_batch()
    if not fresh:      asyncio.create_task(_bg(fetch_batch, "batch:all"))
    return data

async def get_intel():
    data, fresh = cache_get("intel:all")
    if data is None:   return await fetch_intel()
    if not fresh:      asyncio.create_task(_bg(fetch_intel, "intel:all"))
    return data

async def _bg(fn, key):
    try:
        result = await fn()
        cache_set(key, result)
    except Exception as ex:
        print(f"[bg:{key}] error: {ex}", flush=True)

# ── Background loops ───────────────────────────────────────────────────────────
async def _loop_batch():
    await asyncio.sleep(5)
    while True:
        try:    await fetch_batch()
        except Exception as ex: print(f"[loop:batch] {ex}", flush=True)
        await asyncio.sleep(BATCH_SEC)

async def _loop_intel():
    await asyncio.sleep(20)  # wait after batch completes
    while True:
        try:    await fetch_intel()
        except Exception as ex: print(f"[loop:intel] {ex}", flush=True)
        await asyncio.sleep(INTEL_SEC)

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    t1 = asyncio.create_task(_loop_batch())
    t2 = asyncio.create_task(_loop_intel())
    yield
    t1.cancel(); t2.cancel()
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan, title="CS Portal Analytics")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/api/metrics/batch")
async def batch_metrics():
    data = await get_batch()
    payload  = json.dumps(data, separators=(",",":"))
    etag_val = hashlib.md5(payload.encode()).hexdigest()[:16]
    return Response(content=payload, media_type="application/json",
        headers={"Cache-Control": f"public, max-age={BATCH_TTL}, stale-while-revalidate={STALE_TTL}",
                 "ETag": f'"{etag_val}"', "Vary": "Accept-Encoding"})

@app.get("/api/articles")
async def api_articles():
    intel = await get_intel()
    return intel.get("articles", {"articles": [], "total_views": 0})

@app.get("/api/search")
async def api_search():
    intel = await get_intel()
    return intel.get("search", {"top_queries": [], "zero_result": [], "content_gaps": [], "total_searches": 0, "conversion_rate": 0})

@app.get("/api/sessions")
async def api_sessions():
    intel = await get_intel()
    return intel.get("sessions", {"total_sessions": 0, "note": "Disabled to reduce API load."})

@app.get("/api/videos")
async def api_videos():
    intel = await get_intel()
    return intel.get("videos", {"videos": []})

@app.get("/api/categories")
async def api_categories():
    intel = await get_intel()
    return intel.get("categories", {"categories": []})

@app.get("/health")
async def health():
    b, bf = cache_get("batch:all")
    i, fi = cache_get("intel:all")
    return {
        "status":       "ok",
        "api_key_set":  bool(API_KEY),
        "batch_cached": b is not None,
        "batch_fresh":  bf,
        "intel_cached": i is not None,
        "intel_fresh":  fi,
        "cache_entries":len(_cache),
        "calls_per_startup": "14 total (9 batch + 4 topn + 1 timeline)",
        "calls_per_5min":    "9 (batch refresh)",
        "calls_per_hour":    "5 (intel refresh)",
        "ts":           datetime.utcnow().isoformat(),
    }

@app.get("/cache/clear")
async def clear_cache():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
    _cache.clear()
    asyncio.create_task(fetch_batch())
    asyncio.create_task(fetch_intel())
    return {"status": "cleared — refetching (14 CMS calls total)"}

@app.get("/debug/cms")
async def debug_cms():
    client = get_client()
    results = {}
    for name, url, params in [
        ("auth_topn",    f"{CMS_BASE}/cs-portal-auth-events/query/top-n",
         {"event": "auth.login", "groupBy": "userId", "n": 5}),
        ("article_topn", f"{CMS_BASE}/cs-portal-content-events/query/top-n",
         {"event": "article.viewed", "groupBy": "itemId", "n": 5}),
    ]:
        try:
            r = await client.get(url, params=params)
            results[name] = {"status": r.status_code, "preview": r.text[:200]}
        except Exception as e:
            results[name] = {"error": str(e)}
    b, bf = cache_get("batch:all")
    i, fi = cache_get("intel:all")
    results["batch_cached"] = b is not None
    results["intel_cached"] = i is not None
    return results

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f: html = f.read()
    return HTMLResponse(content=html,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"})

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f: content = f.read()
    return Response(content=content, media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"})
