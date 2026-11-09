# Idempotency

Write requests are safe to retry when they carry an idempotency key.

## Idempotency key header

Send `Idempotency-Key` on every `POST` that creates or amends a resource. The value must be a
UUID v4 of at most 64 characters, and it is scoped to a single environment.

## Idempotency key lifetime

Lumen stores an idempotency key for 24 hours. A replay inside that window returns the original
response body unchanged, plus the response header `Idempotent-Replay: true`.

## Safe retry rules for writes

Retry a write only after a connection timeout or a `5xx` response. Do not retry a `4xx` response
other than `408` or `429`. Use at most 3 retries and reuse the same key for every attempt.

## Idempotency key conflicts

Reusing a key with a different request body returns `409 idempotency_key_reuse` and never creates a
second resource. The error body includes `original_request_id` so the first attempt can be traced.

## Concurrent request behaviour

Two requests carrying the same key within the same 5-second window are serialised. The second waits
up to 30 seconds for the first to settle, then returns the stored result or `409`.
