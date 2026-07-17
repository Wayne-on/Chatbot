---
name: tracking
description: Use when a user asks where a parcel is, requests tracking events, or supplies a waybill to continue a tracking inquiry.
---

# Tracking

## Use cases

Tracking status, current node, latest event, delay/exception explanation, and a follow-up after the bot requested a waybill.

## Do not use

Do not use for delivered-not-received, package dimensions, address changes, or complaint creation when those intents are explicit.

## Required parameters

- `waybill_no`

## Optional parameters

- `include_details`
- event limit

## Standard steps

1. Reuse a validated waybill in the current scene unless the user supplies a new one.
2. Ask for the waybill if missing.
3. Call `query_tracking`.
4. Explain only returned status, node, events, ETA, and exception.
5. Complete the scene. If the result recommends follow-up, offer the delivery-followup flow.

## Allowed tools

- `query_tracking`

## Forbidden tools

- `create_complaint`, `urge_delivery`, and `change_address` during a plain tracking request.

## Confirmation

No confirmation is required because this is read-only.

## Exceptions

Invalid waybills are recollected. A failed query produces the fixed unavailable response; never infer tracking.

## State updates

Save `waybill_no`, `last_tool_result`, and set `completed` only after Tool success.

## Reply requirements

Use the conversation language, be concise, and avoid guarantees. See `references/dsl-rules.md` for source behavior.

