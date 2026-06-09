# Security Policy

## Supported Versions

This project is an MVP. Security fixes are expected to land on the current main
branch only.

## Reporting A Vulnerability

If you find a vulnerability, please report it privately to the project
maintainer before opening a public issue. Include:

- affected version or commit
- steps to reproduce
- impact
- any suggested mitigation

Do not include live admin tokens, cookie secrets, contestant endpoint secrets,
or personal data in public issues, logs, screenshots, or pull requests.

## Production Requirements

Production deployments must set:

- `ADMIN_TOKEN`: a long random admin login token
- `ADMIN_COOKIE_SECRET`: a different long random secret for encrypted cookies
- `ADMIN_COOKIE_SECURE=true`: send admin cookies only over HTTPS
- `WCT_ALLOWED_HOSTS`: the public hostnames Cloudflare sends to the origin
- `WCT_ENABLE_HSTS=true`: once HTTPS is working end to end

The FastAPI app should bind only to `127.0.0.1`. Put Cloudflare Tunnel in front
of it, and protect `/tipping/admin*` with Cloudflare Access.

## Known Risk Areas

- Admin endpoint validation makes server-side requests to contestant URLs.
  Treat admin access as trusted and keep Cloudflare Access enabled.
- The public leaderboard API tester sends browser requests to contestant
  endpoints. Contestant endpoints should use their own rate limits and CORS
  rules.
- Persistent state lives in JSON files. Keep `data/registry.json`,
  `data/predictions.json`, `data/scores.json`, `data/run_log.json`, and
  `data/simulations.json` free of private data before publishing.
- Do not publish scraped/raw third-party datasets, source workbooks, or other
  imported data unless you have the right license.
