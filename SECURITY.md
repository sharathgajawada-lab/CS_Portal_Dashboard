# Security checklist

## Admin token

Set `DASHBOARD_ADMIN_TOKEN` in every deployed environment. The dashboard will return `503` for protected endpoints if admin auth is required but no token is configured.

Accepted request formats:

```text
X-Admin-Token: <token>
Authorization: Bearer <token>
```

The browser upload flow stores the token in `sessionStorage` for the current browser session and sends it in `X-Admin-Token`. Tokens are no longer sent in query strings.

## Protected surfaces

Protect or restrict these surfaces in production:

- `/api/csat/raw`
- `/api/csat`
- `/upload/csat`
- `/api/refresh`
- `/api/refresh/csat`
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

Every API/upload/CMS/Supabase/Domo value must be treated as untrusted. Prefer `textContent` for plain text. When a string must be inserted via `innerHTML`, escape it first.
