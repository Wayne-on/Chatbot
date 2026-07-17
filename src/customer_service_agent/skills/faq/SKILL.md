---
name: faq
description: Use for VN logistics policies such as prohibited items, restricted items, delivery lead time, claims rules, and general service guidance.
---

# FAQ

## Use cases

Policy and rule questions that do not require shipment mutation.

## Do not use

Do not answer shipment-specific status from policy content.

## Required parameters

- user query
- language

## Optional parameters

- country/channel, fixed to VN/app in this demo

## Standard steps

Call `retrieve_faq`, answer only from the returned policy, and state that local/system verification prevails when the knowledge is incomplete.
The source-policy summary is in `references/dsl-policies.md`; treat it as Demo context, not production authority.

## Allowed tools

- `retrieve_faq`

## Forbidden tools

- All write tools.

## Confirmation

None.

## Exceptions

If retrieval fails or is insufficient, do not invent policy; offer human verification.

## State updates

Save matched policy and complete the scene.

## Reply requirements

Use the selected conversation language and remain concise.
