"""
CS Portal Analytics Dashboard — Test Suite
Covers: API endpoints, aggregation logic, cache, CMS health, edge cases
Run: pytest test_main.py -v
"""
import pytest
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from httpx import AsyncClient, Response

# ── Import app ────────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Set env before importing app
os.environ["CMS_API_KEY"] = "TEST-API-KEY"

from main import (
    app, cache, cache_get, cache_set,
    aggregate_to_daily, cms_status, EVENTS, DATA_START_DATE
)

client = TestClient(app)

# ══════════════════════════════════════════════════════════════════════════════
# 1. UNIT TESTS — aggregate_to_daily
# ══════════════════════════════════════════════════════════════════════════════

class TestAggregateToDaily:

    def test_aggregates_unix_ms_timestamps(self):
        """Should convert Unix ms timestamps to YYYY-MM-DD and sum counts."""
        series = [
            {"ts": 1745510400000, "count": 5},   # 2025-04-24 00:00:00 UTC
            {"ts": 1745510460000, "count": 3},   # 2025-04-24 00:01:00 UTC
            {"ts": 1745596800000, "count": 10},  # 2025-04-25 00:00:00 UTC
        ]
        result = aggregate_to_daily(series)
        assert len(result) == 2
        dates = {r["date"]: r["count"] for r in result}
        assert dates["2025-04-24"] == 8
        assert dates["2025-04-25"] == 10

    def test_aggregates_iso_date_strings(self):
        """Should handle ISO date string format."""
        series = [
            {"date": "2026-04-24", "count": 100},
            {"date": "2026-04-24", "count": 50},
            {"date": "2026-04-25", "count": 200},
        ]
        result = aggregate_to_daily(series)
        dates = {r["date"]: r["count"] for r in result}
        assert dates["2026-04-24"] == 150
        assert dates["2026-04-25"] == 200

    def test_returns_empty_for_empty_series(self):
        """Should return empty list for empty input."""
        assert aggregate_to_daily([]) == []

    def test_returns_empty_for_none_counts(self):
        """Should handle None count values."""
        series = [{"ts": 1745510400000, "count": None}]
        result = aggregate_to_daily(series)
        assert result[0]["count"] == 0

    def test_sorted_by_date(self):
        """Output should always be sorted by date ascending."""
        series = [
            {"date": "2026-04-26", "count": 1},
            {"date": "2026-04-24", "count": 2},
            {"date": "2026-04-25", "count": 3},
        ]
        result = aggregate_to_daily(series)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)

    def test_includes_event_key(self):
        """Should include event key in each row."""
        series = [{"date": "2026-04-24", "count": 5}]
        result = aggregate_to_daily(series, "profile.viewed")
        assert result[0]["event"] == "profile.viewed"

    def test_skips_invalid_timestamps(self):
        """Should skip rows with invalid/missing timestamp fields."""
        series = [
            {"foo": "bar", "count": 5},  # no ts or date
            {"date": "2026-04-24", "count": 10},
        ]
        result = aggregate_to_daily(series)
        assert len(result) == 1
        assert result[0]["count"] == 10

    def test_handles_large_count_values(self):
        """Should handle large count values without overflow."""
        series = [{"date": "2026-04-24", "count": 999999}]
        result = aggregate_to_daily(series)
        assert result[0]["count"] == 999999


# ══════════════════════════════════════════════════════════════════════════════
# 2. UNIT TESTS — Cache
# ══════════════════════════════════════════════════════════════════════════════

class TestCache:

    def setup_method(self):
        cache.clear()

    def test_cache_miss_returns_none(self):
        data, fresh = cache_get("nonexistent")
        assert data is None
        assert fresh is False

    def test_cache_hit_returns_fresh(self):
        cache_set("test_key", {"series": [1, 2, 3]})
        data, fresh = cache_get("test_key")
        assert data == {"series": [1, 2, 3]}
        assert fresh is True

    def test_stale_cache_returns_data_but_not_fresh(self):
        cache["stale_key"] = {"ts": time.time() - 400, "data": {"series": []}}
        data, fresh = cache_get("stale_key")
        assert data is not None
        assert fresh is False

    def test_expired_cache_returns_none(self):
        cache["expired_key"] = {"ts": time.time() - 4000, "data": {"series": []}}
        data, fresh = cache_get("expired_key")
        assert data is None
        assert fresh is False

    def test_cache_set_and_get(self):
        cache_set("mykey", {"foo": "bar"})
        data, fresh = cache_get("mykey")
        assert data == {"foo": "bar"}
        assert fresh is True


# ══════════════════════════════════════════════════════════════════════════════
# 3. API ENDPOINT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    def setup_method(self):
        cache.clear()

    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_has_required_fields(self):
        r = client.get("/health")
        data = r.json()
        assert "status" in data
        assert "api_key_set" in data
        assert "batch_cached" in data
        assert "batch_fresh" in data
        assert "data_start_date" in data
        assert "cache_entries" in data
        assert "cms" in data

    def test_health_api_key_set(self):
        r = client.get("/health")
        assert r.json()["api_key_set"] is True

    def test_health_data_start_date(self):
        r = client.get("/health")
        assert r.json()["data_start_date"] == DATA_START_DATE


class TestCacheEndpoint:

    def test_cache_clear_returns_200(self):
        r = client.get("/cache/clear")
        assert r.status_code == 200

    def test_cache_clear_response(self):
        r = client.get("/cache/clear")
        assert "cleared" in r.json()["status"]


class TestCmsStatusEndpoint:

    def test_cms_status_returns_200(self):
        with patch("main.check_cms_health", new_callable=AsyncMock):
            r = client.get("/api/cms-status")
            assert r.status_code == 200

    def test_cms_status_has_healthy_field(self):
        with patch("main.check_cms_health", new_callable=AsyncMock):
            r = client.get("/api/cms-status")
            assert "healthy" in r.json()


class TestBatchEndpoint:

    def setup_method(self):
        cache.clear()

    def test_batch_returns_200_with_cached_data(self):
        """Should return 200 when cache has data."""
        mock_data = {e["key"]: {"series": []} for e in EVENTS}
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        assert r.status_code == 200

    def test_batch_returns_all_events(self):
        """Should return data for all events."""
        mock_data = {e["key"]: {"series": []} for e in EVENTS}
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        data = r.json()
        for e in EVENTS:
            assert e["key"] in data

    def test_batch_clears_cache_on_no_cache_header(self):
        """Should clear cache when Cache-Control: no-cache is sent."""
        mock_data = {e["key"]: {"series": [{"date": "2026-04-24", "count": 100}]} for e in EVENTS}
        cache_set("batch:all", mock_data)
        with patch("main.prefetch_all", new_callable=AsyncMock) as mock_prefetch:
            mock_prefetch.return_value = mock_data
            r = client.get("/api/metrics/batch",
                          headers={"Cache-Control": "no-cache"})
            assert r.status_code == 200


class TestDashboardEndpoint:

    def test_dashboard_returns_200(self):
        r = client.get("/")
        assert r.status_code == 200

    def test_dashboard_returns_html(self):
        r = client.get("/")
        assert "text/html" in r.headers["content-type"]

    def test_dashboard_contains_hear_com(self):
        r = client.get("/")
        assert "hear.com" in r.text.lower() or "hear" in r.text.lower()

    def test_dashboard_contains_chart_js(self):
        r = client.get("/")
        assert "chart" in r.text.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 4. CMS FETCH TESTS (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestCmsFetch:

    def setup_method(self):
        cache.clear()

    @pytest.mark.asyncio
    async def test_cms_fetch_uses_correct_params(self):
        """Should use 'since' and 'until' params per OpenAPI spec."""
        from main import cms_fetch

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"series": [{"ts": 1745510400000, "count": 5}]}'

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await cms_fetch(
                "cs-portal-profile-events",
                "profile.viewed",
                since="2026-04-24",
                until="2026-05-25"
            )
            assert "series" in result

    @pytest.mark.asyncio
    async def test_cms_fetch_returns_empty_on_503(self):
        """Should return empty series on CMS 503."""
        from main import cms_fetch

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await cms_fetch(
                "cs-portal-profile-events",
                "profile.viewed",
                since="-30d"
            )
            assert result == {"series": []}

    @pytest.mark.asyncio
    async def test_cms_fetch_returns_empty_on_timeout(self):
        """Should return empty series on timeout."""
        from main import cms_fetch
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )
            result = await cms_fetch(
                "cs-portal-profile-events",
                "profile.viewed",
                since="-30d"
            )
            assert result == {"series": []}

    @pytest.mark.asyncio
    async def test_cms_fetch_uses_api_key_header(self):
        """Should send api-key header (lowercase per OpenAPI spec)."""
        from main import cms_fetch

        captured_headers = {}

        async def mock_get(url, params=None, headers=None):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"series": []}'
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = mock_get
            await cms_fetch("cs-portal-profile-events", "profile.viewed", since="-30d")
            assert "api-key" in captured_headers


# ══════════════════════════════════════════════════════════════════════════════
# 5. EVENTS CONFIG TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestEventsConfig:

    def test_all_events_have_key_and_project(self):
        for e in EVENTS:
            assert "key" in e, f"Missing 'key' in {e}"
            assert "project" in e, f"Missing 'project' in {e}"

    def test_no_duplicate_event_keys(self):
        keys = [e["key"] for e in EVENTS]
        assert len(keys) == len(set(keys)), "Duplicate event keys found"

    def test_article_feedback_uses_correct_project(self):
        """Bug fix: article.feedback must use cs-portal-feedback-events."""
        fb = next(e for e in EVENTS if e["key"] == "article.feedback")
        assert fb["project"] == "cs-portal-feedback-events"

    def test_data_start_date_format(self):
        """DATA_START_DATE must be YYYY-MM-DD."""
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", DATA_START_DATE)

    def test_all_projects_are_valid_format(self):
        for e in EVENTS:
            assert e["project"].startswith("cs-portal-"), \
                f"Invalid project format: {e['project']}"


# ══════════════════════════════════════════════════════════════════════════════
# 6. RESPONSE FORMAT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestResponseFormats:

    def test_health_response_is_json(self):
        r = client.get("/health")
        assert r.headers["content-type"].startswith("application/json")

    def test_batch_response_is_json(self):
        mock_data = {e["key"]: {"series": []} for e in EVENTS}
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        assert r.headers["content-type"].startswith("application/json")

    def test_batch_series_is_list(self):
        mock_data = {e["key"]: {"series": [{"date": "2026-04-24", "count": 5}]}
                     for e in EVENTS}
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        data = r.json()
        for key, val in data.items():
            assert isinstance(val["series"], list), f"{key} series is not a list"

    def test_batch_series_rows_have_date_and_count(self):
        mock_data = {
            "profile.viewed": {"series": [{"date": "2026-04-24", "count": 100, "event": "profile.viewed"}]}
        }
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        series = r.json()["profile.viewed"]["series"]
        assert series[0]["date"] == "2026-04-24"
        assert series[0]["count"] == 100


# ══════════════════════════════════════════════════════════════════════════════
# 7. SECURITY TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurity:

    def test_cors_headers_present(self):
        r = client.get("/health", headers={"Origin": "https://example.com"})
        assert r.status_code == 200

    def test_api_key_not_exposed_in_batch_response(self):
        mock_data = {e["key"]: {"series": []} for e in EVENTS}
        cache_set("batch:all", mock_data)
        r = client.get("/api/metrics/batch")
        assert "API-" not in r.text
        assert "TEST-API-KEY" not in r.text

    def test_api_key_not_in_health_response(self):
        r = client.get("/health")
        assert "TEST-API-KEY" not in r.text

