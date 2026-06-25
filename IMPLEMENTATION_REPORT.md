# Metrics registry

The dashboard should be treated as a governed metrics product, not a set of one-off charts. Any new metric should be added to `metrics_registry.json` before the frontend renders it.

## Required fields for every metric

| Field | Meaning |
|---|---|
| `metric_id` | Stable machine-readable ID. Do not rename without a migration. |
| `name` | User-facing label. |
| `business_definition` | Plain-English definition shown in docs/tooltips. |
| `grain` | Lowest-level row/entity the metric is calculated from. |
| `source` | System or file that owns the raw data. |
| `owner` | Business owner accountable for the definition. |
| `calculation` | Numerator/denominator or aggregation rule. |
| `allowed_dimensions` | Dimensions that can be safely sliced without changing the definition. |
| `caveats` | Sampling, estimation, missing-field, or interpretation warnings. |

## Current high-risk definitions

### CSAT solved rate vs true FCR

Solved rate and true first-call resolution are intentionally separate metrics.

Solved rate comes from the survey `SOLVED` flag. It answers: “Did the respondent say the issue was solved?”

True FCR comes from opportunity/call grouping. It answers: “Was the opportunity resolved with exactly one call?” It requires reliable `OPPORTUNITY_ID` and `CALL_ID` fields. When opportunity IDs are missing, the UI should display a data-quality warning and avoid overclaiming FCR precision.

### Article selected-period views

The current dashboard can show exact daily totals for `article.viewed`, but not exact article-by-day views unless the upstream source provides article-day facts. Until that exists, selected-period article table counts are directional estimates based on all-time article share multiplied by selected-period article traffic. They are useful for prioritization, not for formal reporting.

## Adding a new data source

1. Define its metrics in `metrics_registry.json`.
2. Add parsing and normalization near the backend source adapter.
3. Include row counts, dropped rows, missing critical fields, date range, and schema version in the response.
4. Add tests for empty files, malformed rows, missing optional fields, and new dimensions.
5. Escape every value before rendering it in the browser.
