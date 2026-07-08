# RED TEAM SCENARIOS — Breakout Escape Room Agent
**QA Audit Date:** 2026-06-22  
**Agent Version:** MVP (main.py + app.py)  
**Auditor Role:** Principal QA Engineer  
**Scope:** All conversation paths a manager, investor, customer, or QA tester can invent

---

## CATEGORY A — Booking Flow Scenarios

### A-BOOK (New Booking)

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A001 | User says "I want to book" with zero context (no name, no room, no date) | BookingAgent triggered before qualification complete → may skip slot gathering |
| A002 | User confirms booking but agent hasn't collected customer_name or phone | booking_ready() may pass if first_name extracted from single-word name only |
| A003 | User gives first name only ("Priya") at name-collection state — agent skips last name | `_handle_contact_first_name` stores name, transitions to phone if last_name already set (but it never was) |
| A004 | User's phone number is 10 digits starting with 5 (e.g., 5876543210) — regex requires 6-9 start | Phone extraction silently fails; booking proceeds without phone |
| A005 | User types their name in all caps: "ARJUN SHARMA" | `_is_plausible_name()` lowercases for check; `.title()` titlecases result — should work, but NOISE-word collision ("ARJUN" lowered is "arjun") not in noise set, so should work — verify |
| A006 | User gives a single-word name that is a noise word: "Ready", "Escape", "Room", "Adults" | Noise word rejection blocks name capture; agent loops on name question |
| A007 | User provides a name like "Dr. Mehta" with honorific | Regex strips non-alpha chars; "Dr" becomes candidate, passes plausible-name (2 chars), may store "Dr Mehta" incorrectly |
| A008 | User sends name mid-sentence: "My name is Priya and I want the 3 PM slot" | Name extracted correctly, but slot extracted in the same turn — state machine may skip slot validation |
| A009 | User types their email before their phone when agent waits for phone | Email captured but phone still missing; agent loops asking for phone |
| A010 | Session memory file is corrupted (invalid JSON) | `_load()` catches JSONDecodeError and resets to DEFAULT_MEMORY — but if partial write, data may be partially loaded |

### A-RESCHEDULE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A011 | User says "reschedule to next Monday" before any booking is confirmed | No `booking_ref` exists; `is_cancel_request` check is False; agent may loop on slot question |
| A012 | User says "reschedule to 5 PM" when in WAITING_FOR_SLOT state | `is_change_request` may be False (no "change/instead" keyword); treated as slot selection, not reschedule |
| A013 | User reschedules to a date with no availability | `_handle_alt_date` sets `_STATE_CLOSED` after second failure — user is trapped with no way to restart |
| A014 | User reschedules date but keeps same slot — slot is no longer valid for new date | `_clear_selected_slot` clears selected_slot, but availability is re-checked; however `_last_availability` is overwritten — verify no stale data |

### A-CANCEL

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A015 | User says "I want to cancel" without a booking reference in memory | Agent says "No problem, I've cancelled the booking process" — false confirmation of cancellation of a non-existent booking |
| A016 | User says "cancel my reservation" (uses "reservation" not "booking") | Regex `cancel|delete\s+my\s+booking|reservation` — "reservation" not in pattern → may not trigger cancel path |
| A017 | User asks "can you cancel that?" after a confirmed booking | Pronoun "that" not matched by cancel regex; routed as unrecognized input |
| A018 | User cancels then immediately asks to rebook | After `_STATE_CLOSED`, any new message hits `_handle_post_booking` which closes → user stuck |

### A-ROOM-CHANGE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A019 | User changes room from "Bomb Defusal" to "Prison Break" after slots shown | Room change clears selected_slot and re-runs availability. If new room is at a different location (JP Nagar vs Whitefield), location field may be stale |
| A020 | User mentions a room that doesn't exist: "I want the vampire room" | No room extracted; `target_room` is None; `any_field_changed` is False; message falls through to state machine as unrecognized |
| A021 | User changes room after booking is confirmed | `_STATE_BOOKING_CONFIRMED` or `_STATE_CLOSED` — change request detected, availability re-checked, but booking_ref is still in memory — creates confusion |

### A-DATE-CHANGE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A022 | User says "actually, next month" as date | `_extract_preferred_date` does not handle "next month" — returns empty string; date is not updated; agent asks for date again |
| A023 | User gives a past date: "book for 1 January" (already past) | `_normalise_date` adds 1 year if date < today — date silently moved to next year without informing the user |
| A024 | User gives a date as "day after tomorrow" | Not in relative_terms list; not extracted; agent asks for date again |
| A025 | User gives date in MM/DD format: "06/25" | Regex `\d{1,2}[/-]\d{1,2}` matches but treats as DD/MM → June 25 becomes 06 of month 25 (invalid) |

### A-PARTICIPANT-CHANGE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| A026 | User increases group from 4 to 15 mid-booking | `participants_changed=True`; `set_field` called with force; availability re-run but `_last_availability` slots may not support 15 — gate check at booking_tool.create |
| A027 | User says "we have 0 people" | `_extract_participants` returns 0; `int(0)` is falsy; `participants` field set to 0; `booking_ready()` check `or self.data.get("company_size")` may pass with 0 |
| A028 | User changes from "4 people" to "4 adults and 3 kids" | Compound extraction sums to 7; replaces participants=4; age_group was "adults" but now mixed — age_group not updated automatically |

| A029 | User corrects participant count multiple times in rapid succession: 'we are 4... wait 6... no 5' | Multiple corrections may confuse intent classifier or set_field logic |
| A030 | User reschedules to 'next next Monday' (double relative timing) | Relative date parser may only match one 'next' and resolve to wrong week |
| A031 | User uses word-based date format like 'June twenty-fifth' | Date extractor needs to handle spelled-out ordinals |
| A032 | User specifies booking time as 'noon' or 'midnight' | Time slot lookup expects HH:MM AM/PM, may fail to parse text representations |
| A033 | User specifies time contextually: 'after lunch' or 'before dinner' | Ambiguous time preference needs fallback or time-of-day filtering |
| A034 | User uses Indian date format '25/06/2026' or '25-06' | Regex and normalise_date must correctly handle DD/MM vs MM/DD formats |
| A035 | User enters first name as 'null', 'None', or 'N/A' | Domain keyword check or python type coercion might treat string 'null' as empty/Falsy |
| A036 | User enters double/hyphenated last name: 'Priya Sharma-Mehta' | Name parser split() may truncate or reject hyphenated/spaces in last name |
| A037 | User enters phone number with country code: '+91 98765 43210' or '+919876543210' | Country code extraction might drop prefix digits or fail length checks |
| A038 | User provides an email address instead of phone number when asked for phone | Agent might capture email format as phone or loop repeatedly without warning |
| A039 | User changes room and date in the same message: 'change to hostage on Saturday' | Compound change request might update only one field and keep others stale |
| A040 | User provides location 'Whitefield' but date is empty, then asks to check JP Nagar | State machine transition from CHECKING_AVAILABILITY might trigger slot lookup without date |
| A041 | User provides name lowercase: 'priya sharma' | Memory name parser needs to titlecase automatically for external APIs |
| A042 | User says 'I want to cancel' then 'no wait, keep it' in the same message | Cancel regex might trigger cancellation despite negative correction |
| A043 | User reschedules a booking that was already cancelled | System might attempt to modify cancellation on Kreeda and crash |
| A044 | User specifies participant range: '4 to 6 people' | Parser picks upper/lower bound, but may not trigger correct slot capacity checks |
| A045 | User enters first name 'Arjun' and when asked for last name, says 'just Arjun' | Agent must allow single-word names if user explicitly has no last name |
| A046 | User says 'book for 4' then 'change to 0 people' | Zero validation should happen on set_field before availability check |
| A047 | User specifies date as 'day after tomorrow' (relative term substring issue) | Must check 'day after tomorrow' before 'tomorrow' in extraction loop |
| A048 | User gives past date '1 January' (already past) | Year-bump check must notify user it was moved to next year rather than doing it silently |
| A049 | User gives date in MM/DD format '12/25' | Ambiguity check needs to warn user or safely resolve to Dec 25 |
| A050 | User gives phone number starting with 5 (invalid Indian prefix) | Standard Indian phone validation rejects it; agent must explain validation rule |
| A051 | User says 'book a room' then 'cancel reservation' (synonym matching) | Reservation keyword must trigger cancel handler |
| A052 | User reschedules to 5 PM without explicit change keyword | State WAITING_FOR_SLOT treats 5 PM as slot selection, reschedule needs change indicators |
| A053 | User changes room from Bomb Defusal to Prison Break (different location availability) | Room change must clear cached availability and selected slot |
| A054 | User reschedules to a date with no slots twice in a row | State WAITING_FOR_ALT_DATE might close session on second check, dead-ending user |
| A055 | User says 'yes' to food option and agent thinks it is slot selection | Affirmative follow-up should not trigger slot selection when FAQ is active |
| A056 | User says 'yes' to cancellation policy explanation | Affirmative follow-up should not trigger slot selection when policy is active |
| A057 | User says 'no' to booking details and agent loops | Negative consent should transition back to editing fields instead of looping |
| A058 | User reschedules and wants to keep same slot on a new date | Availability of slot must be checked on the new date |
| A059 | User reschedules and slot is no longer available on the new date | Agent must report slot unavailable and show available slots for new date |
| A060 | User cancels booking and then immediately asks to book a new one | Closed state must allow restart via reset_booking_state() |
| A061 | User enters email first then phone | Expected field flow should handle fields out of order |
| A062 | User enters phone first then email | Expected field flow should handle fields out of order |
| A063 | User says 'actually, change location to JP Nagar' | Location check must update memory and re-run availability |
| A064 | User changes participants from 4 to 15 mid-booking | Capacity checks must rerun immediately to update slot availability |
| A065 | User says 'just myself' (1 participant) | Solo bookings are valid but capacity checks/warnings should handle it safely |
| A066 | User says '1000 people' (excessive count) | Excessive numbers must be rejected before checking provider slots |
| A067 | User dictates phone number as words: 'nine eight...' | ASR normalization must parse spelled-out numbers |
| A068 | User dictates participants as words: 'four people' | ASR normalization must parse spelled-out counts |
| A069 | User books Murder Mystery at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A070 | User books Hostage at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A071 | User books Bomb Defusal at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A072 | User books Classified at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A073 | User books Prison Break at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A074 | User books Curse of the Pharaoh at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A075 | User books Forbidden Forest at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A076 | User books Wizarding Championship at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A077 | User books Undercover at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A078 | User books Hostage at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A079 | User books Prison Break at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A080 | User books Bomb Defusal at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A081 | User books Curse of the Pharaoh at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A082 | User books Curse of the Pharaoh at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A083 | User books Classified at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A084 | User books Classified at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A085 | User books Prison Break at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A086 | User books Undercover at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A087 | User books Undercover at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A088 | User books Wizarding Championship at Koramangala (unsupported room-location combo) | Availability check must report room unavailable at Koramangala |
| A089 | User books Wizarding Championship at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A090 | User books Forbidden Forest at JP Nagar (unsupported room-location combo) | Availability check must report room unavailable at JP Nagar |
| A091 | User books Forbidden Forest at Whitefield (unsupported room-location combo) | Availability check must report room unavailable at Whitefield |
| A092 | User reschedules room to Hostage during booking flow | Room field update must clear select slot and check availability |
| A093 | User reschedules date to next Sunday during booking flow | Date field update must clear select slot and check availability |
| A094 | User reschedules time to 3 PM during booking flow | Time field update must clear select slot and check availability |
| A095 | User changes name to Priya mid-booking | Name field update must overwrite stored first_name/customer_name |
| A096 | User changes phone to 9876543210 mid-booking | Phone field update must overwrite stored phone number |
| A097 | User changes age group to kids mid-booking | Age group field update must overwrite stored age_group |
| A098 | User changes participants to 6 mid-booking | Participants field update must clear slot and check capacity |
| A099 | User changes location to Whitefield mid-booking | Location field update must clear slot and check availability |
| A100 | User changes room to Curse of the Pharaoh mid-booking | Room field update must clear slot and check availability |

---

## CATEGORY B — Recommendation Scenarios

### B-FIRST-TIMER

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| B001 | First-timer asks for recommendation with zero context (no location, no group size) | Recommendation engine may return a room not available at all locations → hallucinated recommendation |
| B002 | First-timer says "this is my first time" — bot captures "First Time" as customer_name | `_is_plausible_name("First Time")` → "first" and "time" are in `rejected_words` → rejected. But pattern `this is\s+([A-Za-z]+)` captures "my" — filtered. Safe. |
| B003 | First-timer says "beginner friendly please" during booking slot-waiting state | Treated as unrecognized by booking state machine; challenge_preference extracted but no room change triggered |
| B004 | Recommendation given for "Murder Mystery at JP Nagar" when it's not available there (only at Kora/Whitefield/JP Nagar) — per knowledge base it IS at JP Nagar | Knowledge base says "offered at Koramangala, Whitefield, and JP Nagar" — verify recommendation engine matches this |

### B-EXPERT-PLAYER

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| B005 | Expert player asks for "hardest room" and is recommended "Bomb Defusal" which is only at Whitefield, but user is at JP Nagar | Recommendation engine returns option without location verification; user led to a room not at their chosen location |
| B006 | Expert says "we've done all your rooms" | No handler for this; agent may recommend the same rooms again |

### B-KIDS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| B007 | Parent books for a group of kids aged 5 | Murder Mystery recommended as "beginner-friendly and great for a wide range of ages" — no age-gating communicated to parent |
| B008 | "Kids party at Whitefield" — recommendation engine | `get_demo_answer` has special kids handler only for location/where/which queries; may fall through to generic |
| B009 | Kids group of 20 at a single room | Capacity limitation response triggered — no redirect to multi-room coordination is given for kids |

### B-CORPORATE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| B010 | Corporate inquiry with 50 employees asks for a room recommendation | Recommendation engine returns "Multi-room event coordination" but gives no concrete next step |
| B011 | Corporate event but user says "escape room" in their message | ConversationManager detects `escape_room_inquiry` via keyword and routes away from corporate flow, clearing corporate fields |

### B-BIRTHDAY

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| B012 | Birthday booking, user asks "which room should we pick?" after filling all birthday fields | Agent in qualification flow (birthday_party intent); recommendation engine runs but recommended_option may overwrite birthday qualification fields |
| B013 | User says "it's my son's birthday" — system may detect birthday_party intent and also extract "son" as participant count context | `_extract_participants` pattern: `"son and X friends" + 1` — no "and N friends" here, so safe |

| B014 | Expert asks for a room with high physical activity or puzzles | Recommendation engine must match category 'expert' and difficulty 'hard' |
| B015 | Kids group wants a room with scary elements (horror theme) | Recommendation engine must verify if room is age-appropriate (horror theme restrictions) |
| B016 | Corporate team of 15 wants a highly collaborative room | Multi-room event recommendation must be triggered for sizes > room capacity |
| B017 | First-timer wants to book a room that has the highest rating | Recommendation engine must use rating metadata if available |
| B018 | User asks 'which room is suitable for senior citizens?' | Need age-appropriate filtration in the recommendation logic |
| B019 | User asks 'which room has the least walking/physical strain?' | Physical effort constraints should be tracked in room attributes |
| B020 | First-timer asks for a room recommendation at Koramangala | Recommendation engine must filter rooms available at Koramangala first |
| B021 | Expert asks for a room recommendation at Whitefield | Recommendation engine must filter rooms available at Whitefield first |
| B022 | Group of kids asks for room recommendation at JP Nagar | Recommendation engine must filter rooms available at JP Nagar first |
| B023 | Corporate group asks for room recommendation at JP Nagar | Recommendation engine must filter rooms available at JP Nagar first |
| B024 | Birthday group asks for room recommendation at Koramangala | Recommendation engine must filter rooms available at Koramangala first |
| B025 | User asks for a room with sci-fi theme | Recommendation engine must match genre tags |
| B026 | User asks for a room with fantasy/magic theme | Recommendation engine must match genre tags |
| B027 | User asks for a room with crime/mystery theme | Recommendation engine must match genre tags |
| B028 | User asks for a room with historical/egyptian theme | Recommendation engine must match genre tags |
| B029 | User asks for a room with prison/escape theme | Recommendation engine must match genre tags |
| B030 | User asks for a room with military/bomb theme | Recommendation engine must match genre tags |
| B031 | User asks for recommendation for 2 players (couples) | Recommendation engine must check if 2-player mode is supported |
| B032 | User asks for recommendation for a large family with kids and grandparents | Mixed age recommendation must default to medium/easy rooms |
| B033 | User asks for 'most popular room' without location | Popularity ranking must be consistent across locations |
| B034 | User asks for 'cheapest escape room' | Pricing information is not available; agent must handle budget questions |
| B035 | User asks 'do you recommend Hostage for a 6-year-old?' | Age restrictions or warnings must be shared for high-intensity rooms |
| B036 | User asks 'is Murder Mystery too hard for beginners?' | Difficulty level explanations must be shared for recommended options |
| B037 | User asks 'which room has a live actor?' | Actor presence tags must be verified in the knowledge base |
| B038 | User asks 'which room is wheelchair accessible?' | Accessibility attributes must be verified in the knowledge base |
| B039 | User asks for recommendation in Hindi/English mixed: 'hame acchi room suggest karo' | ASR/Language robust intent detection for recommendations |
| B040 | User asks for recommendation while booking flow is already active | Must present recommendation without disrupting current slot selection memory |
| B041 | User rejects first recommendation: 'anything else besides Murder Mystery?' | Engine must suggest secondary option from matching categories |
| B042 | User rejects second recommendation: 'do you have a third option?' | Engine must suggest tertiary option or list all available rooms at location |
| B043 | User asks 'which room has the highest success rate?' | Success rate metrics should be explained or generic stats provided |
| B044 | User asks 'which room is the newest?' | Newest room information must be confirmed from knowledge base |
| B045 | User asks 'which room is the longest?' | All games are 50 minutes; must explain duration is standard |
| B046 | User asks for recommendation for a team building event | Must recommend rooms with high collaboration tags |
| B047 | User asks for recommendation for a school excursion | Must recommend age-appropriate and high-capacity rooms |
| B048 | User asks 'is Curse of the Pharaoh scary?' | Scare factor information must be explained clearly |
| B049 | User asks 'is Prison Break physically demanding?' | Physical demands of rooms must be explained clearly |
| B050 | User asks 'what room do you recommend for a stag party?' | Stag party/large group recommendations must suggest competitive options |

---

## CATEGORY C — Availability Scenarios

### C-SHOW-ALL

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| C001 | User asks "show me all available slots" — expects a list | BookingAgent formats slots as natural text; if only one slot available, only that one is shown — user doesn't know it's exhaustive |
| C002 | User asks "what slots do you have on Saturday?" when current date is stored as "Saturday" (already relative) | `_extract_preferred_date("Saturday")` returns "Saturday"; `_normalise_date("Saturday")` converts to next Saturday — may be wrong if today is Saturday |
| C003 | Zero slots returned from simulator | `_handle_availability_check` goes to `_STATE_WAITING_FOR_ALT_DATE` — no stale slot data |
| C004 | Simulator returns slots but all fail capacity check | `capacity_supported=False` triggers capacity limitation response — user told to call events team |

### C-EVENING

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| C005 | User asks "only evening slots please" | No filter for time-of-day in availability check; all slots returned; agent doesn't filter by evening |
| C006 | User asks "what's available after 6 PM" | Same as C005 — no time-of-day filter in orchestrator or booking agent |

### C-CHEAPEST-SLOT

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| C007 | User asks "which slot is cheapest?" | `is_price_question` returns True; agent says it doesn't have exact pricing; doesn't explain slot pricing may differ |
| C008 | User says "I have a budget of 2000 rupees" during availability check | Budget captured in `budget_range`; no pricing comparison made against slots; agent ignores budget constraint |

### C-EARLIEST-SLOT

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| C009 | User says "I want the earliest slot" | No slot ranking logic; `_format_slots` just lists them; user expected to pick; agent doesn't identify earliest |
| C010 | User says "first available" | Word "first" is in `continuation_words` → routing stays on booking_agent; falls to `_handle_slot_selection`; `_extract_slot("first available")` returns "" → "Sorry, I didn't catch that" loop |

---

## CATEGORY D — FAQ Interruption Scenarios

### D-PARKING

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| D001 | User asks "parking?" as a one-word message mid-booking | `is_booking_faq("parking?")` returns True; `get_demo_answer` returns parking info; `_append_booking_resume` appends slot question — works, but booking state not advanced |
| D002 | User asks "is there parking near JP Nagar?" | `parking` keyword triggers FAQ; `get_demo_answer` returns all-locations parking info including JP Nagar — but doesn't confirm specific directions |
| D003 | User asks parking then immediately asks for parking AGAIN | Both handled as FAQ; no loop detection; may loop indefinitely |

### D-FOOD

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| D004 | User asks "do you have vegan options?" | `get_demo_answer("vegan options")` — no "vegan" in knowledge base; returns "" → falls to general response; may hallucinate |
| D005 | User asks about food during qualification when `food_required` is the next expected field | `is_interruption` may be False (not a rules/rooms FAQ); `merge_message` extracts `food_required=True`; qualification advances — correct but order may confuse user |
| D006 | User says "yes" after agent explains food options mid-booking | `is_affirmative_follow_up` triggered; `pending_policy_explanation` is False; affirmative treated as booking confirmation → may prematurely advance booking |

### D-DURATION

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| D007 | User asks "how long is the game?" | `get_demo_answer("how long is the game")` — no "how long" handler in `demo_knowledge.py`; returns "" → agent may not answer |
| D008 | User asks "what is the duration?" | Same as D007 — `duration` is in `faq_keywords` in ConversationManager so routes to inbound_agent as FAQ; `_get_faq_or_knowledge_answer` must handle it |

### D-ARRIVAL-TIME

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| D009 | User asks "when should we arrive?" | No "arrival" specific handler; `arrival` triggers FAQ routing; knowledge base must handle "arrival guidance" |
| D010 | User asks "what time should we reach?" | "reach" not in FAQ keywords; may be misrouted |

### D-CANCELLATION-POLICY

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| D011 | User asks cancellation policy 5 times in a row | `pending_policy_explanation` toggled each time; no loop-break; escalation not triggered by repetition alone (only angry/frustrated sentiment) |
| D012 | User asks "what is your refund policy?" | `refund policy` in `cancellation_policy_terms` → FAQ handled correctly |
| D013 | User says "what if I cancel?" during WAITING_FOR_PHONE state | Cancellation policy FAQ handled; `_append_booking_resume` adds phone prompt — works, but policy not fully explained (offer to explain is given) |

| D014 | User asks 'can we bring outside food?' mid-booking | Outside food policy must be explained from knowledge base |
| D015 | User asks 'is there a cafe inside?' mid-booking | In-house cafe/restaurant details must be explained |
| D016 | User asks 'what happens if we are late?' mid-booking | Late arrival policy must be explained |
| D017 | User asks 'do we get lockers for our bags?' mid-booking | Locker facility details must be explained |
| D018 | User asks 'are mobile phones allowed inside the room?' mid-booking | Mobile phone policy must be explained |
| D019 | User asks 'is there a drinking water facility?' mid-booking | Water availability details must be explained |
| D020 | User asks 'do you have restrooms?' mid-booking | Restroom availability must be explained |
| D021 | User asks 'is there air conditioning in the rooms?' mid-booking | AC availability must be explained |
| D022 | User asks 'do we need to wear specific shoes?' mid-booking | Footwear policy must be explained |
| D023 | User asks 'is parking free?' mid-booking | Parking pricing/details must be explained |
| D024 | User asks 'can pregnant women play?' mid-booking | Pregnancy safety advisory must be explained |
| D025 | User asks 'is the room locked from outside?' mid-booking | Safety exit/lock policy must be explained |
| D026 | User asks 'can we get hints during the game?' mid-booking | Hint system rules must be explained |
| D027 | User asks 'is there a discount for students?' mid-booking | Student discount policy must be explained |
| D028 | User asks 'do you offer corporate discounts?' mid-booking | Corporate pricing/discount policy must be explained |
| D029 | User asks 'can we pay by cash?' mid-booking | Payment modes supported must be explained |
| D030 | User asks 'do you accept UPI payments?' mid-booking | UPI payment support must be explained |
| D031 | User asks 'can we get a GST invoice?' mid-booking | Tax invoice availability must be explained |
| D032 | User asks 'what is the price per person on weekdays?' mid-booking | Weekday pricing details must be explained |
| D033 | User asks 'what is the price per person on weekends?' mid-booking | Weekend pricing details must be explained |
| D034 | User asks 'do kids have a lower ticket price?' mid-booking | Child pricing policy must be explained |
| D035 | User asks 'is it safe for people with claustrophobia?' mid-booking | Claustrophobia safety advice must be explained |
| D036 | User asks 'what is the minimum age to play alone?' mid-booking | Minimum age rules must be explained |
| D037 | User asks 'can we add more players after booking?' mid-booking | Adding players post-booking policy must be explained |
| D038 | User asks 'is there a waiting area for non-players?' mid-booking | Waiting room details must be explained |
| D039 | User asks 'do you have wheelchair access at Koramangala?' mid-booking | Location-specific accessibility must be explained |
| D040 | User asks 'do you have wheelchair access at Whitefield?' mid-booking | Location-specific accessibility must be explained |
| D041 | User asks 'do you have wheelchair access at JP Nagar?' mid-booking | Location-specific accessibility must be explained |
| D042 | User asks 'can we take photos inside the room?' mid-booking | Photography rules must be explained |
| D043 | User asks 'is there a dress code?' mid-booking | Dress code details must be explained |
| D044 | User asks 'do you have gift vouchers?' mid-booking | Gift voucher availability must be explained |
| D045 | User asks 'how do I redeem a gift card?' mid-booking | Gift card redemption steps must be explained |
| D046 | User asks 'is there CCTV inside the room?' mid-booking | CCTV monitoring safety details must be explained |
| D047 | User asks 'what is the safety emergency exit procedure?' mid-booking | Emergency exit procedures must be explained |
| D048 | User asks 'do you have alcohol policy?' mid-booking | Alcohol restriction policy must be explained |
| D049 | User asks 'do you offer birthday decorations?' mid-booking | Birthday setup FAQ must be explained |
| D050 | User asks 'do you have a space for cake cutting?' mid-booking | Cake cutting space FAQ must be explained |

---

## CATEGORY E — Escalation Scenarios

### E-REFUND

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| E001 | User says "I need a refund" | REFUND_REQUEST regex matches; escalation triggered; response says "I'll connect you with our team" — but no handoff tracking ID generated |
| E002 | User says "give me my money back" | `want|need\s+my\s+money\s+back` matches; escalation fired |
| E003 | User says "I deserve a refund" | "deserve" not in REFUND_REQUEST pattern; escalation NOT triggered; user may be left without help |
| E004 | Refund escalation triggered but `HandoffSummaryAgent.generate()` throws an exception | Exception caught in `_enrich_conversation_result`; escalation dict set to `{escalate: False}` — ESCALATION SILENTLY DROPPED |
| E005 | User asks for refund on a booking that has no booking_ref in memory | Escalation triggered but handoff_summary has no booking reference — agent hands off with empty booking details |

### E-COMPLAINT

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| E006 | User says "I'm very unhappy with the service" | `sentiment_agent` may detect "unhappy" as frustrated; after 2 consecutive frustrated turns, escalation fires |
| E007 | User says "this is ridiculous" | "ridiculous" not in escalation triggers; no escalation; frustration may not be captured |
| E008 | User sends an all-caps message: "I AM VERY ANGRY" | SentimentAgent must detect anger from text; "angry" in lowered("I AM VERY ANGRY") → `_detect_sentiment` returns "negative" but not "angry" — escalation may not fire |

### E-SAFETY

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| E009 | User reports "someone is hurt inside the room" | "hurt" in SAFETY_REQUEST → immediate escalation with "alert on-site staff" message |
| E010 | User says "my friend fainted" | "fainted" not in SAFETY_REQUEST regex; safety escalation NOT triggered — user left without urgent response |
| E011 | User says "there's smoke in the room" | "smoke" in SAFETY_REQUEST → escalation fires |
| E012 | Safety escalation triggered but dispatch() exception handler catches it | Exception handler sets `escalate=False` and returns generic fallback — SAFETY ESCALATION SILENTLY DROPPED |

### E-HUMAN-REQUEST

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| E013 | User says "get me a human" | `human representative` pattern — "get me a human" not matched (requires "speak/talk/connect/transfer to" or "I want/need a human") |
| E014 | User says "I want to talk to your manager" | `talk\s+(?:me\s+)?(?:to|with)\s+(?:a\s+)?manager` — "talk to your manager" has "your" not "a/the" → NOT matched |
| E015 | User says "please connect me with someone" | "someone" not in pattern (requires human/person/manager/supervisor/agent) → NOT matched |
| E016 | User says "I need human help" | `I\s+need\s+a?\s+human` — "human help" not matched (requires bare "human" or "person/manager/supervisor/agent") |
| E017 | Human request escalated but agent is in `_STATE_BOOKING_CONFIRMED` | Escalation triggers `should_handoff=True` and changes `next_agent` to `escalation_agent` — but booking state machine is at CONFIRMED; state confusion |

| E018 | User says 'your bot is useless let me talk to someone' | Human request escalation must trigger |
| E019 | User says 'I want to speak with a human support agent' | Human request escalation must trigger |
| E020 | User says 'transfer me to a manager right now' | Human request escalation must trigger |
| E021 | User says 'is there a real person I can chat with?' | Human request escalation must trigger |
| E022 | User says 'I demand to speak to your supervisor' | Human request escalation must trigger |
| E023 | User says 'help me connect with a customer representative' | Human request escalation must trigger |
| E024 | User says 'get me help from a real human' | Human request escalation must trigger |
| E025 | User says 'I need to report an accident inside the escape room' | Safety emergency escalation must trigger |
| E026 | User says 'someone has fainted in the hostage room' | Safety emergency escalation must trigger |
| E027 | User says 'there is a medical emergency here' | Safety emergency escalation must trigger |
| E028 | User says 'some player collapsed and is unconscious' | Safety emergency escalation must trigger |
| E029 | User says 'a player has cut their hand and is bleeding' | Safety emergency escalation must trigger |
| E030 | User says 'the ceiling is leaking and it looks dangerous' | Safety emergency escalation must trigger |
| E031 | User says 'I am having a panic attack and cannot breathe' | Safety emergency escalation must trigger |
| E032 | User says 'I want a refund for my cancelled game' | Refund request escalation must trigger |
| E033 | User says 'please refund the money you charged me' | Refund request escalation must trigger |
| E034 | User says 'I was double charged and need my money back' | Refund request escalation must trigger |
| E035 | User says 'I deserve compensation for the bad service' | Refund/Complaint escalation must trigger |
| E036 | User says 'give me a full refund immediately' | Refund request escalation must trigger |
| E037 | User says 'cancel my booking and return my money' | Refund request escalation must trigger |
| E038 | User says 'I will sue you if I don't get a refund' | Refund/Legal complaint escalation must trigger |
| E039 | User says 'this service is terrible and I want to file a complaint' | Complaint escalation must trigger |
| E040 | User says 'I am extremely unhappy with how I was treated' | Complaint escalation must trigger |
| E041 | User says 'I want to speak to someone about a bad experience' | Complaint/Human request escalation must trigger |
| E042 | User says 'your booking system stole my slot and money' | Complaint/Refund escalation must trigger |
| E043 | User says 'I want to complain about the JP Nagar staff' | Complaint escalation must trigger |
| E044 | User says 'your assistant is keeping me in an infinite loop' | Complaint escalation must trigger |
| E045 | User says 'this is ridiculous support connect me to a human' | Human request escalation must trigger |
| E046 | User says 'I need to talk to a supervisor about my reservation' | Human request escalation must trigger |
| E047 | User says 'please transfer me to a human assistant' | Human request escalation must trigger |
| E048 | User says 'someone broke their leg inside the room' | Safety emergency escalation must trigger |
| E049 | User says 'there is fire and smoke in the building' | Safety emergency escalation must trigger |
| E050 | User says 'a player has had a seizure' | Safety emergency escalation must trigger |

---

## CATEGORY F — Memory Stress Scenarios

### F-CHANGING-PARTICIPANTS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| F001 | User changes participants 3 times: 4 → 8 → 6 → 10 | Each change triggers `participants_changed`; PARTICIPANT_OVERRIDE logged; availability re-run each time — high API call risk |
| F002 | User changes participants from a number to a range: "actually between 8 and 12" | `_extract_participant_range` captures (8, 12); `participants` set to 12; `participants_min=8, participants_max=12` — but earlier single value 4 was logged in audit_trail |
| F003 | User says "we reduced to just 2" during WAITING_FOR_PHONE state | `participants_changed=True`; `set_field` called; availability auto-re-run? NO — booking agent only re-runs when `is_change_request=True`. But `any_field_changed` needs change keyword. "reduced" not in change keywords → participants updated but availability not re-run → slot may no longer be valid |

### F-CHANGING-ROOM

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| F004 | User changes room 4 times during slot-waiting state | Each room change triggers `is_change_request`; slot cleared; availability re-run; no loop detection |
| F005 | User changes room to one not available at their location | Availability check returns `available=False`; goes to `_STATE_WAITING_FOR_ALT_DATE` → user stuck asking for a date when the real problem is room/location mismatch |
| F006 | User changes room after booking confirmed | `_STATE_BOOKING_CONFIRMED` — booking agent runs `_handle_post_booking`; room change is `is_change_request=True` but state overrides to `_handle_post_booking` only if `actionable_state_input=False` |

### F-CHANGING-DATE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| F007 | User changes date 3 times before selecting a slot | Each date change triggers availability re-run; audit_trail grows; no user confirmation required for date changes once `is_change_request=True` |
| F008 | User changes date after slot selected but before name collected | `date_changed=True`; `_clear_selected_slot()` called; selected_slot wiped; goes back to slot waiting — name/phone already captured preserved |
| F009 | User says "tomorrow" as a date, then next day says "tomorrow" again | Two different days; `_extract_preferred_date("tomorrow")` returns "Tomorrow"; `_normalise_date("Tomorrow")` resolves to different days — second call may not detect date has changed |

---

## CATEGORY G — ASR / Location Recognition Failures

### G-JP-NAGAR-VARIANTS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| G001 | "JP Nagar" typed correctly | Exact match → "JP Nagar" ✓ |
| G002 | "J P Nagar" (with space in J P) | `jp` + `nagar` both present → "JP Nagar" ✓ |
| G003 | "JPNagar" (no spaces) | `jp` is not in `lowered("jpnagar")` as a standalone token; `nagar` not isolated → `_extract_location_fuzzy` must match |
| G004 | "Jeep Nagar" (Whisper ASR error) | "jeep" vs "jp nagar"; difflib `get_close_matches("jeep nagar", ["jp nagar"], cutoff=0.55)` — ratio may be below threshold; "jeep" ≠ "jp"; NOT matched → user must repeat |
| G005 | "Jibbing" (severe mishearing) | difflib ratio too low; _KNOWN table doesn't have this → NOT matched |
| G006 | "Jay Pee Nagar" (spelled out) | "jay" and "pee" and "nagar" — "nagar" alone gives difflib match to "jp nagar"; may work via word-level fallback |
| G007 | "JP" alone | `jp` in lowered but `nagar` not present; exact match fails; "jp" alone in difflib vs "jp nagar" — probably not matched |

### G-WHITEFIELD-VARIANTS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| G008 | "Whitefield" typed correctly | Exact match ✓ |
| G009 | "White field" (two words) | `whitefield` not in lowered("white field"); fuzzy: "white field" vs "whitefield" — `_extract_location_fuzzy` tries difflib; ratio("white field", "whitefield") ≈ 0.88 → match ✓ |
| G010 | "Wightfield" (common typo) | difflib("wightfield", "whitefield") ≈ 0.94 → match ✓ |
| G011 | "White Shield" (Whisper error) | In `_KNOWN` table → "Whitefield" ✓ |
| G012 | "Wider Field" | difflib ratio may be sufficient; not in _KNOWN; uncertain |
| G013 | "Vighfield" | difflib test needed; probably too low |

### G-KORAMANGALA-VARIANTS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| G014 | "Koramangala" correctly | Exact ✓ |
| G015 | "Koramangla" (typo) | In `_KNOWN` → "Koramangala" ✓ |
| G016 | "Koramangalar" (extra 'r') | In `_KNOWN` → "Koramangala" ✓ |
| G017 | "Kora" alone | difflib("kora", "koramangala") — ratio very low; NOT matched → user must clarify |
| G018 | "Korman Gala" | Not in _KNOWN; difflib fuzzy uncertain |

| G019 | User says 'Koramangala' with heavy Indian accent: 'Kora-mangla' | Fuzzy matching should resolve to Koramangala |
| G020 | User says 'Whitefield' as 'White field' with a long pause | Fuzzy matching should resolve to Whitefield |
| G021 | User says 'JP Nagar' as 'Jay Pee Nagar' (phonetic spelling) | Fuzzy matching should resolve to JP Nagar |
| G022 | User says 'JP Nagar' as 'J.P. Nagar' with periods | Normalization must strip punctuation and resolve to JP Nagar |
| G023 | User says 'Koramangala' as 'Koramangalar' (ASR extra char) | Fuzzy matching should resolve to Koramangala |
| G024 | User says 'Whitefield' as 'Wightfield' (ASR spelling typo) | Fuzzy matching should resolve to Whitefield |
| G025 | User says 'Whitefield' as 'White Shield' (ASR severe error) | Known replacements mapping should resolve to Whitefield |
| G026 | User says 'JP Nagar' as 'Jeep Nagar' (ASR error) | ASR error map or fuzzy matching should resolve to JP Nagar |
| G027 | User says 'JP Nagar' as 'Jibbing' (ASR extreme error) | Extreme mishearing must be handled (ask user to repeat, no false positive) |
| G028 | User says 'JP Nagar' as 'JP' (abbreviation) | Abbreviation should not match JP Nagar without context/clarification |
| G029 | User says 'Koramangala' as 'Kora' (abbreviation) | Abbreviation should not match Koramangala without context/clarification |
| G030 | User says 'Koramangala' as 'Kora mangala' (ASR split word) | Fuzzy matching should resolve to Koramangala |
| G031 | User says 'Whitefield' as 'Wider Field' (ASR split word) | Fuzzy matching should resolve to Whitefield |
| G032 | User says 'Koramangala' as 'Goramangala' (ASR initial consonant error) | Fuzzy matching should resolve to Koramangala |
| G033 | User says 'Whitefield' as 'Vitefield' (ASR phonetic error) | Fuzzy matching should resolve to Whitefield |
| G034 | User says 'JP Nagar' as 'GB Nagar' (ASR phonetic error) | Fuzzy matching should resolve to JP Nagar |
| G035 | User says 'Murder Mystery' as 'Muder Mystery' (ASR typo) | Fuzzy room matcher should resolve to Murder Mystery |
| G036 | User says 'Prison Break' as 'Prison Brake' (ASR phonetic typo) | Fuzzy room matcher should resolve to Prison Break |
| G037 | User says 'Bomb Defusal' as 'Bomb Diffusal' (ASR typo) | Fuzzy room matcher should resolve to Bomb Defusal |
| G038 | User says 'Classified' as 'Classy Fied' (ASR split) | Fuzzy room matcher should resolve to Classified |
| G039 | User says 'Hostage' as 'Ostage' (ASR dropped H) | Fuzzy room matcher should resolve to Hostage |
| G040 | User says 'Curse of the Pharaoh' as 'Curse of Pharaoh' (ASR dropped word) | Fuzzy room matcher should resolve to Curse of the Pharaoh |
| G041 | User says 'Wizarding Championship' as 'Wizard Championship' (ASR typo) | Fuzzy room matcher should resolve to Wizarding Championship |
| G042 | User says 'Forbidden Forest' as 'Forbidden Forrest' (ASR typo) | Fuzzy room matcher should resolve to Forbidden Forest |
| G043 | User says 'Undercover' as 'Under cover' (ASR split) | Fuzzy room matcher should resolve to Undercover |
| G044 | User says phone number '9 8 7 6 5 4 3 2 1 0' with spaces between every digit | Phone regex must strip spaces and extract 10 digits |
| G045 | User says phone number with ASR background noise: 'my number is 9876543210 ok' | Phone regex must isolate the 10-digit number |
| G046 | User dictates phone number with word digits: 'nine eight seven six five four three two one zero' | Normalization must convert digit words to numbers before extraction |
| G047 | User says participants 'for people' instead of 'four people' (ASR soundalike) | ASR correction must translate common soundalikes to digits |
| G048 | User says participants 'to people' instead of 'two people' (ASR soundalike) | ASR correction must translate common soundalikes to digits |
| G049 | User says participants 'ate people' instead of 'eight people' (ASR soundalike) | ASR correction must translate common soundalikes to digits |
| G050 | User says 'book for free' instead of 'book for three' (ASR soundalike) | ASR correction must check context for soundalikes |

---

## CATEGORY H — Large Group Scenarios

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| H001 | Group of 8 | Within normal capacity; should work fine |
| H002 | Group of 10 | `capacity_supported` check depends on room max; may trigger capacity_limitation_response |
| H003 | Group of 15 | Likely fails capacity; response suggests events team — no callback or email capture |
| H004 | Group of 20 | Same as H003; `capacity_supported=False`; goes to `_STATE_CLOSED` — user stuck |
| H005 | Group of 50 | `int(50)` passed to `_check_availability_operational`; `_slot_has_capacity` checks `int(available) >= 50` — will always fail for standard rooms |
| H006 | Group of 1 | `_extract_participants` matches; valid; may be solo booking — agent doesn't flag or verify |
| H007 | Group of 1000 | `\d{1,4}` regex supports up to 9999; 1000 extracted; capacity check fails; state closes — no meaningful error |
| H008 | "Just me" with no number | No number extracted; `participants` stays empty; agent asks for group size again — correct |
| H009 | "About 10 people" (approximate) | `_capture_bare_count` captures 10 via `around|about` prefix regex — works |
| H010 | "Between 8 and 12" mid-booking | Range extracted; `participants=12`; min/max stored — availability re-run if change request |

| H011 | Corporate customer asks to book a room for 12 players | Must check capacity limit and suggest splitting or events team |
| H012 | Corporate customer asks to book a room for 14 players | Must check capacity limit and suggest splitting or events team |
| H013 | Corporate customer asks to book a room for 16 players | Must check capacity limit and suggest splitting or events team |
| H014 | Corporate customer asks to book a room for 18 players | Must check capacity limit and suggest splitting or events team |
| H015 | Corporate customer asks to book a room for 22 players | Must check capacity limit and suggest splitting or events team |
| H016 | Corporate customer asks to book a room for 25 players | Must check capacity limit and suggest splitting or events team |
| H017 | Corporate customer asks to book a room for 30 players | Must check capacity limit and suggest splitting or events team |
| H018 | Corporate customer asks to book a room for 35 players | Must check capacity limit and suggest splitting or events team |
| H019 | Corporate customer asks to book a room for 40 players | Must check capacity limit and suggest splitting or events team |
| H020 | Corporate customer asks to book a room for 45 players | Must check capacity limit and suggest splitting or events team |
| H021 | Corporate customer asks to book a room for 60 players | Must check capacity limit and suggest splitting or events team |
| H022 | Corporate customer asks to book a room for 75 players | Must check capacity limit and suggest splitting or events team |
| H023 | Corporate customer asks to book a room for 80 players | Must check capacity limit and suggest splitting or events team |
| H024 | Corporate customer asks to book a room for 90 players | Must check capacity limit and suggest splitting or events team |
| H025 | Corporate customer asks to book a room for 100 players | Must check capacity limit and suggest splitting or events team |
| H026 | Corporate customer asks to book a room for 150 players | Must check capacity limit and suggest splitting or events team |
| H027 | Corporate customer asks to book a room for 200 players | Must check capacity limit and suggest splitting or events team |
| H028 | Corporate customer asks to book a room for 250 players | Must check capacity limit and suggest splitting or events team |
| H029 | Corporate customer asks to book a room for 300 players | Must check capacity limit and suggest splitting or events team |
| H030 | Corporate customer asks to book a room for 500 players | Must check capacity limit and suggest splitting or events team |
| H031 | User asks 'can we split 12 people into two rooms?' | Agent should explain splitting is supported and show slots |
| H032 | User asks 'how many players can play in JP Nagar at once?' | Agent should explain location total capacity across all rooms |
| H033 | User asks 'how many players can play in Whitefield at once?' | Agent should explain location total capacity across all rooms |
| H034 | User asks 'how many players can play in Koramangala at once?' | Agent should explain location total capacity across all rooms |
| H035 | User asks 'do you have packages for corporate events?' | State transition to corporate/birthday qualification |
| H036 | User asks 'what corporate team building activities do you have?' | State transition to corporate qualification |
| H037 | User asks 'can we book the entire venue for a private event?' | Private venue booking inquiry must be routed to events team |
| H038 | User asks 'do you provide food and catering for corporate events?' | Food FAQ/Catering details must be explained |
| H039 | User asks 'can we get corporate billing/invoice?' | GST/invoice FAQ must be explained |
| H040 | User says 'we have a team of 15' and agent doesn't prompt for corporate details | Must qualify corporate size (> 8) and offer callback |
| H041 | User says 'we are a group of 20' and then changes to 'actually 8 people' | Must transition from corporate/events flow back to standard room booking |
| H042 | User says 'we are 12 people' and asks for recommendation | Should recommend multi-room options or high-capacity rooms |
| H043 | User asks 'what rooms fit a group of 8?' | Must list rooms with max capacity >= 8 |
| H044 | User asks 'what rooms fit a group of 10?' | Must explain that splitting is recommended as max room capacity is 8 |
| H045 | User asks 'can we fit 9 people in one room?' | Must explain max room capacity is 8 and recommend splitting |
| H046 | User asks 'can we book 3 rooms simultaneously?' | Simultaneous multi-room booking must transition to events team callback |
| H047 | User asks 'do you have corporate rates for weekdays?' | Corporate package weekday pricing must be explained |
| H048 | User asks 'do you have corporate rates for weekends?' | Corporate package weekend pricing must be explained |
| H049 | User asks 'is there a conference room for corporate meetings?' | Conference room availability FAQ must be explained |
| H050 | User asks 'do you have parking space for a corporate bus?' | Bus parking space FAQ must be explained |

---

## CATEGORY I — Contradictory Input Scenarios

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| I001 | "Book for tomorrow" then "no, next week" then "actually next month" | Tomorrow captured → "no" triggers pending_confirmation rejection? No, "no" in `is_booking_consent_no` if consent pending; otherwise treated as continuation. `actual next month` not extracted (no "next month" handler) |
| I002 | "Whitefield" then "actually Koramangala" | `location_changed=True`; `is_change_request=True` (has "actually"); location updated; availability re-run |
| I003 | "4 people" then "8 people" without change keyword | `participants_changed=True`; `any_field_changed=True`; BUT `is_change_request` requires change keyword OR `participants_changed` — it IS in the list: `or participants_changed` → change request fires ✓ |
| I004 | User gives two contradictory locations in same message: "Whitefield or Koramangala" | `_extract_location` returns first match found iterating LOCATIONS tuple: `("Koramangala", "Whitefield", "JP Nagar")` — Koramangala wins; user's preference for Whitefield ignored |
| I005 | User says "book for this Friday" then "no wait, Saturday" | "no" goes to pending_confirmation handler if pending; otherwise "Saturday" extracts as new date; `date_changed=True`; if in booking state, triggers re-run |
| I006 | User says "adults" then "kids" then "adults again" | Each change: `age_group` field update; `set_field` detects change; `is_explicit_change` check — "adults again" has no indicator → `correction` type; if not expected_field, deferred to `pending_confirmation` |
| I007 | User says "JP Nagar" then "Whitefield" then "JP Nagar" | Third change should trigger `pending_confirmation` for low-confidence correction (no keyword); if in booking state, `_clear_selected_slot` called each time |
| I008 | User provides 3 different phone numbers | Only the first valid one is stored (set_field returns False on subsequent calls if `is_explicit_change` is False and field is phone) |

---

## CATEGORY J — Malicious / Weird Inputs

### J-NONSENSE

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| J001 | User sends empty string | `message.strip()` is empty; `if not message: continue` in text loop — not sent to dispatch |
| J002 | User sends only whitespace "   " | Same as J001 |
| J003 | User sends a single character "a" | `_is_garbage_transcript("a")` → True (less than 2 meaningful words) → "Sorry, I didn't catch that" |
| J004 | User sends a number "42" | `_is_garbage_transcript("42")` → `re.fullmatch(r"\d{1,5}", "42")` → not garbage (valid count); captured as participants |
| J005 | User sends SQL injection: "'; DROP TABLE sessions; --" | No SQL; memory is JSON files; input sanitized by context extraction; treated as garbage transcript |
| J006 | User sends HTML: "<script>alert('XSS')</script>" | No HTML rendering; treated as text; `_extract_name` may attempt match; garbage transcript |
| J007 | User sends a 10,000 character message | No length limit on message processing; may cause slow regex evaluation |
| J008 | User sends emoji only: "😀🎉" | `re.findall(r"[a-z0-9]+", lowered)` returns [] → garbage transcript; "Sorry, I didn't catch that" |
| J009 | User sends a phone number formatted as "98765 43210" (with space) | Phone regex joins: `text.replace(" ", "")` → "9876543210" → matched ✓ |
| J010 | User sends "book" as their name | `_is_plausible_name("Book")` → "book" in rejected_words → rejected → "Sorry, could you share your name again?" |

### J-INTERRUPTIONS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| J011 | User switches from escape room inquiry to birthday inquiry mid-qualification | `dispatch()` detects topic switch; fields cleared; new qualification started — good but user must restart entirely |
| J012 | User asks "what is your name?" to the agent | No handler; knowledge base may not have this; may return generic fallback |
| J013 | User says "that's not what I asked" | In `MISUNDERSTANDING` list → escalation triggered after match — even on first occurrence (no minimum count) |
| J014 | User says "you keep asking the same question" | In `MISUNDERSTANDING` list → immediate escalation |
| J015 | User asks the same FAQ 10 times in a row | `failed_answer_count` increments on each "failed" response; FAQ answers are not failures; count stays near 0; no loop detection for repetitive FAQ |
| J016 | User sends random topic: "What is the capital of France?" | No knowledge base entry; `get_demo_answer` returns ""; knowledge retriever may attempt to answer; may hallucinate if OpenAI enabled |
| J017 | User says "yes" when no question was asked | `is_booking_consent_yes` check: `booking_consent_pending` must be True; otherwise treated as continuation word — routes to booking_agent continuation |
| J018 | User says "I already told you" | In MISUNDERSTANDING list → immediate escalation — may fire prematurely on first complaint |
| J019 | User alternates between English and Hindi (code-switching) | No multi-language support; Hindi words treated as garbage or unrecognized; ASR may produce garbage transcript |
| J020 | User dictates their phone number as words: "nine eight seven six five four three two one zero" | `normalize_number_words` only handles 1-20; 7-digit+ numbers as words are not normalized → phone extraction fails |

### J-REPEATED-QUESTIONS

| ID | Scenario | Attack Vector |
|----|----------|---------------|
| J021 | Agent asks "How many people are joining?" 5 times in a row | No answer loop limit; `_waiting_for` stays "participants"; agent will loop indefinitely |
| J022 | Agent asks for phone number 3 times due to bad ASR | `_invalid_response_for_expected_phone` returns standard message; no progressive rephrasing; may frustrate user |
| J023 | Agent asks for location 4 times | Location validation may reject "Whitfield" (typo not in _KNOWN); fuzzy match may still catch it; but if fuzzy fails → 4 identical rejections |

---

## Summary of Critical Attack Surfaces

| Category | Scenario Count | Estimated Critical Issues |
|----------|---------------|--------------------------|
| A. Booking Flow | 100 | 18 |
| B. Recommendations | 50 | 8 |
| C. Availability | 10 | 4 |
| D. FAQ Interruptions | 50 | 6 |
| E. Escalations | 50 | 12 |
| F. Memory Stress | 9 | 5 |
| G. ASR Failures | 50 | 10 |
| H. Large Groups | 50 | 10 |
| I. Contradictory Inputs | 8 | 4 |
| J. Malicious/Weird | 23 | 3 |
| **Total** | **400** | **80** |

---

*Generated by Principal QA Engineer — Red Team Audit, 2026-06-22*
