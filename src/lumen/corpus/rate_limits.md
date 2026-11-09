# Rate limits

Every API key is rate limited per environment.

## Default rate limits

Default rate limit is 120 requests per minute per API key. Burst allowance is 200 requests for up to 10 seconds.
The sandbox environment is limited to 20 requests per minute and has no burst allowance.

## Rate limit response headers

Every response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` holding
UTC epoch seconds. A throttled response also carries `Retry-After` in whole seconds.

## Handling 429 Too Many Requests

`429 too_many_requests` means the key exceeded its per-minute budget. Retry with exponential backoff
starting at 2 seconds, doubling per attempt, at most 5 attempts, plus jitter of up to 500 milliseconds.

## Bulk import throughput limits

`POST /v1/policies/bulk` accepts at most 500 objects per request and is limited to 30 requests per
minute. No more than 5 bulk import jobs may run concurrently in one workspace.

## Rate limit counting rules

Usage is counted per API key per environment, not per user or per IP address. Requests answered with
a `4xx` status still count against the limit. `OPTIONS` requests and `GET /v1/health` do not count.
