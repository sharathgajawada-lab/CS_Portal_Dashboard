# Security checklist

## Admin token

Set `DASHBOARD_ADMIN_TOKEN` in every deployed environment. Protected endpoints return `503` when admin auth is required but no token is configured.

Accepted request formats:

```text
X-Admin-Token: <token>
Authorization: Bearer <token>
```

The browser stores the token only in `sessionStorage` for the current session and sends it through `X-Admin-Token`. Tokens are never sent in query strings.

## CSAT data exposure model

CSAT is Domo-first:

```text
Domo -> backend index -> /api/csat/view for aggregate dashboard rendering
                     -> /api/csat/raw for protected call-level drilldowns
```

`/api/csat/view` is public and sanitized. It removes call IDs, call summaries, opportunity/customer identifiers, response details, and consultant call caches.

`/api/csat/raw` is protected and should be treated as sensitive operational data.

`/upload/csat` remains available only as a protected break-glass backend path. It is not exposed in the browser UI.

## Protected surfaces

Protect or restrict these surfaces in production:

- `/api/csat/raw`
- `/api/csat`
- `/api/refresh/csat`
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
