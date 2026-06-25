# Hard Critique Report - CS Portal Dashboard

Reviewed artifact: `CS_Portal_Dashboard-starter-guides.zip`

Scope reviewed: FastAPI backend, vanilla HTML/CSS/JS frontend, CSAT upload/indexing flow, Starter Guides proxy, tests, project structure, BI/dashboard design, and future-proofing for new data sources.

Important limitation: I reviewed the source code directly. Browser rendering/screenshot capture was blocked in the execution environment, so the visual critique is based on markup, CSS, layout structure, and interaction code rather than a live screenshot.

## Executive verdict

This is a strong internal analytics prototype, but it is not yet a production-grade, future-proof dashboard platform.

The dashboard has useful coverage: portal activity, CSAT, content engagement, videos, categories, sessions, call reasons, consultants, and starter guides. The biggest issue is not lack of features. The issue is that the dashboard has become a large, tightly coupled, hard-coded system where data definitions, UI, API shape, transformations, caching, and operational controls are mixed together.

Current grade by lens:

- UI/UX: B- as an internal prototype; C for executive usability and accessibility.
- BI/data design: C+; many useful metrics, but weak semantic layer, weak metric definitions, and some misleading calculations.
- Full-stack engineering: C-/D+ for production readiness; the code works in places, but the architecture is brittle, insecure by default, and not future-proof.
- Future-proofing: C-; adding new data will require touching too many unrelated parts of the system.

## Most urgent problems

1. The test suite is currently failing.
   - `pytest` result: 43 passed, 18 failed.
   - Failures are mostly from CSAT tests where sample rows are dropped by the hard-coded CSAT team allow-list.
   - Evidence: `main.py:217-233` defines default teams and filters out any other team. The test fixtures use teams such as `Team A`, `T`, and `Team Valid`, so `_build_csat_index()` receives zero usable rows.
   - This is a release blocker because it means CI cannot be trusted.

2. The dashboard exposes sensitive CSAT data without authentication.
   - `/api/csat/raw` serves the full CSAT index with consultant names, teams, call IDs, call summaries, opportunity/customer identifiers, reasons, and response metadata.
   - `/api/refresh`, `/api/refresh/csat`, `/cache/clear`, and debug endpoints are unprotected.
   - `main.py:1924` allows CORS from every origin.
   - This is the highest-risk issue in the codebase.

3. The upload password is insecurely implemented.
   - `main.py:208` defaults `CSAT_UPLOAD_PASSWORD` to `hearcom2024`.
   - `csat.html:3515` sends the password in the query string.
   - Query strings can appear in server logs, browser history, proxies, and monitoring tools.

4. The CSAT team filter is hard-coded and silently drops future data.
   - Default allowed teams are `Team Amplifiers`, `Team Hear4Life`, `Voice AI`, and `Team Sound Check`.
   - Any new team is dropped unless the environment variable is updated.
   - This directly violates the future-proofing requirement.
   - The UI should show dropped row counts and unknown teams; the backend should not silently discard production data.

5. Some metrics are analytically misleading.
   - The CSAT KPI labeled `First-call resolution` appears to display `solved_pct`, not true FCR. True FCR is handled elsewhere using opportunity/call grouping. This is a serious label/definition problem.
   - Article views for selected date ranges are estimated by scaling all-time article counts by a global time-series ratio. That creates fake precision and can mislead users.
   - Session and content metrics appear partially sampled/top-N dependent and should expose coverage and confidence.

6. Frontend XSS risk is high.
   - The frontend uses `innerHTML` extensively with data coming from CMS/Domo/user-controlled files.
   - Counts found: `index.html` has 23 `innerHTML` occurrences, `csat.html` has 33, and `starter_guides.html` has 16.
   - Examples include article labels, search queries, consultant names, team names, call reasons, summaries, video titles, and URLs.
   - The CSAT detail drawer has an `_esc()` helper, which is good, but escaping is inconsistent across the application.

7. The system is a monolith.
   - `main.py` is around 2,700 lines.
   - `index.html` is around 3,200 lines.
   - `csat.html` is around 3,700 lines.
   - Data ingestion, transformation, caching, API endpoints, HTML serving, and operational debug tools are packed into a small number of files.
   - This makes every future data addition risky.

8. There is no formal metric layer.
   - Metric formulas live in Python and JavaScript.
   - Event definitions are duplicated and inconsistent between backend and frontend.
   - There is no metric registry, schema version, data contract, owner, freshness definition, grain definition, or certified metric catalog.

9. The frontend and backend disagree on data start/event definitions.
   - Backend `DATA_START` is `2026-04-24` (`main.py:195`).
   - Frontend `DATA_START` is `2023-06-22` (`index.html:902`).
   - Backend includes `auth.logout`; frontend has `total.views` but omits `auth.logout`.
   - This is exactly how dashboards drift into inconsistent numbers.

10. The dashboard lacks production observability.
   - There is no structured logging, no request IDs, no metrics, no tracing, no job history, no audit trail, and no data quality run table exposed to the UI.
   - Failures are printed but not operationalized.

## UI/UX critique

### What is working

- The product has broad coverage and tries to support real workflows, not just static KPIs.
- Sticky navigation, date filters, page separation, refresh status, chart expansion, exports, drill-down drawers, and consultant/team focus are all good product instincts.
- The dashboard is visually more polished than a raw admin panel. It uses cards, chips, shadows, rounded panels, muted colors, and consistent spacing tokens.
- The CSAT page provides meaningful drill-down paths from KPI to team to consultant to call details.

### What is lagging

#### 1. The dashboard is too dense

The interface is trying to be an executive dashboard, analyst dashboard, operational monitor, drill-down tool, and data QA tool at the same time. That creates cognitive overload.

Symptoms:

- Many small cards compete for attention.
- The header contains navigation, status, date controls, custom dates, and refresh actions in a crowded row.
- There are many charts with similar visual weight.
- Tables, charts, and filters are stacked without a strong story hierarchy.
- Users must infer which metric matters most.

Recommended fix:

Create distinct pages or modes:

- Executive Overview: what changed, what matters, what needs action.
- Drivers: why metrics moved.
- Operations: teams, consultants, calls, workload, exceptions.
- Content: articles, videos, search, categories.
- Data Quality: freshness, dropped rows, missing fields, coverage.

#### 2. The dashboard does not answer the three BI questions clearly

A great BI dashboard should guide the user through:

1. What happened?
2. Why did it happen?
3. What should I do next?

This dashboard mostly shows what happened. It partially supports why. It rarely gives clear action.

Examples:

- Low CSAT reasons are shown, but not converted into recommended action buckets.
- Consultant tables exist, but low sample warnings and coaching prioritization are weak.
- Article performance exists, but content owners do not get a clear action list: retire, update, promote, merge, investigate.
- Search gaps exist in code, but the visible page appears not to have the corresponding DOM elements for `searchTable`, `gapsList`, or `searchStatus`.

Recommended fix:

Add a top "Action Queue" section:

- Consultants needing review, with sample-size thresholds.
- Teams with statistically meaningful drops.
- Articles with high traffic and poor feedback.
- Searches with high volume and low success.
- Call reasons driving negative CSAT.
- Starter guide steps with high drop-off.

#### 3. Scope is confusing

There are several overlapping scopes:

- Global date range.
- Chart-level date controls.
- Team focus.
- Consultant focus.
- All-time vs selected period behavior.
- Custom date range.
- Selected article/video/category drill-downs.

The UI does not make the current analysis contract obvious enough.

Recommended fix:

Add a persistent scope bar that reads like a sentence:

`Showing: CSAT responses | Jun 1-Jun 25, 2026 | Team: Voice AI | Consultant: All | Grain: Day | Data refreshed: 10:22 AM ET`

Every metric card should clearly inherit this scope unless it explicitly says otherwise.

#### 4. Accessibility is not production-ready

Problems visible from the code:

- Many icon-only or compact buttons lack explicit `aria-label`s.
- Modals/drawers do not consistently trap focus.
- There is heavy reliance on color to signal good/bad/neutral.
- Many tables are rendered through raw HTML strings instead of semantic, reusable components.
- Font sizes such as 10-12px are common and will be difficult for many users.
- Keyboard navigation for drill-down workflows appears incomplete.

Recommended fix:

Treat accessibility as a feature, not polish:

- Use semantic buttons and links.
- Add `aria-current` for active navigation.
- Trap focus in modals and drawers.
- Support Escape close consistently.
- Do not rely on color alone.
- Increase base table font size.
- Add visible focus states.
- Add screen-reader text for status chips and metric deltas.

#### 5. Empty, loading, and error states need stronger design

There are loading indicators, but the system sometimes removes the main loading overlay before all data is actually ready. Several sections may silently show empty states or fail without a consistent recovery path.

Recommended fix:

Standardize states for every card:

- Loading skeleton.
- Loaded with data.
- Loaded with no data.
- Partial data with warning.
- Error with retry.
- Stale data warning.

#### 6. Drill-down tables need enterprise-grade behavior

Current tables are useful but not scalable.

Needed improvements:

- Pagination or virtualization.
- Column sorting.
- Column visibility controls.
- Sticky table headers.
- Export exactly what is filtered.
- Search within table.
- Saved views.
- Row-level permissions.
- Clear sample-size warnings.

## BI and data-analysis critique

### 1. There is no certified semantic layer

Right now, the dashboard is calculating metrics in Python and JavaScript directly. This is dangerous because metric definitions become scattered.

A future-proof dashboard needs a metric registry with fields such as:

```yaml
metric_id: csat.avg_rating
name: Average CSAT
business_definition: Average post-call survey rating on a 1-5 scale.
grain: response
allowed_dimensions: [date, week, month, team, consultant, call_reason]
numerator: sum_rating
denominator: response_count
filters: valid_rating = true
timezone: America/New_York
source: domo_csat_export
owner: CS Operations
min_sample_size: 30
schema_version: 1
```

Without this, adding new data means adding code in many files and risking metric drift.

### 2. Metric definitions need tooltips and data contracts

Users should not have to guess what these mean:

- CSAT.
- Solved.
- First-call resolution.
- True FCR.
- Unique opportunities.
- Calls per opportunity.
- Low rating.
- Conversion rate.
- Active users.
- Session.
- Article health.
- Starter guide completion.

Every metric should expose:

- Formula.
- Source table/dataset.
- Date logic.
- Inclusion/exclusion rules.
- Refresh time.
- Known limitations.
- Sample size.

### 3. FCR labeling must be corrected immediately

The CSAT KPI labeled `First-call resolution` appears to use survey solved percentage, not true first-call resolution based on repeat calls/opportunity grouping.

This is not just a wording issue. It changes business interpretation.

Recommended fix:

Use separate labels:

- `Solved rate`: based on survey field `SOLVED`.
- `True first-call resolution`: based on opportunity/call grouping.
- `Repeat-call rate`: percentage of opportunities with more than one call.

### 4. Date-range article metrics are likely misleading

The article table appears to scale all-time article views by a global time-series ratio for the selected date range. That assumes every article follows the same temporal distribution as the entire portal. That is not safe.

Example problem:

- Article A may have spiked this week due to a process change.
- Article B may be old and no longer used.
- A global ratio will hide both realities.

Recommended fix:

Create a real fact table at article-date grain:

- `date`
- `article_id`
- `article_title`
- `category`
- `views`
- `unique_users`
- `feedback_positive`
- `feedback_negative`
- `search_referrals`

Do not show estimated article-period metrics unless clearly labeled as estimated.

### 5. Data quality is mostly invisible

The code drops rows and transforms fields, but the business user does not get enough visibility into what was dropped or why.

Needed data quality cards:

- Source row count.
- Parsed row count.
- Dropped rows by reason.
- Unknown teams.
- Missing consultant ID/name.
- Invalid rating values.
- Missing call ID.
- Missing opportunity ID.
- Duplicate response IDs.
- Date parsing failures.
- Latest source timestamp.
- Refresh duration.

This is essential because the dashboard is used for decisions about people, teams, and customer experience.

### 6. Low sample-size handling is insufficient

Consultant/team leaderboards are risky without sample size warnings. A consultant with three surveys can look excellent or terrible by chance.

Recommended fix:

- Hide ranking until sample size exceeds a threshold.
- Show low-N warning chips.
- Use confidence intervals or at least Wilson intervals for rates.
- Sort by impact, not just rate.
- Prefer "needs review" scoring that combines volume, severity, and confidence.

### 7. The dashboard needs targets, benchmarks, and thresholds

A BI dashboard should not only say "up" or "down". It should say whether the result is good enough.

Add configurable targets:

- CSAT target.
- Low-rating maximum.
- Solved-rate target.
- True FCR target.
- Repeat-call-rate maximum.
- Search no-result maximum.
- Article negative feedback maximum.
- Starter guide completion target.

### 8. Timezone logic needs standardization

The frontend uses browser/UTC date functions in several places, while the backend uses UTC-like logic and some deprecated `datetime.utcnow()` calls.

Recommended fix:

- Choose one business timezone, probably `America/New_York` unless operations require otherwise.
- Store raw timestamps in UTC.
- Convert to business dates in the backend.
- Return `business_date` and `timezone` in API metadata.
- Avoid slicing `toISOString()` for local business dates.

### 9. The dashboard needs annotations

Power BI/Tableau-style dashboards are much more useful when users can see events that explain movement.

Add annotations for:

- Product releases.
- Training changes.
- Team reorganizations.
- Domo/CMS outages.
- Content migrations.
- Holidays.
- Process changes.

## Full-stack and code critique

### 1. The architecture is too tightly coupled

Current structure:

- One large backend file.
- Giant HTML files with inline CSS and JavaScript.
- Global mutable backend state.
- Duplicated frontend/backend constants.
- Direct string-built DOM updates.
- Multiple responsibilities mixed together.

Recommended structure:

```text
app/
  main.py
  config.py
  api/
    portal.py
    csat.py
    starter_guides.py
    admin.py
  services/
    cms_client.py
    csat_ingestion.py
    starter_guides_client.py
    metrics_service.py
  models/
    metrics.py
    csat.py
    portal.py
  jobs/
    refresh_jobs.py
  repositories/
    metrics_repository.py
    csat_repository.py
  security/
    auth.py
    permissions.py
frontend/
  src/
    components/
    pages/
    charts/
    api/
    types/
    utils/
```

### 2. API contracts need typed models

FastAPI is most valuable when the API has Pydantic request/response models. The current API returns flexible dictionaries and compact encoded objects.

Recommended fix:

- Define response models for every endpoint.
- Add `schema_version` to major responses.
- Add `meta` to all analytic responses.
- Validate request parameters.
- Publish API examples through OpenAPI.

### 3. Compact keys make the frontend brittle

The CSAT index uses compact keys such as `t`, `sr`, `s`, `l`, `d`, `tm`, `cn`, `rs`, `ft`, `fr`, and similar.

This saves bytes, but it hurts maintainability. New developers and analysts cannot understand the data contract quickly.

Recommended fix:

Either return readable keys or include a versioned schema map and typed frontend decoder.

### 4. Frontend code should not be giant script blocks

The current HTML files mix:

- Layout.
- CSS.
- API calls.
- Business logic.
- Chart rendering.
- Data transformations.
- Export logic.
- Modal behavior.
- Drill-down behavior.

Recommended fix:

Move to a modern frontend structure, even if staying lightweight:

- Vite + TypeScript.
- Componentized UI.
- Central API client.
- Central chart factory.
- Central date-range utility.
- Shared metric definitions from backend or config.
- State encoded in URL query params.

### 5. Security controls are not acceptable for production

Immediate changes:

- Add authentication.
- Add role-based authorization.
- Restrict CORS.
- Protect admin/debug endpoints.
- Remove default passwords.
- Do not pass secrets in query strings.
- Add rate limiting.
- Add upload size limits.
- Add audit logs.
- Disable debug endpoints in production.
- Sanitize or escape every untrusted string.

### 6. Cache strategy is not durable or multi-instance safe

The backend writes cache to `/tmp/cs_portal_cache.json`. This is fragile in hosted environments and does not work reliably across multiple instances.

Recommended fix:

Use one of:

- Redis for cache and distributed locks.
- Postgres/Supabase tables for metric snapshots.
- Object storage for large precomputed JSON artifacts.

### 7. Refresh jobs need a real job system

The app starts background refresh loops inside the FastAPI lifespan. This can cause duplicate jobs in multi-worker deployments and is difficult to monitor.

Recommended fix:

Use:

- Render cron job.
- APScheduler with a single-worker lock.
- Celery/RQ/Arq if the system grows.
- A `job_runs` table with status, duration, error, row counts, and output metadata.

### 8. Starter Guides proxy needs connection pooling and guardrails

The Starter Guide service creates a new HTTP client for each request and exposes flexible proxy endpoints.

Recommended fix:

- Reuse one `httpx.AsyncClient` during app lifespan.
- Validate allowed events and dimensions.
- Rate limit requests.
- Cache common metric queries.
- Add timeouts and circuit breakers.
- Protect debug discovery endpoints.

### 9. There is dead or inconsistent code

Examples:

- After the CSAT upload `except` block, there is unreachable legacy code referencing older CSAT index shapes.
- Search analytics JS references `searchTable`, `gapsList`, and `searchStatus`, but those elements do not appear in the page markup.
- `app.py` is empty.
- Frontend and backend event lists do not match.

Dead code increases maintenance cost and hides bugs.

### 10. Tests are incomplete and currently failing

What exists:

- Python tests for many backend functions.

What is missing:

- Passing CI baseline.
- Frontend unit tests.
- API contract tests.
- End-to-end smoke tests.
- Security tests.
- Upload parser tests for malformed files.
- Load tests for CSAT/raw and article tables.
- Visual regression tests.

Recommended CI gate:

```text
ruff
black --check
mypy or pyright
pytest
frontend typecheck
frontend unit tests
playwright smoke test
security scan
```

## Future-proofing plan

### Target architecture

Move from hard-coded dashboard pages to a metrics platform pattern.

Core concepts:

1. Data source connector
   - CMS connector.
   - Domo CSAT connector.
   - Starter Guide connector.
   - Future connectors.

2. Raw fact tables
   - Store raw event/response data with minimal transformation.

3. Dimension tables
   - Teams, consultants, articles, videos, categories, starter guides, customers/opportunities if allowed.

4. Metric registry
   - Certified formulas and dimensions.

5. Query API
   - One flexible endpoint that accepts metrics, dimensions, date range, filters, and grain.

6. Dashboard config
   - Pages and cards are configured from JSON/YAML rather than hard-coded everywhere.

7. Data quality layer
   - Every ingest run produces quality metadata.

### Recommended API shape

```json
{
  "dataset": "csat",
  "metrics": ["avg_rating", "low_rating_rate", "true_fcr"],
  "dimensions": ["team", "consultant"],
  "date_range": {"from": "2026-06-01", "to": "2026-06-25"},
  "time_grain": "day",
  "filters": {"team": ["Voice AI"]}
}
```

Response:

```json
{
  "schema_version": 1,
  "meta": {
    "source": "domo_csat",
    "refreshed_at": "2026-06-25T14:30:00Z",
    "timezone": "America/New_York",
    "row_count": 18342,
    "warnings": ["12 rows dropped due to invalid rating"]
  },
  "data": [...]
}
```

### Suggested data model

Minimum useful warehouse-style model:

```text
dim_date
dim_team
dim_consultant
dim_article
dim_video
dim_category
dim_starter_guide
fact_portal_event
fact_csat_response
fact_csat_call
fact_starter_guide_event
fact_search_query
fact_article_daily
fact_video_daily
metric_snapshot
data_quality_run
job_run
```

This makes it much easier to add new data without rewriting the dashboard.

## Prioritized roadmap

### Fix immediately

1. Add authentication and authorization.
2. Restrict CORS.
3. Remove default upload password.
4. Stop sending upload password in query string.
5. Protect refresh, cache clear, upload, raw CSAT, and debug endpoints.
6. Fix failing tests.
7. Correct the FCR/Solved metric labeling.
8. Escape all untrusted frontend data.
9. Remove or disable debug endpoints in production.
10. Show data freshness and row count metadata in the UI.

### Fix next

1. Split `main.py` into modules.
2. Add typed Pydantic response models.
3. Centralize event definitions.
4. Replace hard-coded team allow-list with a managed dimension/config table.
5. Add data quality reporting.
6. Add URL-shareable filter state.
7. Add pagination/virtualization for drill-down tables.
8. Create a central frontend API client.
9. Add frontend tests and E2E smoke tests.
10. Move from `/tmp` cache to Redis/Postgres/object storage.

### Strategic rebuild

1. Introduce a metric registry.
2. Store data in fact/dimension tables.
3. Build a generic metric query API.
4. Componentize the dashboard frontend.
5. Add dashboard configuration files for cards/pages.
6. Add a proper job system with run history.
7. Add role-based data access.
8. Add annotations and targets.
9. Add data lineage and quality dashboards.
10. Use CI/CD gates before deployment.

## Final assessment

The dashboard is valuable and has a lot of useful domain work already inside it. But it is currently over-concentrated in large files, under-protected, semantically inconsistent, and difficult to extend safely.

The highest-leverage shift is this: stop treating the dashboard as a set of pages and start treating it as a metrics product.

That means:

- Certified metrics.
- Versioned schemas.
- Data quality metadata.
- Secure APIs.
- Reusable components.
- Server-side filtering for sensitive data.
- Modular connectors.
- A clear UX story from metric to driver to action.

Until those pieces are in place, every new data source will make the current structure more fragile.
