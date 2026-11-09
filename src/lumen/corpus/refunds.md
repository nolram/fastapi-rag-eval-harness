# Refunds

Refund rules cover cancelled policies and duplicate processing errors.

## Refund eligibility window

A policy cancelled within 14 days of its effective date, with no claim filed, is refunded in full.
A market rule may shorten that 14-day window but never extend it; after day 14 a refund needs an accepted notice.

## Refund processing time

A refund is initiated within 5 business days of an accepted cancellation and settles within 10
business days. Funds are returned to the original payment method only; no alternative channel is offered.

## Refund calculation rules

Refunds are pro-rata on unused policy days. No administrative deduction applies inside the first 14
days; from day 15 a 3% administrative deduction applies to the refundable amount.

## Refund request limits

A policy may receive at most 2 refunds in any 12-month period. A refund request raised more than 90
days after the cancellation date is rejected with `refund_window_expired`.

## Refund failure handling

If a payout fails, Lumen emits `refund.processed` with status `failed` within 60 seconds and retries
the payout once every 24 hours, up to 3 attempts, then escalates the case to support.
