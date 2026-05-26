"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

API call budget (per full refresh):
  Batch time-series : 9  calls (one per event)
  top-n calls       : 4  calls (articles, feedback, categories, user IDs)
  User timelines    : 10 users × 6 projects = 60 calls
  TOTAL             : ~73 calls per refresh (every 1hr)
  Per page load     : 0 calls (all served from cache)
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
CMS_BASE       = "https://cms.audibene.net/api/metrics"
API_KEY        = os.environ.get("CMS_API_KEY", "")
DATA_START     = "2026-04-24"
BATCH_TTL      = 300    # 5 min  — time-series batch
INTEL_TTL      = 3600   # 1 hr   — articles/search/sessions/videos
STALE_TTL      = 7200   # 2 hrs  — stale-while-revalidate
PREFETCH_SEC   = 300    # re-fetch batch every 5 min
SEARCH_GAP_MS  = 3000   # keystroke gap filter
TIME_CAP_S     = 300    # max article dwell time

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

ALL_PROJECTS = [
    "cs-portal-auth-events",
    "cs-portal-content-events",
    "cs-portal-feedback-events",
    "cs-portal-items-events",
    "cs-portal-profile-events",
    "cs-portal-scheduling-events",
]

ACTIVITY_MAP = {
    "auth.login": "auth", "auth.logout": "auth",
    "article.viewed": "articles", "video.watched": "videos",
    "search.performed": "search", "article.feedback": "feedback",
    "category.viewed": "browsing", "order_supplies.visited": "supplies",
    "scheduling.started": "scheduling", "scheduling.completed": "scheduling",
}

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = {}

def cache_get(key):
    e = _cache.get(key)
    if not e:
        return None, False
    age = time.time() - e["ts"]
    ttl   = INTEL_TTL  if key.startswith("intel:") else BATCH_TTL
    stale = STALE_TTL
    if age < ttl:   return e["data"], True
    if age < stale: return e["data"], False
    return None, False

def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}

# ── HTTP client ────────────────────────────────────────────────────────────────
_client = None

def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _client

# ── Semaphores (lazy — created inside async context) ──────────────────────────
_sem_batch    = None
_sem_topn     = None
_sem_timeline = None

def get_sems():
    global _sem_batch, _sem_topn, _sem_timeline
    if _sem_batch is None:
        _sem_batch    = asyncio.Semaphore(5)
        _sem_topn     = asyncio.Semaphore(3)
        _sem_timeline = asyncio.Semaphore(20)
    return _sem_batch, _sem_topn, _sem_timeline

# ── Core HTTP ──────────────────────────────────────────────────────────────────
async def _get(url, params, sem_name, retries=4):
    sb, st, stl = get_sems()
    sem = {"batch": sb, "topn": st, "timeline": stl}[sem_name]
    client = get_client()
    async with sem:
        for attempt in range(retries):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    print(f"[CMS] {r.status_code} {url} retry {attempt+1}", flush=True)
                    await asyncio.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    print(f"[CMS] {r.status_code} {url}", flush=True)
                    return None
                text = r.text.strip()
                return json.loads(text) if text else None
            except Exception as ex:
                print(f"[CMS] exception {url}: {ex}", flush=True)
                await asyncio.sleep(2 ** attempt)
    return None

async def cms_timeseries(project, event):
    data = await _get(f"{CMS_BASE}/{project}/query/time-series",
                      {"event": event, "bucket": "day"}, "batch")
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

async def cms_topn(project, event, group_by="itemId", n=10):
    data = await _get(f"{CMS_BASE}/{project}/query/top-n",
                      {"event": event, "groupBy": group_by, "n": n}, "topn")
    if not data:
        return []
    rows = data.get("top", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []

async def cms_timeline(project, user_id, limit=500):
    data = await _get(f"{CMS_BASE}/{project}/query/user-timeline",
                      {"userId": user_id, "since": "180d", "limit": limit}, "timeline")
    if not data:
        return []
    events = data.get("events", data) if isinstance(data, dict) else data
    return sorted(events, key=lambda e: e.get("timestamp", 0)) if isinstance(events, list) else []

def _etype(e):
    return e.get("event_type") or e.get("eventType") or ""

def _props(e):
    p = e.get("properties")
    return p if isinstance(p, dict) else {}

# ── BATCH PREFETCH (time-series for KPI cards) ────────────────────────────────
async def prefetch_batch():
    print(f"[{datetime.utcnow().isoformat()}] Prefetching batch...", flush=True)
    result = {}
    for ev in EVENTS:
        series = await cms_timeseries(ev["project"], ev["key"])
        result[ev["key"]] = {"series": series}
        await asyncio.sleep(0.1)
    cache_set("batch:all", result)
    print(f"[{datetime.utcnow().isoformat()}] Batch done — {sum(len(v['series']) for v in result.values())} points", flush=True)
    return result

# ── INTELLIGENCE COMPUTE (single function, all secondary data) ─────────────────
# Fetches timelines ONCE and derives articles + search + sessions + videos
async def compute_intelligence():
    """
    Single compute: 4 top-n calls + 60 timeline calls = ~64 total.
    Returns everything needed for all secondary endpoints.
    """
    print(f"[{datetime.utcnow().isoformat()}] Computing intelligence...", flush=True)

    # ── Step 1: Top-n calls (4 total) ──────────────────────────────────────────
    views_rows, fb_rows, cat_rows, user_rows = await asyncio.gather(
        cms_topn("cs-portal-content-events",  "article.viewed",   "itemId", 10),
        cms_topn("cs-portal-feedback-events", "article.feedback", "itemId", 10),
        cms_topn("cs-portal-content-events",  "category.viewed",  "itemId", 10),
        cms_topn("cs-portal-auth-events",     "auth.login",       "userId", 10),
    )
    print(f"  top-n: articles={len(views_rows)} feedback={len(fb_rows)} cats={len(cat_rows)} users={len(user_rows)}", flush=True)

    # ── Step 2: Get user IDs (exclude anonymous) ───────────────────────────────
    user_ids = [
        r.get("userId") or r.get("itemId") or ""
        for r in user_rows
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:10]
    print(f"  user_ids: {len(user_ids)}", flush=True)

    # ── Step 3: Fetch all timelines in parallel (60 calls max) ────────────────
    # Each user × 6 projects — one gather call, all at once
    all_timelines = []
    if user_ids:
        tasks = []
        for uid in user_ids:
            for project in ALL_PROJECTS:
                tasks.append(cms_timeline(project, uid))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Group by user_id
        per_user = defaultdict(list)
        for i, events in enumerate(results):
            uid = user_ids[i // len(ALL_PROJECTS)]
            if isinstance(events, list):
                per_user[uid].extend(events)
        # Sort each user's timeline by timestamp
        for uid in user_ids:
            tl = sorted(per_user[uid], key=lambda e: e.get("timestamp", 0))
            all_timelines.append(tl)
        print(f"  timelines fetched: {len(all_timelines)} users, {sum(len(t) for t in all_timelines)} events total", flush=True)

    # ── Step 4: Derive all intelligence from timelines ─────────────────────────
    article_times = defaultdict(list)
    article_next  = defaultdict(Counter)
    feedback_sent = defaultdict(lambda: {"helpful": 0, "not_helpful": 0})
    query_counter = Counter()
    zero_result_q = Counter()
    search_converted = 0
    search_total  = 0
    video_counter = Counter()
    video_urls    = {}
    all_sessions  = []

    for tl in all_timelines:
        # ── Article time spent + navigation paths ──────────────────────────────
        for i, ev in enumerate(tl):
            et    = _etype(ev)
            props = _props(ev)
            aid   = ev.get("item_id") or props.get("articleKey") or ""

            if et == "article.viewed" and aid:
                sid = ev.get("session_id") or ""
                t0  = ev.get("timestamp", 0)
                for j in range(i+1, len(tl)):
                    nev = tl[j]
                    if nev.get("session_id") != sid and sid:
                        break
                    if _etype(nev) == "article.viewed":
                        gap = (nev.get("timestamp", 0) - t0) / 1000
                        if 5 <= gap <= TIME_CAP_S:
                            article_times[aid].append(round(gap))
                        next_aid = nev.get("item_id") or _props(nev).get("articleKey") or ""
                        if next_aid and next_aid != aid:
                            article_next[aid][next_aid] += 1
                        break

            if et == "article.feedback" and aid:
                val = props.get("value", "")
                if val in ("helpful", "not_helpful"):
                    feedback_sent[aid][val] += 1

            # ── Search intelligence ────────────────────────────────────────────
            if et == "search.performed":
                query = (props.get("query") or "").strip()
                if query:
                    ts = ev.get("timestamp", 0)
                    is_final = True
                    if i+1 < len(tl):
                        nev = tl[i+1]
                        if _etype(nev) == "search.performed" and (nev.get("timestamp",0)-ts) < SEARCH_GAP_MS:
                            is_final = False
                    if is_final:
                        search_total += 1
                        ql = query.lower()
                        query_counter[ql] += 1
                        if props.get("resultCount") == 0:
                            zero_result_q[ql] += 1
                        sid = ev.get("session_id") or ""
                        for j in range(i+1, len(tl)):
                            nev = tl[j]
                            if nev.get("session_id") != sid and sid:
                                break
                            if _etype(nev) == "article.viewed":
                                search_converted += 1
                                break

            # ── Video intelligence ─────────────────────────────────────────────
            if et == "video.watched":
                title = props.get("videoTitle", "").strip()
                url   = props.get("videoUrl", "")
                if title:
                    video_counter[title] += 1
                    if url and title not in video_urls:
                        video_urls[title] = url

        # ── Session analytics ──────────────────────────────────────────────────
        sessions_map = defaultdict(list)
        for ev in tl:
            sid = ev.get("session_id") or "unknown"
            if sid != "unknown":
                sessions_map[sid].append(ev)

        for sid, evts in sessions_map.items():
            evts_s  = sorted(evts, key=lambda e: e.get("timestamp", 0))
            ts_list = [e.get("timestamp", 0) for e in evts_s if e.get("timestamp")]
            if len(ts_list) < 2:
                continue
            dur_s = min((max(ts_list) - min(ts_list)) / 1000, 28800)
            meaningful = [e for e in evts_s if _etype(e) != "profile.viewed"]
            ec    = len(meaningful)
            depth = "bounce" if ec <= 2 else "normal" if ec <= 9 else "deep"
            has_logout = any(_etype(e) == "auth.logout" for e in evts_s)
            date_str   = datetime.utcfromtimestamp(min(ts_list)/1000).strftime("%Y-%m-%d")
            act_time   = defaultdict(float)
            for i in range(len(meaningful)-1):
                gap    = min((meaningful[i+1].get("timestamp",0)-meaningful[i].get("timestamp",0))/1000, 300)
                bucket = ACTIVITY_MAP.get(_etype(meaningful[i]), "other")
                act_time[bucket] += gap
            all_sessions.append({
                "date": date_str, "duration_s": round(dur_s),
                "has_logout": has_logout, "event_count": ec,
                "depth": depth, "act_time": dict(act_time),
            })

    # ── Step 5: Assemble article performance ──────────────────────────────────
    total_views = sum(int(r.get("count", 0)) for r in views_rows)
    articles = []
    for r in views_rows:
        aid    = r.get("itemId") or r.get("item_id") or ""
        views  = int(r.get("count", 0))
        label  = aid.replace("-"," ").replace("_"," ").title()
        fb     = feedback_sent.get(aid, {"helpful":0,"not_helpful":0})
        hlp    = fb["helpful"]; nhlp = fb["not_helpful"]
        tot_fb = hlp + nhlp
        hlp_pct = round(hlp/tot_fb*100) if tot_fb else None
        times   = article_times.get(aid, [])
        avg_t   = round(sum(times)/len(times)) if times else None
        score_v = min(30, round(views/max(total_views,1)*300))
        score_h = round(hlp_pct*0.4) if hlp_pct is not None else 15
        score_t = min(30, round(avg_t/TIME_CAP_S*30)) if avg_t else 15
        is_dead = len(article_next) > 0 and all(aid not in c for c in article_next.values())
        next_arts = [{"id":k,"label":k.replace("-"," ").title(),"count":v}
                     for k,v in sorted(article_next.get(aid,{}).items(), key=lambda x:-x[1])[:3]]
        articles.append({
            "id": aid, "label": label, "views": views,
            "share_pct": round(views/total_views*100,1) if total_views else 0,
            "helpful": hlp, "not_helpful": nhlp, "helpful_pct": hlp_pct,
            "total_feedback": tot_fb,
            "avg_seconds": avg_t,
            "min_seconds": round(min(times)) if times else None,
            "max_seconds": round(max(times)) if times else None,
            "time_sample": len(times),
            "health_score": score_v+score_h+score_t,
            "is_dead_end": is_dead, "next_articles": next_arts,
        })
    articles.sort(key=lambda x: -x["views"])

    # ── Step 6: Assemble search intelligence ──────────────────────────────────
    article_slugs = {(r.get("itemId") or "").lower().replace("-"," ") for r in views_rows}
    gaps = []
    for q, cnt in query_counter.most_common(30):
        q_words = set(q.lower().split())
        matched = any(len(q_words & set(slug.split())) >= 1 for slug in article_slugs)
        gaps.append({"query":q,"count":cnt,"is_zero_result":q in zero_result_q,"has_content":matched})

    # ── Step 7: Assemble session analytics ────────────────────────────────────
    sess_result = {"total_sessions":0,"avg_seconds":0,"median_seconds":0,
                   "p90_seconds":0,"pct_with_logout":0,
                   "depth_distribution":{},"activity_breakdown":[],"daily_avg":[],
                   "note":"Based on top-10 most active authenticated users (CMS top-n cap=10)"}
    if all_sessions:
        durs    = sorted(s["duration_s"] for s in all_sessions)
        n       = len(durs)
        act_tot = defaultdict(float)
        for s in all_sessions:
            for b,t in s["act_time"].items(): act_tot[b] += t
        tot_t   = sum(act_tot.values()) or 1
        daily_m = defaultdict(list)
        for s in all_sessions: daily_m[s["date"]].append(s["duration_s"])
        sess_result.update({
            "total_sessions": n,
            "avg_seconds":    round(sum(durs)/n),
            "median_seconds": durs[n//2],
            "p90_seconds":    durs[int(n*0.9)],
            "pct_with_logout":round(sum(1 for s in all_sessions if s["has_logout"])/n*100,1),
            "depth_distribution": dict(Counter(s["depth"] for s in all_sessions)),
            "activity_breakdown": sorted([
                {"bucket":b,"label":b.replace("_"," ").title(),
                 "avg_seconds":round(act_tot[b]/n),
                 "pct_time":round(act_tot[b]/tot_t*100,1)}
                for b in act_tot if b not in ("auth","other")
            ], key=lambda x:-x["avg_seconds"]),
            "daily_avg": [{"date":d,"avg_seconds":round(sum(v)/len(v)),"count":len(v)}
                          for d,v in sorted(daily_m.items())],
        })

    # ── Step 8: Categories ─────────────────────────────────────────────────────
    categories = [
        {"path":  r.get("itemId") or "",
         "label": (r.get("itemId") or "").replace("/category/","").replace("-"," ").title() or "Home",
         "count": int(r.get("count", 0))}
        for r in cat_rows
    ]

    # ── Step 9: Videos ─────────────────────────────────────────────────────────
    videos = [{"title":t,"count":c,"url":video_urls.get(t,"")}
              for t,c in video_counter.most_common(20)]

    result = {
        "articles": {
            "articles": articles, "total_views": total_views,
            "computed_at": datetime.utcnow().isoformat(),
            "note": "Time spent based on sample of top-10 authenticated users",
        },
        "search": {
            "top_queries":     [{"query":q,"count":c} for q,c in query_counter.most_common(20)],
            "zero_result":     [{"query":q,"count":c} for q,c in zero_result_q.most_common(10)],
            "content_gaps":    gaps,
            "total_searches":  search_total,
            "conversion_rate": round(search_converted/search_total*100,1) if search_total else 0,
            "computed_at":     datetime.utcnow().isoformat(),
            "note": "Based on top-10 authenticated users",
        },
        "sessions":   sess_result,
        "categories": {"categories": categories, "computed_at": datetime.utcnow().isoformat()},
        "videos":     {"videos": videos, "computed_at": datetime.utcnow().isoformat(),
                       "note": "Sample of top-10 users. itemId not populated on video events."},
        "computed_at": datetime.utcnow().isoformat(),
    }
    cache_set("intel:all", result)
    print(f"[{datetime.utcnow().isoformat()}] Intelligence done — articles={len(articles)} searches={search_total} sessions={len(all_sessions)}", flush=True)
    return result

async def get_intel():
    data, fresh = cache_get("intel:all")
    if data is None:
        data = await compute_intelligence()
    elif not fresh:
        asyncio.create_task(compute_intelligence())
    return data

# ── Background tasks ───────────────────────────────────────────────────────────
async def _bg_prefetch():
    await asyncio.sleep(5)
    while True:
        try:
            await prefetch_batch()
        except Exception as ex:
            print(f"Batch prefetch error: {ex}", flush=True)
        await asyncio.sleep(PREFETCH_SEC)

async def _bg_intel():
    await asyncio.sleep(15)  # wait for server to stabilise
    while True:
        try:
            await compute_intelligence()
        except Exception as ex:
            print(f"Intel compute error: {ex}", flush=True)
        await asyncio.sleep(INTEL_TTL)

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    t1 = asyncio.create_task(_bg_prefetch())
    t2 = asyncio.create_task(_bg_intel())
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
    data, fresh = cache_get("batch:all")
    if data is None:
        data = await prefetch_batch()
    elif not fresh:
        asyncio.create_task(prefetch_batch())
    payload  = json.dumps(data, separators=(",",":"))
    etag_val = hashlib.md5(payload.encode()).hexdigest()[:16]
    return Response(content=payload, media_type="application/json",
        headers={"Cache-Control":f"public, max-age={BATCH_TTL}, stale-while-revalidate={STALE_TTL}",
                 "ETag":f'"{etag_val}"', "Vary":"Accept-Encoding"})

@app.get("/api/articles")
async def api_articles():
    intel = await get_intel()
    return intel.get("articles", {"articles":[],"total_views":0})

@app.get("/api/search")
async def api_search():
    intel = await get_intel()
    return intel.get("search", {"top_queries":[],"zero_result":[],"content_gaps":[],"total_searches":0,"conversion_rate":0})

@app.get("/api/sessions")
async def api_sessions():
    intel = await get_intel()
    return intel.get("sessions", {"total_sessions":0})

@app.get("/api/videos")
async def api_videos():
    intel = await get_intel()
    return intel.get("videos", {"videos":[]})

@app.get("/api/categories")
async def api_categories():
    intel = await get_intel()
    return intel.get("categories", {"categories":[]})

@app.get("/health")
async def health():
    b, bf = cache_get("batch:all")
    i, fi = cache_get("intel:all")
    return {
        "status":        "ok",
        "api_key_set":   bool(API_KEY),
        "batch_cached":  b is not None,
        "batch_fresh":   bf,
        "intel_cached":  i is not None,
        "intel_fresh":   fi,
        "cache_entries": len(_cache),
        "ts":            datetime.utcnow().isoformat(),
    }

@app.get("/cache/clear")
async def clear_cache():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
    _cache.clear()
    asyncio.create_task(prefetch_batch())
    asyncio.create_task(compute_intelligence())
    return {"status": "cleared — recomputing batch and intelligence"}

@app.get("/debug/cms")
async def debug_cms():
    client = get_client()
    results = {}
    for name, url, params in [
        ("auth_topn",    f"{CMS_BASE}/cs-portal-auth-events/query/top-n",
         {"event":"auth.login","groupBy":"userId","n":5}),
        ("article_topn", f"{CMS_BASE}/cs-portal-content-events/query/top-n",
         {"event":"article.viewed","groupBy":"itemId","n":5}),
        ("timeseries",   f"{CMS_BASE}/cs-portal-auth-events/query/time-series",
         {"event":"auth.login","bucket":"day"}),
    ]:
        try:
            r = await client.get(url, params=params)
            body = r.text[:300]
            results[name] = {"status": r.status_code, "preview": body}
        except Exception as e:
            results[name] = {"error": str(e)}
    results["api_key_set"] = bool(API_KEY)
    results["intel_cached"] = cache_get("intel:all")[0] is not None
    return results

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        html = f.read()
    return HTMLResponse(content=html,
        headers={"Cache-Control":"public, max-age=300, stale-while-revalidate=3600"})

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f:
        content = f.read()
    return Response(content=content, media_type="application/javascript",
        headers={"Cache-Control":"public, max-age=86400"})
