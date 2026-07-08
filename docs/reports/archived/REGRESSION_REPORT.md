# Breakout Agent MVP Regression Report

## Scope and baseline

The audit compared the current working tree with `1bad71d` and `d2e58b8`, replayed saved sessions
`019eddf8-19bf-700e-8c59-eab7964023b1`, `019eddc8-6336-7000-8df8-16e9782b76f5`, and
`019eddde-fa40-7114-a373-b58fd93cfe58`, and ran the complete automated suite.

The latest booking work was uncommitted, so individual regressions cannot honestly be attributed to a
new commit hash. They are identified below by code area. Some failures predate booking hardening.

## Findings and root causes

| Area | Root cause | Before/failing behavior | Stabilized behavior |
|---|---|---|---|
| Name extraction | `this is` and `I am` accepted arbitrary trailing phrases as names in `ConversationMemory` and `QualificationAgent`. This existed in `1bad71d`. | `This is the first time` persisted `The First Time`. | Candidates pass a shared plausibility check. First-time/booking/date phrases are rejected and explicit names are accepted. |
| Date extraction | The saved API process did not normalize spoken compound ordinals before extraction. There was no extraction/persistence telemetry. | `Twenty fourth of June` repeatedly left `preferred_date` empty. | Numeric, spoken ordinal, and relative forms are extracted. Kreeda normalization remains ISO at the orchestrator boundary. |
| FAQ routing | Unknown inventory wording such as `What are all available?` had no grounded answer and fell through to qualification. | Agent asked age instead of answering. | Inventory questions return capacity-filtered room options first without appending qualification. |
| Recommendation breadth | Contextual recommendation code reduced follow-up requests to the first beginner room. | `Tell me more options` returned only Murder Mystery. | Follow-ups return multiple inventory/capacity-valid choices using group size and location. |
| Booking handoff | Escape-room `handoff_ready` required contact but not a concrete room. Router qualification could hand off with incomplete booking prerequisites. | Contact was requested before room/availability/slot; incomplete turns could enter BookingAgent. | Handoff requires participants, age group, location, concrete room, and date. BookingAgent collects contact after verified slot selection. |
| Ambiguous recommendation acceptance | `Book it` treated a multi-room recommendation as selected and could emit a generic completion message. | Fake `captured all information` response with no concrete room. | Agent asks which specific recommended room the customer wants. |
| Booking/FAQ ownership | Question and room-name classification ran before actionable booking state. | `Undercover at 8:20 PM` became a room FAQ and lost slot progress. | Date/slot/contact inputs keep BookingAgent ownership; FAQs preserve slot state. |
| Memory changes | Low-confidence correction state and numeric ranges could corrupt age/date state. | Time/group ranges became ages; accepted date changes looped. | Group ranges are separate, age ranges require age context, and explicit/restated date changes persist. |
| Capacity | Recommendation and raw slot presence were considered before game min/max and slot capacity. | Bomb Defusal could be suggested to 15 people; nine raw slots could become a confusing empty result. | Capacity is applied before recommendation and booking progression; raw and bookable slots remain separately observable. |
| Confirmation | Generated wording and provider responses could imply completion without a provider identifier. | Booking was described as set before creation. | Confirmation requires verified availability, selected slot, name, phone, confirmed provider response, and persisted ID/reference. |

## Affected files

- `src/memory/conversation_memory.py`
- `src/agents/qualification_agent.py`
- `src/agents/inbound_agent.py`
- `src/agents/booking_agent.py`
- `src/orchestration/conversation_manager.py`
- `src/orchestration/booking_orchestrator.py`
- `src/services/recommendation_engine.py`
- `src/response_composer.py`
- `main.py`

## Verification

- Named-session replay: name rejection, FAQ-first behavior, expanded options, and `24 June` persistence pass.
- JP Nagar regressions: relative-date normalization, explicit date replacement, age-range protection, and capacity filtering pass.
- Birthday/package regression: packages do not call Kreeda game availability and do not claim confirmation.
- Booking provider contract: availability -> cart -> booking -> `booking_id` -> `booking_reference` passes with a mocked live provider.
- Full suite: **321 passed**.

## Residual production risk

A read-only live Kreeda check succeeded for four players in Whitefield on 24 June: Murder Mystery returned nine
verified, capacity-supported slots and live min/max capacity `2-7`. Provider cart/booking request shapes and success/
failure behavior are verified with contract mocks, but a new real customer booking was intentionally not created during
stabilization. External booking creation is therefore **PARTIALLY WORKING** until an authorized live smoke test returns
an actual booking reference.
