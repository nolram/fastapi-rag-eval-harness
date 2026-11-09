# Service level agreement

The SLA applies to the production API and is measured over a calendar month.

## Uptime commitment

Lumen commits to 99.95% availability per calendar month, which is an error budget of 21.6 minutes in
a 30-day month. Availability excludes agreed maintenance windows and customer-caused failures.

## Service credit process

A credit claim must be filed within 30 days after the month end and must quote the incident IDs. Each
0.1% of availability below the target earns a 10% service credit, capped at 100% of the monthly commitment.

## Incident response times by severity

Sev 1 (production outage) is answered in 30 minutes, 24/7. Sev 2 (degraded) in 4 hours, Sev 3 in 1
business day, Sev 4 in 3 business days. Send claims to you@example.com with the `X-Request-Id` values.

## Scheduled maintenance windows

Maintenance runs on Tuesdays from 02:00 to 04:00 UTC, at most 4 times per month, announced 72 hours
in advance. A maintenance window that exceeds 45 minutes counts against the uptime commitment.

## Latency targets

`GET /v1/policies/{id}` targets p50 120 ms and p99 900 ms. List endpoints target p99 2000 ms at
`limit=200`. Latency is measured at the API edge and excludes client network time.
