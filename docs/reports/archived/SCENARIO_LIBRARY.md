# Scenario Library

All business facts below are conditional on verified state. Bracketed values must come from memory, knowledge, or a live provider response.

## 01. Explicit Human Request
**Category:** Human request  
**Trigger:** `Let me speak to a person.`  
**Expected AI Response:** `Of course. I'll pass this to the team with the details you've already shared.`  
**Escalation Required:** Yes  
**Handoff Summary:** Known identity/contact, intent, booking context, and explicit human request.

## 02. Manager Request
**Category:** Human request  
**Trigger:** Customer asks for a manager.  
**Expected AI Response:** `Understood. I'll escalate this to a manager with the conversation context.`  
**Escalation Required:** Yes  
**Handoff Summary:** Complaint, attempted resolution, sentiment, booking/payment identifiers.

## 03. AI Objection
**Category:** Human request  
**Trigger:** `I don't want to talk to a bot.`  
**Expected AI Response:** `Understood. I'll connect you with the team.`  
**Escalation Required:** Yes  
**Handoff Summary:** Intent and all collected fields; do not debate identity.

## 04. Repeated Service Failure
**Category:** Angry customer  
**Trigger:** Customer says they have called repeatedly without resolution.  
**Expected AI Response:** `I'm sorry you've had to repeat this. I'll escalate it with the history already attached.`  
**Escalation Required:** Yes  
**Handoff Summary:** Prior contacts, unresolved issue, desired outcome, sentiment.

## 05. Profanity From Frustration
**Category:** Angry customer  
**Trigger:** Profanity directed at the service, not a threat.  
**Expected AI Response:** `I hear how frustrating this has been. Let me get someone who can resolve it directly.`  
**Escalation Required:** Yes  
**Handoff Summary:** Complaint and one attempted resolution.

## 06. Abusive Caller
**Category:** Angry customer  
**Trigger:** Repeated personal abuse after a boundary is stated.  
**Expected AI Response:** `I want to help, but I can't continue with abusive language. I can hand this to the team.`  
**Escalation Required:** Yes  
**Handoff Summary:** Intent, abuse boundary, safety/threat indicators.

## 07. Injury On Site
**Category:** Safety issue  
**Trigger:** Someone is injured at a venue.  
**Expected AI Response:** `Please alert the on-site staff immediately. I'm escalating this as urgent now.`  
**Escalation Required:** Yes  
**Handoff Summary:** Venue, room, injury report, caller contact, timestamp.

## 08. Breathing Or Panic Emergency
**Category:** Safety issue  
**Trigger:** Trouble breathing, panic, or unresponsive participant.  
**Expected AI Response:** `Please get on-site staff or emergency help immediately. I'm escalating this as urgent.`  
**Escalation Required:** Yes  
**Handoff Summary:** Verbatim symptoms, exact venue/room if known, contact.

## 09. Fire Or Smoke
**Category:** Safety issue  
**Trigger:** Fire, smoke, burning smell, or immediate physical danger.  
**Expected AI Response:** `Move to safety and alert emergency/on-site staff now. I'm escalating immediately.`  
**Escalation Required:** Yes  
**Handoff Summary:** Location, danger reported, timestamp, caller details.

## 10. Accessibility Before Booking
**Category:** Accessibility request  
**Trigger:** Wheelchair, mobility, sensory, or other accommodation question.  
**Expected AI Response:** `I don't want to guess about access for that room. I'll have the venue confirm what they can support.`  
**Escalation Required:** Yes  
**Handoff Summary:** Requested accommodation, venue/date/room, contact preference.

## 11. Accessibility During Visit
**Category:** Accessibility request  
**Trigger:** Customer needs an immediate accommodation on site.  
**Expected AI Response:** `Please speak to the on-site team now; I'll flag the request to them as urgent.`  
**Escalation Required:** Yes  
**Handoff Summary:** Current venue/room, accommodation needed, status.

## 12. Unknown Business Question
**Category:** Unknown question  
**Trigger:** Answer is absent from verified sources.  
**Expected AI Response:** `I don't have a verified answer for that, so I won't guess. I can ask the team to confirm.`  
**Escalation Required:** Yes  
**Handoff Summary:** Exact question and related context.

## 13. Franchise Or Employment Query
**Category:** Unknown question  
**Trigger:** Franchise, job, vendor, or partnership inquiry.  
**Expected AI Response:** `That sits outside bookings. I'll route the question to the right team.`  
**Escalation Required:** Yes  
**Handoff Summary:** Inquiry type, organization/name, contact, exact request.

## 14. Language Request
**Category:** Escalation required  
**Trigger:** Customer requests a language the agent cannot reliably use.  
**Expected AI Response:** `I'll connect you with someone who can continue in [language].`  
**Escalation Required:** Yes  
**Handoff Summary:** Language, intent, known booking context.

## 15. No Slots For Chosen Room
**Category:** Booking unavailable  
**Trigger:** Live provider returns no valid slots.  
**Expected AI Response:** `That room has no verified slots for [date]. I can check another date or a different live room at [location].`  
**Escalation Required:** No  
**Handoff Summary:** Not applicable unless customer requests human help.

## 16. Requested Slot Disappears
**Category:** Booking unavailable  
**Trigger:** Selected slot is absent on recheck.  
**Expected AI Response:** `That slot is no longer available. The closest verified options are [provider slots]. Which works?`  
**Escalation Required:** No  
**Handoff Summary:** If escalated, include requested and alternative slots.

## 17. No Inventory At Location
**Category:** Booking unavailable  
**Trigger:** Room is not returned in live location inventory.  
**Expected AI Response:** `[room] isn't in the current [location] inventory. I can check verified alternatives there.`  
**Escalation Required:** No  
**Handoff Summary:** Requested room/location and provider result.

## 18. Large Group Exceeds Room
**Category:** Large group  
**Trigger:** Participants exceed selected live product capacity.  
**Expected AI Response:** `That room supports up to [verified capacity], so it won't fit [participants] together. I can check other live options or involve the events team.`  
**Escalation Required:** No  
**Handoff Summary:** Escalate for manual split/quote with count, age, date, location.

## 19. Uncertain Group Range
**Category:** Large group  
**Trigger:** `We may be 10 to 15.`  
**Expected AI Response:** `I'll keep it as 10 to 15 for now. Capacity has to work for the upper end; which location are you considering?`  
**Escalation Required:** No  
**Handoff Summary:** Preserve range exactly if escalated.

## 20. Participant Count Corrected
**Category:** Booking modification  
**Trigger:** `We are actually seven.`  
**Expected AI Response:** `Updated to seven. I'll recheck capacity and availability with that count.`  
**Escalation Required:** No  
**Handoff Summary:** Old/new count and recheck result if provider fails.

## 21. Birthday Package Inquiry
**Category:** Birthday party  
**Trigger:** Customer asks about a birthday package.  
**Expected AI Response:** `Sure. Is this mainly an escape-room booking or a broader party package?`  
**Escalation Required:** No  
**Handoff Summary:** For event-team handoff: age, count, location, date, package interests.

## 22. Custom Birthday Decor
**Category:** Birthday party  
**Trigger:** Custom theme, decor, catering, or entertainment request.  
**Expected AI Response:** `That needs a custom event check. I'll pass the details to the events team rather than promise it here.`  
**Escalation Required:** Yes  
**Handoff Summary:** Custom request, budget if volunteered, count/date/location/contact.

## 23. Birthday Booking Language
**Category:** Birthday party  
**Trigger:** `Book the birthday package.`  
**Expected AI Response:** `I'll treat that as a package inquiry, not an escape-room game name. What date are you planning?`  
**Escalation Required:** No  
**Handoff Summary:** Event requirements if package cannot transact live.

## 24. Corporate Team Outing
**Category:** Corporate booking  
**Trigger:** Company offsite or team-building inquiry.  
**Expected AI Response:** `Got it. Roughly how many people are joining?`  
**Escalation Required:** No  
**Handoff Summary:** Company, group size, location, date, format.

## 25. Complex Corporate Quote
**Category:** Corporate booking  
**Trigger:** Multi-room, catering, facilitation, or custom quote.  
**Expected AI Response:** `That needs a current custom quote. I'll send the requirements to the events team.`  
**Escalation Required:** Yes  
**Handoff Summary:** Full requirements and constraints, without invented estimate.

## 26. Hybrid Or Virtual Event
**Category:** Corporate booking  
**Trigger:** Remote/hybrid participants.  
**Expected AI Response:** `I can record that as a hybrid-event inquiry and have the team verify current formats. How many people are remote?`  
**Escalation Required:** Yes  
**Handoff Summary:** On-site/remote counts, date/time zone, desired outcome.

## 27. Wrong Location Selected
**Category:** Wrong location  
**Trigger:** Chosen room is unavailable in selected live branch inventory.  
**Expected AI Response:** `[room] isn't returned for [location]. I can check where it is currently offered or show verified rooms at [location].`  
**Escalation Required:** No  
**Handoff Summary:** Requested combination and live lookup result.

## 28. Customer Changes Location
**Category:** Wrong location  
**Trigger:** `Make it Whitefield instead.`  
**Expected AI Response:** `Changed to Whitefield. I'll recheck the room, capacity, pricing, and slots there.`  
**Escalation Required:** No  
**Handoff Summary:** Old/new branch and provider recheck result if needed.

## 29. Change Date Midway
**Category:** Booking modification  
**Trigger:** Customer replaces a previously supplied date.  
**Expected AI Response:** `Updated to [new normalized date]. I'll refresh availability for that date.`  
**Escalation Required:** No  
**Handoff Summary:** Old/new date and booking reference if modifying an existing booking.

## 30. Change Room Midway
**Category:** Booking modification  
**Trigger:** Customer chooses a different room.  
**Expected AI Response:** `Changed to [room]. I'll verify it at [location] and refresh availability.`  
**Escalation Required:** No  
**Handoff Summary:** Old/new room, current booking fields.

## 31. Change Slot Midway
**Category:** Booking modification  
**Trigger:** Customer chooses a different time before creation.  
**Expected AI Response:** `I'll switch to [time] after verifying that slot is still available.`  
**Escalation Required:** No  
**Handoff Summary:** Old/new slot and provider error if any.

## 32. Modify Confirmed Booking
**Category:** Booking modification  
**Trigger:** Customer has a booking reference and wants a change.  
**Expected AI Response:** `Please share the booking reference. I'll check what can be changed without assuming the outcome.`  
**Escalation Required:** No  
**Handoff Summary:** Reference, requested change, policy/provider result if escalation needed.

## 33. Cancellation Information
**Category:** Cancellation  
**Trigger:** Customer asks what the cancellation policy is.  
**Expected AI Response:** Give the current verified policy only; do not cancel anything.  
**Escalation Required:** No  
**Handoff Summary:** Not applicable.

## 34. Cancellation Action
**Category:** Cancellation  
**Trigger:** Customer asks to cancel a real booking.  
**Expected AI Response:** `Please share the booking reference so I can verify the booking before any action.`  
**Escalation Required:** No  
**Handoff Summary:** Reference, customer identity, requested action, provider outcome.

## 35. Cancellation Dispute
**Category:** Refund request  
**Trigger:** Customer disputes a fee or policy application.  
**Expected AI Response:** `I won't decide a disputed refund here. I'll send the booking and timing details to the team for review.`  
**Escalation Required:** Yes  
**Handoff Summary:** Reference, booking date, cancellation time, disputed amount/policy.

## 36. Refund Request
**Category:** Refund request  
**Trigger:** `I want my money back.`  
**Expected AI Response:** `I'll have the team verify the booking and applicable policy; I can't promise an approval.`  
**Escalation Required:** Yes  
**Handoff Summary:** Reference, payment details, reason, desired outcome.

## 37. Duplicate Charge
**Category:** Refund request  
**Trigger:** Customer reports being charged twice.  
**Expected AI Response:** `I won't guess about a payment discrepancy. I'm escalating this for an immediate check.`  
**Escalation Required:** Yes  
**Handoff Summary:** Amounts, timestamps, payment identifiers, booking reference/contact.

## 38. Charged Without Booking
**Category:** Technical failure  
**Trigger:** Payment taken but no booking reference.  
**Expected AI Response:** `I'm escalating this now so the payment and booking can be checked together.`  
**Escalation Required:** Yes  
**Handoff Summary:** Payment proof/details, attempted slot, customer contact, timestamp.

## 39. Availability Provider Failure
**Category:** Technical failure  
**Trigger:** Kreeda availability errors or times out.  
**Expected AI Response:** `I can't verify live availability right now, so I won't claim a slot. I can retry or hand this to the team.`  
**Escalation Required:** No  
**Handoff Summary:** Request payload context and sanitized provider error if escalated.

## 40. Cart Creation Failure
**Category:** Technical failure  
**Trigger:** Cart call fails after slot selection.  
**Expected AI Response:** `The booking hasn't been created. I've kept your details and can retry without starting over.`  
**Escalation Required:** No  
**Handoff Summary:** Context, selected slot, sanitized error, actions attempted.

## 41. Booking Creation Failure
**Category:** Technical failure  
**Trigger:** Final provider call returns an error.  
**Expected AI Response:** Ask only for a missing recoverable field; otherwise explain that confirmation has not occurred and escalate/retry.  
**Escalation Required:** No  
**Handoff Summary:** Cart/order IDs, sanitized error, complete preserved context.

## 42. Single-Word Name
**Category:** Booking creation  
**Trigger:** Customer supplies one name only.  
**Expected AI Response:** `Thanks, I have [first name]. What's the last name for the booking?`  
**Escalation Required:** No  
**Handoff Summary:** Not applicable.

## 43. Noisy Phone Number
**Category:** Booking creation  
**Trigger:** ASR yields an invalid/ambiguous phone number.  
**Expected AI Response:** `I didn't get a valid number. Please say the digits once more.`  
**Escalation Required:** No  
**Handoff Summary:** If handed off, mark phone unconfirmed.

## 44. Premature Confirmation Challenge
**Category:** Booking creation  
**Trigger:** Customer asks whether booking is confirmed before an ID exists.  
**Expected AI Response:** `Not yet. It's confirmed only when the booking system returns a reference.`  
**Escalation Required:** No  
**Handoff Summary:** Current missing fields/provider status.

## 45. Confirmed Booking
**Category:** Booking creation  
**Trigger:** Provider success includes persisted booking ID/reference.  
**Expected AI Response:** `Your booking is confirmed. Your reference is [booking_reference].`  
**Escalation Required:** No  
**Handoff Summary:** Not applicable; persist identifiers.

## 46. FAQ During Booking
**Category:** Unknown question  
**Trigger:** Parking, duration, rules, or food question during active booking.  
**Expected AI Response:** Answer from verified knowledge, then ask only the next missing booking field.  
**Escalation Required:** No  
**Handoff Summary:** Only if answer is not verified.

## 47. Price During Booking
**Category:** Unknown question  
**Trigger:** `What is the price?` during active booking.  
**Expected AI Response:** Give a provider-derived total; otherwise say exact pricing is not verified and continue the booking without describing the room.  
**Escalation Required:** No  
**Handoff Summary:** Price question and current booking context if manual quote needed.

## 48. Discount Or Promotion
**Category:** Unknown question  
**Trigger:** Discount, offer, coupon, promo, deal, or membership.  
**Expected AI Response:** `I don't have a verified promotion for this booking. The team can confirm any current offer.`  
**Escalation Required:** No  
**Handoff Summary:** Booking context and requested promotion if routed.

## 49. Customer Pauses
**Category:** Booking modification  
**Trigger:** `Let me check and call back.`  
**Expected AI Response:** `No problem. Your conversation details can stay here; nothing is confirmed without a booking reference.`  
**Escalation Required:** No  
**Handoff Summary:** Not applicable.

## 50. Caller Says Stop
**Category:** Escalation required  
**Trigger:** `Stop`, `pause`, or clear withdrawal of consent.  
**Expected AI Response:** `Okay, I'll stop here.`  
**Escalation Required:** No  
**Handoff Summary:** None unless a separate safety/payment trigger exists.
