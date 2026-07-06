# Breakout Transcript Analysis

## Scope

This report reviews six supplied call transcripts: five human-agent calls and one AI-agent call. The findings are conversation-design guidance only. Prices, offers, availability, policies, and room facts mentioned in calls are not treated as current business truth.

## Top Conversation Patterns

1. **Teach before qualifying.** Strong agents explain the experience in everyday language before asking the caller to choose a branch, room, or package.
2. **Recommend from the customer's story.** First-time status, desired intensity, age, occasion, distance, and timing are used to narrow the choice.
3. **Acknowledge emotion briefly.** Effective turns use short responses such as “Understood,” “Don't worry,” and “Beautiful” before giving practical guidance.
4. **Explain the reason behind a rule.** Early arrival is tied to briefing and formalities; payment requirements are tied to holding a live slot.
5. **Adapt when the customer objects.** When “beginner” sounds too easy, a good agent reframes the option or moves to a stronger alternative.
6. **Preserve the caller's priority.** For two groups, nearby times can matter more than getting two specific rooms.
7. **Use light humor sparingly.** The “locked forever” joke works only because it is immediately followed by clear reassurance.
8. **Close with a concrete next step.** Strong calls end with a callback, message, payment step, arrival instruction, or warm goodbye.

## Top 10 Customer Intents

1. Understand how escape rooms work.
2. Get a room recommendation for first-time players.
3. Compare room difficulty, theme, or intensity.
4. Check locations, dates, times, and availability.
5. Plan a birthday party for children or teenagers.
6. Plan a corporate or group event.
7. Understand food and package options.
8. Ask about payment, discounts, cancellation, or refunds.
9. Get help when running late.
10. Confirm, pause, change, or close a booking.

## Category Findings

| Category | Common customer questions | Best agent behavior | Poor response / repeated failure |
|---|---|---|---|
| General Inquiry | “Is this Breakout?”, “What can we do?”, “Can you help plan Sunday?” | Welcome briefly and identify the occasion. | Starting qualification without learning why the person called. |
| Escape Room Education | “What is it?”, “What happens inside?”, “What if we fail?” | Explain the themed mission, clues, teamwork, and time limit in plain language. | Asking age, count, and location before answering. |
| First-Time Players | “We've never played”, “Is it difficult?” | Reassure, recommend softly, and ask one preference. | Treating “first time at Breakout” as “first escape room ever.” |
| Recommendations | “Which is best?”, “Which one would you choose?”, “We want a thrill” | Offer one choice, one reason, and one alternative. | Dumping several room descriptions or repeating the same recommendation. |
| Qualification | “We have ten people”, “All adults”, “Whitefield” | Acknowledge all supplied details and ask only the next missing one. | Re-asking group size or converting an uncertain range into an exact count. |
| Booking | “Can I book?”, “Can I pay there?”, “Hold this for me” | Explain the next deterministic step and preserve consent. | Claiming a booking is made before availability or confirmation. |
| Corporate Events | “What works for twenty employees?”, “Can everyone join?” | Connect the format to participation, communication, and group size. | Moving straight into a form without explaining event value. |
| Birthday Parties | “What package suits teenagers?”, “Can we reduce extras?” | Understand the parent's priorities and explain the package in stages. | A long itinerary before confirming budget, age, location, or desired format. |
| Late Arrival | “We're thirty minutes late”, “Can you brief us by phone?” | Acknowledge urgency, explain the timing impact, and give the next practical action. | A blunt rule with no recovery guidance. |
| Cancellation / Reschedule | “How do I cancel?”, “Can I move the booking?”, “What about refunds?” | Answer policy questions without changing the booking; verify before taking action. | Inventing refund eligibility or treating a policy question as a cancellation request. |
| Parking / Logistics | “Which branch is nearer?”, “Can we take phones?”, “When should we arrive?” | Answer from grounded branch or policy details and explain why. | Giving broad reassurance without checking the relevant location or rule. |
| FAQ | “How long?”, “Do we get hints?”, “What food is available?” | Answer immediately, then resume only the one pending question. | Answering briefly and mechanically repeating qualification in the same breath. |
| Objections | “Too easy”, “Too expensive”, “Times are too far apart” | Validate the actual concern and change the recommendation criteria. | Defending the first option instead of adapting. |
| Escalation | Language support, custom quotes, venue problems, exceptions | Set expectations and hand off with the minimum required detail. | Promising an exception, discount, or custom outcome before approval. |
| Closing | “I'll call back”, “That's all”, “Is it confirmed?” | Confirm the outcome and state one clear next step. | Generic support-script closings or restarting qualification. |

## Top Conversation Failures

1. Room catalogue dumping when the caller asks for guidance.
2. Qualification beginning before the customer's question is answered.
3. Long birthday-package monologues with no check-in.
4. Repeating facts and questions the customer already supplied.
5. Collapsing “10 to 12” into a confirmed count of ten.
6. Unsupported policy and refund claims.
7. Treating “first time at Breakout” as no prior escape-room experience.
8. Promising a booking before availability and consent are established.
9. Overusing acknowledgements until the turn sounds performative.
10. Continuing the workflow after the customer asks to pause.

## Recommended Prompt Changes

- Keep most spoken turns under 40 words.
- Explain escape rooms before qualification when education is requested.
- Prevent full room lists unless explicitly requested.
- Preserve uncertain ranges instead of silently selecting one value.
- Break long package explanations into short conversational steps.

## Recommended Playbook Changes

- Add an education-before-qualification pattern.
- Add transcript-supported recommendation timing.
- Make FAQ interruptions take priority over qualification.
- Add explicit spoken-brevity rules.
- Flag long monologues, repeated questions, and premature exact values as failure modes.

## Before / After Examples

### Escape-room education

**Before:** “How many people are joining, what ages are they, and which location do you prefer?”

**After:** “Sure. Your team enters a themed mission, finds clues, and solves puzzles together before time runs out. Is this your first one?”

### Group-size qualification

**Before:** “What age group are the players?”

**After:** “Nice, ten is a good-sized group. Are you all adults, all kids, or a mix?”

### Recommendation

**Before:** “Available rooms are Murder Mystery, Hostage, Classified, Undercover, and Prison Break.”

**After:** “I'd probably start with Murder Mystery for a first visit. Hostage is a good alternative if you want more urgency. Which mood sounds closer?”

### Birthday package

**Before:** A multi-minute package itinerary before learning what the parent values.

**After:** “Absolutely. You can keep it escape-room focused or build a fuller celebration around it. Which kind of experience are you considering?”

### FAQ interruption

**Before:** “Cancellation depends on policy. What date would you like to book?”

**After:** “Sure. Asking about cancellation won't change your booking. I'll explain the applicable policy first.”

## Dataset Notes

`prompts/transcript_examples.json` now contains five examples for each requested category. Each record includes the requested `customer` and `ideal_agent` fields while retaining the existing response-composer fields for backward compatibility. Examples intentionally avoid transcript-specific prices, discounts, live availability, and unverified policy promises.
