# Conversation Quality Transcript Audit

Date: 2026-06-24

Scope: real customer transcripts only. This report intentionally avoids booking logic, memory, routing, sentiment, escalation, handoff, API contracts, and test coverage except where they explain conversation-quality causes.

Production prompt files were not changed. Proposed prompt/playbook replacements are provided separately.

## Reviewed Conversations

| ID | File | Primary Situation |
|---|---|---|
| T01 | `176800523299776846077413_33334324.txt` | Two-person same-day slot and price inquiry |
| T02 | `176800543755077354959933_33334324.txt` | Price objection and first-time recommendation |
| T03 | `176800590135370925755315_33334324.txt` | Payment-link clarification |
| T04 | `176800709374821248617439_33334324.txt` | Slot reservation with child/adult count ambiguity |
| T05 | `176800818516064659178403_33334324.txt` | Wrong booking date email |
| T06 | `176800870327941125809534_33334324.txt` | Payment confirmation and arrival question |
| T07 | `176800871436274368582817_33334324.txt` | Slot inquiry, recommendation, price objection |
| T08 | `17680111923406545431744_33334324.txt` | Couple birthday celebration inquiry |
| T09 | `176869678861607319835599 33334324 (1) (1).txt` | Large teen birthday package inquiry |
| T10 | `176869780335079165303379 33334324.txt` | Five-person slot request and intermediate preference |
| T11 | `177371777091826325286822 33334324.txt` | Late-night birthday / room-options inquiry |
| T12 | `176800709374821248617439 33334324.txt` | Duplicate variant of T04 |
| T13 | `176801644484817275226779 33334324.txt` | Whitefield first-time booking |
| T14 | `17680111923406545431744 33334324.txt` | Duplicate variant of T08 |
| T15 | `177338585618167251811317 33334324.txt` | First-time thrill/scary preference, bilingual comfort |
| T16 | `176861879834514101769376 33334324.txt` | Ten-person two-room booking, payment uncertainty |
| T17 | `176846346753641052941417 33334324.txt` | Late-arrival rescue and phone briefing |

Frequency scan across 17 unique text transcripts:

- `okay`: 366 occurrences
- `correct`: 40 occurrences
- `first time` / `first timer`: 41 occurrences
- `may I know`: 17 occurrences
- `recommend`: 17 occurrences
- `last name`: 8 occurrences
- `good name`: 6 occurrences

These frequencies point to repeated verbal habits and form-style progression, not only isolated bad turns.

## Current Asset Comparison

### Personality Prompt

The current personality prompt already asks for short turns, one question at a time, natural transitions, and direct answers. It also encourages phrases like `Beautiful`, `Don't worry`, and `You'll love it`.

Quality gap: some encouraged phrases can become overused or too strong in real calls. The prompt does not strongly enough ban stacked acknowledgements such as `okay okay okay`, nor does it provide enough repair language for uncertainty, missed hearing, or customer hesitation.

Likely causes:

- Prompt: over-allows filler and unsupported excitement.
- Playbook: lacks explicit examples for replacing form-style questions.
- Backend logic: can still hand the composer a draft whose shape is already mechanical.

### Conversation Playbook

The playbook is directionally good. It says to acknowledge, answer first, avoid catalogues, ask one question, and avoid repetition.

Quality gap: it does not contain enough negative examples from actual calls. It also does not specify when to stop asking first-time questions and recommend immediately, or how to handle "I already know what I want" / "book it" moments like a host.

Likely causes:

- Playbook: needs stronger anti-repetition, direct-intent, and host-like guidance.
- Agent routing/backend logic: may still select a qualification step when the customer asked for options or recommendations.

### Recommendation Logic

The recommendation engine is conservative and location/capacity-aware, which is good. It often defaults to `Murder Mystery` and `Hostage` for beginners.

Quality gap: real callers often express a preference beyond beginner status: thrill, intermediate, couple birthday, large teen party, late-night celebration, "not horror", "more adventurous", "what options do you have." The recommendation guidance should tell the agent how to confidently use that preference instead of collapsing back to the same beginner script.

Likely causes:

- Recommendation rules: too beginner-first when the customer asks for intermediate/thrill.
- Prompt/playbook: not enough language for "guided choice" vs "form collection."
- Backend logic: if only one recommendation object is supplied, wording may lack alternatives.

## Top 50 Conversation-Quality Failures

| # | Failure | Example Evidence | Cause | Proposed Fix |
|---:|---|---|---|---|
| 1 | Stacked acknowledgements make the agent sound nervous | T04/T09/T13: repeated `Okay, okay, okay` | Prompt + Playbook | Add a filler budget: one acknowledgement max, skip if previous turn used one |
| 2 | Name asked too early before helping | T08 asks name before explaining birthday options | Playbook | Delay name until booking/reservation or when needed for continuity |
| 3 | Re-asking name after already heard it | T08 asks Ishaan's name twice | Backend logic + Playbook | Add "if name was just supplied, do not repair unless essential" wording |
| 4 | First-time question repeated excessively | T01/T02/T07/T13 | Playbook + Recommendation rules | Ask once; after answered, recommend or proceed |
| 5 | Beginner recommendation ignores stated intermediate preference | T10: customer says "we do not want beginner only" | Recommendation rules | Treat intermediate/thrill as a preference override |
| 6 | Beginner recommendation ignores stated scary/thrill preference | T15 repeatedly returns to Murder Mystery | Recommendation rules | Say no horror, then offer closest thriller/suspense option confidently |
| 7 | Raw room explanation is too long | T02 Hostage description is a full plot speech | Playbook | One-line story hook first; offer details only if asked |
| 8 | Escape-room explanation overexplains basics | T08/T15 long "locked in thematic room" monologue | Prompt + Playbook | Use a 20-second explanation pattern |
| 9 | Agent asks a form question after clear direct request | T10 asks first-time before answering room availability | Backend logic + Playbook | Answer direct availability/options first, then ask one narrowing question |
| 10 | Direct price objection handled defensively | T02: "charges are quite reasonable with us" | Playbook | Use value framing and budget-respecting language |
| 11 | Discount handling encourages negotiation loop | T02 repeats 5%, then future discounts | Playbook | State current offer once, then guide decision |
| 12 | Weak recommendation reason | "beginner friendly game" only | Recommendation rules | Tie to user context: first time, couple, teen group, thrill |
| 13 | Missed recommendation timing | T01 lists slots before recommending best first-time option | Recommendation rules | When enough context exists, recommend while presenting slots |
| 14 | Asking "reserve?" before helping compare times | T04 customer asks about 3:30/4 after reserve prompt | Playbook | When timing concern appears, solve timing first |
| 15 | Late-arrival response starts harshly | T17: "We cannot help you out" | Prompt | Use rescue language: "Let's try to save as much game time as possible" |
| 16 | Uses fear/scolding around lateness | T17/T04/T13 policy warnings repeated | Prompt + Playbook | Explain once, in calm operational language |
| 17 | Awkward "good name" phrase | T10/T15/T17 | Prompt | Replace with "May I have your name?" |
| 18 | Last-name collection friction | T04/T10/T13/T16 | Backend logic fixed recently + Playbook | New prompt should say collect natural name only |
| 19 | Long birthday package monologue | T09 several minutes before budget question answered | Playbook | Ask "escape-room only or full celebration?" early and compare briefly |
| 20 | Missed budget sensitivity | T09 customer worries about budget; agent continues package details | Playbook | Acknowledge budget and present two simple paths |
| 21 | Too many details before answering "what's included?" | T09/T11 | Playbook | Use "short version" first |
| 22 | Location guidance overconfident | T15: "all are best", "you will love it" | Prompt | Ban unsupported superlatives |
| 23 | Customer language comfort mishandled | T15 offers Kannada handoff, customer says English okay | Playbook | Confirm language and continue simply |
| 24 | Joke creates anxiety | T15 "locked forever" scares customer | Prompt | Ban safety jokes about being locked in |
| 25 | Repeats policy after customer already accepted | T03/T13 arrival/refund reminders | Playbook | Confirm once; avoid repeated warning stack |
| 26 | Confirms wrong details late | T03 asks location/game after payment made | Backend logic | Prompt can add "verify before payment, not after" but code owns order |
| 27 | Missed emotional reassurance after customer says "Oh my God" | T16 slot concern | Prompt + Playbook | Acknowledge loss, then offer closest solution |
| 28 | Asking unnecessary experience details while customer needs availability | T16 | Playbook + Backend logic | If exact time request exists, check time first |
| 29 | Weak handling of "we might be late" | T04 | Playbook | Offer next safer slot and arrival tradeoff |
| 30 | Repeats customer count incorrectly | T04 six vs seven, T16 eight vs ten | Backend logic + Playbook | Repeat only uncertain field, not whole form |
| 31 | Spelling capture is clunky | T04/T10/T16/T17 | Prompt | Ask for spelling only when needed; confirm concise chunks |
| 32 | Overuses customer name | T04/T13 | Prompt | Use name sparingly: open/close and important confirmations |
| 33 | Recommendation arrives after customer already committed | T02 recommends Murder Mystery after Hostage flow | Playbook | Recommend before reservation decision, not after payment discussion |
| 34 | Lists unavailable or irrelevant options after preference | T15 customer asks scary; agent returns generic explanation | Recommendation rules | Use "closest match" pattern |
| 35 | Weak direct answer to "what rooms do you have?" | T11 explains only beginner two, then says more exist | Playbook | Provide 2-3 grouped options and ask preference |
| 36 | "Would you like me to reserve?" sounds script-like | T07/T11/T13 | Prompt | Replace with "Shall I hold that slot?" |
| 37 | "I will require details" sounds bureaucratic | T04/T13/T16 | Prompt | Replace with "I’ll take the details now" |
| 38 | Missing excitement for first-time players | T01/T07/T10 | Prompt | Short warmth: "Nice, first one at Breakout. I'd start with..." |
| 39 | Missing excitement for birthday/couple context | T08/T11 | Prompt + Playbook | Match the occasion briefly before constraints |
| 40 | Awkward transition after FAQ to qualification | T08 price -> concept explanation | Playbook | Answer price, then offer details; don't detour unless needed |
| 41 | Heavy apologies for hearing issues | T08 | Prompt | Use light repair: "Got it now, Ishaan." |
| 42 | Unclear comparison language | T10 "following levels" | Recommendation playbook | Use "Murder Mystery is easiest, Hostage is one step up" |
| 43 | Uses unsupported "best" claims | T15 | Prompt + Recommendation rules | Say "best fit for what you asked" only when grounded |
| 44 | Fails to ask what matters most when multiple constraints exist | T09/T16 | Playbook | Ask priority: budget, timing, room type, celebration scope |
| 45 | Does not summarize options compactly | T09 birthday packages | Playbook | Use side-by-side two-option summary |
| 46 | Long policy/payment speech | T04/T13 | Prompt | Split into payment link + arrival reminder only |
| 47 | "No refund/reschedule" wording is abrupt | T03/T04/T13 | Prompt | Use "Please treat the slot as fixed once paid" |
| 48 | Weak pause behavior | T02/T07/T11 "call back" moments | Playbook | Warm close with what to ask for on callback |
| 49 | Agent sometimes says "just a minute" repeatedly | T04/T13/T16 | Prompt | Say once; then return with result |
| 50 | Conversation feels like data entry after decision | T04/T13/T16 | Playbook | Use host frame: "Great, I'll hold it under your name" |

## Top 20 Highest-Frequency Failures

| Rank | Failure | Evidence |
|---:|---|---|
| 1 | Overuse of `okay` / `okay okay` | 366 occurrences |
| 2 | Repeated first-time questioning | 41 occurrences of first-time wording |
| 3 | Overuse of `correct` as filler | 40 occurrences |
| 4 | Form-style `may I know` phrasing | 17 occurrences |
| 5 | Recommending Murder Mystery by default | 33 mentions across many contexts |
| 6 | Long room-story monologues | T02, T08, T11, T15, T17 |
| 7 | Arrival-policy repetition | T03, T04, T06, T10, T13, T17 |
| 8 | Payment-policy repetition | T03, T04, T13, T16 |
| 9 | Asking details before answering direct question | T01, T08, T10, T16 |
| 10 | Name asked too early | T08, T11, T15 |
| 11 | Unnecessary last-name/spelling friction | T04, T10, T13, T16 |
| 12 | Weak price-objection handling | T02, T09, T11, T16 |
| 13 | Poor handling of uncertainty/hearing repair | T08, T15, T17 |
| 14 | Overuse of customer name | T04, T13, T16 |
| 15 | Repeating customer details inaccurately | T04, T16 |
| 16 | Overly formal/bureaucratic phrasing | T04, T10, T13, T16 |
| 17 | Weak transitions from FAQ back to booking | T08, T11, T16 |
| 18 | Fails to ask priority when multiple constraints exist | T09, T16 |
| 19 | Unsupported superlatives | T15 |
| 20 | Low excitement for birthdays/first-timers | T01, T07, T08, T11 |

## Top 20 Highest-Impact Failures

| Rank | Failure | Why It Matters | Cause |
|---:|---|---|---|
| 1 | Defaulting to beginner recommendation despite stronger preference | Loses trust and makes guidance feel generic | Recommendation rules |
| 2 | Asking form questions before answering direct intent | Customer feels unheard | Backend logic + Playbook |
| 3 | Late-arrival rescue starts with "cannot help" | Makes urgent customers more anxious | Prompt |
| 4 | Long birthday monologues before budget/options | High abandonment risk | Playbook |
| 5 | Scary/thrill request not handled directly | Missed sales moment | Recommendation rules |
| 6 | Price objection handled defensively | Creates negotiation rather than value | Playbook |
| 7 | Repeated detail collection | Sounds like a form, not a host | Backend logic + Prompt |
| 8 | Joke about being locked forever | Safety anxiety | Prompt |
| 9 | Wrong/late verification after payment | Trust damage | Backend logic |
| 10 | Capacity/group split not guided confidently | Confuses groups | Recommendation rules + Playbook |
| 11 | Poor timing tradeoff guidance | Customers need help choosing slots | Playbook |
| 12 | Overuse of filler | Lowers confidence | Prompt |
| 13 | Unnecessary name/last-name collection | Adds friction | Prompt + Backend logic |
| 14 | Weak first-time excitement | Missed emotional connection | Prompt |
| 15 | Missed budget-sensitive comparison | Makes packages feel expensive/unclear | Playbook |
| 16 | Overexplaining room stories | Slows voice calls | Playbook |
| 17 | Unsupported "best/you'll love it" claims | Risk of hallucinated confidence | Prompt |
| 18 | Repeating refund/reschedule warnings | Sounds punitive | Prompt |
| 19 | Weak pause/callback close | Missed conversion follow-up | Playbook |
| 20 | FAQ answer detours into qualification | Breaks conversational flow | Backend logic + Playbook |

## Cause Distribution

Approximate cause assignment from the top 50 failures:

- Prompt: 17
- Playbook: 20
- Recommendation rules/guidelines: 8
- Agent routing/backend logic: 5

Because this pass is prompt/playbook-only, the backend-caused issues should be treated as known limits: prompt changes can soften wording, but cannot fully fix wrong routing, stale state, or incorrect operational drafts.

## Before/After Examples

### First-Time Recommendation

Before:

> Are you playing for the first time? ... So, I would recommend going for Murder Mystery as beginner.

After:

> Nice, first one at Breakout. I'd start with Murder Mystery: easy to get into, still a proper detective challenge. If you want one step more intense, Hostage is the next pick.

### Customer Wants Intermediate

Before:

> Murder Mystery is not very easy, it is challenging but it has good solvability.

After:

> Got it, not too easy. I'd put you on Hostage if the slot works; it has more urgency than Murder Mystery but is still manageable for a first Breakout game.

### Scary/Thrill Preference

Before:

> We don't have horror horror kind of thing...

After:

> We don't do horror or jump-scare rooms. Closest fit is Murder Mystery for suspense, or Hostage if you want more pressure. Do you want suspense or urgency?

### Birthday Budget

Before:

> I'll take next few minutes to walk you through how the birthday party will look like...

After:

> Totally fair to watch the budget. There are two paths: escape-room only, or a birthday package with food and space. Want the quick price comparison first?

### Late Arrival

Before:

> We cannot help you out because we run on back-to-back time slots.

After:

> I hear you. Since slots run back-to-back, the best move is to save as much game time as possible. How far away are you right now?

## Conversation Quality Score Improvements

These are projected improvements if the proposed prompt/playbook changes are adopted and the composer follows them:

| Area | Current | Proposed | Basis |
|---|---:|---:|---|
| Brevity | 6/10 | 8/10 | Adds hard caps for story hooks and package explanations |
| Naturalness | 5/10 | 8/10 | Removes filler, `good name`, stacked acknowledgements |
| Recommendation Confidence | 5/10 | 8/10 | Adds preference-led recommendations |
| First-Time Guidance | 6/10 | 8/10 | Gives ready-made first-timer language |
| FAQ/Booking Transitions | 6/10 | 7/10 | Better wording, but backend routing may still limit |
| Birthday/Package Handling | 5/10 | 8/10 | Adds budget-first and two-path summaries |
| Empathy/Rescue | 5/10 | 8/10 | Replaces blunt policy openings |
| Repetition Control | 4/10 | 8/10 | Explicit filler and repeated-question bans |

Overall projected conversation-quality score: 5.3/10 -> 7.9/10.

## Risk Assessment of Prompt Changes

| Risk | Level | Mitigation |
|---|---|---|
| More confident recommendations could sound like unsupported claims | Medium | Proposed language says "best fit for what you asked", not "best overall" |
| Shorter answers might omit policy details | Medium | Keep policy details in approved business drafts; prompt only shortens delivery |
| Banning filler may make responses too abrupt | Low | Prompt still allows one natural acknowledgement |
| Recommendation alternatives may conflict with availability | Medium | Proposed guidelines require "if available / if inventory supports it" |
| Budget language could imply negotiation | Medium | Proposed language says compare paths, not invent discounts |
| Prompt cannot fix wrong backend routing | High | Explicitly marked as backend/routing cause where applicable |

## Recommended Prompt-Only Changes

1. Add a hard anti-filler rule: no stacked `okay`, no repeated acknowledgement on adjacent turns.
2. Replace `May I know your good name?` / `first and last name` with natural single-name language.
3. Add "answer direct intent first" examples.
4. Add "preference overrides beginner" recommendation guidance.
5. Add short templates for first-time, thrill, couple birthday, large group, budget-sensitive, and late-arrival moments.
6. Limit room story hooks to one sentence unless the customer asks for details.
7. Limit package explanations to a two-path summary before details.
8. Add "rescue language" for late, confused, or frustrated customers.
