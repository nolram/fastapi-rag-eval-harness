# Data retention

Retention is measured from the lifecycle date of each record, not from the ingestion date.

## Retention schedule for policy records

Policy records are retained for 10 years after the policy end date. Claim files are retained for 7
years after claim closure. A quote that was never bound is retained for 2 years after it expires.

## Deletion request processing time

A deletion request is acknowledged within 3 business days and erasure completes within 30 days.
Records under legal hold are excluded, and the acknowledgement states how many records are held.

## Backup and snapshot retention

Daily snapshots are retained for 35 days. Point-in-time recovery reaches back 14 days, and a restore
can take up to 6 hours, during which read traffic may be stale by up to 15 minutes.

## Log retention

API request logs are retained for 30 days. Audit logs are retained for 7 years, are append-only, and
are not removed by a deletion request.

## Anonymization for analytics

Direct identifiers are dropped from the analytics store 90 days after ingestion. An analytics row is
suppressed whenever its cohort contains fewer than 20 policies.
