# Breakout Conversation Intelligence Report

## Scope and Sources

This analysis covers the seven transcript files supplied with this upgrade: six human-agent calls and one AI-agent call. It also uses the supplied FAQ document and two Breakout workbooks to distinguish conversational technique from business facts.

Transcript claims about live prices, offers, and availability are treated as historical call context, not current policy. The FAQ and workbooks remain the factual reference for room, location, age, capacity, parking, timing, event, food, cancellation, and rescheduling information.

## Executive Findings

The strongest Breakout calls do not behave like forms. The agent first discovers what the customer is trying to achieve, gives enough explanation to make the next question feel useful, and adapts when the customer's real priority changes.

The best recurring pattern is:

1. Acknowledge the situation.
2. Answer the immediate question.
3. Explain one reason or useful distinction.
4. Offer one grounded recommendation or alternative.
5. Ask one next question.

The weakest calls reverse that order: they collect fields before answering, repeat qualification after an FAQ, list every room, or continue booking after the customer expresses hesitation.

## Most Common Customer Intents

1. Learn what an escape room is and what happens inside.
2. Find a first-time-friendly room.
3. Compare mystery, suspense, challenge, and horror expectations.
4. Check location, date, time, and room availability.
5. Plan a birthday celebration or compare party formats.
6. Plan a corporate or large-group event.
7. Understand food, event-space, and activity options.
8. Ask about arrival time, lateness, parking, and venue logistics.
9. Ask about advance payment, cancellation, rescheduling, refund, or discounts.
10. Pause, confirm, modify, or close a booking decision.

## Conversation Patterns

### 1. Opening

**Observed pattern:** Human agents use a short service opening, then let the caller explain the purpose. They do not request contact details in the greeting.

**Effective shape:** “Hi, this is Breakout. How can I help?”

**Failure:** A branded but mechanical AI introduction followed immediately by location or participant questions.

### 2. Discovery

**Observed pattern:** Good discovery identifies the occasion and the customer's desired experience. In the late-night birthday call, timing was more important than room theme; in the birthday package call, budget and food mattered more than decoration.

**System implication:** Qualification order should remain deterministic, but customer-facing wording should follow the caller's active priority.

### 3. Recommendation

**Observed pattern:** Human agents usually offer one starting point, explain one distinction, and introduce a harder or differently styled alternative only if useful.

**Effective response:** “For a first visit, I'd probably start with Murder Mystery. Hostage is the faster, more urgent alternative.”

**Failure:** A six-room numbered catalogue followed by another qualification question.

### 4. First-Time Players

**Observed pattern:** Agents explain the experience, reduce anxiety, and distinguish “first time anywhere” from “first time at Breakout.” Customers who reject an easy experience are moved toward a stronger option rather than forced back into a beginner script.

### 5. Group Size

**Observed pattern:** Group size is acknowledged in context. Larger groups may be split across rooms or activities; a range remains a range until confirmed.

**Failure:** Converting “10 to 12” into ten, then asking the count again later.

### 6. FAQ

**Observed pattern:** Effective agents answer the practical question first. Escape-room education, duration, hints, late arrival, payment, food, and room story questions are handled before qualification resumes.

**Failure:** Answering one sentence and immediately repeating “What date would you like?” regardless of the customer's concern.

### 7. Corporate Events

**Observed pattern:** The value proposition is participation, communication, collaboration, and a format that works for the whole group. Group size and location matter after that value is established.

### 8. Objections

**Observed pattern:** Agents identify the real objection: difficulty, budget, timing, payment readiness, or package composition. They adapt the option instead of defending the original recommendation.

**Effective move:** “The timing sounds like the main concern. Should I prioritize nearby slots over specific rooms?”

### 9. Booking Transition

**Observed pattern:** Booking begins after a clear option and customer intent emerge. Availability, payment, and reservation limits are explained with an operational reason.

**Failure:** Saying “I'll book that” before date, availability, contact details, or confirmation are established.

### 10. Escalation

Escalation is appropriate when:

- the customer explicitly asks for a human or language support;
- frustration or misunderstanding repeats;
- availability or booking integrations fail;
- the customer disputes a policy, fee, cancellation, or refund outcome;
- the requested custom event needs a quotation or decision outside the inbound agent's authority.

### 11. Closing

**Observed pattern:** Human calls close around a concrete next step: call back after checking, receive details, complete payment, arrive early, or enjoy the game.

**Failure:** Restarting qualification after the customer pauses or using generic satisfaction-script language.

## Best Transcript-Supported Agent Behaviors

- “Don't worry” followed by a concrete reassurance.
- “Beautiful” or “Sounds fun” when the customer's excitement genuinely supports it.
- “Understood” before adapting to budget or timing.
- Explaining why arrival time matters: briefing and formalities protect game time.
- Explaining why advance payment matters: live slots cannot be held indefinitely.
- Recommending by experience and desired intensity instead of listing inventory.
- Offering the closest workable alternative when a requested late-night format is unavailable.
- Letting a hesitant customer check with the group without pressure.

## Common Failures

1. Catalog dumping.
2. Multiple questions in one response.
3. Repeating known participant, age, location, or date fields.
4. Treating uncertain ranges as exact values.
5. Confusing first-time-at-Breakout with first-time-ever.
6. Long package monologues before understanding priorities.
7. Unsupported claims about discounts, refunds, availability, or popularity.
8. Booking language before availability and consent.
9. Ignoring customer hesitation.
10. Repeating qualification immediately after an FAQ.
11. Failing to escalate repeated misunderstanding.
12. Losing context when a customer asks a temporary side question.

## Natural Conversation Structures

### Education flow

Customer question → plain-language explanation → reassurance → one discovery question.

### Recommendation flow

Known preference → soft primary recommendation → one reason → optional alternative → one decision question.

### Constraint flow

Customer goal → acknowledge → state limitation once → closest permitted alternative → one choice question.

### FAQ interruption flow

Answer FAQ fully → preserve existing state → resume only the pending question.

### Hesitation flow

Acknowledge uncertainty → stop booking momentum → offer a small comparison or pause → no pressure.

### Escalation flow

Detect trigger → preserve context → produce concise handoff → human takeover without making unsupported promises.

## Recommended System Improvements

Implemented in this pass:

- Transcript-driven prompt examples for late-night celebrations, no-horror preferences, timing objections, hesitation, and package discovery.
- Playbook rules for constraints, closest alternatives, hesitation, and pressure-free pauses.
- Continuous sentiment history across conversation stages.
- Escalation detection for explicit human requests, repeated frustration, repeated misunderstanding, booking/API failures, and policy disputes.
- Human-readable handoff summaries with booking state and outstanding questions.
- Shared dispatch integration so terminal, voice, FastAPI, and Vapi-backed conversations receive the same metadata.

Recommended later, without changing this release:

- Store explicit Vapi call IDs beside backend session IDs for end-to-end observability.
- Add a human-handoff transport after the destination system is selected.
- Calibrate sentiment rules on a larger labelled set of real Breakout calls.
- Add venue-approved multilingual handoff phrases.

## Example Conversations

### First-time group

**Customer:** “We've never done an escape room before.”

**Agent:** “Awesome, first visits are a lot of fun. I'd start with something easy to get into. How many people are joining?”

### Customer wants education

**Customer:** “Tell me about escape rooms.”

**Agent:** “Sure. Your team enters a themed mission, finds clues, and solves puzzles together before time runs out. Is this your first one?”

### Timing constraint

**Customer:** “We want a midnight birthday celebration.”

**Agent:** “Sounds lovely. We don't have that late-night format, but I can suggest the closest available option. Which location works for you?”

### Hesitation

**Customer:** “I'm deciding between two dates, so don't reserve it yet.”

**Agent:** “No worries, I won't reserve anything yet. Check with your group and come back once the date is clear.”

### Late arrival

**Customer:** “We're running thirty minutes late.”

**Agent:** “No worries. The sessions run back to back, so the delay may reduce your play time. How far away are you now?”

## Before and After

### Education

**Before:** “How many players? What ages? Which location?”

**After:** “Sure. An escape room is a themed team mission where you find clues and solve puzzles against the clock. Is this your first one?”

### Recommendation

**Before:** “Available rooms are Murder Mystery, Hostage, Classified, Bomb Defusal, Prison Break, and Undercover.”

**After:** “I'd probably start with Murder Mystery for a first visit. Hostage is the more urgent alternative. Which style sounds better?”

### Birthday discovery

**Before:** A complete package itinerary before identifying budget or priorities.

**After:** “Absolutely. You can keep it escape-room focused or build a fuller celebration around it. Roughly how many guests are coming?”

### Booking pause

**Before:** Continue asking for date, name, or payment.

**After:** “No worries, I won't reserve anything yet. Check with your group and call back when you're ready.”

### Failed assistance

**Before:** Repeat the same unavailable or failed lookup response.

**After:** Preserve the context, flag escalation internally, and give the human a summary readable in under thirty seconds.
