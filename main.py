"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

CALL BUDGET:
  Full refresh: 19 calls, fully sequential, 1s gap between each
    - 9 time-series (KPIs)
    - 5 top-n queries (articles, feedback, categories, video users, search users)
    - 4 content timelines (top users from cs-portal-content-events)
    - 2 feedback timelines (top 2 users from cs-portal-feedback-events)
                        → unlocks helpful/not_helpful split per article
  Schedule: every 2 hours
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
import httpx, os, asyncio, time, json, hashlib, csv

# ── Config ─────────────────────────────────────────────────────────────────────
CMS_BASE      = "https://cms.audibene.net/api/metrics"
API_KEY       = os.environ.get("CMS_API_KEY", "")
DATA_START    = "2026-04-24"

# ── CSAT Survey Data ────────────────────────────────────────────────────────────
# Loaded once at startup from data/call_quality.csv
# Each row: {rating, consultant_id, consultant_name, team, date, solved}
_csat_rows: list = []

def _load_csat_csv():
    """Load call quality CSV. Checks root and data/ subfolder."""
    global _csat_rows
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_quality.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "call_quality.csv"),
        os.path.join(os.getcwd(), "call_quality.csv"),
        os.path.join(os.getcwd(), "data", "call_quality.csv"),
        "/app/call_quality.csv",
        "/app/data/call_quality.csv",
        "call_quality.csv",
        "data/call_quality.csv",
    ]
    print(f"[csat] looking for call_quality.csv in: {paths}", flush=True)
    for path in paths:
        if os.path.exists(path):
            rows = []
            with open(path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append({
                            "rating":   int(row["RATING"]),
                            "cid":      row["CONSULTANT_ID"],
                            "name":     row["CONSULTANT_NAME"],
                            "team":     row["CONSULTANT_TEAM"].strip(),
                            "date":     row["DATE"],
                            "solved":   row["SOLVED"].strip().lower() == "true",
                        })
                    except (ValueError, KeyError):
                        continue
            _csat_rows = rows
            print(f"[csat] loaded {len(rows)} survey rows from {path}", flush=True)
            return
    print("[csat] data/call_quality.csv not found — CSAT section will be empty", flush=True)

def _compute_csat(date_from: str = "", date_to: str = "") -> dict:
    """Compute all CSAT aggregations, optionally filtered by date range."""
    rows = _csat_rows
    if not rows:
        return {"available": False, "note": "Upload data/call_quality.csv to enable CSAT section"}

    # Date filter
    if date_from and date_to:
        rows = [r for r in rows if date_from <= r["date"] <= date_to]
    elif date_from:
        rows = [r for r in rows if r["date"] >= date_from]

    if not rows:
        return {"available": True, "total": 0, "filtered": True,
                "date_from": date_from, "date_to": date_to,
                "note": "No surveys in selected date range"}

    total     = len(rows)
    ratings   = [r["rating"] for r in rows]
    avg       = round(sum(ratings) / total, 2)
    solved    = sum(1 for r in rows if r["solved"])
    solved_pct = round(solved / total * 100, 1)
    low       = sum(1 for r in rows if r["rating"] <= 2)
    low_pct   = round(low / total * 100, 1)

    # Rating distribution
    dist = Counter(ratings)
    rating_dist = [{"rating": i, "count": dist.get(i, 0),
                    "pct": round(dist.get(i, 0) / total * 100, 1)} for i in range(1, 6)]

    # Solved vs unsolved avg
    solved_rows   = [r for r in rows if r["solved"]]
    unsolved_rows = [r for r in rows if not r["solved"]]
    avg_solved   = round(sum(r["rating"] for r in solved_rows) / len(solved_rows), 2) if solved_rows else None
    avg_unsolved = round(sum(r["rating"] for r in unsolved_rows) / len(unsolved_rows), 2) if unsolved_rows else None

    # Weekly trend (group by ISO week)
    week_data: dict = defaultdict(lambda: {"ratings": [], "solved": 0, "total": 0})
    for r in rows:
        try:
            from datetime import date as dt_date
            d = dt_date.fromisoformat(r["date"])
            # Monday of that week
            week_start = (d - __import__('datetime').timedelta(days=d.weekday())).isoformat()
            week_data[week_start]["ratings"].append(r["rating"])
            week_data[week_start]["total"] += 1
            if r["solved"]:
                week_data[week_start]["solved"] += 1
        except Exception:
            continue
    weekly_trend = sorted([
        {"week": wk,
         "avg_rating": round(sum(v["ratings"]) / len(v["ratings"]), 2),
         "solved_pct": round(v["solved"] / v["total"] * 100, 1),
         "total": v["total"]}
        for wk, v in week_data.items() if v["ratings"]
    ], key=lambda x: x["week"])

    # Team stats
    team_data: dict = defaultdict(lambda: {"ratings": [], "solved": 0, "total": 0})
    for r in rows:
        t = r["team"]
        team_data[t]["ratings"].append(r["rating"])
        team_data[t]["total"] += 1
        if r["solved"]:
            team_data[t]["solved"] += 1
    teams = sorted([
        {"team": t,
         "avg_rating": round(sum(v["ratings"]) / len(v["ratings"]), 2),
         "solved_pct": round(v["solved"] / v["total"] * 100, 1),
         "total": v["total"],
         "low_pct": round(sum(1 for x in v["ratings"] if x <= 2) / v["total"] * 100, 1)}
        for t, v in team_data.items()
    ], key=lambda x: -x["total"])

    # Consultant stats (min 10 surveys in range)
    cons_data: dict = defaultdict(lambda: {"name": "", "team": "", "ratings": [], "solved": 0, "total": 0})
    for r in rows:
        c = r["cid"]
        cons_data[c]["name"] = r["name"]
        cons_data[c]["team"] = r["team"]
        cons_data[c]["ratings"].append(r["rating"])
        cons_data[c]["total"] += 1
        if r["solved"]:
            cons_data[c]["solved"] += 1
    cons_list = [
        {"cid": c,
         "name": v["name"],
         "team": v["team"],
         "avg_rating": round(sum(v["ratings"]) / len(v["ratings"]), 2),
         "solved_pct": round(v["solved"] / v["total"] * 100, 1),
         "total": v["total"],
         "low_pct": round(sum(1 for x in v["ratings"] if x <= 2) / v["total"] * 100, 1)}
        for c, v in cons_data.items() if v["total"] >= 10
    ]
    top_consultants  = sorted(cons_list, key=lambda x: -x["avg_rating"])[:10]
    low_consultants  = sorted(cons_list, key=lambda x: x["avg_rating"])[:10]

    return {
        "available":      True,
        "filtered":       bool(date_from),
        "date_from":      date_from,
        "date_to":        date_to,
        "total":          total,
        "avg_rating":     avg,
        "solved_pct":     solved_pct,
        "low_pct":        low_pct,
        "avg_solved":     avg_solved,
        "avg_unsolved":   avg_unsolved,
        "rating_dist":    rating_dist,
        "weekly_trend":   weekly_trend,
        "teams":          teams,
        "top_consultants": top_consultants,
        "low_consultants": low_consultants,
    }

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

        # ── Calls 15-18: content timelines + feedback timelines ──────────────
        all_events = []
        fb_events  = []   # feedback timeline events (separate project)
        for uid in users:
            tl = await _timeline("cs-portal-content-events", uid, limit=500)
            all_events.extend(tl)
            print(f"[refresh] timeline {uid[:16]}: {len(tl)} events", flush=True)

        # Fetch feedback timelines for top 2 users (within call budget)
        for uid in users[:2]:
            tl = await _timeline("cs-portal-feedback-events", uid, limit=200)
            fb_events.extend(tl)
            print(f"[refresh] fb-timeline {uid[:16]}: {len(tl)} events", flush=True)

        print(f"[refresh] intel calls done — {len(all_events)} content events, {len(fb_events)} feedback events", flush=True)

        sorted_ev = sorted(all_events, key=lambda e: e.get("timestamp", 0))

        # ── Extract feedback sentiment from feedback timelines ────────────────
        fb_helpful     = defaultdict(int)   # aid -> helpful count
        fb_not_helpful = defaultdict(int)   # aid -> not_helpful count
        for ev in fb_events:
            if _etype(ev) != "article.feedback":
                continue
            props = _props(ev)
            aid   = ev.get("item_id") or props.get("articleKey") or ""
            val   = props.get("value", "")
            if not aid:
                continue
            if val == "helpful":
                fb_helpful[aid] += 1
            elif val == "not_helpful":
                fb_not_helpful[aid] += 1

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

        # ── Extract article intelligence from timelines ───────────────────────
        article_times   = defaultdict(list)   # aid -> [seconds spent]
        article_next    = defaultdict(Counter) # aid -> {next_aid: count}
        article_bounces = defaultdict(int)     # aid -> bounce count (<30s)
        article_revisits= defaultdict(int)     # aid -> revisit count
        session_durations = []
        session_depths    = []
        hour_counts       = defaultdict(int)   # hour -> event count
        seen_article_sessions = defaultdict(set)  # aid -> set of session_ids

        # Group by session
        sessions_map = defaultdict(list)
        for ev in sorted_ev:
            sid = ev.get("session_id") or "nosession"
            sessions_map[sid].append(ev)

        for sid, evts in sessions_map.items():
            if sid == "nosession": continue
            evts_s = sorted(evts, key=lambda e: e.get("timestamp", 0))
            ts_list = [e.get("timestamp",0) for e in evts_s if e.get("timestamp")]
            if len(ts_list) >= 2:
                dur = min((max(ts_list)-min(ts_list))/1000, 28800)
                session_durations.append(round(dur))
                # Hour of day from session start
                hour = datetime.utcfromtimestamp(min(ts_list)/1000).hour
                hour_counts[hour] += 1

            meaningful = [e for e in evts_s if _etype(e) != "profile.viewed"]
            session_depths.append(len(meaningful))

            # Article time spent + bounce + navigation paths
            for i, ev in enumerate(evts_s):
                et    = _etype(ev)
                props = _props(ev)
                aid   = ev.get("item_id") or props.get("articleKey") or ""
                if et == "article.viewed" and aid:
                    t0 = ev.get("timestamp", 0)
                    # Revisit detection
                    if sid in seen_article_sessions[aid]:
                        article_revisits[aid] += 1
                    else:
                        seen_article_sessions[aid].add(sid)
                    # Time spent + bounce
                    for j in range(i+1, len(evts_s)):
                        nev = evts_s[j]
                        if _etype(nev) == "article.viewed":
                            gap = (nev.get("timestamp",0) - t0) / 1000
                            if 5 <= gap <= 300:
                                article_times[aid].append(round(gap))
                                if gap < 30:
                                    article_bounces[aid] += 1
                            next_aid = nev.get("item_id") or _props(nev).get("articleKey") or ""
                            if next_aid and next_aid != aid:
                                article_next[aid][next_aid] += 1
                            break

        # ── Session analytics from timelines ──────────────────────────────────
        n_sess = len(session_durations)
        if n_sess > 0:
            durs_s = sorted(session_durations)
            avg_dur    = round(sum(durs_s)/n_sess)
            median_dur = durs_s[n_sess//2]
            p90_dur    = durs_s[int(n_sess*0.9)]
            depths     = Counter("bounce" if d<=2 else "normal" if d<=9 else "deep" for d in session_depths)
        else:
            avg_dur = median_dur = p90_dur = 0
            depths = {}

        # ── daily_avg: avg session duration grouped by date ───────────────────
        daily_dur_map = defaultdict(list)  # date -> [seconds]
        for sid, evts in sessions_map.items():
            if sid == "nosession": continue
            evts_s = sorted(evts, key=lambda e: e.get("timestamp", 0))
            ts_list = [e.get("timestamp",0) for e in evts_s if e.get("timestamp")]
            if len(ts_list) >= 2:
                dur = min((max(ts_list)-min(ts_list))/1000, 28800)
                d   = datetime.utcfromtimestamp(min(ts_list)/1000).strftime("%Y-%m-%d")
                daily_dur_map[d].append(round(dur))
        daily_avg = [
            {"date": d, "avg_seconds": round(sum(v)/len(v))}
            for d, v in sorted(daily_dur_map.items())
        ]

        # ── activity_breakdown: avg seconds per event type per session ────────
        event_type_times = defaultdict(list)
        for sid, evts in sessions_map.items():
            if sid == "nosession": continue
            evts_s = sorted(evts, key=lambda e: e.get("timestamp", 0))
            # Time between consecutive events of each type
            for i, ev in enumerate(evts_s[:-1]):
                t0 = ev.get("timestamp", 0)
                t1 = evts_s[i+1].get("timestamp", 0)
                gap = (t1 - t0) / 1000
                if 2 <= gap <= 600:  # ignore sub-2s and >10min gaps
                    event_type_times[_etype(ev)].append(gap)
        activity_keys = ["article.viewed", "search.performed", "video.watched", "category.viewed"]
        activity_labels = {"article.viewed":"Reading","search.performed":"Searching",
                           "video.watched":"Watching","category.viewed":"Browsing"}
        total_act_time = sum(
            sum(event_type_times[k]) for k in activity_keys if event_type_times[k]
        )
        activity_breakdown = []
        for k in activity_keys:
            times_k = event_type_times.get(k, [])
            if not times_k: continue
            avg_s = round(sum(times_k)/len(times_k))
            pct   = round(sum(times_k)/max(total_act_time,1)*100)
            activity_breakdown.append({"label": activity_labels[k], "avg_seconds": avg_s, "pct_time": pct})
        activity_breakdown.sort(key=lambda x: -x["avg_seconds"])

        # ── pct_with_logout: sessions that have an explicit logout ────────────
        sessions_with_logout = sum(
            1 for sid, evts in sessions_map.items()
            if sid != "nosession" and any(_etype(e) == "auth.logout" for e in evts)
        )
        pct_logout = round(sessions_with_logout / max(n_sess, 1) * 100, 1)

        # ── KPI-derived metrics (from batch — zero extra calls) ───────────────
        def series_total(key):
            return sum(p.get("count",0) for p in batch.get(key,{}).get("series",[]))
        def series_last_n(key, n=7):
            s = batch.get(key,{}).get("series",[])
            return sum(p.get("count",0) for p in s[-n:]) if s else 0

        total_logins   = series_total("auth.login")
        total_articles = series_total("article.viewed")
        total_searches = series_total("search.performed")
        total_videos   = series_total("video.watched")

        # Content consumption ratio — articles per login
        consumption_ratio = round(total_articles/max(total_logins,1)*100)/100

        # Search frustration index — searches per login (high = struggling)
        frustration_idx = round(total_searches/max(total_logins,1)*100)/100

        # Engagement velocity — last 7 days vs previous 7 days
        logins_last7  = series_last_n("auth.login", 7)
        logins_prev7  = series_last_n("auth.login", 14) - logins_last7
        velocity = round((logins_last7 - logins_prev7) / max(logins_prev7,1) * 100, 1) if logins_prev7 else 0

        # Self-service sessions (login → article → no search) from timelines
        self_service = 0
        total_sess_with_login = 0
        for sid, evts in sessions_map.items():
            types = [_etype(e) for e in evts]
            if "auth.login" in types:
                total_sess_with_login += 1
                if "article.viewed" in types and "search.performed" not in types:
                    self_service += 1
        self_service_rate = round(self_service/max(total_sess_with_login,1)*100,1)

        # ── Assemble articles with full intelligence ──────────────────────────
        fb_map      = {r.get("itemId") or "": int(r.get("count",0)) for r in fb_rows}
        total_views = sum(int(r.get("count",0)) for r in art_rows)
        articles    = []
        for r in art_rows:
            aid   = r.get("itemId") or r.get("item_id") or ""
            views = int(r.get("count", 0))
            fb    = fb_map.get(aid, 0)
            times = article_times.get(aid, [])
            avg_t = round(sum(times)/len(times)) if times else None
            min_t = round(min(times)) if times else None
            max_t = round(max(times)) if times else None
            bounce_rate = round(article_bounces.get(aid,0)/max(len(times),1)*100,1) if times else None
            revisits = article_revisits.get(aid, 0)
            next_arts = [{"id":k,"label":k.replace("-"," ").title(),"count":v}
                         for k,v in sorted(article_next.get(aid,{}).items(),key=lambda x:-x[1])[:3]]
            # Feedback sentiment from timeline sample
            hlp     = fb_helpful.get(aid, 0)
            not_hlp = fb_not_helpful.get(aid, 0)
            hlp_total = hlp + not_hlp
            hlp_pct = round(hlp / hlp_total * 100, 1) if hlp_total > 0 else None
            # Use timeline sentiment if available, else fall back to top-n count only
            fb_total = hlp_total if hlp_total > 0 else fb
            # Health score: views(40) + time_spent(30) + feedback(30)
            score_v = min(40, round(views/max(total_views,1)*400))
            score_t = min(30, round(avg_t/300*30)) if avg_t else 10
            score_f = min(30, fb_total * 5) if fb_total > 0 else 10
            # Bonus for high helpful rate
            if hlp_pct and hlp_pct >= 80: score_f = min(30, score_f + 5)
            # Penalty for high bounce or low helpful rate
            if bounce_rate and bounce_rate > 60: score_t = max(0, score_t - 15)
            if hlp_pct and hlp_pct < 40: score_f = max(0, score_f - 10)
            health = score_v + score_t + score_f
            # Priority flag: high views + high bounce or no feedback or low helpful rating
            needs_attention = (views > 50 and (
                (bounce_rate and bounce_rate > 60)
                or fb_total == 0
                or (hlp_pct is not None and hlp_pct < 50)
            ))
            articles.append({
                "id": aid,
                "label": aid.replace("-"," ").replace("_"," ").title(),
                "views": views,
                "share_pct": round(views/total_views*100,1) if total_views else 0,
                "helpful": hlp, "not_helpful": not_hlp, "helpful_pct": hlp_pct,
                "total_feedback": fb_total,
                "avg_seconds": avg_t,
                "min_seconds": min_t,
                "max_seconds": max_t,
                "bounce_rate": bounce_rate,
                "revisits": revisits,
                "time_sample": len(times),
                "health_score": health,
                "needs_attention": needs_attention,
                "is_dead_end": len(article_next) > 0 and aid not in {k for c in article_next.values() for k in c},
                "next_articles": next_arts,
            })

        # Sort by needs_attention first, then by views
        articles.sort(key=lambda x: (-x["needs_attention"], -x["views"]))

        # ── Article improvement priority ──────────────────────────────────────
        priority = [
            {"id": a["id"], "label": a["label"], "views": a["views"],
             "issue": "High bounce rate" if (a["bounce_rate"] and a["bounce_rate"]>60)
                      else "No feedback despite views" if a["total_feedback"]==0 and a["views"]>50
                      else "Low time spent" if (a["avg_seconds"] and a["avg_seconds"]<30)
                      else "Watch",
             "health_score": a["health_score"]}
            for a in articles if a["needs_attention"]
        ]

        # ── Content gaps ──────────────────────────────────────────────────────
        art_slugs = {(r.get("itemId") or "").lower().replace("-"," ") for r in art_rows}
        gaps = []
        for q, cnt in query_counter.most_common(20):
            q_words = set(q.lower().split())
            matched = any(len(q_words & set(s.split())) >= 1 for s in art_slugs)
            gaps.append({"query":q,"count":cnt,
                         "is_zero_result":q in zero_result_q,
                         "has_content":matched})

        # ── Weekly digest ─────────────────────────────────────────────────────
        top_art = articles[0]["label"] if articles else "N/A"
        digest_items = []
        if velocity > 10:  digest_items.append(f"Logins up {velocity}% vs last week")
        elif velocity < -10: digest_items.append(f"Logins down {abs(velocity)}% vs last week — worth investigating")
        if priority:       digest_items.append(f"{len(priority)} article(s) need attention")
        if gaps and any(not g["has_content"] for g in gaps):
            n_gaps = sum(1 for g in gaps if not g["has_content"])
            digest_items.append(f"{n_gaps} search term(s) have no matching article")
        digest_items.append(f"Most viewed: {top_art}")
        digest = " · ".join(digest_items) if digest_items else "No significant changes this week"

        # ── Store intel ───────────────────────────────────────────────────────
        intel = {
            "articles": {
                "articles": articles, "total_views": total_views,
                "priority": priority,
                "computed_at": datetime.utcnow().isoformat(),
                "note": "Views & feedback from top-n. Time/bounce from timeline sample.",
            },
            "search": {
                "top_queries":     [{"query":q,"count":c} for q,c in query_counter.most_common(20)],
                "zero_result":     [{"query":q,"count":c} for q,c in zero_result_q.most_common(10)],
                "content_gaps":    gaps,
                "total_searches":  search_total,
                "conversion_rate": round(search_conv/search_total*100,1) if search_total else 0,
                "frustration_index": frustration_idx,
                "computed_at":     datetime.utcnow().isoformat(),
                "note": "Queries from timeline sample. Frustration index = searches/logins.",
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
                "note": "From timeline sample.",
            },
            "sessions": {
                "total_sessions":  n_sess,
                "avg_seconds":     avg_dur,
                "median_seconds":  median_dur,
                "p90_seconds":     p90_dur,
                "pct_with_logout": pct_logout,
                "depth_distribution": dict(depths),
                "activity_breakdown": activity_breakdown,
                "daily_avg": daily_avg,
                "computed_at": datetime.utcnow().isoformat(),
                "note": "From timeline sample of top video/search users.",
            },
            "insights": {
                "consumption_ratio":  consumption_ratio,
                "frustration_index":  frustration_idx,
                "engagement_velocity":velocity,
                "self_service_rate":  self_service_rate,
                "hour_distribution":  dict(hour_counts),
                "weekly_digest":      digest,
                "computed_at":        datetime.utcnow().isoformat(),
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
    _load_csat_csv()        # load call quality survey data
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
    return (data or {}).get("sessions", {"total_sessions":0,"note":"Session sample from timelines."})

@app.get("/api/insights")
async def api_insights():
    data, _ = cache_get("intel:all")
    return (data or {}).get("insights", {
        "consumption_ratio":0,"frustration_index":0,
        "engagement_velocity":0,"self_service_rate":0,
        "hour_distribution":{},"weekly_digest":"No data yet.","computed_at":""
    })

@app.get("/api/videos")
async def api_videos():
    data, _ = cache_get("intel:all")
    return (data or {}).get("videos", {"videos":[]})

@app.get("/api/categories")
async def api_categories():
    data, _ = cache_get("intel:all")
    return (data or {}).get("categories", {"categories":[]})

@app.get("/api/csat")
async def api_csat(date_from: str = "", date_to: str = ""):
    """Call Quality Survey data, optionally filtered by date range."""
    return _compute_csat(date_from=date_from, date_to=date_to)

@app.get("/debug/csat")
async def debug_csat():
    """Check whether call_quality.csv was loaded successfully."""
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_quality.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "call_quality.csv"),
        os.path.join(os.getcwd(), "call_quality.csv"),
        os.path.join(os.getcwd(), "data", "call_quality.csv"),
        "/app/call_quality.csv",
        "/app/data/call_quality.csv",
    ]
    return {
        "rows_loaded": len(_csat_rows),
        "available": len(_csat_rows) > 0,
        "cwd": os.getcwd(),
        "paths_checked": {p: os.path.exists(p) for p in paths},
        "sample": _csat_rows[:2] if _csat_rows else [],
    }

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
        "call_budget":   "19 sequential calls per refresh, 1s gap each, every 2 hours",
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
