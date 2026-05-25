"""
CS Portal Analytics Dashboard — Backend
hear.com · Customer Support Intelligence Platform

Architecture:
  - Single shared httpx client with connection pooling
  - In-memory cache with TTL tiers (5min batch, 1hr sessions)
  - Background prefetch keeps cache warm
  - All CMS API calls go through this server — browser never touches CMS directly
  - Date filtering is client-side (CMS ignores since= on time-series)
  - top-n is hard-capped at 10 by CMS — session stats uses sample accordingly
"""

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
from collections import defaultdict, Counter
from typing import Optional
import httpx, os, asyncio, time, json, hashlib

# ── Config ────────────────────────────────────────────────────────────────────
CMS_BASE        = "https://cms.audibene.net/api/metrics"
API_KEY         = os.environ.get("CMS_API_KEY", "")
DATA_START      = "2026-04-24"
CACHE_TTL       = 300      # 5 min — batch data
STALE_TTL       = 3600     # 1 hr  — stale-while-revalidate
SESSION_TTL     = 3600     # 1 hr  — session stats (expensive)
PREFETCH_SEC    = 300      # prefetch every 5 min
SEARCH_GAP_MS   = 3000     # gap to detect "final" vs keystroke search
TIME_CAP_S      = 300      # cap time-spent-per-article at 5 min

# Events with data — confirmed via Claude Code investigation 2026-05-25
# profile.viewed excluded — confirmed heartbeat/auto-fire, empty properties
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

# All projects for session timeline fetches
ALL_PROJECTS = [
    "cs-portal-auth-events",
    "cs-portal-content-events",
    "cs-portal-feedback-events",
    "cs-portal-items-events",
    "cs-portal-profile-events",
    "cs-portal-scheduling-events",
]

# Activity bucket map for session analysis
ACTIVITY_MAP = {
    "auth.login":             "auth",
    "auth.logout":            "auth",
    "article.viewed":         "articles",
    "video.watched":          "videos",
    "search.performed":       "search",
    "article.feedback":       "feedback",
    "category.viewed":        "browsing",
    "order_supplies.visited": "supplies",
    "scheduling.started":     "scheduling",
    "scheduling.completed":   "scheduling",
}

ACTIVITY_LABELS = {
    "articles":  "Articles",
    "videos":    "Videos",
    "search":    "Search",
    "browsing":  "Category browsing",
    "supplies":  "Supplies",
    "feedback":  "Feedback",
    "scheduling":"Scheduling",
    "auth":      "Auth",
}

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str):
    e = _cache.get(key)
    if not e:
        return None, False
    age = time.time() - e["ts"]
    ttl   = SESSION_TTL if key.startswith("session:") else CACHE_TTL
    stale = SESSION_TTL * 2 if key.startswith("session:") else STALE_TTL
    if age < ttl:   return e["data"], True
    if age < stale: return e["data"], False
    return None, False

def cache_set(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}

def cache_del(key: str):
    _cache.pop(key, None)

# ── HTTP client ───────────────────────────────────────────────────────────────
_client: Optional[httpx.AsyncClient] = None

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            headers={"api-key": API_KEY, "Accept": "application/json"},
        )
    return _client

# ── Semaphores ────────────────────────────────────────────────────────────────
_sem_batch    = asyncio.Semaphore(5)   # batch time-series calls
_sem_topn     = asyncio.Semaphore(3)   # top-n calls
_sem_timeline = asyncio.Semaphore(20)  # timeline calls (higher — many in parallel)

# ── CMS helpers ───────────────────────────────────────────────────────────────
async def _get(url: str, params: dict, sem: asyncio.Semaphore, retries: int = 4) -> dict | list | None:
    client = get_client()
    async with sem:
        for attempt in range(retries):
            try:
                r = await client.get(url, params=params)
                if r.status_code in (429, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    return None
                text = r.text.strip()
                return json.loads(text) if text else None
            except Exception:
                await asyncio.sleep(2 ** attempt)
    return None

async def cms_timeseries(project: str, event: str) -> list:
    """Returns [{ts, count}] — NOTE: since= is ignored by CMS, always returns all data."""
    url  = f"{CMS_BASE}/{project}/query/time-series"
    data = await _get(url, {"event": event, "bucket": "day"}, _sem_batch)
    if not data:
        return []
    series = data.get("series", [])
    # Convert Unix ms timestamps to daily aggregation
    daily = defaultdict(int)
    for p in series:
        ts = p.get("ts") or p.get("timestamp")
        count = int(p.get("count", 0) or 0)
        if ts:
            try:
                d = datetime.utcfromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                daily[d] += count
            except Exception:
                pass
    return [{"date": d, "count": daily[d]} for d in sorted(daily)]

async def cms_topn(project: str, event: str, group_by: str = "itemId", n: int = 10) -> list:
    """Returns top-N items. NOTE: CMS hard-caps at 10 regardless of n param."""
    url  = f"{CMS_BASE}/{project}/query/top-n"
    data = await _get(url, {"event": event, "groupBy": group_by, "n": n}, _sem_topn)
    if not data:
        return []
    # Unwrap {"top": [...]} envelope
    rows = data.get("top", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []

async def cms_timeline(project: str, user_id: str, limit: int = 500) -> list:
    """Returns events for one user from one project, sorted oldest-first."""
    url  = f"{CMS_BASE}/{project}/query/user-timeline"
    data = await _get(url, {"userId": user_id, "since": "180d", "limit": limit}, _sem_timeline)
    if not data:
        return []
    events = data.get("events", data) if isinstance(data, dict) else data
    if not isinstance(events, list):
        return []
    return sorted(events, key=lambda e: e.get("timestamp", 0))

async def cms_all_projects_timeline(user_id: str) -> list:
    """Merge events across all projects for one user, sorted by timestamp."""
    results = await asyncio.gather(
        *[cms_timeline(p, user_id) for p in ALL_PROJECTS],
        return_exceptions=True
    )
    events = []
    for r in results:
        if isinstance(r, list):
            events.extend(r)
    return sorted(events, key=lambda e: e.get("timestamp", 0))

# ── Aggregation helpers ───────────────────────────────────────────────────────
def _etype(e: dict) -> str:
    return e.get("event_type") or e.get("eventType") or ""

def _props(e: dict) -> dict:
    p = e.get("properties")
    return p if isinstance(p, dict) else {}

# ── Batch prefetch ────────────────────────────────────────────────────────────
async def prefetch_batch() -> dict:
    print(f"[{datetime.utcnow().isoformat()}] Prefetching batch...")
    result = {}
    for ev in EVENTS:
        series = await cms_timeseries(ev["project"], ev["key"])
        result[ev["key"]] = {"series": series}
        await asyncio.sleep(0.15)
    cache_set("batch:all", result)
    print(f"[{datetime.utcnow().isoformat()}] Batch cached — {len(result)} events")
    return result

async def _background_prefetch():
    await asyncio.sleep(3)
    while True:
        try:
            await prefetch_batch()
        except Exception as ex:
            print(f"Prefetch error: {ex}")
        await asyncio.sleep(PREFETCH_SEC)

# ── Article performance ───────────────────────────────────────────────────────
async def _fetch_article_performance() -> dict:
    """
    Combines:
      - article views (top-n by itemId)
      - feedback sentiment per article (top-n + timeline sentiment split)
      - time spent per article (from top-10 user timelines — sample-based)
    """
    # 1. Top articles by views
    views_rows = await cms_topn("cs-portal-content-events", "article.viewed", "itemId", 10)

    # 2. Feedback top-n to get article list
    fb_rows = await cms_topn("cs-portal-feedback-events", "article.feedback", "itemId", 10)

    # 3. Top users for timeline-based calculations
    user_rows = await cms_topn("cs-portal-auth-events", "auth.login", "userId", 10)
    user_ids  = [
        r.get("userId") or r.get("itemId") or ""
        for r in user_rows
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:10]

    # 4. Fetch timelines in parallel for time-spent and feedback sentiment
    timelines = await asyncio.gather(
        *[cms_all_projects_timeline(uid) for uid in user_ids],
        return_exceptions=True
    )

    # Build article stats from timelines
    article_times: dict[str, list] = defaultdict(list)   # article_id -> [seconds]
    feedback_sentiment: dict[str, dict] = defaultdict(lambda: {"helpful": 0, "not_helpful": 0})
    article_next: dict[str, Counter]    = defaultdict(Counter)  # navigation paths

    for tl in timelines:
        if not isinstance(tl, list):
            continue
        for i, ev in enumerate(tl):
            et = _etype(ev)
            props = _props(ev)
            article_id = ev.get("item_id") or props.get("articleKey") or props.get("itemId") or ""

            # Time spent — gap to next article.viewed in same session
            if et == "article.viewed" and article_id:
                sid = ev.get("session_id") or ""
                t0  = ev.get("timestamp", 0)
                # Find next article.viewed in same session
                for j in range(i + 1, len(tl)):
                    nev = tl[j]
                    if nev.get("session_id") != sid and sid:
                        break
                    if _etype(nev) == "article.viewed":
                        gap = (nev.get("timestamp", 0) - t0) / 1000
                        if 5 <= gap <= TIME_CAP_S:
                            article_times[article_id].append(gap)
                        # Navigation path
                        next_art = nev.get("item_id") or _props(nev).get("articleKey") or ""
                        if next_art and next_art != article_id:
                            article_next[article_id][next_art] += 1
                        break

            # Feedback sentiment
            if et == "article.feedback" and article_id:
                val = props.get("value", "")
                if val in ("helpful", "not_helpful"):
                    feedback_sentiment[article_id][val] += 1

    # 5. Assemble article list from views top-n
    articles = []
    total_views = sum(int(r.get("count", 0)) for r in views_rows)

    for r in views_rows:
        aid     = r.get("itemId") or r.get("item_id") or ""
        views   = int(r.get("count", 0))
        label   = aid.replace("-", " ").replace("_", " ").title()
        fb      = feedback_sentiment.get(aid, {"helpful": 0, "not_helpful": 0})
        helpful = fb["helpful"]
        not_hlp = fb["not_helpful"]
        total_fb= helpful + not_hlp
        hlp_pct = round(helpful / total_fb * 100) if total_fb > 0 else None

        times   = article_times.get(aid, [])
        avg_t   = round(sum(times) / len(times)) if times else None
        min_t   = round(min(times)) if times else None
        max_t   = round(max(times)) if times else None

        # Dead-end: never appears as "next article" in anyone's path
        is_dead_end = all(aid not in counter for counter in article_next.values()) and len(article_next) > 0

        # Health score: 0-100
        # Components: views weight (30), helpful% (40), time-spent (30)
        score_views   = min(30, round(views / max(total_views, 1) * 300))
        score_helpful = round(hlp_pct * 0.4) if hlp_pct is not None else 15  # neutral if no feedback
        score_time    = min(30, round(avg_t / TIME_CAP_S * 30)) if avg_t else 15
        health_score  = score_views + score_helpful + score_time

        # Next articles (top 3)
        next_arts = [
            {"id": k, "label": k.replace("-", " ").title(), "count": v}
            for k, v in sorted(article_next.get(aid, {}).items(), key=lambda x: -x[1])[:3]
        ]

        articles.append({
            "id":          aid,
            "label":       label,
            "views":       views,
            "share_pct":   round(views / total_views * 100, 1) if total_views else 0,
            "helpful":     helpful,
            "not_helpful": not_hlp,
            "helpful_pct": hlp_pct,
            "total_feedback": total_fb,
            "avg_seconds": avg_t,
            "min_seconds": min_t,
            "max_seconds": max_t,
            "time_sample": len(times),
            "health_score":health_score,
            "is_dead_end": is_dead_end,
            "next_articles": next_arts,
        })

    # Sort by views desc
    articles.sort(key=lambda x: -x["views"])

    # Also build feedback for articles NOT in top-10 views
    for r in fb_rows:
        aid = r.get("itemId") or r.get("item_id") or ""
        if aid and not any(a["id"] == aid for a in articles):
            fb    = feedback_sentiment.get(aid, {"helpful": 0, "not_helpful": 0})
            total_fb = fb["helpful"] + fb["not_helpful"]
            articles.append({
                "id": aid, "label": aid.replace("-", " ").title(),
                "views": 0, "share_pct": 0,
                "helpful": fb["helpful"], "not_helpful": fb["not_helpful"],
                "helpful_pct": round(fb["helpful"]/total_fb*100) if total_fb else None,
                "total_feedback": total_fb,
                "avg_seconds": None, "min_seconds": None, "max_seconds": None,
                "time_sample": 0, "health_score": 0, "is_dead_end": False, "next_articles": [],
            })

    return {
        "articles":    articles,
        "total_views": total_views,
        "computed_at": datetime.utcnow().isoformat(),
        "note":        "Time spent based on sample of top-10 authenticated users",
    }

# ── Search intelligence ───────────────────────────────────────────────────────
async def _fetch_search_intelligence() -> dict:
    """
    Extracts final search queries (not keystrokes), zero-result searches,
    and content gap detection from user timelines.
    """
    user_rows = await cms_topn("cs-portal-auth-events", "auth.login", "userId", 10)
    user_ids  = [
        r.get("userId") or r.get("itemId") or ""
        for r in user_rows
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:10]

    timelines = await asyncio.gather(
        *[cms_timeline("cs-portal-content-events", uid) for uid in user_ids],
        return_exceptions=True
    )

    query_counter    = Counter()
    zero_result_q    = Counter()
    search_converted = 0   # search → article view in same session
    search_total     = 0

    for tl in timelines:
        if not isinstance(tl, list):
            continue
        for i, ev in enumerate(tl):
            if _etype(ev) != "search.performed":
                continue
            props = _props(ev)
            query = (props.get("query") or "").strip()
            result_count = props.get("resultCount")
            if not query:
                continue

            # Final query detection: next event is NOT a search within 3s
            ts = ev.get("timestamp", 0)
            is_final = True
            if i + 1 < len(tl):
                nev = tl[i + 1]
                gap = nev.get("timestamp", 0) - ts
                if _etype(nev) == "search.performed" and gap < SEARCH_GAP_MS:
                    is_final = False

            if not is_final:
                continue

            search_total += 1
            query_lower = query.lower()
            query_counter[query_lower] += 1

            if result_count == 0:
                zero_result_q[query_lower] += 1

            # Check if session converted to article view after this search
            sid = ev.get("session_id") or ""
            for j in range(i + 1, len(tl)):
                nev = tl[j]
                if nev.get("session_id") != sid and sid:
                    break
                if _etype(nev) == "article.viewed":
                    search_converted += 1
                    break

    # Top articles for content gap cross-reference
    article_rows = await cms_topn("cs-portal-content-events", "article.viewed", "itemId", 10)
    article_slugs = {
        (r.get("itemId") or "").lower().replace("-", " ")
        for r in article_rows
    }

    # Content gap: top searches with no matching article slug
    top_queries = query_counter.most_common(30)
    gaps = []
    for q, cnt in top_queries:
        q_words = set(q.lower().split())
        matched = any(
            len(q_words & set(slug.split())) >= 1
            for slug in article_slugs
        )
        gaps.append({
            "query":   q,
            "count":   cnt,
            "is_zero_result": q in zero_result_q,
            "has_content":    matched,
        })

    conversion_rate = round(search_converted / search_total * 100, 1) if search_total else 0

    return {
        "top_queries":       [{"query": q, "count": c} for q, c in query_counter.most_common(20)],
        "zero_result":       [{"query": q, "count": c} for q, c in zero_result_q.most_common(10)],
        "content_gaps":      gaps,
        "total_searches":    search_total,
        "conversion_rate":   conversion_rate,
        "computed_at":       datetime.utcnow().isoformat(),
        "note":              "Based on sample of top-10 authenticated users",
    }

# ── Session sample ────────────────────────────────────────────────────────────
async def _fetch_session_sample() -> dict:
    """
    Session analytics based on top-10 authenticated users.
    NOTE: CMS top-n is hard-capped at 10. Full population analytics
    requires a CMS API change to increase the cap or add histogram endpoint.
    """
    user_rows = await cms_topn("cs-portal-auth-events", "auth.login", "userId", 10)
    user_ids  = [
        r.get("userId") or r.get("itemId") or ""
        for r in user_rows
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:10]

    timelines = await asyncio.gather(
        *[cms_all_projects_timeline(uid) for uid in user_ids],
        return_exceptions=True
    )

    all_sessions = []
    for tl in timelines:
        if not isinstance(tl, list):
            continue
        # Group by session_id
        sessions: dict[str, list] = defaultdict(list)
        for ev in tl:
            sid = ev.get("session_id") or "unknown"
            if sid == "unknown":
                continue
            sessions[sid].append(ev)

        for sid, evts in sessions.items():
            evts_sorted = sorted(evts, key=lambda e: e.get("timestamp", 0))
            ts_list     = [e.get("timestamp", 0) for e in evts_sorted if e.get("timestamp")]
            if len(ts_list) < 2:
                continue

            t_start    = min(ts_list)
            t_end      = max(ts_list)
            duration_s = min((t_end - t_start) / 1000, 28800)  # cap 8hr
            date_str   = datetime.utcfromtimestamp(t_start / 1000).strftime("%Y-%m-%d")
            has_logout = any(_etype(e) == "auth.logout" for e in evts)

            # Profile.viewed excluded — confirmed heartbeat
            meaningful = [e for e in evts_sorted if _etype(e) != "profile.viewed"]
            event_count = len(meaningful)

            # Activity time breakdown
            act_time = defaultdict(float)
            for i in range(len(meaningful) - 1):
                gap = min((meaningful[i+1].get("timestamp",0) - meaningful[i].get("timestamp",0)) / 1000, 300)
                bucket = ACTIVITY_MAP.get(_etype(meaningful[i]), "other")
                act_time[bucket] += gap

            # Session depth category
            if event_count <= 2:   depth = "bounce"
            elif event_count <= 9: depth = "normal"
            else:                  depth = "deep"

            all_sessions.append({
                "session_id":  sid,
                "date":        date_str,
                "duration_s":  round(duration_s),
                "has_logout":  has_logout,
                "event_count": event_count,
                "depth":       depth,
                "act_time":    dict(act_time),
            })

    if not all_sessions:
        return {
            "total_sessions": 0, "avg_seconds": 0, "median_seconds": 0,
            "p90_seconds": 0, "pct_with_logout": 0,
            "depth_distribution": {}, "activity_breakdown": [], "daily_avg": [],
            "computed_at": datetime.utcnow().isoformat(),
            "note": "No sessions found in sample",
        }

    durations  = sorted(s["duration_s"] for s in all_sessions)
    n          = len(durations)
    avg_s      = round(sum(durations) / n)
    median_s   = durations[n // 2]
    p90_s      = durations[int(n * 0.9)]
    pct_logout = round(sum(1 for s in all_sessions if s["has_logout"]) / n * 100, 1)

    # Depth distribution
    depth_dist = Counter(s["depth"] for s in all_sessions)

    # Activity breakdown
    act_total = defaultdict(float)
    for s in all_sessions:
        for bucket, t in s["act_time"].items():
            act_total[bucket] += t
    total_t = sum(act_total.values()) or 1
    activity_breakdown = sorted([
        {
            "bucket":      b,
            "label":       ACTIVITY_LABELS.get(b, b.title()),
            "avg_seconds": round(act_total[b] / n),
            "pct_time":    round(act_total[b] / total_t * 100, 1),
        }
        for b in act_total if b not in ("auth", "other")
    ], key=lambda x: -x["avg_seconds"])

    # Daily avg
    daily: dict[str, list] = defaultdict(list)
    for s in all_sessions:
        daily[s["date"]].append(s["duration_s"])
    daily_avg = [
        {"date": d, "avg_seconds": round(sum(v)/len(v)), "count": len(v)}
        for d, v in sorted(daily.items())
    ]

    return {
        "total_sessions":    n,
        "avg_seconds":       avg_s,
        "median_seconds":    median_s,
        "p90_seconds":       p90_s,
        "pct_with_logout":   pct_logout,
        "depth_distribution":dict(depth_dist),
        "activity_breakdown":activity_breakdown,
        "daily_avg":         daily_avg,
        "computed_at":       datetime.utcnow().isoformat(),
        "note":              "Based on top-10 most active authenticated users. CMS top-n is hard-capped at 10.",
    }

# ── Video intelligence ────────────────────────────────────────────────────────
async def _fetch_video_intelligence() -> dict:
    """
    Per-video counts from user timelines (itemId is null on video events,
    so we group by properties.videoTitle).
    """
    user_rows = await cms_topn("cs-portal-content-events", "video.watched", "userId", 10)
    user_ids  = [
        r.get("userId") or r.get("itemId") or ""
        for r in user_rows
        if (r.get("userId") or r.get("itemId") or "") not in ("anonymous", "")
    ][:10]

    timelines = await asyncio.gather(
        *[cms_timeline("cs-portal-content-events", uid) for uid in user_ids],
        return_exceptions=True
    )

    video_counter = Counter()
    video_urls    = {}

    for tl in timelines:
        if not isinstance(tl, list):
            continue
        for ev in tl:
            if _etype(ev) != "video.watched":
                continue
            props = _props(ev)
            title = props.get("videoTitle", "").strip()
            url   = props.get("videoUrl", "")
            if title:
                video_counter[title] += 1
                if url and title not in video_urls:
                    video_urls[title] = url

    videos = [
        {"title": t, "count": c, "url": video_urls.get(t, "")}
        for t, c in video_counter.most_common(20)
    ]

    return {
        "videos":      videos,
        "computed_at": datetime.utcnow().isoformat(),
        "note":        "Sample of top-10 video watchers. itemId not populated on video events.",
    }

# ── Category intelligence ─────────────────────────────────────────────────────
async def _fetch_categories() -> dict:
    rows = await cms_topn("cs-portal-content-events", "category.viewed", "itemId", 10)
    cats = [
        {
            "path":  r.get("itemId") or r.get("item_id") or "",
            "label": (r.get("itemId") or "").replace("/category/", "").replace("-", " ").title() or "Home",
            "count": int(r.get("count", 0)),
        }
        for r in rows
    ]
    return {"categories": cats, "computed_at": datetime.utcnow().isoformat()}

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_background_prefetch())
    yield
    task.cancel()
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan, title="CS Portal Analytics")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/metrics/batch")
async def batch_metrics():
    """Main time-series batch — all events, all-time data."""
    data, fresh = cache_get("batch:all")
    if data is None:
        data = await prefetch_batch()
    elif not fresh:
        asyncio.create_task(prefetch_batch())
    payload  = json.dumps(data, separators=(",", ":"))
    etag_val = hashlib.md5(payload.encode()).hexdigest()[:16]
    return Response(
        content=payload, media_type="application/json",
        headers={
            "Cache-Control": f"public, max-age={CACHE_TTL}, stale-while-revalidate={STALE_TTL}",
            "ETag": f'"{etag_val}"', "Vary": "Accept-Encoding",
        },
    )

@app.get("/api/articles")
async def article_performance(force: bool = False):
    """Article performance: views, time spent, feedback sentiment, health score."""
    key = "articles:performance"
    if not force:
        data, fresh = cache_get(key)
        if fresh:   return data
        if data:    asyncio.create_task(_bg("articles:performance", _fetch_article_performance)); return data
    result = await _fetch_article_performance()
    cache_set(key, result)
    return result

@app.get("/api/search")
async def search_intelligence(force: bool = False):
    """Search analytics: final queries, zero-results, content gaps, conversion rate."""
    key = "search:intelligence"
    if not force:
        data, fresh = cache_get(key)
        if fresh:   return data
        if data:    asyncio.create_task(_bg("search:intelligence", _fetch_search_intelligence)); return data
    result = await _fetch_search_intelligence()
    cache_set(key, result)
    return result

@app.get("/api/sessions")
async def session_sample(force: bool = False):
    """Session analytics (sample of top-10 authenticated users)."""
    key = "session:stats"
    if not force:
        data, fresh = cache_get(key)
        if fresh:   return data
        if data:    asyncio.create_task(_bg("session:stats", _fetch_session_sample)); return data
    result = await _fetch_session_sample()
    cache_set(key, result)
    return result

@app.get("/api/videos")
async def video_intelligence(force: bool = False):
    """Per-video counts from user timelines."""
    key = "videos:intelligence"
    if not force:
        data, fresh = cache_get(key)
        if fresh:   return data
        if data:    asyncio.create_task(_bg("videos:intelligence", _fetch_video_intelligence)); return data
    result = await _fetch_video_intelligence()
    cache_set(key, result)
    return result

@app.get("/api/categories")
async def categories():
    """Top content categories."""
    key = "categories:top"
    data, fresh = cache_get(key)
    if fresh: return data
    result = await _fetch_categories()
    cache_set(key, result)
    return result

@app.get("/health")
async def health():
    data, fresh = cache_get("batch:all")
    return {
        "status":        "ok",
        "api_key_set":   bool(API_KEY),
        "batch_cached":  data is not None,
        "batch_fresh":   fresh,
        "cache_entries": len(_cache),
        "data_start":    DATA_START,
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
    return {"status": "cleared, client reset, prefetching..."}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        html = f.read()
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"},
    )

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f:
        content = f.read()
    return Response(
        content=content, media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )

# ── Background refresh helper ─────────────────────────────────────────────────
async def _bg(key: str, fn):
    try:
        result = await fn()
        cache_set(key, result)
    except Exception as ex:
        print(f"Background refresh error [{key}]: {ex}")
