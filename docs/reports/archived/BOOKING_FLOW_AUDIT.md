# Booking Flow Audit

## Current Execution Path

`Vapi -> POST /chat -> dispatch() -> ConversationManager.select_agent() -> InboundAgent or BookingAgent -> BookingOrchestrator -> BreakoutBookingProvider -> Kreeda`

Response wording then passes through `ResponseComposer`, which must preserve the approved business draft.

## Component Findings

### ConversationManager

- `src/orchestration/conversation_manager.py:38` gives an existing `booking_started` flow first priority unless the message is an explicit topic switch.
- The general branch still contains broad FAQ/recommendation rules at lines 100-115. In isolation, terms such as `available`, `price`, or a question mark can select FAQ. The booking-state guard is therefore essential.
- Risk: `_is_explicit_topic_switch()` must remain narrow. A booking FAQ is not a new intent.

### dispatch()

- `main.py:214-230` merges booking input, sets `booking_started=True`, and logs `BOOKING_AGENT_SELECTED`.
- `main.py:304-306` returns FAQ/recommendation interruptions to the active booking flow.
- This layer is the final routing defense; changes here can either preserve or erase booking priority.

### InboundAgent

- `src/agents/inbound_agent.py:313-364` detects interruptions while trying to preserve an active flow intent.
- `src/agents/inbound_agent.py:366-381` can run recommendation selection and store a room. This is appropriate before booking, but must not overwrite a chosen room during a locked booking unless the user explicitly changes it.
- Historical failure mechanism: an FAQ classification became `general_faq`, then room-description generation ran even though room/location were already stored.

### QualificationAgent

- `src/agents/qualification_agent.py:86-147` asks the next missing field and updates memory.
- Historical loops occurred when qualification state and booking state had different concepts of the next field. Booking completion must use the booking gate, not general qualification completeness.

### BookingAgent

- `src/agents/booking_agent.py:739-785` evaluates the booking gate, requires a provider `booking_id`, persists the ID/reference, and only then returns confirmation.
- `src/agents/booking_agent.py:809` owns the booking-gate field calculation.
- Booking questions and slot handling remain here when `booking_started` is true.
- Risk: any response path above the gate that uses `set`, `confirmed`, or `booked` can still create a fake spoken confirmation even when state is correct.

### ConversationMemory

- `src/memory/conversation_memory.py:244-345` performs extraction/merge.
- `src/memory/conversation_memory.py:370-386` exposes diagnostics and general flow missing fields.
- `booking_started`, selected slot, contact parts, and provider identifiers are explicit state.
- Historical failure mechanisms were stale-value rejection and intent changes clearing or ignoring booking data. Corrections must overwrite immediately while unrelated FAQ turns must not clear state.

### ResponseComposer

- `src/response_composer.py:62` detects booking lock and includes booking identifiers in safe state.
- `src/response_composer.py:197` distinguishes a real confirmed booking by ID/reference.
- It must remain a surface rewriter. It must never generate a room explanation, recommendation, availability, or confirmation that the approved draft did not contain.

## Where Failures Occur

| Failure | Proven mechanism / control point |
|---|---|
| Booking intent lost | Broad FAQ/recommendation detection before booking priority, or active intent overwritten by `general_faq` |
| FAQ overrides booking | `available`, `price`, question punctuation, or room wording classified as FAQ without active-booking guard |
| Recommendation overrides booking | Recommendation request/engine runs after room selection and treats an interruption as a new selection |
| Confirmation loops | Qualification next-field state diverges from booking gate; response recaps completed fields |
| Memory ignored | Routing or response generation consults intent/mode instead of current structured booking state |
| Fake confirmation | Surface language says `set` before provider result contains a booking identifier |

## Required Invariants

1. Explicit corrections merge before route selection.
2. `booking_started` survives FAQs and recommendation questions unless the user explicitly abandons or switches the booking.
3. Booking state, not intent label, determines missing fields.
4. Room/location/date present plus booking language selects BookingAgent.
5. Slot/price/availability/capacity/modification questions remain inside BookingAgent during an active booking.
6. Contact collection proceeds first name, last name, phone.
7. Provider failures preserve room, location, date, participants, availability, and selected slot.
8. Confirmation requires persisted `booking_id` or `booking_ref`.

## Audit Result

The current code contains defenses for the known routing regressions and has targeted tests. No production-code change is justified by this prompt/content audit. Remaining confidence requires live provider smoke testing and production transcript monitoring, not another prompt rewrite.
