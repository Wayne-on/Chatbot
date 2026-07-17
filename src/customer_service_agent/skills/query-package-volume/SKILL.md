---
name: query-package-volume
description: Use when a user asks for package length, width, height, measured volume, or volumetric weight for a waybill.
---

# Query package volume

## Use cases

Read-only package dimension and volume inquiries.

## Do not use

Do not use to quote final charges or infer dimensions without a Tool result. This scene was requested for migration but is not present in the source DSL.

## Required parameters

- `waybill_no`

## Optional parameters

- `include_details`

## Standard steps

Ask for a missing waybill, call `query_package_volume`, state measured fields and identify the formula as demo-only.

## Allowed tools

- `query_package_volume`

## Forbidden tools

- All write tools.

## Confirmation

None; the operation is read-only.

## Exceptions

If measurements are absent or the query fails, explain that the value is unavailable; do not calculate from guesses.

## State updates

Save the waybill and Tool result, then mark the scene completed.

## Reply requirements

Never present demo volumetric weight as a binding charge.

