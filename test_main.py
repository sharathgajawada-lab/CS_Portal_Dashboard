"""
CS Portal Analytics Dashboard — Test Suite
Covers: _build_csat_index, cache, CSAT index, API endpoints, config
Run: pytest test_main.py -v
"""
import pytest
import json
import time
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("CMS_API_KEY", "TEST-API-KEY")

from main import (
    app, _cache, cache_get, cache_set,
    _build_csat_index, EVENTS, DATA_START,
)
from fastapi.testclient import TestClient

client = TestClient(app)


# ══════════════════════════════════════════════════════════════════════════════
# 1. UNIT TESTS — _build_csat_index
# ══════════════════════════════════════════════════════════════════════════════

def _sample_rows():
    return [
        {"rating": 5, "cid": "c1", "name": "Alice", "team": "Team A", "date": "2026-05-01", "solved": True},
        {"rating": 3, "cid": "c2", "name": "Bob",   "team": "Team A", "date": "2026-05-01", "solved": False},
        {"rating": 4, "cid": "c1", "name": "Alice", "team": "Team A", "date": "2026-05-02", "solved": True},
        {"rating": 2, "cid": "c3", "name": "Carol", "team": "Team B", "date": "2026-05-02", "solved": False},
        {"rating": 1, "cid": "c2", "name": "Bob",   "team": "Team A", "date": "2026-05-03", "solved": False},
    ]


class TestBuildCsatIndex:

    def test_returns_available_true(self):
        idx = _build_csat_index(_sample_rows())
        assert idx["available"] is True

    def test_date_min_max(self):
        idx = _build_csat_index(_sample_rows())
        assert idx["date_min"] == "2026-05-01"
        assert idx["date_max"] == "2026-05-03"

    def test_total_rows(self):
        idx = _build_csat_index(_sample_rows())
        assert idx["total_rows"] == 5

    def test_days_present(self):
        idx = _build_csat_index(_sample_rows())
        assert "2026-05-01" in idx["days"]
        assert "2026-05-02" in idx["days"]
        assert "2026-05-03" in idx["days"]

    def test_day_totals(self):
        idx = _build_csat_index(_sample_rows())
        assert idx["days"]["2026-05-01"]["t"] == 2
        assert idx["days"]["2026-05-02"]["t"] == 2
        assert idx["days"]["2026-05-03"]["t"] == 1

    def test_day_sum_ratings(self):
        idx = _build_csat_index(_sample_rows())
        # 2026-05-01: ratings 5+3 = 8
        assert idx["days"]["2026-05-01"]["sr"] == 8

    def test_day_solved(self):
        idx = _build_csat_index(_sample_rows())
        # 2026-05-01: 1 solved (Alice only)
        assert idx["days"]["2026-05-01"]["s"] == 1

    def test_day_low_ratings(self):
        idx = _build_csat_index(_sample_rows())
        # 2026-05-02: Carol = 2 (low); 2026-05-03: Bob = 1 (low)
        assert idx["days"]["2026-05-02"]["l"] == 1
        assert idx["days"]["2026-05-03"]["l"] == 1

    def test_team_aggregation_uses_tm_key(self):
        """_build_csat_index must use 'tm' not 'teams' — frontend depends on this."""
        idx = _build_csat_index(_sample_rows())
        dm = idx["days"]["2026-05-01"]
        assert "tm" in dm, "'tm' key missing — frontend will not find team data"
        assert "teams" not in dm, "stale 'teams' key found — will confuse consumers"

    def test_consultant_aggregation_uses_cn_key(self):
        """_build_csat_index must use 'cn' not 'cons' — frontend depends on this."""
        idx = _build_csat_index(_sample_rows())
        dm = idx["days"]["2026-05-01"]
        assert "cn" in dm, "'cn' key missing — frontend will not find consultant data"
        assert "cons" not in dm, "stale 'cons' key found — will confuse consumers"

    def test_team_a_totals_on_may_01(self):
        idx = _build_csat_index(_sample_rows())
        tm = idx["days"]["2026-05-01"]["tm"]
        assert "Team A" in tm
        assert tm["Team A"]["t"] == 2

    def test_consultant_in_cn(self):
        idx = _build_csat_index(_sample_rows())
        cn = idx["days"]["2026-05-01"]["cn"]
        assert "c1" in cn
        assert cn["c1"]["n"] == "Alice"

    def test_rating_distribution_d_key(self):
        idx = _build_csat_index(_sample_rows())
        dm = idx["days"]["2026-05-01"]
        assert "d" in dm
        assert dm["d"][5] == 1
        assert dm["d"][3] == 1

    def test_bad_names_excluded_from_cn(self):
        rows = [
            {"rating": 4, "cid": "x1", "name": "None",     "team": "T", "date": "2026-05-01", "solved": True},
            {"rating": 4, "cid": "x2", "name": "frank ai", "team": "T", "date": "2026-05-01", "solved": True},
            {"rating": 4, "cid": "x3", "name": "Frank AI Bot", "team": "T", "date": "2026-05-01", "solved": True},
            {"rating": 4, "cid": "x4", "name": "Alice",    "team": "T", "date": "2026-05-01", "solved": True},
        ]
        idx = _build_csat_index(rows)
        cn = idx["days"]["2026-05-01"]["cn"]
        assert "x1" not in cn, "name='None' should be excluded"
        assert "x2" not in cn, "name='frank ai' should be excluded"
        assert "x3" not in cn, "name starting with 'frank ai' should be excluded"
        assert "x4" in cn, "valid name should be included"

    def test_bad_teams_excluded_from_tm(self):
        rows = [
            {"rating": 4, "cid": "x1", "name": "Alice", "team": "",    "date": "2026-05-01", "solved": True},
            {"rating": 4, "cid": "x2", "name": "Bob",   "team": "N/A", "date": "2026-05-01", "solved": True},
            {"rating": 4, "cid": "x3", "name": "Carol", "team": "Team Valid", "date": "2026-05-01", "solved": True},
        ]
        idx = _build_csat_index(rows)
        tm = idx["days"]["2026-05-01"]["tm"]
        assert "" not in tm
        assert "N/A" not in tm
        assert "Team Valid" in tm

    def test_week_cons_built(self):
        idx = _build_csat_index(_sample_rows())
        assert "week_cons" in idx
        assert isinstance(idx["week_cons"], dict)

    def test_empty_rows_returns_empty_index(self):
        idx = _build_csat_index([])
        assert idx["date_min"] == ""
        assert idx["date_max"] == ""
        assert idx["days"] == {}

    def test_rows_sorted_by_date(self):
        """Rows passed in unsorted order should still produce correct date_min/max."""
        rows = [
            {"rating": 5, "cid": "c1", "name": "Alice", "team": "T", "date": "2026-05-10", "solved": True},
            {"rating": 3, "cid": "c1", "name": "Alice", "team": "T", "date": "2026-05-01", "solved": True},
        ]
        idx = _build_csat_index(rows)
        assert idx["date_min"] == "2026-05-01"
        assert idx["date_max"] == "2026-05-10"


# ══════════════════════════════════════════════════════════════════════════════
# 2. UNIT TESTS — cache
# ══════════════════════════════════════════════════════════════════════════════

class TestCache:

    def setup_method(self):
        _cache.clear()

    def test_cache_miss_returns_none_false(self):
        data, fresh = cache_get("nonexistent")
        assert data is None
        assert fresh is False

    def test_cache_hit_returns_fresh(self):
        cache_set("k", {"x": 1})
        data, fresh = cache_get("k")
        assert data == {"x": 1}
        assert fresh is True

    def test_stale_cache_returns_data_not_fresh(self):
        """Age > CACHE_TTL but < STALE_TTL → returns data, fresh=False."""
        _cache["stale"] = {"ts": time.time() - 7300, "data": {"y": 2}}
        data, fresh = cache_get("stale")
        assert data == {"y": 2}
        assert fresh is False

    def test_expired_cache_returns_none(self):
        """Age > STALE_TTL → returns None."""
        _cache["old"] = {"ts": time.time() - 90000, "data": {"z": 3}}
        data, fresh = cache_get("old")
        assert data is None
        assert fresh is False

    def test_overwrite_existing_key(self):
        cache_set("k", {"v": 1})
        cache_set("k", {"v": 2})
        data, _ = cache_get("k")
        assert data == {"v": 2}


# ══════════════════════════════════════════════════════════════════════════════
# 3. API ENDPOINT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    def test_returns_200(self):
        assert client.get("/health").status_code == 200

    def test_head_returns_200(self):
        """UptimeRobot sends HEAD — must be supported."""
        assert client.head("/health").status_code == 200

    def test_has_required_fields(self):
        d = client.get("/health").json()
        for field in ("status", "api_key_set", "batch_cached", "batch_fresh", "ts"):
            assert field in d, f"Missing field: {field}"

    def test_api_key_set_true(self):
        assert client.get("/health").json()["api_key_set"] is True

    def test_status_ok(self):
        assert client.get("/health").json()["status"] == "ok"


class TestDashboardPage:

    def test_root_returns_200(self):
        assert client.get("/").status_code == 200

    def test_root_is_html(self):
        assert "text/html" in client.get("/").headers["content-type"]

    def test_csat_returns_200(self):
        assert client.get("/csat").status_code == 200

    def test_csat_is_html(self):
        assert "text/html" in client.get("/csat").headers["content-type"]

    def test_root_no_cache_headers(self):
        r = client.get("/")
        assert "no-store" in r.headers.get("cache-control", "").lower()

    def test_csat_no_cache_headers(self):
        r = client.get("/csat")
        assert "no-store" in r.headers.get("cache-control", "").lower()


class TestBatchEndpoint:

    def setup_method(self):
        _cache.clear()

    def test_returns_200(self):
        cache_set("batch:all", {e["key"]: {"series": []} for e in EVENTS})
        assert client.get("/api/metrics/batch").status_code == 200

    def test_returns_empty_dict_when_no_cache(self):
        r = client.get("/api/metrics/batch")
        assert r.status_code == 200
        assert r.json() == {}

    def test_all_events_present_when_cached(self):
        cache_set("batch:all", {e["key"]: {"series": []} for e in EVENTS})
        data = client.get("/api/metrics/batch").json()
        for e in EVENTS:
            assert e["key"] in data

    def test_has_etag_header(self):
        cache_set("batch:all", {e["key"]: {"series": []} for e in EVENTS})
        r = client.get("/api/metrics/batch")
        assert "etag" in r.headers

    def test_api_key_not_in_response(self):
        cache_set("batch:all", {e["key"]: {"series": []} for e in EVENTS})
        r = client.get("/api/metrics/batch")
        assert "TEST-API-KEY" not in r.text


class TestCsatRawEndpoint:

    def test_returns_200(self):
        assert client.get("/api/csat/raw").status_code == 200

    def test_no_cache_headers(self):
        r = client.get("/api/csat/raw")
        cc = r.headers.get("cache-control", "").lower()
        assert "no-store" in cc or "no-cache" in cc

    def test_returns_available_false_when_no_data(self):
        """When no CSV loaded and no JSON file, must return available:false gracefully."""
        import main as m
        original = m._csat_index.copy()
        m._csat_index.clear()
        try:
            r = client.get("/api/csat/raw")
            assert r.status_code == 200
            d = r.json()
            assert d.get("available") is False
        finally:
            m._csat_index.update(original)


class TestApiArticlesEndpoint:

    def setup_method(self):
        _cache.clear()

    def test_returns_200(self):
        assert client.get("/api/articles").status_code == 200

    def test_returns_articles_key(self):
        d = client.get("/api/articles").json()
        assert "articles" in d

    def test_returns_empty_list_when_no_cache(self):
        d = client.get("/api/articles").json()
        assert d["articles"] == []


class TestApiSessionsEndpoint:

    def setup_method(self):
        _cache.clear()

    def test_returns_200(self):
        assert client.get("/api/sessions").status_code == 200

    def test_returns_total_sessions(self):
        d = client.get("/api/sessions").json()
        assert "total_sessions" in d


class TestCacheEndpoints:

    def test_cache_clear_returns_200(self):
        assert client.get("/cache/clear").status_code == 200

    def test_api_refresh_returns_200(self):
        assert client.get("/api/refresh").status_code == 200

    def test_api_refresh_has_status(self):
        d = client.get("/api/refresh").json()
        assert "status" in d


# ══════════════════════════════════════════════════════════════════════════════
# 4. CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestEventsConfig:

    def test_all_events_have_required_fields(self):
        for e in EVENTS:
            assert "key" in e
            assert "project" in e
            assert "label" in e
            assert "color" in e

    def test_no_duplicate_event_keys(self):
        keys = [e["key"] for e in EVENTS]
        assert len(keys) == len(set(keys))

    def test_article_feedback_uses_feedback_project(self):
        fb = next(e for e in EVENTS if e["key"] == "article.feedback")
        assert fb["project"] == "cs-portal-feedback-events"

    def test_all_projects_start_with_cs_portal(self):
        for e in EVENTS:
            assert e["project"].startswith("cs-portal-")

    def test_data_start_is_valid_date_format(self):
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", DATA_START)

    def test_exactly_9_events(self):
        """9 events = 9 timeseries calls per refresh cycle."""
        assert len(EVENTS) == 9


# ══════════════════════════════════════════════════════════════════════════════
# 5. INTEGRATION — _build_csat_index → /api/csat/raw pipeline
# ══════════════════════════════════════════════════════════════════════════════

class TestCsatIndexPipeline:

    def test_index_data_written_to_csat_index(self):
        import main as m
        rows = _sample_rows()
        _build_csat_index(rows)
        assert m._csat_index.get("day_map") is not None
        assert m._csat_index.get("index_data") is not None

    def test_index_data_day_map_uses_new_schema(self):
        """After _build_csat_index, day_map entries must use compact keys t/sr/s/l/d/tm/cn."""
        import main as m
        _build_csat_index(_sample_rows())
        dm = next(iter(m._csat_index["day_map"].values()))
        for key in ("t", "sr", "s", "l", "d", "tm", "cn"):
            assert key in dm, f"Compact key '{key}' missing from day_map entry"
        for stale_key in ("total", "sum_r", "solved", "low", "teams", "cons"):
            assert stale_key not in dm, f"Stale key '{stale_key}' found — should not exist"

    def test_raw_endpoint_returns_days_after_build(self):
        import main as m
        _build_csat_index(_sample_rows())
        # Serve from in-memory (no file needed)
        if os.path.exists(m.CSAT_JSON_PATH):
            os.remove(m.CSAT_JSON_PATH)
        r = client.get("/api/csat/raw")
        assert r.status_code == 200
        d = r.json()
        assert d.get("available") is True
        assert "days" in d
