---
name: change-address
description: Use when a user requests a delivery-address change or delivery rescheduling for an existing waybill.
---

# Change address

## Use cases

Address-change requests before final delivery.

## Do not use

Do not use after delivery, when out for delivery, or when eligibility says no.

## Required parameters

- `waybill_no`
- `new_address`

## Optional parameters

- recipient name and phone when required by the real backend

## Standard steps

Collect the waybill, call `check_address_change`, collect a complete address, show the target, confirm, re-check eligibility, then call `change_address` with idempotency.

## Allowed tools

- `check_address_change`
- `change_address`
- `transfer_to_human`

## Forbidden tools

- Complaint and delivery-followup write tools.

## Confirmation

Explicit confirmation is mandatory.

## Exceptions

Do not promise success. Do not retry a timed-out write without idempotency reconciliation.

## State updates

Store the address only for the active scene; clear it on completion/cancel/switch according to retention policy.

## Reply requirements

Show the planned address for confirmation without placing it in logs.

