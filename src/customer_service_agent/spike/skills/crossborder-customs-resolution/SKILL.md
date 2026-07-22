---
name: crossborder-customs-resolution
description: Resolve a cross-border customs hold by investigating declarations and versioned policy, collecting documents over time, comparing costs with user constraints, waiting for compliance review, and replanning when rejected.
allowed-tools: query_crossborder_case get_customs_declaration retrieve_route_policy inspect_document_bundle quote_customs_options request_information submit_compliance_review request_compliance_decision submit_customs_documents request_return_to_sender
---

# Cross-border customs resolution

## Goal

Resolve a customs hold without inventing regulations. The user permits document supplementation only when the total charge is within the stated budget; otherwise the shipment should be returned after explicit approval.

## Mandatory workflow

1. Use `write_todos` before business Tools. Include investigation, documents, quote, compliance, final approval, write, and verification steps.
2. Delegate initial case, declaration, and policy investigation exactly once to the `customs-compliance-analyst` subagent. The main agent intentionally does not own those three read Tools.
3. Require the specialist to call `query_crossborder_case`, `get_customs_declaration`, and `retrieve_route_policy`. Reuse its report without repeating those reads. Every policy statement must reference returned policy IDs and policy version.
4. Inspect documents already on file. If required documents are missing, call `request_information` and pause. For this Demo, accepted IDs are:
   - `DOC-BAT-VALID` or `DOC-BAT-EXPIRED`;
   - `DOC-LIQ-VALID`;
   - invoice `DOC-INVOICE-001` is already on file.
5. When the user supplies document IDs, call `inspect_document_bundle` with all documents including the existing invoice.
6. Call `quote_customs_options` and compare the supplementation amount with the user's `500000 VND` limit.
7. If the bundle is complete and within budget, call `submit_compliance_review`, then `request_compliance_decision` and pause for a simulated human decision.
8. After compliance approval, propose `submit_customs_documents`. It is approval-gated by the runtime.
9. After compliance rejection, or when the cost is above budget, explain why supplementation is blocked and propose `request_return_to_sender`. It is separately approval-gated.
10. Return the actual submission/return receipt, policy IDs, document validation, quote version, and evidence IDs.

If the supplied battery report is expired and the user says there is no valid replacement, do not ask for the same document again. The original objective already authorizes the return alternative, so call `request_return_to_sender`; the runtime will still pause for the required final approval.

## Dynamic rules

- Never treat this Mock policy as legal advice or a real customs decision.
- Never submit incomplete or expired material.
- Never bypass human compliance review.
- Never ignore the user's budget.
- Never claim partial removal is possible when the route Tool says it is unavailable.
- A rejection must cause replanning; it must not continue the previously approved path.
- Do not finish the task with a prose question when a required `request_information` or approval-gated resolution Tool is available.
- Never invent a case, review, submission, return, policy, or evidence ID.

## User-facing result

Reply in Chinese for the Demo. Separate verified facts, human decision, executed action, cost, reference ID, and remaining uncertainty.
