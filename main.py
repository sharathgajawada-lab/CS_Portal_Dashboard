"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

CALL BUDGET:
  Full refresh: 17 calls, fully sequential, 1s gap between each
  Schedule: every 2 hours (not 5min/1hr separately)
  Per page load: 0 calls — always served from cache
  On CMS error: serve stale cache, retry next cycle
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
CACHE_TTL     = 7200   # 2 hr fresh
STALE_TTL     = 86400  # 24 hr stale — serve old data if CMS is down
REFRESH_SEC   = 7200   # refresh every 2 hours
CALL_GAP      = 1.0    # 1 second between every CMS call
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
CACHE_FILE = "/tmp/cs_portal_cache.json"

def cache_get(key):
    e = _cache.get(key)
    if not e: return None, False
    age = time.time() - e["ts"]
    if age < CACHE_TTL:  return e["data"], True
    if age < STALE_TTL:  return e["data"], False
    return None, False

def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}
    # Persist to disk so cache survives server restarts
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(_cache, f)
    except Exception as ex:
        print(f"[cache] disk write failed: {ex}", flush=True)

def load_cache_from_disk():
    """Load cache from disk on startup — survives Render restarts."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        _cache.update(data)
        keys = list(data.keys())
        print(f"[cache] loaded from disk: {keys}", flush=True)
    except FileNotFoundError:
        print("[cache] no disk cache found — fresh start", flush=True)
    except Exception as ex:
        print(f"[cache] disk read failed: {ex}", flush=True)

# ── HTTP client ────────────────────────────────────────────────────────────────
_client = None

def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _client

# ── Single lock — only ONE refresh at a time ───────────────────────────────────
_refresh_lock = None

def get_lock():
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock

# ── Core HTTP — one call at a time ─────────────────────────────────────────────
async def _get(url, params, retries=3):
    client = get_client()
    for attempt in range(retries):
        try:
            r = await client.get(url, params=params)
            if r.status_code in (429, 502, 503, 504):
                wait = 5 * (attempt + 1)
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
            await asyncio.sleep(5 * (attempt + 1))
    return None

async def _call(url, params):
    """Make one CMS call then wait CALL_GAP seconds before returning."""
    result = await _get(url, params)
    await asyncio.sleep(CALL_GAP)
    return result

async def _timeseries(project, event):
    data = await _call(f"{CMS_BASE}/{project}/query/time-series",
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
    data = await _call(f"{CMS_BASE}/{project}/query/top-n",
                       {"event": event, "groupBy": group_by, "n": n})
    if not data: return []
    rows = data.get("top", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []

async def _timeline(project, user_id, limit=500):
    data = await _call(f"{CMS_BASE}/{project}/query/user-timeline",
                       {"userId": user_id, "since": "30d", "limit": limit})
    if not data: return []
    events = data.get("events", []) if isinstance(data, dict) else data
    return sorted(events, key=lambda e: e.get("timestamp", 0)) if isinstance(events, list) else []

def _etype(e): return e.get("event_type") or e.get("eventType") or ""
def _props(e):
    p = e.get("properties")
    return p if isinstance(p, dict) else {}

# ── FULL REFRESH — all 17 calls, fully sequential ─────────────────────────────
async def full_refresh():
    """
    17 sequential CMS calls with 1s gap each = ~25 seconds total.
    Protected by lock — only one refresh runs at a time.
    """
    lock = get_lock()
    if lock.locked():
        print("[refresh] already running, skipping", flush=True)
        return
    async with lock:
        print(f"[refresh] starting at {datetime.utcnow().isoformat()}", flush=True)
        batch  = {}
        intel  = {}

        # ── Calls 1-9: time-series for KPIs ──────────────────────────────────
        for ev in EVENTS:
            series = await _timeseries(ev["project"], ev["key"])
            batch[ev["key"]] = {"series": series}

        cache_set("batch:all", batch)
        print(f"[refresh] batch done — {sum(len(v['series']) for v in batch.values())} points", flush=True)

        # ── Call 10: top articles ─────────────────────────────────────────────
        art_rows = await _topn("cs-portal-content-events", "article.viewed", "itemId", 10)

        # ── Call 11: top feedback ─────────────────────────────────────────────
        fb_rows = await _topn("cs-portal-feedback-events", "article.feedback", "itemId", 10)

        # ── Call 12: top categories ───────────────────────────────────────────
        cat_rows = await _topn("cs-portal-content-events", "category.viewed", "itemId", 10)

        # ── Call 13: top video watchers ───────────────────────────────────────
        video_user_rows = await _topn("cs-portal-content-events", "video.watched", "userId", 10)

        # ── Call 14: top search users ─────────────────────────────────────────
        search_user_rows = await _topn("cs-portal-content-events", "search.performed", "userId", 10)

        # Merge unique non-anonymous users from both — up to 4
        seen = set()
        users = []
        for rows in (video_user_rows, search_user_rows):
            for r in rows:
                uid = r.get("userId") or r.get("itemId") or ""
                if uid and uid not in ("anonymous", "") and uid not in seen:
                    seen.add(uid)
                    users.append(uid)
            if len(users) >= 4:
                users = users[:4]
                break

        # ── Calls 15-18: content timelines ───────────────────────────────────
        all_events = []
        for uid in users:
            tl = await _timeline("cs-portal-content-events", uid, limit=500)
            all_events.extend(tl)
            print(f"[refresh] timeline {uid[:16]}: {len(tl)} events", flush=True)

        print(f"[refresh] intel calls done — {len(all_events)} total events", flush=True)

        # ── Extract videos ────────────────────────────────────────────────────
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

        # ── Extract search queries ────────────────────────────────────────────
        query_counter = Counter()
        zero_result_q = Counter()
        search_total  = 0
        search_conv   = 0
        sorted_ev     = sorted(all_events, key=lambda e: e.get("timestamp", 0))
        for i, ev in enumerate(sorted_ev):
            if _etype(ev) != "search.performed": continue
            props = _props(ev)
            q = (props.get("query") or "").strip()
            if not q: continue
            ts = ev.get("timestamp", 0)
            is_final = True
            if i+1 < len(sorted_ev):
                nev = sorted_ev[i+1]
                if _etype(nev) == "search.performed" and (nev.get("timestamp",0)-ts) < SEARCH_GAP_MS:
                    is_final = False
            if not is_final: continue
            search_total += 1
            ql = q.lower()
            query_counter[ql] += 1
            if props.get("resultCount") == 0:
                zero_result_q[ql] += 1
            sid = ev.get("session_id") or ""
            for j in range(i+1, len(sorted_ev)):
                nev = sorted_ev[j]
                if nev.get("session_id") != sid and sid: break
                if _etype(nev) == "article.viewed": search_conv += 1; break

        # ── Assemble articles ─────────────────────────────────────────────────
        fb_map      = {r.get("itemId") or "": int(r.get("count",0)) for r in fb_rows}
        total_views = sum(int(r.get("count",0)) for r in art_rows)
        articles    = []
        for r in art_rows:
            aid   = r.get("itemId") or r.get("item_id") or ""
            views = int(r.get("count", 0))
            fb    = fb_map.get(aid, 0)
            score = min(60, round(views/max(total_views,1)*60)) + 20
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

        # ── Content gaps ──────────────────────────────────────────────────────
        art_slugs = {(r.get("itemId") or "").lower().replace("-"," ") for r in art_rows}
        gaps = []
        for q, cnt in query_counter.most_common(20):
            q_words = set(q.lower().split())
            matched = any(len(q_words & set(s.split())) >= 1 for s in art_slugs)
            gaps.append({"query":q,"count":cnt,
                         "is_zero_result":q in zero_result_q,
                         "has_content":matched})

        # ── Store intel ───────────────────────────────────────────────────────
        intel = {
            "articles": {
                "articles": articles, "total_views": total_views,
                "computed_at": datetime.utcnow().isoformat(),
                "note": "Views & feedback from top-n API.",
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
                "videos": [{"title":t,"count":c,"url":video_urls.get(t,"")}
                           for t,c in video_counter.most_common(20)],
                "computed_at": datetime.utcnow().isoformat(),
                "note": "From top-3 video watchers' timelines.",
            },
            "sessions": {
                "total_sessions":0,"avg_seconds":0,"median_seconds":0,
                "p90_seconds":0,"pct_with_logout":0,
                "depth_distribution":{},"activity_breakdown":[],"daily_avg":[],
                "computed_at": datetime.utcnow().isoformat(),
                "note": "Session analytics requires 60+ API calls. Disabled to protect CMS.",
            },
        }
        cache_set("intel:all", intel)
        print(f"[refresh] complete — {len(articles)} articles, {len(video_counter)} videos, {search_total} searches", flush=True)

# ── Background loop — one refresh every 2 hours ────────────────────────────────
async def _refresh_loop():
    await asyncio.sleep(10)  # let server boot fully
    while True:
        try:
            await full_refresh()
        except Exception as ex:
            print(f"[loop] error: {ex}", flush=True)
        await asyncio.sleep(REFRESH_SEC)

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    load_cache_from_disk()  # restore cache immediately on startup
    t = asyncio.create_task(_refresh_loop())
    yield
    t.cancel()
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
    data, _ = cache_get("batch:all")
    if not data:
        data = {}
    payload  = json.dumps(data, separators=(",",":"))
    etag_val = hashlib.md5(payload.encode()).hexdigest()[:16]
    return Response(content=payload, media_type="application/json",
        headers={"Cache-Control":f"public, max-age={CACHE_TTL}, stale-while-revalidate={STALE_TTL}",
                 "ETag":f'"{etag_val}"',"Vary":"Accept-Encoding"})

@app.get("/api/articles")
async def api_articles():
    data, _ = cache_get("intel:all")
    return (data or {}).get("articles", {"articles":[],"total_views":0})

@app.get("/api/search")
async def api_search():
    data, _ = cache_get("intel:all")
    return (data or {}).get("search", {"top_queries":[],"zero_result":[],"content_gaps":[],"total_searches":0,"conversion_rate":0})

@app.get("/api/sessions")
async def api_sessions():
    data, _ = cache_get("intel:all")
    return (data or {}).get("sessions", {"total_sessions":0,"note":"Disabled."})

@app.get("/api/videos")
async def api_videos():
    data, _ = cache_get("intel:all")
    return (data or {}).get("videos", {"videos":[]})

@app.get("/api/categories")
async def api_categories():
    data, _ = cache_get("intel:all")
    return (data or {}).get("categories", {"categories":[]})

@app.get("/health")
async def health():
    b, bf = cache_get("batch:all")
    i, fi = cache_get("intel:all")
    intel = i or {}
    lock  = get_lock()
    return {
        "status":        "ok",
        "api_key_set":   bool(API_KEY),
        "batch_cached":  b is not None,
        "batch_fresh":   bf,
        "intel_cached":  i is not None,
        "intel_fresh":   fi,
        "refresh_running": lock.locked(),
        "intel_data": {
            "articles":  len(intel.get("articles",{}).get("articles",[])),
            "videos":    len(intel.get("videos",{}).get("videos",[])),
            "categories":len(intel.get("categories",{}).get("categories",[])),
            "queries":   len(intel.get("search",{}).get("top_queries",[])),
        },
        "call_budget":   "17 sequential calls per refresh, 1s gap each, every 2 hours",
        "ts":            datetime.utcnow().isoformat(),
    }

@app.get("/cache/clear")
async def clear_cache():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
    _cache.clear()
    lock = get_lock()
    if not lock.locked():
        asyncio.create_task(full_refresh())
        return {"status": "cleared — 1 refresh queued (17 sequential calls, ~25s)"}
    return {"status": "cleared — refresh already running, will complete shortly"}

@app.get("/debug/cms")
async def debug_cms():
    client = get_client()
    try:
        r = await client.get(f"{CMS_BASE}/cs-portal-content-events/query/top-n",
                             params={"event":"article.viewed","groupBy":"itemId","n":3})
        cms_status = {"status": r.status_code, "preview": r.text[:150]}
    except Exception as e:
        cms_status = {"error": str(e)}
    b, _  = cache_get("batch:all")
    i, _  = cache_get("intel:all")
    intel = i or {}
    lock  = get_lock()
    return {
        "cms":           cms_status,
        "batch_cached":  b is not None,
        "intel_cached":  i is not None,
        "refresh_running": lock.locked(),
        "articles":      len(intel.get("articles",{}).get("articles",[])),
        "videos":        len(intel.get("videos",{}).get("videos",[])),
        "queries":       len(intel.get("search",{}).get("top_queries",[])),
    }

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
