"""
Static UX contract tests for the world-class dashboard pass.
These tests ensure the shipped HTML keeps the shared universal chart experience,
Domo-first CSAT workflow, and accessible command controls in place.
"""
from pathlib import Path
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from main import app

ROOT = Path(__file__).parent
PAGES = ["index.html", "csat.html", "starter_guides.html"]
client = TestClient(app)


def _html(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _asset(name: str) -> str:
    root_name = {"dashboard_ux.css": "portal-overrides.css", "dashboard_ux.js": "portal-system.js"}.get(name, name)
    return (ROOT / root_name).read_text(encoding="utf-8")


def test_world_class_ux_assets_are_linked_on_every_page():
    for page in PAGES:
        html = _html(page)
        assert "portal-overrides.css" in html
        assert "portal-system.js" in html
        assert "/assets/dashboard_ux.css" not in html
        assert "/assets/dashboard_ux.js" not in html
    assert "World-class dashboard UX layer" in _asset("dashboard_ux.css")
    assert "World-class dashboard UX engine" in _asset("dashboard_ux.js")
    assert "Emergency v6 visual correction" in _asset("dashboard_ux.css")


def test_universal_chart_actions_exist_in_shared_ux_engine():
    js = _asset("dashboard_ux.js")
    required_actions = [
        'data-ux-action="insight"',
        'data-ux-action="data"',
        'data-ux-action="csv"',
        'data-ux-action="png"',
        'data-ux-action="expand"',
    ]
    assert "function enhanceCharts" in js
    assert "function openStudio" in js
    for action in required_actions:
        assert action in js, f"{action} missing from shared UX engine"


def test_dashboard_pages_have_charts_for_the_universal_toolbar_to_enhance():
    minimum_canvas_counts = {
        "index.html": 6,
        "csat.html": 8,
        "starter_guides.html": 4,
    }
    for page, minimum in minimum_canvas_counts.items():
        soup = BeautifulSoup(_html(page), "html.parser")
        canvas_ids = [tag.get("id") for tag in soup.find_all("canvas") if tag.get("id")]
        assert len(canvas_ids) >= minimum, f"Expected at least {minimum} canvases in {page}"
        page_canvas_ids = [cid for cid in canvas_ids if cid not in {"chartModalCanvas", "uxStudioCanvas"}]
        assert len(page_canvas_ids) == len(set(page_canvas_ids)), f"Duplicate page canvas IDs in {page}"


def test_csat_ui_is_domo_first_not_excel_upload_first():
    html = _html("csat.html")
    assert "CSAT data is pulled directly from Domo" in html
    assert "Voice AI" in html
    assert '<input type="file"' not in html
    assert '/upload/csat' not in html


def test_accessibility_and_power_user_controls_are_present_in_ux_engine():
    js = _asset("dashboard_ux.js")
    assert "Skip to dashboard" in js
    assert "Dashboard overview and controls" in js
    assert "role=\"toolbar\"" in js
    assert "aria-modal" in js
    assert "e.key.toLowerCase() === 'k'" in js



def test_csat_has_dedicated_voice_ai_lens_and_chart():
    html = _html("csat.html")
    assert 'id="voiceAiLensCard"' in html
    assert 'id="voiceAiTrendChart"' in html
    assert "Focus Voice AI" in html
    assert "Voice AI lens" in html


def test_pages_expose_page_identity_for_shared_ux_engine():
    expected = {
        "index.html": 'data-dashboard-page="/"',
        "csat.html": 'data-dashboard-page="/csat"',
        "starter_guides.html": 'data-dashboard-page="/starter-guides"',
    }
    for page, marker in expected.items():
        assert marker in _html(page)
    assert "document.body?.dataset?.dashboardPage" in _asset("dashboard_ux.js")

def test_shared_assets_are_served_by_backend_with_expected_content_types():
    css = client.get("/portal-overrides.css")
    js = client.get("/portal-system.js")
    legacy_css = client.get("/assets/dashboard_ux.css")
    legacy_js = client.get("/assets/dashboard_ux.js")
    missing = client.get("/assets/not_allowed.js")
    assert css.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    assert "World-class dashboard UX layer" in css.text
    assert "Emergency v6 visual correction" in css.text
    assert js.status_code == 200
    assert "javascript" in js.headers.get("content-type", "")
    assert "World-class dashboard UX engine" in js.text
    assert "6.0.0-voice-ai-team-dropdowns-every-chart-expand" in js.text
    assert legacy_css.status_code == 200
    assert legacy_js.status_code == 200
    assert missing.status_code == 404


def test_csat_removes_old_top_diagnostic_cards_and_unlock_ui():
    html = _html("csat.html")
    removed = [
        'id="csatDataQuality"',
        'id="csatSourceStrip"',
        'id="csatExecutiveBrief"',
        'domoAccessModal',
        'Admin access',
        'Unlock raw drilldowns',
    ]
    for text in removed:
        assert text not in html


def test_universal_chart_engine_generates_expand_for_every_chart():
    js = _asset("dashboard_ux.js")
    assert 'data-ux-action="expand"' in js
    assert 'chart-expand-btn ux-generated-expand' in js
    chart_line = "const canvases = Array.from(document.querySelectorAll('canvas[id]')).filter(canvas => !canvas.id.startsWith('ux') && canvas.id !== 'chartModalCanvas');"
    assert chart_line in js
