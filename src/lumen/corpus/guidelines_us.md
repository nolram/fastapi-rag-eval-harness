# US market guidelines

These rules apply when the policyholder is located in the United States.

## Cancellation notice period

The policyholder must give at least 30 days written notice to cancel. Lumen applies the cancellation
on the 31st day after receipt and sends written confirmation within 1 business day.

## Claim filing window

A claim must be filed within 180 days of the date of loss. A claim filed on day 181 or later is
denied with `claim_late`, unless the policyholder shows the loss was not reasonably discoverable.

## Right of withdrawal

The policyholder may withdraw within 14 calendar days after the policy documents are delivered, with
no penalty and no reason required. Lumen sends the withdrawal receipt within 10 business days.

## Data residency

Primary and backup policy data for US policyholders is stored in the `us-east` region only. No US
policy data is replicated outside the United States, and support access comes from US-based seats.

## Complaint response SLA

A complaint is acknowledged within 2 business days and resolved within 15 business days. Once the
policyholder escalates, Lumen has 30 days to send a final written position to the state regulator.

## Waiting period for pre-existing conditions

Covered conditions that existed before inception carry a 180-day waiting period, and no waiting period may exceed 180 days.

## State filing requirement

Every policy form needs a state filing number before it can be issued, and an unfiled form returns `form_not_filed` at bind time.
