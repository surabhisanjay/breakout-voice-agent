# Merge Plan

## Personality

Keep the current approved-draft and grounding boundary. Merge Claude's stronger answer-first, anti-repetition, hesitation, and escalation wording. Remove its default-room rule and mandatory arrival instruction unless runtime facts explicitly supply them.

## Conversation Playbook

Keep current qualification, range-preservation, FAQ-interruption, and constraint rules. Merge Claude's operational flow layout, payment-dispute handling, modification discipline, and concise handoff language. Replace every concrete example with a grounded or parameterized example.

## Escalation

Merge Claude's P0/P1/P2 taxonomy with the current sentiment/escalation agent. The classifier can recommend a priority; production routing must still determine the actual channel and availability of a human.

## Handoff

Merge Claude's structured schema with current memory fields. Add `booking_id`, `booking_ref`, `selected_slot`, `actions_attempted`, and `provider_error`; never infer missing values.

## Transcript Examples

Keep the current `customer_message/state/approved_draft/composed_response` schema. Merge Claude's natural repair, interruption, and escalation turns only after deleting business claims that do not come from the approved draft or structured state.

## Scenario Handling

Use Claude's trigger/behavior/escalation format, but make responses conditional on verified state. Package, price, capacity, availability, payment, accessibility, and policy facts must come from a designated source or live provider.

## Deployment Rule

These generated files are review artifacts. Promote them only through targeted prompt tests and transcript replay; do not copy them into `prompts/` as a bulk replacement.
