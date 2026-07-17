---
name: fallback
description: Use when a logistics request cannot be classified, is out of scope, repeatedly fails, or the user asks for a human agent.
---

# Fallback and human transfer

## Use cases

Unclear requests, cancellation, unsupported topics, repeated failure, and explicit human requests.

## Do not use

Do not override a clear active scene or silently discard provided slots.

## Required parameters

- reason for human transfer when transferring

## Optional parameters

- unresolved intent context

## Standard steps

Clarify once, preserve a valid active scene, respect cancellation/switch, and transfer on explicit request or repeated failure threshold.

## Allowed tools

- `transfer_to_human`
- `retrieve_faq` for a genuine policy fallback

## Forbidden tools

- All business mutations.

## Confirmation

No confirmation is needed for a human transfer, but do not create other business records.

## Exceptions

If transfer fails, provide a fixed safe response without a fake queue number.

## State updates

Set `cancelled` or `transfer`; clear pending write confirmation on cancel/switch.

## Reply requirements

Ask one actionable question at a time and use the current language.

