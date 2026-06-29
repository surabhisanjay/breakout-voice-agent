# Unsafe Content Review

## Block From Production

| Source | Claim | Risk | Safe handling |
|---|---|---|---|
| Claude conversation playbook, pricing | Four or more customers automatically receive 10% off | No discount appears in the authoritative source manifest | Say discounts must be verified; do not quote one |
| Claude transcript examples | Students receive 10% off with ID | Unsupported promotion | Route to current pricing/offer source |
| Claude examples | Discounts may be stacked or optimized | Unsupported eligibility/promotion logic | Human verification only |
| Claude playbook/examples | Exact tax-inclusive totals such as Rs 944 or Rs 4,300 | Source prices say `From`; tax inclusion and arithmetic rules are not established | Quote only a live checkout/provider total |
| Claude playbook | Rs 2,800 booking total | Invented scenario total, not a provider result | Parameterize from checkout response |
| Claude birthday examples | Free 15-minute cake/photo space | Not present in the supplied authoritative source manifest | Do not promise space or price |
| Claude booking/payment examples | Payment links expire in 15 minutes | Not established by current provider contract | Repeat expiry only if the generated link supplies it |
| Claude examples | UPI always works on the payment link | Unsupported payment-method claim | Ask the payment provider or human team |
| Claude availability examples | Whitefield is wide open, Koramangala has evenings, JP Nagar is full | Fabricated live availability | Query Kreeda for the requested date/room/location |
| Claude examples | A 7 PM slot is open or a slot can be held while details are collected | Availability and hold are transient; no hold result is shown | Say only what the provider response proves |
| Claude personality/playbook | Always arrive 15-20 minutes early | No authoritative operational instruction found in the reviewed sources | Include only when supplied by venue/provider policy |
| Claude examples | A confirmation text will arrive immediately | Unsupported notification guarantee | Return the actual booking reference and describe only observed notifications |
| Claude playbook | A human will call back within an hour | Unsupported SLA | State that the request is being handed off without a deadline |
| Claude large-group examples | Automatic two-room competitive split | Room adjacency, simultaneous slots, inventory, and package support are not proven | Offer human event planning after live checks |
| Claude examples | No room holds more than eight | Static source contains room-specific maxima of seven or eight; live inventory can differ | Validate the chosen live product, not a global slogan |
| Claude examples | Children count exactly like adults for price/headcount | Unsupported universal policy | Verify age and pricing rules for the selected product |
| Claude examples | Rooms are dim but not pitch black and staff are always nearby | Unsupported safety/operational claim | Escalate accessibility or safety specifics to the venue |
| Claude recommendation text | Murder Mystery has a good solve rate | No solve-rate evidence | Remove |
| Claude playbook | Murder Mystery is the universal default/best first-timer anchor | Catalog facts do not establish universal quality or availability at a requested slot | Filter live inventory and use stated preferences |
| Claude event examples | Specific package contents/upgrade availability are guaranteed | Source sheet is descriptive, not a live purchasable package contract | Treat as an inquiry and obtain a current quote |

## Source Caveats

The repository source manifest supports static room locations, listed min/max capacities, age bands, `From` prices, event descriptions, parking, operating hours, and graduated cancellation/rescheduling text. Even those facts can become stale. Pricing, inventory, availability, checkout totals, promotions, and booking status require live confirmation.

## Production Rule

No response may convert a transcript claim, illustrative example, or prompt sentence into business truth. Facts must originate in structured state, a designated knowledge source, or a successful provider response for the current request.
