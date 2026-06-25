# Security Notes

This dashboard is Domo-first for CSAT and open-drilldown by default in the UI because the requested product behavior is that no CSAT chart/table/detail path appears locked.

## Admin token

Set `DASHBOARD_ADMIN_TOKEN` in deployed environments. It still protects admin/debug/cache/break-glass routes such as:

- `/api/refresh`
- `/cache/clear`
- `/debug/*`
- `/upload/csat`

`CSAT_UPLOAD_PASSWORD` is accepted only as a backwards-compatible alias. New deployments should use `DASHBOARD_ADMIN_TOKEN`.

## CSAT drilldowns

Default behavior:

```text
Domo dataset -> backend CSAT index -> /api/csat/raw -> open dashboard drilldowns
```

`/api/csat/raw` is open by default so the CSAT page can show call-level drilldowns without admin locks. For a public or external deployment, set:

```bash
CSAT_DRILLDOWNS_REQUIRE_TOKEN=true
```

When that flag is enabled, `/api/csat/raw` and the backwards-compatible `/api/csat` alias require `X-Admin-Token` or `Authorization: Bearer <token>`.

## Domo refresh

`/api/refresh/csat` is callable from the CSAT page and pulls directly from Domo when `DOMO_CLIENT_ID`, `DOMO_CLIENT_SECRET`, and `DOMO_DATASET_ID` are configured.

## CORS

Leave `ALLOWED_ORIGINS` blank for same-origin deployment. Set it to an explicit comma-separated allow-list if the API is called from another origin.

## Break-glass ingestion

`POST /upload/csat` remains available only as a backend recovery/backfill path and is not exposed in the browser UI. Keep it protected with `DASHBOARD_ADMIN_TOKEN`.
