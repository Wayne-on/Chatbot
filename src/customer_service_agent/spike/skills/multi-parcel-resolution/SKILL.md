---
name: multi-parcel-resolution
description: Investigate and safely resolve a single order split across multiple shipments with different live states, shared user constraints, and independent write actions.
allowed-tools: resolve_order_waybills query_tracking query_existing_cases query_pod_evidence check_address_change check_outlet_hold verify_receiver request_information submit_address_change submit_delivery_followup submit_missing_delivery_case submit_outlet_hold
---

# Multi-parcel order resolution

## Goal

Resolve an order-level objective when the order has an unknown number of shipments and each shipment requires a different treatment.

## Mandatory workflow

1. Use `write_todos` before business Tools. Keep separate steps for discovery, investigation, missing information, approval, writes, and final verification.
2. Delegate order discovery and evidence investigation exactly once to the `multi-parcel-operations-analyst` subagent. The main agent intentionally does not own those bulk-read Tools.
3. The specialist must call `resolve_order_waybills`, operate only on returned allowlisted waybills, and inspect every shipment rather than a sample. Reuse its report; do not repeat the same reads in the main context.
4. For each shipment, collect live tracking and check existing cases. Then use only the relevant specialist read Tool:
   - in transit: `check_address_change`;
   - delayed: determine follow-up eligibility and duplicate cases;
   - delivered but reported missing: `query_pod_evidence`;
   - out for delivery: `check_outlet_hold`.
5. Consolidate all missing user information into one `request_information` call. For the fixed Demo order, request the new address and phone last four digits together.
6. After the user responds, call `verify_receiver` only for the delivered-but-missing shipment.
7. Propose independent write actions. Never silently apply one shipment's action to another shipment.
8. Write Tools are approval-gated by the runtime. Do not claim that an action succeeded before receiving its Tool receipt.
9. After approved writes, produce a per-waybill table containing live status, chosen action, result/reference ID, and evidence IDs.

## Dynamic rules

- A failed or blocked branch must not erase successful work on other shipments.
- Recheck eligibility inside each write Tool; a plan may become invalid while the task is paused.
- Do not create a duplicate follow-up when an equivalent active case already exists.
- Do not create a delivered-not-received case unless receiver verification succeeded.
- Do not invent a ticket, request, evidence, SLA, or delivery promise.

## User-facing result

Reply in Chinese for the Demo. Explain what the Agent investigated, what was approved and executed, what is still pending, and which evidence supports every conclusion.
