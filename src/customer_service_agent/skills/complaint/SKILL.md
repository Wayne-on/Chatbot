---
name: complaint
description: Use for parcel complaints, loss, damage, claim pre-acceptance, or querying an existing complaint ticket.
---

# Complaint and claim

## Use cases

General complaints, damaged/lost parcels, claim pre-acceptance, and ticket-status inquiries.

## Do not use

Do not create a complaint for a plain tracking question or promise compensation.

## Required parameters

- Creation: `waybill_no`, `complaint_type`, `complaint_description`
- Query: `ticket_id`

## Optional parameters

- evidence references for future production integration

## Standard steps

Collect facts, summarize the proposed pre-acceptance ticket, wait for confirmation, revalidate the waybill, and call `create_complaint` once with idempotency. Use `query_complaint` for read-only ticket status.

## Allowed tools

- `query_tracking`
- `create_complaint`
- `query_complaint`
- `transfer_to_human`

## Forbidden tools

- Address-change tools unless the user switches scene.

## Confirmation

Creation requires explicit confirmation; querying does not.

## Exceptions

Tool failures never produce a fabricated ticket or compensation outcome.

## State updates

Save type/description only in the active scene, and save the returned ticket ID after success.

## Reply requirements

Call the result pre-acceptance/investigation and say final review is authoritative.

