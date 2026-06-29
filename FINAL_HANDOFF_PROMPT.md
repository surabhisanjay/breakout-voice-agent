# Final Handoff Prompt (Candidate)

Create a factual, action-oriented handoff from supplied conversation state. Never infer or fill missing values. Use `not_collected`, `unspecified`, or `none`.

Preserve exact names, phone digits, participant ranges, dates, times, amounts, provider errors, and identifiers. Keep the summary to 2-4 sentences. Do not expose this internal summary to the customer.

```json
{
  "customer_name": "",
  "phone": "",
  "intent": "",
  "location": "",
  "room": "",
  "date": "",
  "selected_slot": "",
  "participants": "",
  "booking_id": "",
  "booking_ref": "",
  "sentiment": "neutral|positive|frustrated|distressed|urgent",
  "priority": "P0|P1|P2",
  "escalation_reason": "",
  "actions_attempted": [],
  "provider_error": "",
  "conversation_summary": ""
}
```

The summary must say what the customer wants, what is verified, what remains open, what was attempted, and why a human is needed.
