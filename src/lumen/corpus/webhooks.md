# Webhooks

Lumen pushes lifecycle events to HTTPS endpoints registered per environment.

## Webhook signature verification

Verify `X-Lumen-Signature: sha256=<hex>`, an HMAC-SHA256 of the raw request body keyed with the
endpoint secret. Reject any delivery whose `X-Lumen-Timestamp` header is older than 300 seconds.

## Delivery retry schedule

A failed delivery is retried 8 times over 24 hours, at 1s, 10s, 60s, 5m, 30m, 2h, 8h and 16h.
Deduplicate on `event_id`: the same event can be delivered twice, and an `event_id` stays reserved for 48 hours.

## Endpoint timeout and acknowledgement

The endpoint must answer with a `2xx` status within 5 seconds. A slower answer, a `3xx`, a `4xx` or a
`5xx` counts as a failed delivery and enters the retry schedule.

## Webhook event types

There are 14 event types in total. The core types are `policy.created`, `policy.cancelled`,
`claim.submitted`, `claim.decided` and `refund.processed`. Delivery is at-least-once and unordered.

## Webhook endpoint limits and secret rotation

Each workspace may register at most 10 endpoints, HTTPS only. Rotate an endpoint secret every 180
days; after rotation, the previous secret still verifies signatures for 24 hours.
