#!/usr/bin/env python3
"""Browser click smoke for the dashboard UI.

The sandbox Chromium policy blocks localhost navigation, so this test renders the
real HTML files with local assets inlined, stubs API responses through
Playwright routing, and clicks every detected chart Expand action.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://dashboard-qa.local/"

CHART_STUB = r"""
(function(){
  const instances = new Map();
  function getCanvas(target){ return target && target.canvas ? target.canvas : target; }
  function Chart(target, config){
    this.canvas = getCanvas(target);
    this.config = config || {};
    this.data = (config && config.data) || { labels: [], datasets: [] };
    this.options = (config && config.options) || {};
    this.ctx = this.canvas && this.canvas.getContext ? this.canvas.getContext('2d') : null;
    instances.set(this.canvas, this);
  }
  Chart.defaults = { color:'#64748b', borderColor:'#dde1ec', font:{} };
  Chart.getChart = function(target){ return instances.get(getCanvas(target)) || null; };
  Chart.prototype.update = function(){};
  Chart.prototype.destroy = function(){ instances.delete(this.canvas); };
  Chart.prototype.toBase64Image = function(){
    try { return this.canvas.toDataURL('image/png'); } catch(e) { return 'data:image/png;base64,'; }
  };
  Chart.prototype.getElementsAtEventForMode = function(){
    const ds = (this.data.datasets || [])[0] || { data: [] };
    return ds.data && ds.data.length ? [{ datasetIndex:0, index:0 }] : [];
  };
  window.Chart = Chart;
})();
"""


def iso_days(n: int = 42):
    end = date.today()
    start = end - timedelta(days=n - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def series(mult: int, offset: int = 0):
    return [{"date": d, "count": max(1, ((i + 3 + offset) * mult) % 95 + 5)} for i, d in enumerate(iso_days())]


def make_portal_batch():
    keys = [
        "auth.login", "auth.logout", "article.viewed", "search.performed", "video.watched",
        "total.views", "category.viewed", "article.feedback", "order_supplies.visited", "scheduling.started",
    ]
    return {key: {"series": series(idx + 2, idx)} for idx, key in enumerate(keys)}


def make_articles():
    return {
        "total_views": 3280,
        "articles": [
            {"id":"a1","label":"Troubleshooting Bluetooth pairing","slug":"bluetooth-pairing","url":"https://example.com/a1","views":840,"helpful_pct":82,"total_feedback":44,"is_dead_end":False,"category":"Troubleshooting","health_score":88},
            {"id":"a2","label":"Voice AI follow-up workflow","slug":"voice-ai-follow-up","url":"https://example.com/a2","views":610,"helpful_pct":76,"total_feedback":29,"is_dead_end":False,"category":"Voice AI","health_score":79},
            {"id":"a3","label":"Scheduling reschedule steps","slug":"schedule-reschedule","url":"https://example.com/a3","views":455,"helpful_pct":61,"total_feedback":18,"is_dead_end":True,"category":"Scheduling","health_score":55},
            {"id":"a4","label":"Order supplies guide","slug":"supplies","url":"https://example.com/a4","views":390,"helpful_pct":90,"total_feedback":23,"is_dead_end":False,"category":"Supplies","health_score":91},
        ],
        "note": "QA stub data",
    }


def make_search():
    return {
        "top_queries": [
            {"query":"voice ai", "count": 218},
            {"query":"pairing", "count": 164},
            {"query":"reschedule", "count": 139},
            {"query":"invoice", "count": 88},
        ],
        "zero_result": [{"query":"invoice", "count": 12}],
        "content_gaps": [
            {"query":"invoice", "count": 88, "has_content": False, "is_zero_result": True},
            {"query":"domo refresh", "count": 31, "has_content": False, "is_zero_result": False},
        ],
        "total_searches": 1260,
        "conversion_rate": 67,
        "note": "QA stub data",
    }


def make_categories():
    return {"categories": [
        {"path":"Troubleshooting", "label":"Troubleshooting", "count": 940},
        {"path":"Voice AI", "label":"Voice AI", "count": 760},
        {"path":"Scheduling", "label":"Scheduling", "count": 610},
        {"path":"Supplies", "label":"Supplies", "count": 330},
        {"path":"Billing", "label":"Billing", "count": 250},
    ]}


def make_sessions():
    days = iso_days(14)
    return {
        "available": True,
        "total_sessions": 420,
        "total_users": 76,
        "median_seconds": 210,
        "avg_seconds": 295,
        "p90_seconds": 780,
        "pct_with_logout": 68,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "note": "QA stub Supabase session model",
        "depth_distribution": {"bounce": 68, "normal": 254, "deep": 98},
        "daily_avg": [{"date": d, "avg_seconds": 220 + (i % 6) * 35} for i, d in enumerate(days)],
        "prev_daily_avg": [{"date": d, "avg_seconds": 180 + (i % 5) * 25} for i, d in enumerate(days)],
        "activity_breakdown": [
            {"label":"Article reading", "avg_seconds": 420, "pct_time": 44},
            {"label":"Search", "avg_seconds": 170, "pct_time": 18},
            {"label":"Video", "avg_seconds": 240, "pct_time": 24},
            {"label":"Navigation", "avg_seconds": 110, "pct_time": 14},
        ],
        "hour_distribution": {str(h): (h % 6 + 1) * 7 for h in range(7, 19)},
    }


def make_insights():
    return {
        "weekly_digest": "Portal usage is healthy; Voice AI and troubleshooting are the strongest demand signals.",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "consumption_ratio": 2.8,
        "frustration_index": 1.4,
        "engagement_velocity": 12,
        "self_service_rate": 58,
        "hour_distribution": {str(h): (h % 6 + 1) * 7 for h in range(7, 19)},
    }


def make_videos():
    return {"videos":[
        {"title":"Bluetooth reset walkthrough", "url":"https://example.com/v1", "count": 340},
        {"title":"Voice AI call review", "url":"https://example.com/v2", "count": 290},
        {"title":"Scheduling best practices", "url":"https://example.com/v3", "count": 210},
    ], "note":"QA stub data"}


def make_sg_timeseries(mult=3):
    return {"project":"cs-portal-starter-guide-events", "series":[{"date": d, "count": ((i + 2) * mult) % 50 + 10} for i, d in enumerate(iso_days(35))]}


def make_sg_topn():
    return {"project":"cs-portal-starter-guide-events", "top":[
        {"id":"voice-ai-intro", "label":"Voice AI intro", "count": 740},
        {"id":"setup-checklist", "label":"Setup checklist", "count": 620},
        {"id":"pairing-step", "label":"Pairing step", "count": 515},
        {"id":"schedule-step", "label":"Schedule step", "count": 420},
    ]}


def make_csat_index():
    sys.path.insert(0, str(ROOT))
    from main import _build_csat_index
    rows = []
    start = date.today() - timedelta(days=29)
    teams = ["Team Amplifiers", "Team Hear4Life", "Team Sound Check", "VoiceAI"]
    reasons = ["Billing", "Scheduling", "Device pairing", "Voice AI follow-up"]
    for d in range(30):
        for i, team in enumerate(teams):
            rating = 5 if (d + i) % 6 else 2
            rows.append({
                "date": (start + timedelta(days=d)).isoformat(),
                "datetime": (start + timedelta(days=d)).isoformat() + "T10:00:00",
                "team": team,
                "cid": f"C{i}",
                "name": f"{team} Agent",
                "rating": rating,
                "solved": rating >= 4,
                "reason": reasons[i],
                "summary": f"QA sample summary for {reasons[i]}",
                "summary_raw": f"QA sample summary for {reasons[i]}",
                "call_id": f"call-{d}-{i}",
                "opp_id": f"opp-{d}-{i}",
                "response_id": f"resp-{d}-{i}",
                "created_by_id": "u1",
                "owner_id": "o1",
            })
    return _build_csat_index(rows)


PORTAL_BATCH = make_portal_batch()
ARTICLES = make_articles()
SEARCH = make_search()
CATEGORIES = make_categories()
SESSIONS = make_sessions()
INSIGHTS = make_insights()
VIDEOS = make_videos()
CSAT_INDEX = make_csat_index()


def prepare_html(fname: str) -> str:
    soup = BeautifulSoup((ROOT / fname).read_text(), "html.parser")
    if soup.head:
        base = soup.new_tag("base", href=BASE)
        soup.head.insert(0, base)
        storage = soup.new_tag("script")
        storage.string = """
(() => {
  function makeStorage(){ const store = {}; return { getItem:k => Object.prototype.hasOwnProperty.call(store,k) ? store[k] : null, setItem:(k,v) => { store[k] = String(v); }, removeItem:k => { delete store[k]; }, clear:() => { Object.keys(store).forEach(k => delete store[k]); } }; }
  try { Object.defineProperty(window, 'localStorage', { value: makeStorage(), configurable: true }); } catch (_) {}
  try { Object.defineProperty(window, 'sessionStorage', { value: makeStorage(), configurable: true }); } catch (_) {}
})();
"""
        soup.head.insert(1, storage)
    for tag in list(soup.find_all("script", src=True)):
        src = tag.get("src", "")
        replacement = soup.new_tag("script")
        if "chart" in src.lower():
            replacement.string = CHART_STUB
            tag.replace_with(replacement)
        elif "dashboard_ux.js" in src or "portal-system.js" in src:
            asset = ROOT / "portal-system.js"
            if not asset.exists():
                asset = ROOT / "assets" / "dashboard_ux.js"
            replacement.string = asset.read_text()
            tag.replace_with(replacement)
    for tag in list(soup.find_all("link", href=True)):
        href = tag.get("href", "")
        if "dashboard_ux.css" in href or "portal-overrides.css" in href:
            style = soup.new_tag("style")
            asset = ROOT / "portal-overrides.css"
            if not asset.exists():
                asset = ROOT / "assets" / "dashboard_ux.css"
            style.string = asset.read_text()
            tag.replace_with(style)
        elif "fonts.googleapis.com" in href:
            tag.decompose()
    return str(soup)


def json_response(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def route_handler(route, request):
    parsed = urlparse(request.url)
    path = parsed.path
    if path == "/api/metrics/batch":
        return json_response(route, PORTAL_BATCH)
    if path == "/api/articles":
        return json_response(route, ARTICLES)
    if path == "/api/search":
        return json_response(route, SEARCH)
    if path in {"/api/sessions/full", "/api/sessions"}:
        return json_response(route, SESSIONS)
    if path == "/api/videos":
        return json_response(route, VIDEOS)
    if path == "/api/categories":
        return json_response(route, CATEGORIES)
    if path == "/api/insights":
        return json_response(route, INSIGHTS)
    if path in {"/api/csat/raw", "/api/csat/view"}:
        return json_response(route, CSAT_INDEX)
    if path == "/api/csat/status":
        return json_response(route, {
            "available": True,
            "date_min": CSAT_INDEX.get("date_min"),
            "date_max": CSAT_INDEX.get("date_max"),
            "total_rows": CSAT_INDEX.get("total_rows"),
            "generated": CSAT_INDEX.get("generated"),
            "source": {"active":"domo", "domo_configured": True, "raw_drilldowns_public": True, "refresh_requires_admin": False},
            "quality": CSAT_INDEX.get("quality", {}),
        })
    if path == "/api/refresh/csat":
        return json_response(route, {"success": True, "message":"Domo CSAT refreshed", "rows": CSAT_INDEX.get("total_rows")})
    if path == "/api/sg/metrics/timeseries":
        mult = 5 if "answer_submitted" in parsed.query else 7
        return json_response(route, make_sg_timeseries(mult))
    if path == "/api/sg/metrics/topn":
        return json_response(route, make_sg_topn())
    if path.startswith("/api/sg/journeys"):
        return json_response(route, {"journeys": [], "items": [], "answers": [], "total": 0})
    route.fulfill(status=204, body="")


def collect_console(page, messages):
    page.on("console", lambda msg: messages.append({"type": msg.type, "text": msg.text}))
    page.on("pageerror", lambda exc: messages.append({"type": "pageerror", "text": str(exc)}))


def page_expand_smoke(page, fname: str, label: str, setup=None):
    errors = []
    console = []
    collect_console(page, console)
    page.set_content(prepare_html(fname), wait_until="load")
    if setup:
        setup(page)
    page.wait_for_timeout(3500)
    ux_version = page.evaluate("window.DashboardUX && window.DashboardUX.version")
    enhanced = page.locator('canvas[data-ux-enhanced="1"]').count()
    action_expands = page.locator('[data-ux-action="expand"][data-chart-id]').count()
    visible_expand = page.locator('[data-ux-action="expand"][data-chart-id]').count()
    chart_ids = page.evaluate("Array.from(document.querySelectorAll('canvas[data-ux-enhanced=\"1\"]')).map(c => c.id)")
    if enhanced == 0:
        errors.append(f"{label}: no charts were enhanced by the UX engine")
    if action_expands < enhanced:
        errors.append(f"{label}: only {action_expands} Expand actions for {enhanced} enhanced charts")
    for chart_id in chart_ids:
        try:
            loc = page.locator(f'[data-ux-action="expand"][data-chart-id="{chart_id}"]').first
            loc.click(timeout=3000)
            page.locator('#uxChartStudio.open').wait_for(timeout=3000)
            title = page.locator('#uxStudioTitle').inner_text(timeout=2000)
            if not title.strip():
                errors.append(f"{label}:{chart_id}: studio title was empty")
            page.locator('[data-ux-close="studio"]').click(timeout=2000)
            page.wait_for_timeout(75)
        except Exception as exc:
            errors.append(f"{label}:{chart_id}: expand click failed: {exc}")
    return {
        "page": label,
        "ux_version": ux_version,
        "enhanced_charts": enhanced,
        "expand_action_buttons": action_expands,
        "visible_expand_buttons": visible_expand,
        "chart_ids": chart_ids,
        "errors": errors,
        "console_errors": [m for m in console if m["type"] in {"error", "pageerror"}],
    }


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        ctx.route("**/*", route_handler)
        results = []

        page = ctx.new_page()
        results.append(page_expand_smoke(page, "index.html", "Portal Activity"))
        page.close()

        page = ctx.new_page()
        results.append(page_expand_smoke(page, "csat.html", "CSAT / Call Quality"))
        page_text = page.locator("body").inner_text(timeout=3000)
        forbidden = ["Data quality:", "Executive readout", "Primary risk", "Protected drilldowns", "Admin access", "Unlock raw drilldowns", "Voice AI lens", "Domo team"]
        for term in forbidden:
            if term.lower() in page_text.lower():
                results[-1]["errors"].append(f"CSAT still shows removed/locked UI text: {term}")
        if "Voice AI" not in page_text:
            results[-1]["errors"].append("CSAT page does not show Voice AI in the loaded UI")
        page.close()

        page = ctx.new_page()
        def open_metrics(pg):
            pg.evaluate("if (typeof showSection === 'function') showSection('s4')")
            pg.wait_for_timeout(800)
        results.append(page_expand_smoke(page, "starter_guides.html", "Starter Guides", setup=open_metrics))
        page.close()
        browser.close()

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "rendering_method": "real HTML with local assets inlined; API calls stubbed by Playwright routes",
        "results": results,
        "passed": all(not r["errors"] for r in results),
        "total_enhanced_charts": sum(r["enhanced_charts"] for r in results),
        "total_expand_clicks": sum(len(r["chart_ids"]) for r in results),
    }
    (ROOT / "docs" / "FINAL_UI_CLICK_QA.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
