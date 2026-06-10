"""
CS Portal Analytics — main.py
hear.com · Customer Support Intelligence

CALL BUDGET:
  Full refresh — fully sequential, 1s gap between each call
    - 9  time-series (KPIs, one per event type)
    - 3  top-n data   (articles, feedback, categories)
    - 3  top-n user IDs (video, search, article — to build user list)
    = 15 fixed calls
    + up to 10 content timelines  (Supabase enabled)
    + up to  2 feedback timelines (first 2 users only)
    = up to 27 calls total with Supabase / 21 without
  Schedule: every 2 hours
  Per page load: 0 calls — always served from cache
  On CMS error: serve stale cache, retry next cycle
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
from collections import defaultdict, Counter
import httpx, os, asyncio, time, json, hashlib, csv, secrets, io

# ── Supabase client (lazy init) ───────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_sb_enabled  = bool(SUPABASE_URL and SUPABASE_KEY)

async def _sb_request(method: str, path: str, body: dict = None) -> dict:
    """Make a request to Supabase REST API."""
    if not _sb_enabled:
        return {}
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    url = f"{SUPABASE_URL}/rest/v1{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        if method == "GET":
            r = await client.get(url, headers=headers)
        elif method == "POST":
            headers["Prefer"] = "return=representation"
            r = await client.post(url, headers=headers, json=body)
        elif method == "UPSERT":
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
            r = await client.post(url, headers=headers, json=body)
        else:
            return {}
        if r.status_code in (200, 201, 204):
            try: return r.json()
            except: return {}
        print(f"[supabase] {method} {path} → {r.status_code}: {r.text[:200]}", flush=True)
        return {}

async def _sb_ensure_tables():
    """Create tables if they don't exist using Supabase SQL API."""
    if not _sb_enabled:
        return
    sql = """
    CREATE TABLE IF NOT EXISTS cs_user_timelines (
        user_id      TEXT NOT NULL,
        project      TEXT NOT NULL,
        event_type   TEXT,
        item_id      TEXT,
        session_id   TEXT,
        ts           BIGINT,
        event_date   TEXT,
        properties   JSONB,
        fetched_at   TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (user_id, project, ts)
    );
    CREATE TABLE IF NOT EXISTS cs_user_fetch_log (
        user_id      TEXT PRIMARY KEY,
        last_fetched TIMESTAMPTZ DEFAULT NOW(),
        event_count  INT DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_timelines_date ON cs_user_timelines(event_date);
    CREATE INDEX IF NOT EXISTS idx_timelines_user ON cs_user_timelines(user_id);
    CREATE INDEX IF NOT EXISTS idx_timelines_type ON cs_user_timelines(event_type);
    """
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
            headers=headers,
            json={"sql": sql}
        )
        if r.status_code not in (200, 201, 204):
            # Try direct SQL via pg REST
            print(f"[supabase] table creation via RPC failed ({r.status_code}), trying direct", flush=True)
    print(f"[supabase] tables ready", flush=True)

async def _sb_get_unfetched_users(all_user_ids: list, limit: int = 10) -> list:
    """Return users we haven't fetched yet (or haven't fetched in 48h)."""
    if not _sb_enabled or not all_user_ids:
        return all_user_ids[:limit]
    # Get users we've already fetched recently
    result = await _sb_request("GET",
        "/cs_user_fetch_log?select=user_id,last_fetched&order=last_fetched.asc")
    if isinstance(result, list):
        fetched_recently = {
            r["user_id"] for r in result
            if r.get("last_fetched") and
            (datetime.utcnow() - datetime.fromisoformat(
                r["last_fetched"].replace("Z","").split("+")[0]
            )).total_seconds() < 48 * 3600
        }
        # Prioritise users not yet fetched
        unfetched = [u for u in all_user_ids if u not in fetched_recently]
        if not unfetched:
            # All fetched recently — refresh oldest ones (preserve Supabase's asc order)
            known_set = set(all_user_ids)
            unfetched = [r["user_id"] for r in result if r["user_id"] in known_set]
        return unfetched[:limit]
    return all_user_ids[:limit]

async def _sb_store_events(user_id: str, project: str, events: list):
    """Store timeline events for a user in Supabase."""
    if not _sb_enabled or not events:
        return
    rows = []
    for ev in events:
        ts = ev.get("timestamp", 0)
        if not ts:
            continue
        from datetime import datetime as dt
        try:
            event_date = dt.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        except Exception:
            continue
        rows.append({
            "user_id":    user_id,
            "project":    project,
            "event_type": _etype(ev),
            "item_id":    ev.get("item_id") or ev.get("itemId") or "",
            "session_id": ev.get("session_id") or ev.get("sessionId") or "",
            "ts":         int(ts),
            "event_date": event_date,
            "properties": _props(ev),
        })
    if not rows:
        return
    # Upsert in batches of 500
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        await _sb_request("UPSERT", "/cs_user_timelines", batch)
    # Update fetch log
    await _sb_request("UPSERT", "/cs_user_fetch_log", [{
        "user_id":    user_id,
        "last_fetched": datetime.utcnow().isoformat() + "Z",
        "event_count": len(rows),
    }])
    print(f"[supabase] stored {len(rows)} events for {user_id[:16]}", flush=True)

async def _sb_get_all_events(date_from: str = "", date_to: str = "") -> list:
    """Fetch all stored timeline events from Supabase, optionally filtered by date."""
    if not _sb_enabled:
        return []
    path = "/cs_user_timelines?select=user_id,event_type,item_id,session_id,ts,event_date,properties&project=eq.cs-portal-content-events"
    if date_from:
        path += f"&event_date=gte.{date_from}"
    if date_to:
        path += f"&event_date=lte.{date_to}"
    path += "&limit=100000&order=ts.asc"
    result = await _sb_request("GET", path)
    if isinstance(result, list):
        print(f"[supabase] loaded {len(result)} events from DB", flush=True)
        return result
    return []

async def _sb_get_user_count() -> int:
    """Get total number of unique users stored."""
    if not _sb_enabled:
        return 0
    result = await _sb_request("GET", "/cs_user_fetch_log?select=user_id")
    return len(result) if isinstance(result, list) else 0



# ── Config ─────────────────────────────────────────────────────────────────────
CMS_BASE      = "https://cms.audibene.net/api/metrics"
API_KEY       = os.environ.get("CMS_API_KEY", "")
DATA_START    = "2026-04-24"

# ── CSAT Survey Data ────────────────────────────────────────────────────────────
# Loaded once at startup from call_quality.csv
# Also rebuilt on-demand via POST /upload/csat
_csat_rows: list = []
_csat_index: dict = {}  # pre-built day-level index for O(days) not O(rows) queries

# Upload password — set CSAT_UPLOAD_PASSWORD env var on Render
# Default is "hearcom2024" — change it in Render environment variables
UPLOAD_PASSWORD = os.environ.get("CSAT_UPLOAD_PASSWORD", "hearcom2024")

# Path where csat_index.json is saved for serving
CSAT_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "csat_index.json")

# CS team allow-list — the raw Domo dataset contains teams/departments we don't
# want (German teams, sales, etc.), so we keep ONLY these. To add a new CS team
# later, append it here OR set the CSAT_TEAMS env var (comma-separated) in Render.
# Matching is case-insensitive and trims surrounding whitespace.
_DEFAULT_CSAT_TEAMS = "Team Amplifiers,Team Hear4Life,Voice AI,Team Sound Check"
CSAT_TEAMS_ALLOW = [
    t.strip() for t in os.getenv("CSAT_TEAMS", _DEFAULT_CSAT_TEAMS).split(",") if t.strip()
]
_CSAT_TEAMS_ALLOW_LC = {t.lower() for t in CSAT_TEAMS_ALLOW}


def _filter_allowed_teams(rows: list) -> list:
    """Keep only rows whose CONSULTANT_TEAM is in the allow-list (case-insensitive)."""
    if not _CSAT_TEAMS_ALLOW_LC:
        return rows
    kept = [r for r in rows if (r.get("team") or "").strip().lower() in _CSAT_TEAMS_ALLOW_LC]
    dropped = len(rows) - len(kept)
    if dropped:
        print(f"[csat] team filter: kept {len(kept)} rows, dropped {dropped} "
              f"(allow-list: {CSAT_TEAMS_ALLOW})", flush=True)
    return kept



def _build_csat_index(rows: list) -> dict:
    """Build the pre-aggregated day/week index from raw survey rows.
    Used by every data path — Domo pull, startup CSV, and manual upload.
    """
    global _csat_rows, _csat_index

    # Drop teams/departments outside the CS allow-list before anything else,
    # so no data source (Domo, CSV, upload) can leak unwanted teams in.
    rows = _filter_allowed_teams(rows)

    rows.sort(key=lambda r: r["date"])
    _csat_rows = rows

    # ── True FCR pre-pass ────────────────────────────────────────────────────
    # "First-call resolved" = the FIRST call on an opportunity was SOLVED and the
    # opportunity has NO later call ever (any follow-up call = not resolved).
    # We tag each row: fcr_eval=True only for the FIRST call of each opportunity
    # (that first call is the unit we score), and fcr_ok=True if resolved.
    # Rows with no opp_id are not eligible (fcr_eval stays False) so true FCR
    # simply has no data until OPPORTUNITY_ID is present in the dataset.
    _opp_calls = {}
    for _r in rows:
        oid = (_r.get("opp_id") or "").strip()
        _r["_fcr_eval"] = False
        _r["_fcr_ok"] = False
        if oid:
            _opp_calls.setdefault(oid, []).append(_r)
    for oid, calls in _opp_calls.items():
        # earliest call by full datetime (fallback to date) is the "first contact"
        calls.sort(key=lambda x: (x.get("datetime") or x.get("date") or ""))
        first = calls[0]
        first["_fcr_eval"] = True
        # resolved only if the first call was solved AND it's the only call ever
        first["_fcr_ok"] = bool(first.get("solved")) and len(calls) == 1

    BAD_NAMES = {"none", "null", "", "n/a"}
    day_map   = {}
    week_cons = {}

    import datetime as _dt_mod

    for r in rows:
        d    = r["date"]
        team = r["team"]
        cid  = r["cid"]
        name = r["name"]
        bad_team = not team or team.strip().lower() in BAD_NAMES
        # Frank AI bots (Voice AI team) are treated as consultants so the Voice AI
        # team shows members and an individual-calls table like any other team.
        bad_name = (not name or name.strip().lower() in BAD_NAMES)

        if d not in day_map:
            day_map[d] = {"t":0,"sr":0,"s":0,"l":0,"d":{},"tm":{},"cn":{},"rs":{},"ft":0,"fr":0}
        dm = day_map[d]
        reason = (r.get("reason") or "").strip() or "Unspecified"
        dm["t"]  += 1
        dm["sr"] += r["rating"]
        dm["s"]  += int(r["solved"])
        dm["l"]  += int(r["rating"] <= 2)
        dm["d"][r["rating"]] = dm["d"].get(r["rating"], 0) + 1
        if reason not in dm["rs"]:
            dm["rs"][reason] = {"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0}}
        dm["rs"][reason]["t"] += 1
        dm["rs"][reason]["sr"] += r["rating"]
        dm["rs"][reason]["s"] += int(r["solved"])
        dm["rs"][reason]["l"] += int(r["rating"] <= 2)
        dm["rs"][reason]["d"][r["rating"]] = dm["rs"][reason]["d"].get(r["rating"], 0) + 1
        # True FCR counters (only the first call of each opportunity is evaluated)
        if r.get("_fcr_eval"):
            dm["ft"] += 1
            dm["fr"] += int(r.get("_fcr_ok", False))

        if not bad_team:
            t = team.strip()
            if t not in dm["tm"]:
                dm["tm"][t] = {"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0},"rs":{},"ft":0,"fr":0}
            dm["tm"][t]["t"]  += 1
            dm["tm"][t]["sr"] += r["rating"]
            dm["tm"][t]["s"]  += int(r["solved"])
            dm["tm"][t]["l"]  += int(r["rating"] <= 2)
            dm["tm"][t]["d"][r["rating"]] = dm["tm"][t]["d"].get(r["rating"], 0) + 1
            if reason not in dm["tm"][t]["rs"]:
                dm["tm"][t]["rs"][reason] = {"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0}}
            dm["tm"][t]["rs"][reason]["t"] += 1
            dm["tm"][t]["rs"][reason]["sr"] += r["rating"]
            dm["tm"][t]["rs"][reason]["s"] += int(r["solved"])
            dm["tm"][t]["rs"][reason]["l"] += int(r["rating"] <= 2)
            dm["tm"][t]["rs"][reason]["d"][r["rating"]] = dm["tm"][t]["rs"][reason]["d"].get(r["rating"], 0) + 1
            if r.get("_fcr_eval"):
                dm["tm"][t]["ft"] += 1
                dm["tm"][t]["fr"] += int(r.get("_fcr_ok", False))

        if not bad_name:
            # Per-day consultant data — ensures team totals == sum of consultant totals
            if cid not in dm["cn"]:
                dm["cn"][cid] = {"n":name,"tm":team.strip(),"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0},"rs":{},"c":[],"ft":0,"fr":0}
            dm["cn"][cid]["t"]  += 1
            dm["cn"][cid]["sr"] += r["rating"]
            dm["cn"][cid]["s"]  += int(r["solved"])
            dm["cn"][cid]["l"]  += int(r["rating"] <= 2)
            dm["cn"][cid]["d"][r["rating"]] = dm["cn"][cid]["d"].get(r["rating"], 0) + 1
            if reason not in dm["cn"][cid]["rs"]:
                dm["cn"][cid]["rs"][reason] = {"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0}}
            dm["cn"][cid]["rs"][reason]["t"] += 1
            dm["cn"][cid]["rs"][reason]["sr"] += r["rating"]
            dm["cn"][cid]["rs"][reason]["s"] += int(r["solved"])
            dm["cn"][cid]["rs"][reason]["l"] += int(r["rating"] <= 2)
            dm["cn"][cid]["rs"][reason]["d"][r["rating"]] = dm["cn"][cid]["rs"][reason]["d"].get(r["rating"], 0) + 1
            if r.get("_fcr_eval"):
                dm["cn"][cid]["ft"] += 1
                dm["cn"][cid]["fr"] += int(r.get("_fcr_ok", False))
            # Per-call record for individual-call tables (Call ID → UCJ link).
            # Compact keys: i=call_id, r=rating, s=solved(0/1), rs=reason. Date comes from day key.
            call_id = r.get("call_id") or ""
            if call_id:
                dm["cn"][cid]["c"].append({
                    "i": call_id,
                    "r": r["rating"],
                    "s": int(r["solved"]),
                    "rs": reason,
                })

        if not bad_name:
            try:
                parts = d.split("-")
                dt  = _dt_mod.date(int(parts[0]), int(parts[1]), int(parts[2]))
                wk  = (dt - _dt_mod.timedelta(days=dt.weekday())).isoformat()
            except Exception:
                continue
            if wk not in week_cons:
                week_cons[wk] = {}
            if cid not in week_cons[wk]:
                week_cons[wk][cid] = {"n":name,"tm":team,"t":0,"sr":0,"s":0,"l":0,"d":{1:0,2:0,3:0,4:0,5:0}}
            week_cons[wk][cid]["t"]  += 1
            week_cons[wk][cid]["sr"] += r["rating"]
            week_cons[wk][cid]["s"]  += int(r["solved"])
            week_cons[wk][cid]["l"]  += int(r["rating"] <= 2)
            week_cons[wk][cid]["d"][r["rating"]] = week_cons[wk][cid]["d"].get(r["rating"], 0) + 1

    dates     = sorted(day_map.keys())
    index_data = {
        "available":  True,
        "date_min":   dates[0]  if dates else "",
        "date_max":   dates[-1] if dates else "",
        "days":       day_map,
        "week_cons":  week_cons,
        "total_rows": len(rows),
        "generated":  datetime.utcnow().isoformat() + "Z",
    }

    # Also keep the old _csat_index format for the /api/csat endpoint
    _csat_index["day_map"]   = day_map
    _csat_index["date_min"]  = index_data["date_min"]
    _csat_index["date_max"]  = index_data["date_max"]
    _csat_index["dates"]     = [r["date"] for r in rows]
    _csat_index["rows"]      = rows
    _csat_index["index_data"] = index_data   # cached for /api/csat/raw

    print(f"[csat] index built: {len(rows):,} rows · {len(day_map)} days · {len(week_cons)} weeks", flush=True)
    return index_data

def _parse_csv_text(text: str) -> list:
    """Parse CSAT CSV text (from disk or Domo export) into survey rows."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            raw_date = row.get("DATE") or row.get("DATETIME") or ""
            date_str = raw_date[:10]  # take just YYYY-MM-DD from datetime
            # opportunity id (for true FCR) — tolerate a few likely column names.
            opp = (row.get("OPPORTUNITY_ID") or row.get("OPPORTUNITYID")
                   or row.get("OpportunityId") or row.get("opportunityId")
                   or row.get("OPPORTUNITY") or "")
            rows.append({
                "rating": int(float(row["RATING"])),  # handles "5.0" and "5"
                "cid":    row["CONSULTANT_ID"],
                "name":   row["CONSULTANT_NAME"],
                "team":   row["CONSULTANT_TEAM"].strip(),
                "date":   date_str,
                "datetime": str(raw_date).strip(),  # full timestamp for FCR ordering
                "solved": str(row["SOLVED"]).strip().lower() == "true",
                "call_id": (row.get("CALL_ID") or "").strip(),
                "opp_id": str(opp).strip(),
                "reason": (row.get("REASON_FOR_CALL__C") or row.get("REASON_FOR_CALL")
                           or row.get("CALL_REASON") or row.get("REASON") or "").strip(),
            })
        except (ValueError, KeyError):
            continue
    return rows


# ── Domo automated pull ────────────────────────────────────────────────────────
# Pulls the CSAT dataset straight from Domo on the 2-hour refresh cycle, so the
# manual "Update CSAT" upload is no longer required. Credentials come from env
# vars set in Render (never hard-coded):
#   DOMO_CLIENT_ID, DOMO_CLIENT_SECRET, DOMO_DATASET_ID
# The OAuth client must be created at developer.domo.com with the `data` scope
# (requires a Domo admin). Tokens last ~1 hour; we fetch a fresh one each pull.
DOMO_CLIENT_ID     = os.getenv("DOMO_CLIENT_ID", "")
DOMO_CLIENT_SECRET = os.getenv("DOMO_CLIENT_SECRET", "")
DOMO_DATASET_ID    = os.getenv("DOMO_DATASET_ID", "")
DOMO_API_BASE      = os.getenv("DOMO_API_BASE", "https://api.domo.com")


def _domo_configured() -> bool:
    return bool(DOMO_CLIENT_ID and DOMO_CLIENT_SECRET and DOMO_DATASET_ID)


def _domo_fetch_csv() -> str | None:
    """Authenticate to Domo and export the CSAT dataset as CSV text.

    Returns the CSV string on success, or None on any failure (caller falls
    back to the bundled CSV). Synchronous httpx — called from the refresh loop
    via asyncio.to_thread so it never blocks the event loop.
    """
    if not _domo_configured():
        return None
    try:
        with httpx.Client(timeout=60.0) as client:
            # 1) OAuth client-credentials grant → bearer token (Basic auth: id:secret).
            #    A 400 invalid_request usually means the requested scope doesn't match
            #    what the client was granted. So try a few strategies in order:
            #    (a) no scope → Domo returns the client's own granted scopes,
            #    (b) scope=data, (c) scope="data user". First 200 wins.
            token = None
            last_err = ""
            for scope in (None, "data", "data user"):
                params = {"grant_type": "client_credentials"}
                if scope:
                    params["scope"] = scope
                tok = client.get(
                    f"{DOMO_API_BASE}/oauth/token",
                    params=params,
                    auth=(DOMO_CLIENT_ID, DOMO_CLIENT_SECRET),
                )
                if tok.status_code == 200:
                    token = tok.json().get("access_token")
                    granted = tok.json().get("scope", "?")
                    print(f"[domo] auth ok (scope requested={scope or 'none'}, granted={granted})", flush=True)
                    break
                last_err = f"HTTP {tok.status_code} {tok.text[:150]}"
                print(f"[domo] auth attempt scope={scope or 'none'} failed: {last_err}", flush=True)
            if not token:
                print(f"[domo] auth failed on all scope attempts — last: {last_err}", flush=True)
                return None

            # 2) Export dataset as CSV (includeHeader so DictReader gets column names).
            exp = client.get(
                f"{DOMO_API_BASE}/v1/datasets/{DOMO_DATASET_ID}/data",
                params={"includeHeader": "true"},
                headers={"Authorization": f"Bearer {token}", "Accept": "text/csv"},
            )
            if exp.status_code != 200:
                print(f"[domo] export failed: HTTP {exp.status_code} {exp.text[:200]}", flush=True)
                return None
            csv_text = exp.text
            if not csv_text or "CONSULTANT_ID" not in csv_text:
                print("[domo] export returned unexpected/empty data — keeping previous", flush=True)
                return None
            print(f"[domo] exported dataset {DOMO_DATASET_ID[:8]}… ({len(csv_text)//1024} KB)", flush=True)
            return csv_text
    except Exception as e:
        print(f"[domo] pull error: {e}", flush=True)
        return None


def _refresh_csat_from_domo() -> bool:
    """Pull from Domo and rebuild the CSAT index. Returns True if it succeeded."""
    csv_text = _domo_fetch_csv()
    if csv_text is None:
        return False
    rows = _parse_csv_text(csv_text)
    if not rows:
        print("[domo] parsed 0 rows — keeping previous index", flush=True)
        return False
    _build_csat_index(rows)
    _save_csat_json()
    print(f"[domo] CSAT index rebuilt from Domo — {len(rows)} rows", flush=True)
    return True


def _load_csat_csv():
    """Load CSAT data at startup. Prefer a live Domo pull; fall back to bundled CSV."""
    if _domo_configured():
        print("[csat] Domo configured — pulling at startup", flush=True)
        if _refresh_csat_from_domo():
            return
        print("[csat] Domo pull failed at startup — falling back to bundled CSV", flush=True)
    else:
        print("[csat] Domo not configured (set DOMO_* env vars) — using bundled CSV", flush=True)

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
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, newline='', encoding='utf-8') as f:
            rows = _parse_csv_text(f.read())
        _build_csat_index(rows)
        _save_csat_json()
        print(f"[csat] loaded from {path}", flush=True)
        return
    print("[csat] call_quality.csv not found — upload via dashboard", flush=True)


def _parse_excel_bytes(data: bytes) -> list:
    """Parse Excel file bytes into survey rows. Returns list of row dicts."""
    import datetime as _dt_mod
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("openpyxl not installed — add to requirements.txt")

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    raw_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not raw_rows:
        raise ValueError("Empty workbook")

    headers = [str(h).strip().upper() if h else "" for h in raw_rows[0]]
    col     = {name: i for i, name in enumerate(headers)}
    required = {"RATING", "CONSULTANT_ID", "CONSULTANT_NAME", "CONSULTANT_TEAM", "DATETIME", "SOLVED"}
    missing  = required - set(col.keys())
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    rows = []
    for row in raw_rows[1:]:
        try:
            rating  = int(row[col["RATING"]])
            cid     = str(row[col["CONSULTANT_ID"]] or "").strip()
            name    = str(row[col["CONSULTANT_NAME"]] or "").strip()
            team    = str(row[col["CONSULTANT_TEAM"]] or "").strip()
            dt_val  = row[col["DATETIME"]]
            solved  = str(row[col["SOLVED"]] or "").strip().lower() == "true"
            call_id = str(row[col["CALL_ID"]] or "").strip() if "CALL_ID" in col else ""
            opp_id  = ""
            for _k in ("OPPORTUNITY_ID", "OPPORTUNITYID", "OPPORTUNITY"):
                if _k in col:
                    opp_id = str(row[col[_k]] or "").strip()
                    break
            reason = ""
            for _k in ("REASON_FOR_CALL__C", "REASON_FOR_CALL", "CALL_REASON", "REASON"):
                if _k in col:
                    reason = str(row[col[_k]] or "").strip()
                    break
            if isinstance(dt_val, (_dt_mod.datetime, _dt_mod.date)):
                date_str = dt_val.strftime("%Y-%m-%d")
                dt_full  = dt_val.isoformat()
            elif isinstance(dt_val, (int, float)):
                _d = (_dt_mod.date(1899, 12, 30) + _dt_mod.timedelta(days=float(dt_val)))
                date_str = _d.strftime("%Y-%m-%d")
                dt_full  = _d.isoformat()
            else:
                date_str = str(dt_val)[:10]
                dt_full  = str(dt_val).strip()
            if not (1 <= rating <= 5) or not cid or not date_str:
                continue
            rows.append({"rating":rating,"cid":cid,"name":name,
                         "team":team,"date":date_str,"datetime":dt_full,"solved":solved,
                         "call_id":call_id,"opp_id":opp_id,"reason":reason})
        except (TypeError, ValueError):
            continue
    return rows


def _save_csat_json():
    """Save the current index to data/csat_index.json for serving."""
    index_data = _csat_index.get("index_data")
    if not index_data:
        return
    try:
        os.makedirs(os.path.dirname(CSAT_JSON_PATH), exist_ok=True)
        with open(CSAT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(index_data, f, separators=(",",":"))
        print(f"[csat] saved index to {CSAT_JSON_PATH}", flush=True)
    except Exception as e:
        print(f"[csat] failed to save JSON: {e}", flush=True)



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
    if not daily:
        return []
    # Fill in zero-count days so the series is dense (no gaps).
    # This prevents the frontend from truncating charts when some events
    # have sparse data for parts of the date range.
    from datetime import date as _date, timedelta as _td
    d_min = _date.fromisoformat(min(daily))
    d_max = _date.fromisoformat(max(daily))
    result = []
    cur = d_min
    while cur <= d_max:
        result.append({"date": cur.isoformat(), "count": daily.get(cur.isoformat(), 0)})
        cur += _td(days=1)
    return result

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

# ── FULL REFRESH — sequential CMS calls with 1s gap each ─────────────────────
async def full_refresh():
    """
    15 fixed CMS calls + up to 12 timeline calls (Supabase) = up to 27 total.
    1s gap between every call. Protected by lock — only one refresh at a time.
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

        # ── Calls 13-15: get all available user IDs from 3 sources ─────────────
        video_user_rows  = await _topn("cs-portal-content-events", "video.watched",    "userId", 10)
        search_user_rows = await _topn("cs-portal-content-events", "search.performed", "userId", 10)
        art_user_rows    = await _topn("cs-portal-content-events", "article.viewed",   "userId", 10)

        # Collect all unique non-anonymous user IDs
        seen = set()
        all_known_users = []
        for rows in (video_user_rows, search_user_rows, art_user_rows):
            for r in rows:
                uid = r.get("userId") or r.get("itemId") or ""
                if uid and uid.lower() not in ("anonymous", "") and uid not in seen:
                    seen.add(uid)
                    all_known_users.append(uid)

        # With Supabase: rotate through all known users across refresh cycles
        # Without Supabase: fall back to top 4
        if _sb_enabled:
            users_to_fetch = await _sb_get_unfetched_users(all_known_users, limit=10)
        else:
            users_to_fetch = all_known_users[:4]

        print(f"[refresh] fetching timelines for {len(users_to_fetch)} users (of {len(all_known_users)} known)", flush=True)

        # ── Content timelines ────────────────────────────────────────────────
        all_events = []
        fb_events  = []
        for uid in users_to_fetch:
            tl = await _timeline("cs-portal-content-events", uid, limit=500)
            all_events.extend(tl)
            print(f"[refresh] timeline {uid[:16]}: {len(tl)} events", flush=True)
            if _sb_enabled:
                await _sb_store_events(uid, "cs-portal-content-events", tl)

        # Feedback timelines for first 2 users
        for uid in users_to_fetch[:2]:
            tl = await _timeline("cs-portal-feedback-events", uid, limit=200)
            fb_events.extend(tl)
            if _sb_enabled:
                await _sb_store_events(uid, "cs-portal-feedback-events", tl)
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
# Supabase free tier pauses after 1 week of inactivity.
# We ping it every 6 hours to keep it alive — cheap and reliable.
SUPABASE_KEEPALIVE_SEC = 6 * 3600

async def _supabase_keepalive_loop():
    """Ping Supabase every 6 hours to prevent free-tier pausing."""
    await asyncio.sleep(60)  # wait for startup to settle
    while True:
        if _sb_enabled:
            try:
                result = await _sb_request("GET", "/cs_user_fetch_log?select=user_id&limit=1")
                print(f"[supabase] keepalive ping OK", flush=True)
            except Exception as ex:
                print(f"[supabase] keepalive ping failed: {ex}", flush=True)
        await asyncio.sleep(SUPABASE_KEEPALIVE_SEC)

async def _refresh_loop():
    await asyncio.sleep(10)  # let server boot fully
    while True:
        try:
            await full_refresh()
        except Exception as ex:
            print(f"[loop] error: {ex}", flush=True)
        # Refresh CSAT from Domo on the same 2-hour cycle (no-op if not configured).
        if _domo_configured():
            try:
                await asyncio.to_thread(_refresh_csat_from_domo)
            except Exception as ex:
                print(f"[loop] domo csat refresh error: {ex}", flush=True)
        await asyncio.sleep(REFRESH_SEC)

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    load_cache_from_disk()
    _load_csat_csv()
    if _sb_enabled:
        await _sb_ensure_tables()
        user_count = await _sb_get_user_count()
        print(f"[supabase] connected — {user_count} users in DB", flush=True)
    else:
        print("[supabase] not configured — using local timeline only", flush=True)
    t = asyncio.create_task(_refresh_loop())
    k = asyncio.create_task(_supabase_keepalive_loop())  # prevents Supabase free-tier pause
    yield
    t.cancel()
    k.cancel()
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

@app.get("/api/sessions/full")
async def api_sessions_full(date_from: str = "", date_to: str = ""):
    """Full session analytics from all users stored in Supabase.
    Falls back to in-memory timeline sample if Supabase is unavailable.
    """
    if not _sb_enabled:
        # Supabase not configured — serve in-memory sample so dashboard still works
        data, _ = cache_get("intel:all")
        sessions = (data or {}).get("sessions", {})
        sessions["note"] = "Supabase not configured — showing in-memory sample (top users only)"
        return {"available": True, **sessions}

    try:
        events = await _sb_get_all_events(date_from=date_from, date_to=date_to)
    except Exception as ex:
        # Supabase is down — fall back to in-memory cache so dashboard stays functional
        print(f"[supabase] sessions/full fallback to cache: {ex}", flush=True)
        data, _ = cache_get("intel:all")
        sessions = (data or {}).get("sessions", {})
        sessions["note"] = "Supabase temporarily unavailable — showing cached sample"
        return {"available": True, **sessions}

    if not events:
        user_count = await _sb_get_user_count()
        return {"available": True, "total_users": user_count, "total_sessions": 0,
                "note": f"{user_count} users in DB — building up data each refresh cycle"}

    from datetime import datetime as _dt
    sessions_map = defaultdict(list)
    for ev in events:
        uid = ev.get("user_id", "")
        sid = ev.get("session_id") or ""
        ts  = ev.get("ts", 0)
        if not ts: continue
        sessions_map[f"{uid}::{sid or 'nosession'}"].append(ts)

    durations, depths = [], []
    daily_map, hour_map = defaultdict(list), defaultdict(int)
    for key, timestamps in sessions_map.items():
        if len(timestamps) < 2: continue
        ts_s = sorted(timestamps)
        dur  = min((ts_s[-1] - ts_s[0]) / 1000, 28800)
        if dur < 5: continue
        durations.append(dur)
        depths.append(len(timestamps))
        d = _dt.utcfromtimestamp(ts_s[0] / 1000).strftime("%Y-%m-%d")
        daily_map[d].append(round(dur))
        hour_map[_dt.utcfromtimestamp(ts_s[0] / 1000).hour] += 1

    n = len(durations)
    if n == 0:
        return {"available": True, "total_sessions": 0, "note": "No qualifying sessions found"}

    ds = sorted(durations)
    depth_dist = Counter("bounce" if d<=2 else "normal" if d<=9 else "deep" for d in depths)
    daily_avg  = sorted([{"date":d,"avg_seconds":round(sum(v)/len(v))} for d,v in daily_map.items()], key=lambda x:x["date"])

    # Activity breakdown
    event_by_sess = defaultdict(list)
    for ev in events:
        uid = ev.get("user_id",""); sid = ev.get("session_id") or ""
        event_by_sess[f"{uid}::{sid or 'nosession'}"].append(ev)
    event_times = defaultdict(list)
    for key, evs in event_by_sess.items():
        evs_s = sorted(evs, key=lambda e: e.get("ts",0))
        for i, ev in enumerate(evs_s[:-1]):
            gap = (evs_s[i+1].get("ts",0) - ev.get("ts",0)) / 1000
            if 2 <= gap <= 600:
                event_times[ev.get("event_type","")].append(gap)
    labels = {"article.viewed":"Reading","search.performed":"Searching","video.watched":"Watching","category.viewed":"Browsing"}
    total_t = sum(sum(v) for v in event_times.values() if v)
    activity_breakdown = sorted([
        {"label":labels[k],"avg_seconds":round(sum(v)/len(v)),"pct_time":round(sum(v)/max(total_t,1)*100)}
        for k,v in event_times.items() if k in labels and v
    ], key=lambda x:-x["avg_seconds"])

    user_count = await _sb_get_user_count()
    return {
        "available": True, "total_users": user_count, "total_sessions": n,
        "avg_seconds": round(sum(ds)/n), "median_seconds": ds[n//2], "p90_seconds": ds[int(n*.9)],
        "pct_with_logout": 0,
        "depth_distribution": dict(depth_dist),
        "daily_avg": daily_avg, "activity_breakdown": activity_breakdown,
        "hour_distribution": {str(h):c for h,c in sorted(hour_map.items())},
        "computed_at": datetime.utcnow().isoformat(),
        "note": f"From {user_count} users in Supabase — {'full' if user_count >= 50 else 'building up coverage'} ({n} sessions)",
    }

@app.get("/api/csat/raw")
async def api_csat_raw():
    """Serve pre-built csat_index.json — used by frontend for client-side filtering.
    Never cached — must always serve the latest uploaded file.
    """
    _NO_CACHE = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }
    if os.path.exists(CSAT_JSON_PATH):
        return FileResponse(CSAT_JSON_PATH, media_type="application/json",
                           headers=_NO_CACHE)
    index_data = _csat_index.get("index_data")
    if index_data:
        return Response(content=json.dumps(index_data), media_type="application/json",
                       headers=_NO_CACHE)
    return JSONResponse({"available": False, "note": "Upload call_quality.xlsx via the dashboard"},
                       headers=_NO_CACHE)


@app.post("/upload/csat")
async def upload_csat(file: UploadFile = File(...), password: str = ""):
    """Upload a new call quality Excel file. Rebuilds the CSAT index immediately.
    Protected by CSAT_UPLOAD_PASSWORD environment variable.
    """
    # Password check
    if not secrets.compare_digest(password, UPLOAD_PASSWORD):
        raise HTTPException(status_code=401, detail="Wrong password")

    # Validate file type
    fname = file.filename or ""
    if not fname.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="File must be .xlsx, .xls, or .csv")

    try:
        data = await file.read()
        print(f"[upload] received {fname} ({len(data)//1024} KB)", flush=True)

        if fname.lower().endswith(".csv"):
            # Parse CSV directly
            rows = _parse_csv_text(data.decode("utf-8", errors="ignore"))
        else:
            rows = _parse_excel_bytes(data)

        if len(rows) < 100:
            raise HTTPException(status_code=400,
                detail=f"Only {len(rows)} valid rows found — check file format")

        # Rebuild index in memory
        index_data = _build_csat_index(rows)

        # Save to disk so it persists and gets served via /api/csat/raw
        _save_csat_json()

        return {
            "success":    True,
            "rows":       len(rows),
            "date_min":   index_data["date_min"],
            "date_max":   index_data["date_max"],
            "generated":  index_data["generated"],
            "message":    f"✓ CSAT data updated: {len(rows):,} surveys loaded",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


    """Return full pre-indexed CSAT data for client-side filtering.
    Fetched ONCE on page load — all date filtering happens in the browser.
    Payload: day_map keyed by date, plus consultant/team lookup tables.
    """
    if not _csat_index.get("day_map"):
        return {"available": False, "note": "Upload call_quality.csv to enable CSAT section"}

    day_map = _csat_index["day_map"]

    # Flatten day_map to a compact array for smaller payload
    # Each entry: [date, total, sum_rating, solved, low, dist{1-5}, teams{}, cons{}]
    days_payload = {}
    for date, dm in day_map.items():
        days_payload[date] = {
            "t":  dm["total"],
            "sr": dm["sum_r"],
            "s":  dm["solved"],
            "l":  dm["low"],
            "d":  dm.get("dist", {}),
            "tm": {t: {"t":v["t"],"sr":v["sr"],"s":v["s"],"l":v["l"],"d":v.get("d",{})}
                   for t, v in dm.get("tm", {}).items()},
            "cn": {c: {"n":v["n"],"tm":v["tm"],"t":v["t"],
                       "sr":v["sr"],"s":v["s"],"l":v["l"],"d":v.get("d",{})}
                   for c, v in dm.get("cn", {}).items()},
        }

    return {
        "available":  True,
        "date_min":   _csat_index.get("date_min", ""),
        "date_max":   _csat_index.get("date_max", ""),
        "days":       days_payload,
        "total_rows": len(_csat_rows),
    }

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
        "domo": {
            "configured": _domo_configured(),
            "client_id_set": bool(DOMO_CLIENT_ID),
            "secret_set": bool(DOMO_CLIENT_SECRET),
            "dataset_id_set": bool(DOMO_DATASET_ID),
            "source": "domo" if _domo_configured() else "bundled_csv",
        },
        "team_allow_list": CSAT_TEAMS_ALLOW,
    }

@app.get("/debug/csat/dates")
async def debug_csat_dates():
    """Per-month row counts — reveals gaps (e.g. a missing March–April) and whether
    a gap is in the raw data vs introduced downstream."""
    from collections import Counter
    by_month = Counter()
    by_day = Counter()
    for r in _csat_rows:
        d = (r.get("date") or "")[:10]
        if len(d) >= 7:
            by_month[d[:7]] += 1
        if d:
            by_day[d] += 1
    months = dict(sorted(by_month.items()))
    days_sorted = sorted(by_day.keys())
    # find gaps of >1 day between consecutive populated days
    gaps = []
    import datetime as _dt
    for a, b in zip(days_sorted, days_sorted[1:]):
        try:
            da = _dt.date.fromisoformat(a); db = _dt.date.fromisoformat(b)
            delta = (db - da).days
            if delta > 1:
                gaps.append({"after": a, "before": b, "missing_days": delta - 1})
        except Exception:
            continue
    return {
        "total_rows": len(_csat_rows),
        "date_min": days_sorted[0] if days_sorted else None,
        "date_max": days_sorted[-1] if days_sorted else None,
        "rows_per_month": months,
        "gaps_over_1_day": gaps[:50],
        "distinct_days": len(days_sorted),
    }

@app.get("/health")
@app.head("/health")  # UptimeRobot sends HEAD requests — must support both
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
        "call_budget":   "15 fixed + up to 12 timeline calls per refresh (27 max), 1s gap each, every 2 hours",
        "ts":            datetime.utcnow().isoformat(),
    }

@app.get("/api/refresh")
async def api_refresh():
    """Manually trigger a full data refresh.
    Same as /cache/clear but with a clean, memorable URL.
    Safe to call any time — protected by lock so only one runs at a time.
    """
    lock = get_lock()
    if not lock.locked():
        asyncio.create_task(full_refresh())
        return {"status": "refresh started", "note": "Takes ~30-60s. Check /health for completion."}
    return {"status": "refresh already running", "note": "Check /health for progress."}

@app.get("/api/refresh/csat")
async def api_refresh_csat():
    """Manually pull the latest CSAT data from Domo and rebuild the index.
    Replaces the manual 'Update CSAT' upload when Domo is configured.
    """
    if not _domo_configured():
        return JSONResponse(
            {"status": "domo not configured",
             "note": "Set DOMO_CLIENT_ID, DOMO_CLIENT_SECRET, DOMO_DATASET_ID env vars."},
            status_code=400,
        )
    ok = await asyncio.to_thread(_refresh_csat_from_domo)
    if ok:
        idx = _csat_index.get("index_data", {})
        return {"status": "csat refreshed from domo",
                "data_min": idx.get("date_min"),
                "data_max": idx.get("date_max"),
                "rows": idx.get("total_rows")}
    return JSONResponse(
        {"status": "domo pull failed", "note": "Check server logs for the [domo] error line."},
        status_code=502,
    )

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
        return {"status": "cleared — 1 refresh queued (up to 27 sequential calls, ~30-60s)"}
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

@app.get("/debug/video-topn")
async def debug_video_topn():
    """Test whether the CMS supports grouping video.watched by properties.videoTitle.
    If it returns video titles, we can replace the timeline-sample approach with a
    single direct CMS call and get ALL videos, not just from sampled users.
    """
    client = get_client()
    results = {}
    # Test 1: group by properties.videoTitle (ideal — gives titles directly)
    try:
        r = await client.get(f"{CMS_BASE}/cs-portal-content-events/query/top-n",
                             params={"event":"video.watched","groupBy":"properties.videoTitle","n":10})
        results["by_videoTitle"] = {"status": r.status_code, "data": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results["by_videoTitle"] = {"error": str(e)}
    await asyncio.sleep(1)
    # Test 2: group by itemId (known to be null — confirming)
    try:
        r = await client.get(f"{CMS_BASE}/cs-portal-content-events/query/top-n",
                             params={"event":"video.watched","groupBy":"itemId","n":10})
        results["by_itemId"] = {"status": r.status_code, "data": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results["by_itemId"] = {"error": str(e)}
    await asyncio.sleep(1)
    # Test 3: group by userId (how many unique users watch videos)
    try:
        r = await client.get(f"{CMS_BASE}/cs-portal-content-events/query/top-n",
                             params={"event":"video.watched","groupBy":"userId","n":10})
        results["by_userId"] = {"status": r.status_code, "data": r.json() if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results["by_userId"] = {"error": str(e)}
    return results

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f: html = f.read()
    return HTMLResponse(content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache"})

@app.get("/csat", response_class=HTMLResponse)
async def csat_page():
    """Call Quality & CSAT page — served from csat.html."""
    with open("csat.html") as f: html = f.read()
    return HTMLResponse(content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache"})

@app.get("/sw.js")
async def service_worker():
    with open("sw.js") as f: content = f.read()
    return Response(content=content, media_type="application/javascript",
        headers={"Cache-Control":"public, max-age=86400"})
