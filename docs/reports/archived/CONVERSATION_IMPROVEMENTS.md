# Conversation Improvements

## Evidence Reviewed

- Claude's five playbook/prompt files and 50 transcript additions.
- Existing prompt assets and `docs/transcript_analysis.md`.
- 20 persisted API sessions and the repository log corpus.
- Known production transcripts covering repeated participant, age, location, date, slot, and confirmation failures.

Corpus searches still find 41 age-group phrases, 40 participant questions, and 25 booking-confirmation phrases in stored sessions/logs. These counts are signals, not proof that every occurrence is a defect; the supplied transcripts prove several are.

## Repetition Problems

1. **Known fields are re-asked.** Participant count, age group, location, and date were collected, then requested again.
2. **Confirmation replaces progression.** The agent reconfirms settled fields instead of asking for the next missing field.
3. **Full summaries recur.** Booking details are repeated before a slot or contact details exist and after unrelated FAQs.
4. **Recommendation anchors repeat.** A rejected room can be proposed again without incorporating the objection.
5. **Repair turns repeat the failure.** After an extraction miss, the agent often restates everything rather than asking only for the ambiguous value.

## Robotic Behaviors

- Explaining a room after the customer says `book it`.
- Dumping a room list when the customer asks for guidance.
- Asking a qualification question before answering an FAQ.
- Treating `first time` as a name candidate or as proof of experience level without context.
- Saying a booking is `set`, `almost complete`, or `being processed` without a provider ID.
- Using repeated acknowledgments on every turn.
- Asking multiple form-like questions in one spoken response.
- Treating a changed participant count/date/room as a new conversation instead of a booking modification.

## Human Conversation Opportunities

| Customer says | Better response shape |
|---|---|
| `Just book it.` | `Sure. What date are you planning to visit?` |
| `I already told you, ten to fifteen.` | `You're right, I have ten to fifteen. Which location works?` |
| `Is 3 PM available?` | Answer from cached/current provider slots, then ask the next missing field |
| `What is the price?` | Give a verified live total, or say exact pricing is not available; do not describe the room |
| `We are actually seven.` | Update seven immediately, rerun capacity/availability, report only the result |
| `My first name is Siddharth.` | Store it and ask only for the last name |
| `Can I speak to a person?` | Handoff immediately with context preserved |
| `I need to check with my friends.` | Pause without pressure and keep state intact |

## Production Conversation Contract

1. Answer the present question first.
2. Apply all explicit corrections before routing.
3. Compute the next question from booking state, never from a generic intent template.
4. Ask one question per turn.
5. Never confirm without a persisted provider booking ID/reference.
6. Use FAQ answers as interruptions, not workflow resets.
7. On ambiguity, ask for only the ambiguous value.
8. On failure, preserve valid context and explain the single next action.
9. Do not claim a hold, price, policy, promotion, capacity, or availability without a source/result.
10. Keep most voice turns below 40 words.

## Evaluation Rubric

A replay passes only when every user-provided field remains available, direct questions are answered, the workflow advances monotonically, no unsupported claim is spoken, and confirmation contains a real persisted identifier.
