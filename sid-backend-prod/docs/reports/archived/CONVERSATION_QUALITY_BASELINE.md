# Conversation Quality Baseline

Date: 2026-06-24

Purpose: create a golden conversation-quality regression suite before deploying any prompt or playbook changes.

No production code, prompts, playbooks, recommendation logic, routing, booking logic, sentiment logic, escalation logic, handoff logic, memory logic, or API contracts were modified.

## Execution Mode

The current production prompt path was smoke-tested first.

- Current `breakout_personality_prompt.txt` loaded: yes
- Current `conversation_playbook.txt` loaded into composer: yes
- Current transcript examples loaded: 80
- LLM-on smoke request result: failed with OpenAI quota `429`
- Current production fallback response was returned

Because the LLM prompt path is currently unavailable in this environment, the 25-scenario baseline was run against the current deterministic production behavior with:

- `OPENAI_API_KEY=""`
- `BOOKING_PROVIDER=mock`
- current production prompt/playbook files left untouched
- current app dispatch/inbound/booking behavior left untouched

This baseline is still useful as a regression guard because it captures the customer-facing behavior the app serves when the production prompt path fails or is disabled. A second LLM-on baseline should be run once quota is available.

## Scoring Rubric

Each scenario is scored 1-5:

- **Naturalness:** sounds like a human booking host, not a script.
- **Recommendation:** recommendation is specific, confident, and context-aware.
- **Direct:** answers direct questions before asking new questions.
- **Repetition:** avoids repeated filler and repeated questions.
- **Efficiency:** moves toward the booking goal without unnecessary collection.
- **Guidance:** helps the customer make the next decision.

Overall score is the average of those six dimensions.

## Fail Conditions Checked

- Repeated `okay` filler
- Repeated `correct` filler
- Asking a question before answering a direct question
- Ignoring customer preference
- Asking for name before helping
- Long room monologues
- Unsupported `best` claims
- FAQ answer ignored
- Excessive policy repetition

## Baseline Summary

- Scenarios run: 25
- Average overall score: **4.59 / 5**
- Hard fail-condition hits: 18

Fail-condition frequency:

| Fail Condition | Count |
|---|---:|
| Unsupported best claim / overconfident wording | 9 |
| Asked name before helping | 3 |
| Direct question not answered | 3 |
| Preference not reflected | 2 |
| Excessive policy repetition | 1 |

## Golden Scenario Scorecard

| ID | Scenario | Naturalness | Recommendation | Direct | Repetition | Efficiency | Guidance | Overall | Fail Conditions |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| S01 | First-time player booking | 4 | 5 | 5 | 5 | 5 | 5 | 4.83 | Unsupported best claim |
| S02 | Returning player booking | 4 | 5 | 5 | 5 | 4 | 5 | 4.67 | Unsupported best claim |
| S03 | Couple booking | 5 | 5 | 5 | 5 | 4 | 5 | 4.83 | Asked name before helping |
| S04 | Birthday booking | 4 | 3 | 5 | 5 | 5 | 5 | 4.50 | Unsupported best claim |
| S05 | Corporate booking | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S06 | Large group booking | 4 | 5 | 5 | 5 | 5 | 5 | 4.83 | Unsupported best claim |
| S07 | Thrill preference | 4 | 5 | 5 | 5 | 5 | 5 | 4.83 | Unsupported best claim |
| S08 | Scary/horror preference | 4 | 2 | 5 | 5 | 5 | 3 | 4.00 | Preference not reflected; unsupported best claim |
| S09 | Intermediate difficulty preference | 5 | 5 | 5 | 5 | 5 | 5 | 5.00 | None |
| S10 | Budget-sensitive customer | 5 | 2 | 5 | 5 | 5 | 3 | 4.17 | Preference not reflected |
| S11 | Price objection | 5 | 3 | 3 | 5 | 4 | 4 | 4.00 | Direct question not answered; asked name before helping |
| S12 | FAQ interruption | 4 | 3 | 5 | 5 | 5 | 5 | 4.50 | Unsupported best claim |
| S13 | Cancellation policy question | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | Excessive policy repetition |
| S14 | Parking question | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S15 | Food/package question | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S16 | Late-arrival concern | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S17 | Slot comparison | 5 | 5 | 3 | 5 | 5 | 4 | 4.50 | Direct question not answered |
| S18 | Room comparison | 5 | 5 | 5 | 5 | 5 | 5 | 5.00 | None |
| S19 | Location comparison | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S20 | Room change | 4 | 3 | 5 | 5 | 5 | 5 | 4.50 | Unsupported best claim |
| S21 | Date change | 4 | 3 | 5 | 5 | 5 | 5 | 4.50 | Unsupported best claim |
| S22 | Human request | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S23 | Refund request | 5 | 3 | 5 | 5 | 5 | 5 | 4.67 | None |
| S24 | Customer confusion | 5 | 3 | 3 | 5 | 5 | 4 | 4.17 | Direct question not answered |
| S25 | Customer frustration | 5 | 3 | 5 | 5 | 4 | 5 | 4.50 | Asked name before helping |

## Scenario Details

### S01 — First-Time Player Booking

Turns:

1. `We are four adults and this is our first escape room.`
2. `Whitefield`
3. `Tomorrow`
4. `Murder Mystery`
5. `7 PM`

Responses:

1. `For a group of 4 adults, I'd probably choose Classified or Bomb Defusal: mystery for the first, intensity for the second. Which location works best: Koramangala, Whitefield, or JP Nagar?`
2. `For a group of 4 adults visiting Whitefield, I'd probably go with Bomb Defusal for intensity. Undercover is the more story-led option. Are you looking for something challenging or more story-driven?`
3. `Sorry, I didn't catch that. Could you repeat it?`
4. `I've checked availability for Tomorrow at Whitefield. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`
5. `Sorry, 7:00 PM isn't one of the available slots. You can choose from 10:00 AM, 12:00 PM, and 3:00 PM. Which would you prefer?`

Notes: Good booking protection, but the first-time recommendation is not aligned with the proposed first-time guidance. The date turn produced a weak repair.

### S02 — Returning Player Booking

Turns:

1. `We played Murder Mystery before and want to book another room for three adults.`
2. `JP Nagar`
3. `Tomorrow`
4. `7 PM`

Responses:

1. `I've noted Murder Mystery. Which location would you prefer?`
2. `I've noted Murder Mystery. Perfect, I've noted JP Nagar. What date would you like to visit?`
3. `I've checked availability for Tomorrow at JP Nagar. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`
4. `Sorry, 7:00 PM isn't one of the available slots. You can choose from 10:00 AM, 12:00 PM, and 3:00 PM. Which would you prefer?`

Notes: The customer's intent was "another room," but the response preserved Murder Mystery. This is likely backend/routing rather than prompt-only.

### S03 — Couple Booking

Turns:

1. `I want to book an escape room for two people, we are a couple and beginners.`
2. `Whitefield`
3. `Tomorrow`

Responses:

1. `Nice. I'd probably go with Murder Mystery for the story and teamwork. Hostage is the more urgent option. Are you looking for something relaxed or more challenging?`
2. `Got it. What date are you planning for?`
3. `Got it, Tomorrow. Perfect. What's your name?`

Notes: Strong recommendation; name collection starts before showing availability or room confirmation.

### S04 — Birthday Booking

Turns:

1. `I am planning a birthday celebration for 12 kids.`
2. `They are 13 to 14.`
3. `Whitefield`
4. `What package options do you have?`

Responses:

1. `Nice, 12 guests. Which location works best: Koramangala, Whitefield, or JP Nagar?`
2. `Nice. Which location works best: Koramangala, Whitefield, or JP Nagar?`
3. `Great. Whitefield can accommodate approximately 35-40 guests. What date were you thinking of visiting us for the party?`
4. `I'm not sure about that — our team would be the best people to help. Got it. What date are you planning for?`

Notes: Re-asks location after age is supplied and does not answer the package-options question. Strong candidate for proposed playbook improvement.

### S05 — Corporate Booking

Turns:

1. `We need a corporate team outing for 20 employees.`
2. `Whitefield`
3. `Next Friday`
4. `What food options do you have?`

Responses:

1. `Nice. For a team of 20, I'd probably combine Escape Rooms and Scavenger Hunt for teamwork and full-group involvement. Which location are you considering?`
2. `Got it. What date are you planning for?`
3. `Got it, Next Friday. Sounds good. Do you need food and beverages as well?`
4. `Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events.`

Notes: One of the stronger flows. Proposed prompts should preserve this behavior.

### S06 — Large Group Booking

Turns:

1. `We are 15 adults and want to book Bomb Defusal.`
2. `Whitefield`
3. `Tomorrow`

Responses:

1. `I've noted Bomb Defusal. Which location would you prefer?`
2. `I've noted Bomb Defusal. Perfect, I've noted Whitefield. What date would you like to visit?`
3. `I've checked availability for Tomorrow at Whitefield. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`

Notes: Efficient, but no host-style capacity guidance in the spoken flow.

### S07 — Thrill Preference

Turns:

1. `We are first timers but want something thrilling and adventurous.`
2. `JP Nagar`
3. `Four adults`

Responses:

1. `No worries. I'd probably start with Murder Mystery for a first visit. Hostage is the more urgent option if you want extra pressure. How many people are joining?`
2. `JP Nagar has street parking and offers Murder Mystery, Hostage, and Prison Break.`
3. `For a group of 4 adults, I'd probably choose Classified or Bomb Defusal: mystery for the first, intensity for the second. Which location works best: Koramangala, Whitefield, or JP Nagar?`

Notes: First response is decent, but later recommendation ignores the already supplied JP Nagar and suggests rooms from other locations.

### S08 — Scary/Horror Preference

Turns:

1. `Do you have any scary or horror escape room? I want something spooky.`
2. `JP Nagar`

Responses:

1. `Got it. To suggest the perfect game, how many people are joining?`
2. `Got it. To suggest the perfect game, how many people are joining?`

Notes: Clear preference is ignored. This is one of the highest-value prompt/playbook improvement cases.

### S09 — Intermediate Difficulty Preference

Turns:

1. `We do not want beginner only, we are okay with intermediate.`
2. `Koramangala`
3. `Five people`

Responses:

1. `No worries. I'd probably start with Murder Mystery for a first visit. Hostage is the more urgent option if you want extra pressure. How many people are joining?`
2. `Koramangala has basement and street parking, and offers Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Undercover, The Wizarding Championship, and The Forbidden Forest.`
3. `Perfect. What age group are the players: adults, kids, or a mix?`

Notes: Acceptable baseline, though the first answer still starts from beginner language.

### S10 — Budget-Sensitive Customer

Turns:

1. `We are 14 kids for a birthday but I am worried about budget. What are the options?`
2. `Koramangala`

Responses:

1. `Which location would you prefer?`
2. `Perfect, I've noted Koramangala. What date would you like to visit?`

Notes: Budget and options are ignored. Strong candidate for the proposed birthday/budget playbook.

### S11 — Price Objection

Turn:

1. `It is a little expensive. Is there any discount available?`

Response:

1. `I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

Notes: Direct question is only partially handled, then jumps to name/number. Strong candidate for proposed price-objection language.

### S12 — FAQ Interruption

Turns:

1. `We want to book Murder Mystery at Whitefield tomorrow.`
2. `Is parking available?`

Responses:

1. `I've checked availability for Tomorrow at Whitefield. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`
2. `Parking depends on the branch: Koramangala has basement and street parking, Whitefield has basement car parking, and JP Nagar has street parking. Your available slots are 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best?`

Notes: FAQ is answered and booking resumes. Proposed prompt can make it shorter by answering only Whitefield parking.

### S13 — Cancellation Policy Question

Turn:

1. `What is your cancellation policy?`

Response:

1. `Cancellation charges depend on how far in advance the cancellation is made. I can explain the policy without changing any booking, and the appropriate team can help with a cancellation or refund request.`

Notes: Direct and safe, but policy wording is a little dense.

### S14 — Parking Question

Turn:

1. `Is parking available at Whitefield?`

Response:

1. `Koramangala has basement and street parking, Whitefield has basement car parking with dedicated spots and general parking, and JP Nagar has street parking.`

Notes: Correct, but it answers all locations instead of just Whitefield.

### S15 — Food/Package Question

Turn:

1. `Do you have food options for birthday parties?`

Response:

1. `Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events.`

Notes: Direct, but mentions corporate events for a birthday question. Prompt-only changes may not fix this if the knowledge retrieval draft is wrong.

### S16 — Late-Arrival Concern

Turn:

1. `We have a 7 PM booking but we are running 30 minutes late. Can you help?`

Response:

1. `Don't worry, let me help. The game starts at the scheduled time because sessions run back to back, so arriving late reduces the time available inside the room.`

Notes: Good rescue tone. Proposed prompt should preserve this.

### S17 — Slot Comparison

Turn:

1. `For Murder Mystery at Koramangala, should I take 3:15 or 5:30? We might be late.`

Response:

1. `Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery. It is a classic, immersive detective investigation where you find clues and solve a crime. It is beginner-friendly and great for a wide range of ages. It is offered at Koramangala, Whitefield, and JP Nagar.`

Notes: Direct slot-comparison question is ignored and replaced by a room monologue.

### S18 — Room Comparison

Turn:

1. `What is better for first timers, Murder Mystery or Hostage?`

Response:

1. `Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery. It is a classic, immersive detective investigation where you find clues and solve a crime. It is beginner-friendly and great for a wide range of ages. It is offered at Koramangala, Whitefield, and JP Nagar.`

Notes: Gives a recommendation but not a comparison. Proposed recommendation playbook should improve this.

### S19 — Location Comparison

Turn:

1. `I am near Marathahalli. Which location should I choose?`

Response:

1. `Breakout has locations in Koramangala, Whitefield, and JP Nagar.`

Notes: Direct but weak guidance; it lists locations without advising.

### S20 — Room Change

Turns:

1. `Book Murder Mystery at Whitefield tomorrow for four adults.`
2. `Actually change it to Hostage.`

Responses:

1. `I've checked availability for Tomorrow at Whitefield. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`
2. `Sorry, I didn't catch that. The available slots are 10:00 AM, 12:00 PM, and 3:00 PM. Which time would you prefer?`

Notes: Room change is not acknowledged in the spoken response. Likely backend/state handling, not prompt-only.

### S21 — Date Change

Turns:

1. `Book Hostage at JP Nagar for four adults tomorrow.`
2. `Actually make it 26 June.`

Responses:

1. `I've checked availability for Tomorrow at JP Nagar. We have slots at 10:00 AM, 12:00 PM, and 3:00 PM. Which time works best for you?`
2. `Sorry, I didn't catch that. The available slots are 10:00 AM, 12:00 PM, and 3:00 PM. Which time would you prefer?`

Notes: Date change is not acknowledged in the spoken response. Likely backend/state handling, not prompt-only.

### S22 — Human Request

Turn:

1. `I want to speak to a human.`

Response:

1. `Of course. I'll connect you with our team and pass along the details already shared.`

Notes: Strong baseline. Preserve.

### S23 — Refund Request

Turn:

1. `I want a refund.`

Response:

1. `I'll connect you with our team to review the refund request and the booking details.`

Notes: Strong baseline. Preserve.

### S24 — Customer Confusion

Turn:

1. `I am confused. What exactly happens inside an escape room?`

Response:

1. `No worries. Got it. How many people are joining?`

Notes: Direct education request is ignored. Strong candidate for conversation playbook improvement.

### S25 — Customer Frustration

Turn:

1. `No no no, that is not what I asked. You are not understanding me.`

Response:

1. `I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

Notes: Escalation-safe, but not emotionally satisfying. It asks for contact before repair.

## Most Likely To Improve From Proposed Prompt / Playbook Changes

| Scenario | Why It Should Improve |
|---|---|
| S08 Scary/horror preference | Proposed playbook explicitly answers no-horror and gives closest suspense/pressure options. |
| S10 Budget-sensitive customer | Proposed birthday/budget pattern gives two paths before collecting more fields. |
| S11 Price objection | Proposed price-objection pattern answers discount uncertainty before asking for contact. |
| S17 Slot comparison | Proposed direct-intent rule should answer timing tradeoff instead of room monologue. |
| S18 Room comparison | Proposed recommendation playbook gives a real Murder Mystery vs Hostage comparison. |
| S19 Location comparison | Proposed host guidance should recommend a practical location rather than listing all branches. |
| S24 Customer confusion | Proposed education-before-qualification pattern explains escape rooms before asking participants. |
| S25 Customer frustration | Proposed rescue/repair tone should acknowledge frustration before handoff/contact collection. |
| S04 Birthday booking | Proposed package-options pattern should answer package comparison before asking date again. |
| S12 FAQ interruption | Proposed FAQ pattern should shorten the parking answer to the relevant location. |

## Least Likely To Improve From Prompt Alone

| Scenario | Reason |
|---|---|
| S20 Room change | The change is not acknowledged before slot continuation; likely backend/state handling. |
| S21 Date change | The date change is not acknowledged before slot continuation; likely backend/state handling. |
| S02 Returning player booking | The phrase "another room" still preserves Murder Mystery; likely extraction/routing behavior. |
| S15 Food/package question | Birthday food question returns corporate-event wording; likely knowledge retrieval/content mapping. |

## Baseline Verdict

The current booking flow is protected enough to continue prompt work, but the baseline shows clear quality gaps in:

1. Direct-question handling
2. Preference-sensitive recommendations
3. Budget-sensitive birthday guidance
4. Confusion/frustration repair
5. Comparison answers
6. Overbroad FAQ answers

The proposed Personality Prompt and Conversation Playbook are most likely to improve spoken naturalness and direct-answer behavior. They will not fully fix backend-owned issues where the wrong draft is selected before the prompt layer sees it.
