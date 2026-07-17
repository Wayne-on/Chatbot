---
name: delivered-not-received
description: Use when tracking shows delivered but the recipient reports that the parcel was not received.
---

# Delivered but not received

## Use cases

The user explicitly reports a delivered/signed parcel is missing.

## Do not use

Do not use when live tracking is not delivered; explain the real status instead.

## Required parameters

- `waybill_no`
- `phone_last4`

## Optional parameters

- complaint description

## Standard steps

1. Collect the waybill.
2. Call `query_tracking` and require `status=delivered`.
3. Ask for phone last four.
4. Call `verify_receiver`.
5. Show the investigation action and wait for confirmation.
6. On confirmation, re-check tracking and identity.
7. Call `create_complaint` with an idempotency key.

## Allowed tools

- `query_tracking`
- `verify_receiver`
- `create_complaint`
- `transfer_to_human`

## Forbidden tools

- Address-change or delivery-followup write tools.

## Confirmation

Explicit confirmation is mandatory before complaint creation.

## Exceptions

Never bypass identity verification, infer delivery, or invent a ticket ID.

## State updates

Persist only phone last four, not a full phone or credential. Clear pending action on cancel/switch.

## Reply requirements

Mention POD/signing facts only if returned, and do not promise compensation.

