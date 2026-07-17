---
name: delivery-followup
description: Use when tracking is delayed, has not updated, or the user asks to urge delivery or open a delivery investigation.
---

# Delivery follow-up

## Use cases

Delayed tracking and formal urging/investigation requests.

## Do not use

Do not use when the user only wants status, or when the parcel is already delivered and missing.

## Required parameters

- `waybill_no`
- reason

## Optional parameters

- latest tracking context

## Standard steps

1. Collect the waybill.
2. Call `query_tracking` and require `can_urge=true`.
3. Show the exact planned follow-up.
4. Wait for explicit confirmation.
5. Re-query tracking and revalidate eligibility.
6. Call `urge_delivery` once with an idempotency key.

## Allowed tools

- `query_tracking`
- `urge_delivery`
- `transfer_to_human`

## Forbidden tools

- Complaint and address-change tools.

## Confirmation

Explicit confirmation is mandatory before `urge_delivery`.

## Exceptions

Do not retry a timed-out write blindly. Return only a real ticket ID.

## State updates

Use `waiting_confirmation` and `pending_confirmation`; complete only on Tool success.

## Reply requirements

Do not guarantee delivery time or outlet action.

