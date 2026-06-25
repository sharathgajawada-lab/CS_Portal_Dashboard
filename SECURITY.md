# Security checklist

## Admin token

Set `DASHBOARD_ADMIN_TOKEN` in deployed environments for break-glass, cache, debug, and legacy protected endpoints. Raw CSAT and CSAT refresh are open by default in this internal-dashboard build per product requirement.

Accepted request formats:

```text
X-Admin-Token: <token>
Authorization: Bearer <token>
```

Tokens are never sent in query strings. The current CSAT UI does not show an admin unlock modal.

## CSAT data exposure model

CSAT is Domo-first:

```text
Domo -> backend index -> /api/csat/view for sanitized aggregate rendering
                     -> /api/csat/raw for open internal call-level drilldowns
```

`/api/csat/view` is public and sanitized. It removes call IDs, call summaries, opportunity/customer identifiers, response details, and consultant call caches.

`/api/csat/raw` is open by default for the internal dashboard and should be treated as sensitive operational data. Keep the deployment internal, or set `CSAT_RAW_PUBLIC=false` and add a proper permission layer before broader exposure.

`/upload/csat` remains available only as a protected break-glass backend path. It is not exposed in the browser UI.

## Protected surfaces

Protect or restrict these surfaces in production according to deployment context:

- `/api/csat/raw` when the dashboard is not strictly internal
- `/api/refresh/csat` when refresh should be admin-only (`CSAT_REFRESH_REQUIRES_ADMIN=true`)
- `/api/csat`
- `/api/refresh`
- `/upload/csat`
- `/cache/clear`
- `/debug/*`

## CORS

CORS is not opened by default. Configure `ALLOWED_ORIGINS` only when the dashboard must be called from another origin.

Example:

```bash
export ALLOWED_ORIGINS=https://cs-portal-dashboard.onrender.com,https://internal.example.com
```

## Secrets

Do not commit or expose:

- `CMS_API_KEY`
- `SUPABASE_KEY`
- `DASHBOARD_ADMIN_TOKEN`
- `DOMO_CLIENT_SECRET`

## Frontend safety

Every API/CMS/Supabase/Domo/break-glass value must be treated as untrusted. Prefer `textContent` for plain text. When a string must be inserted via `innerHTML`, escape it first.
