# Root Cause Report

Scope: conversation state management only. No prompt, personality, transcript-example, or recommendation-content changes were made in this pass.

## P0: Missing-field progression bug

Root cause: `QualificationAgent.update_and_qualify()` and `BookingAgent.handle_message()` validated the currently requested field before treating other valid fields as useful state. When the agent was waiting for `location`, turns like `Tomorrow` or `7 PM` were handled as invalid location answers, so the data was not acknowledged and progression appeared stuck.

Affected files and functions:
- `src/agents/qualification_agent.py`: `update_and_qualify()`, `_ack_for_captured_field()`
- `src/agents/booking_agent.py`: `handle_message()`, `_handle_availability_check()`, `_missing_field_response()`
- `src/memory/conversation_memory.py`: `update_from_message()`, `merge_message()`, `_extract_time()`

## P0: Loop escalation false positives

Root cause: `EscalationAgent.evaluate()` treated three repeated agent responses as a loop without checking whether the customer was actively correcting booking state. Normal update turns such as date, room, slot, participant, phone, name, and location corrections could be misread as loop evidence.

Affected file and function:
- `src/agents/escalation_agent.py`: `evaluate()`, `_is_active_booking_update()`

## P0: FAQ interruption coverage

Root cause: active qualification/booking FAQ handling did not cover all required topics and policy-worded cancellation questions could overwrite `escape_room_inquiry` with `cancellation_request` instead of behaving as an FAQ interruption.

Affected files and functions:
- `src/agents/inbound_agent.py`: `handle_message()`, `_fallback_response()`, `_is_qualification_interruption()`, `_answer_faq()`
- `src/agents/booking_agent.py`: `_is_booking_faq()`, `_booking_faq_answer()`, `_append_booking_resume()`

## P1: Spoken phone number parsing

Root cause: phone extraction only accepted numeric strings. Spoken forms such as `double nine`, `triple nine`, `oh`, and `zero` were not converted before validation.

Affected file and function:
- `src/memory/conversation_memory.py`: `_extract_phone()`, `_extract_spoken_phone()`

## P1: Name correction support

Root cause: name extraction supported initial name capture but not correction phrasing like `Actually use Siddharth Khandelwal` or `Actually Sidd Khandelwal`.

Affected files and functions:
- `src/memory/conversation_memory.py`: `_extract_name()`
- `src/agents/booking_agent.py`: `handle_message()`, `_store_name_parts()`

## P1: State persistence during qualification

Root cause: valid side-channel fields captured while another field was missing were not returned by `ConversationMemory.update_from_message()`, so qualification could not tell that useful state had changed.

Affected files and functions:
- `src/memory/conversation_memory.py`: `update_from_message()`
- `src/agents/qualification_agent.py`: `update_and_qualify()`

