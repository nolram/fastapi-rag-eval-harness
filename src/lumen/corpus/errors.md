# Error responses

Errors use standard HTTP status codes plus a stable machine-readable error code.

## Error response body format

Every error returns `{"error": {"code", "message", "request_id", "field_errors"}}`. Codes are
lower_snake_case and stable across versions, so branch on `code` and never on the `message` text.

## HTTP status code reference

`400` malformed request, `401` missing or expired credential, `403` authenticated but forbidden,
`404` unknown resource, `409` conflict or idempotency reuse, `410` sunset version, `422` semantic
validation failure, `429` rate limited, `500` Lumen fault, `503` temporarily unavailable.

## Request ID tracing

Every response includes the `X-Request-Id` header. Request IDs stay searchable in Lumen logs for 30
days. Quote the `request_id` from the error body when you open a support ticket.

## Validation error details

A `422` response lists up to 25 problems in `field_errors`, each with a JSON-pointer `field`, a
`code` and a `message`. A request with any field error applies no partial changes.

## Retryable server errors

`500` and `503` are retryable, and `503` always carries `Retry-After: 30`. Retrying a write with the
same `Idempotency-Key` cannot duplicate the resource, because the stored response is replayable for 24 hours.
