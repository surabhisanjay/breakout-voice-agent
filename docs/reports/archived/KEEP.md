# Keep From The Current MVP

## Decision

The current production assets are stronger wherever correctness depends on runtime state. Keep these elements as the baseline; Claude's files should not replace them wholesale.

1. **Approved-draft boundary.** `prompts/breakout_personality_prompt.txt` clearly limits the composer to wording an already-approved response. It does not let the language model decide policy, pricing, availability, routing, or booking outcomes.
2. **Grounding guardrails.** The current prompt explicitly forbids adding prices, discounts, capacities, policies, availability, game details, promises, or exceptions.
3. **Structured-memory reuse.** The current prompt and playbook require checking structured state and prohibit re-asking known fields.
4. **Question-first handling.** Existing guidance answers direct questions before qualification and resumes only the one pending field.
5. **Range preservation.** Existing guidance keeps `10 to 12` as a range instead of manufacturing an exact count.
6. **Booking truth.** Current code confirms only after a provider result contains a real `booking_id`/`booking_ref`; this is safer than Claude examples that say a slot is held or confirmation text will arrive.
7. **Runtime availability.** Current booking behavior uses provider results rather than static examples for slots, capacity, and room inventory.
8. **Short response contract.** The current 1-3 sentence, one-question, under-40-word guidance is already concise and production-oriented.
9. **FAQ interruption behavior.** Existing playbook answers the interruption, preserves booking state, and resumes only the next missing detail.
10. **No speculative popularity claims.** Existing assets forbid `best`, `popular`, `easy`, or suitability claims without grounded facts.
11. **No production changes from transcript folklore.** `docs/transcript_analysis.md` explicitly treats call content as conversation evidence, not business truth.
12. **Deterministic booking gates.** Required room, location, date, slot, first name, last name, phone, availability, and provider confirmation live in code rather than the personality prompt.

## Keep, But Continue Testing

- Booking priority and `booking_started` lock in `ConversationManager` and `main.dispatch()`.
- Latest-value memory overwrite for participant/date corrections.
- Booking failure recovery that preserves room, date, location, and slot.
- ResponseComposer's booking-state restrictions.

These are architecturally correct, but their production reliability still depends on provider and transcript replay evidence.
