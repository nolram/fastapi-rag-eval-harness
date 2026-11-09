# Authentication

Every Lumen API call authenticates with an API key or an OAuth2 client-credentials token.

## API key rotation

API keys expire 90 days after creation. A retired key keeps working for a 24-hour grace period so a
rotation does not break live traffic. A workspace may hold at most 5 active API keys at once.

## Bearer token lifetime

Request a token from `POST /v1/oauth/token` with client credentials. Access tokens expire after
3600 seconds, and there is no refresh token; request a new token before `expires_in` reaches 0.
Send it as `Authorization: Bearer <token>` on every request.

## IP allowlist

Each environment accepts at most 20 CIDR blocks on its IP allowlist. Allowlist edits take effect
within 5 minutes. A request from an address that is not listed returns `403 ip_not_allowlisted`.

## Authentication failure responses

A missing or expired credential returns `401 unauthorized`. A valid credential without the required
scope returns `403 forbidden`. Five failed attempts within 60 seconds lock the API key for 15 minutes.

## Environment selection

Send `X-Lumen-Environment: production` or `X-Lumen-Environment: sandbox`. Keys are not portable
between environments, and a sandbox key sent to production returns `401 unauthorized`.
