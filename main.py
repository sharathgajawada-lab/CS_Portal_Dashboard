"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

CALL BUDGET (confirmed lean):
  Batch  (every 5 min)  :  9 calls  — time-series per event
  Intel  (every 1 hr)   : 13 calls  — everything else
    - 3 top-n  : articles, feedback, categories
    - 1 top-n  : top video watcher (userId on video.watched) → 1 user
    - 1 top-n  : top search user   (userId on search.performed) → 1 user
    - 4 timelines: video_user × content-events (videos+search)
                   search_user × content-events (search if different user)
                   BUT if same user — 1 timeline covers both → 3 calls
    Worst case: 3+5 = 8 intel calls. Best: 3+4 = 7.
  TOTAL WORST CASE: 9 + 8 = 17 calls per full refresh
  Per page load: 0 calls (cache)
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
BATCH_TTL     = 300    # 5 min
INTEL_TTL     = 3600   # 1 hr
STALE_TTL     = 7200   # 2 hr stale-ok
BATCH_SEC     = 300
INTEL_SEC     = 3600
SEARCH_GAP_MS = 3000

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
    if not e: return None, False
    age = time.time() - e["ts"]
    ttl = INTEL_TTL if key == "intel:all" else BATCH_TTL
    if age < ttl:     return e["data"], True
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
            timeout=25,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _client

_sem = None
def get_sem():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(2)  # max 2 concurrent — gentle on CMS
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
                    wait = 3 * (attempt + 1)
                    print(f"[CMS] {r.status_code} waiting {wait}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                if r.status_code != 200:
                    print(f"[CMS] {r.status_code} {url}", flush=True)
                    return None
                text = r.text.strip()
                return json.loads(text) if text else None
            except Exception as ex:
                print(f"[CMS] {ex}", flush=True)
                await asyncio.sleep(3 * (attempt + 1))
    return None

async def _timeseries(project, event):
    data = await _get(f"{CMS_BASE}/{project}/query/time-series",
                      {"event": event, "bucket": "day"})
    if not data: return []
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
    if not data: return []
    rows = data.get("top", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []

async def _timeline(project, user_id, limit=500):
    data = await _get(f"{CMS_BASE}/{project}/query/user-timeline",
                      {"userId": user_id, "since": "30d", "limit": limit})
    if not data: return []
    events = data.get("events", []) if isinstance(data, dict) else data
    return sorted(events, key=lambda e: e.get("timestamp", 0)) if isinstance(events, list) else []

def _etype(e): return e.get("event_type") or e.get("eventType") or ""
def _props(e):
    p = e.get("properties")
    return p if isinstance(p, dict) else {}

# ── BATCH: 9 calls ─────────────────────────────────────────────────────────────
async def fetch_batch():
    print("[batch] fetching...", flush=True)
    result = {}
    # Sequential with small gap — gentler than parallel
    for ev in EVENTS:
        s = await _timeseries(ev["project"], ev["key"])
        result[ev["key"]] = {"series": s}
        await asyncio.sleep(0.3)
    cache_set("batch:all", result)
    print(f"[batch] done", flush=True)
    return result

# ── INTEL: 8-9 calls ──────────────────────────────────────────────────────────
async def fetch_intel():
    print("[intel] fetching...", flush=True)

    # Step 1: 3 top-n for articles/feedback/categories (sequential, gentle)
    art_rows  = await _topn("cs-portal-content-events",  "article.viewed",   "itemId", 10)
    await asyncio.sleep(0.3)
    fb_rows   = await _topn("cs-portal-feedback-events", "article.feedback", "itemId", 10)
    await asyncio.sleep(0.3)
    cat_rows  = await _topn("cs-portal-content-events",  "category.viewed",  "itemId", 10)
    await asyncio.sleep(0.3)

    print(f"[intel] articles={len(art_rows)} feedback={len(fb_rows)} cats={len(cat_rows)}", flush=True)

    # Step 2: Find best user for video+search (1 top-n call)
    # Use video.watched userId — heavy video users also tend to search
    video_users = await _topn("cs-portal-content-events", "video.watched", "userId", 10)
    await asyncio.sleep(0.3)

    # Pick top non-anonymous users — try up to 3 to get good coverage
    candidates = [
        r.get("userId") or r.get("itemId") or ""
        for r in video_users
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:3]

    # Step 3: Fetch content timeline for each candidate (max 3 calls)
    # This gives us video titles AND search queries
    all_events = []
    for uid in candidates:
        tl = await _timeline("cs-portal-content-events", uid, limit=500)
        all_events.extend(tl)
        await asyncio.sleep(0.3)
        print(f"[intel] timeline {uid[:20]}: {len(tl)} events", flush=True)

    print(f"[intel] total events: {len(all_events)}", flush=True)

    # ── Extract videos ────────────────────────────────────────────────────────
    video_counter = Counter()
    video_urls    = {}
    for ev in all_events:
        if _etype(ev) == "video.watched":
            props = _props(ev)
            title = props.get("videoTitle", "").strip()
            url   = props.get("videoUrl", "")
            if title:
                video_counter[title] += 1
                if url and title not in video_urls:
                    video_urls[title] = url

    # ── Extract search queries ────────────────────────────────────────────────
    query_counter = Counter()
    zero_result_q = Counter()
    search_total  = 0
    search_conv   = 0
    sorted_events = sorted(all_events, key=lambda e: e.get("timestamp", 0))
    for i, ev in enumerate(sorted_events):
        if _etype(ev) != "search.performed": continue
        props = _props(ev)
        q = (props.get("query") or "").strip()
        if not q: continue
        ts = ev.get("timestamp", 0)
        is_final = True
        if i+1 < len(sorted_events):
            nev = sorted_events[i+1]
            if _etype(nev) == "search.performed" and (nev.get("timestamp",0)-ts) < SEARCH_GAP_MS:
                is_final = False
        if not is_final: continue
        search_total += 1
        ql = q.lower()
        query_counter[ql] += 1
        if props.get("resultCount") == 0:
            zero_result_q[ql] += 1
        sid = ev.get("session_id") or ""
        for j in range(i+1, len(sorted_events)):
            nev = sorted_events[j]
            if nev.get("session_id") != sid and sid: break
            if _etype(nev) == "article.viewed": search_conv += 1; break

    # ── Assemble articles ─────────────────────────────────────────────────────
    fb_map      = {r.get("itemId") or "": int(r.get("count",0)) for r in fb_rows}
    total_views = sum(int(r.get("count",0)) for r in art_rows)
    articles    = []
    for r in art_rows:
        aid   = r.get("itemId") or r.get("item_id") or ""
        views = int(r.get("count", 0))
        fb    = fb_map.get(aid, 0)
        score = min(60, round(views/max(total_views,1)*60)) + 20  # views(60) + base(20) + feedback bonus
        if fb > 0: score = min(100, score + 20)
        articles.append({
            "id": aid,
            "label": aid.replace("-"," ").replace("_"," ").title(),
            "views": views,
            "share_pct": round(views/total_views*100,1) if total_views else 0,
            "helpful": 0, "not_helpful": 0, "helpful_pct": None,
            "total_feedback": fb,
            "avg_seconds": None, "min_seconds": None, "max_seconds": None,
            "time_sample": 0,
            "health_score": score,
            "is_dead_end": False, "next_articles": [],
        })

    # ── Content gap detection ─────────────────────────────────────────────────
    art_slugs = {(r.get("itemId") or "").lower().replace("-"," ") for r in art_rows}
    gaps = []
    for q, cnt in query_counter.most_common(20):
        q_words = set(q.lower().split())
        matched = any(len(q_words & set(s.split())) >= 1 for s in art_slugs)
        gaps.append({"query":q,"count":cnt,"is_zero_result":q in zero_result_q,"has_content":matched})

    # ── Assemble result ───────────────────────────────────────────────────────
    result = {
        "articles": {
            "articles":    articles,
            "total_views": total_views,
            "computed_at": datetime.utcnow().isoformat(),
            "note": "Views & feedback from top-n. Time spent requires more API calls.",
        },
        "search": {
            "top_queries":     [{"query":q,"count":c} for q,c in query_counter.most_common(20)],
            "zero_result":     [{"query":q,"count":c} for q,c in zero_result_q.most_common(10)],
            "content_gaps":    gaps,
            "total_searches":  search_total,
            "conversion_rate": round(search_conv/search_total*100,1) if search_total else 0,
            "computed_at":     datetime.utcnow().isoformat(),
            "note": "From top-3 video watchers' content timelines.",
        },
        "categories": {
            "categories": [{
                "path":  r.get("itemId") or "",
                "label": (r.get("itemId") or "").replace("/category/","").replace("-"," ").title() or "Home",
                "count": int(r.get("count",0)),
            } for r in cat_rows],
            "computed_at": datetime.utcnow().isoformat(),
        },
        "videos": {
            "videos":      [{"title":t,"count":c,"url":video_urls.get(t,"")} for t,c in video_counter.most_common(20)],
            "computed_at": datetime.utcnow().isoformat(),
            "note": "From top-3 video watchers' timelines.",
        },
        "sessions": {
            "total_sessions":0,"avg_seconds":0,"median_seconds":0,
            "p90_seconds":0,"pct_with_logout":0,
            "depth_distribution":{},"activity_breakdown":[],"daily_avg":[],
            "computed_at": datetime.utcnow().isoformat(),
            "note": "Session analytics requires 60+ API calls. Disabled to protect CMS stability.",
        },
        "computed_at": datetime.utcnow().isoformat(),
    }
    cache_set("intel:all", result)
    n_videos  = len(video_counter)
    n_queries = len(query_counter)
    print(f"[intel] done — {len(articles)} articles, {n_videos} videos, {n_queries} queries, {len(cat_rows)} cats", flush=True)
    return result

async def get_batch():
    data, fresh = cache_get("batch:all")
    if data is None: return await fetch_batch()
    if not fresh:    asyncio.create_task(_bg(fetch_batch, "batch:all"))
    return data

async def get_intel():
    data, fresh = cache_get("intel:all")
    if data is None: return await fetch_intel()
    if not fresh:    asyncio.create_task(_bg(fetch_intel, "intel:all"))
    return data

async def _bg(fn, key):
    try:
        result = await fn()
        cache_set(key, result)
    except Exception as ex:
        print(f"[bg:{key}] {ex}", flush=True)

# ── Background loops ───────────────────────────────────────────────────────────
async def _loop_batch():
    await asyncio.sleep(8)
    while True:
        try:    await fetch_batch()
        except Exception as ex: print(f"[loop:batch] {ex}", flush=True)
        await asyncio.sleep(BATCH_SEC)

async def _loop_intel():
    await asyncio.sleep(30)  # let batch finish first
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

@app.get("/api/metrics/batch")
async def batch_metrics():
    data = await get_batch()
    payload  = json.dumps(data, separators=(",",":"))
    etag_val = hashlib.md5(payload.encode()).hexdigest()[:16]
    return Response(content=payload, media_type="application/json",
        headers={"Cache-Control":f"public, max-age={BATCH_TTL}, stale-while-revalidate={STALE_TTL}",
                 "ETag":f'"{etag_val}"',"Vary":"Accept-Encoding"})

@app.get("/api/articles")
async def api_articles():
    return (await get_intel()).get("articles", {"articles":[],"total_views":0})

@app.get("/api/search")
async def api_search():
    return (await get_intel()).get("search", {"top_queries":[],"zero_result":[],"content_gaps":[],"total_searches":0,"conversion_rate":0})

@app.get("/api/sessions")
async def api_sessions():
    return (await get_intel()).get("sessions", {"total_sessions":0})

@app.get("/api/videos")
async def api_videos():
    return (await get_intel()).get("videos", {"videos":[]})

@app.get("/api/categories")
async def api_categories():
    return (await get_intel()).get("categories", {"categories":[]})

@app.get("/health")
async def health():
    b, bf = cache_get("batch:all")
    i, fi = cache_get("intel:all")
    intel = i or {}
    n_arts  = len(intel.get("articles",{}).get("articles",[]))
    n_vids  = len(intel.get("videos",{}).get("videos",[]))
    n_cats  = len(intel.get("categories",{}).get("categories",[]))
    n_q     = len(intel.get("search",{}).get("top_queries",[]))
    return {
        "status":        "ok",
        "api_key_set":   bool(API_KEY),
        "batch_cached":  b is not None,
        "batch_fresh":   bf,
        "intel_cached":  i is not None,
        "intel_fresh":   fi,
        "intel_data":    {"articles":n_arts,"videos":n_vids,"categories":n_cats,"search_queries":n_q},
        "call_budget":   "9 batch (every 5m) + ~8 intel (every 1h) = ~17 total",
        "ts":            datetime.utcnow().isoformat(),
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
    return {"status":"cleared — ~17 CMS calls queued (sequential, gentle)"}

@app.get("/debug/cms")
async def debug_cms():
    client = get_client()
    results = {}
    try:
        r = await client.get(f"{CMS_BASE}/cs-portal-content-events/query/top-n",
                             params={"event":"article.viewed","groupBy":"itemId","n":3})
        results["article_topn"] = {"status":r.status_code,"preview":r.text[:150]}
    except Exception as e:
        results["article_topn"] = {"error":str(e)}
    b, _ = cache_get("batch:all")
    i, _ = cache_get("intel:all")
    intel = i or {}
    results["batch_cached"]   = b is not None
    results["intel_cached"]   = i is not None
    results["intel_articles"] = len(intel.get("articles",{}).get("articles",[]))
    results["intel_videos"]   = len(intel.get("videos",{}).get("videos",[]))
    results["intel_queries"]  = len(intel.get("search",{}).get("top_queries",[]))
    return results

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f: html = f.read()
    return HTMLResponse(content=html,
        headers={"Cache-Control":"public, max-age=300, stale-while-revalidate=3600"})

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f: content = f.read()
    return Response(content=content, media_type="application/javascript",
        headers={"Cache-Control":"public, max-age=86400"})
