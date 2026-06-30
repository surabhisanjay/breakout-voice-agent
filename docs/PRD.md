# Breakout Escape Rooms: Conversational Robustness & Quality Requirements
*(PRD Section 8.4 Extension)*

## Overview

While basic booking mechanics are critical, the primary risk for a customer-facing voice agent is failure to handle messy, non-linear, and emotional real-world conversations. This document defines the conversational quality expectations and robustness testing parameters for the Breakout voice agent.

---

## 8.4.1 Conversational Recovery Requirements

The AI agent must display natural, adaptive behavior when dealing with common conversational friction points:

1. **Incomplete or Contradictory Information**: Accept estimates or ambiguous parameters gracefully rather than forcing immediate resolution.
2. **Interruptions & Topic Switches**: Answer mid-conversation questions (e.g., parking, amenities) and return to the main flow seamlessly.
3. **Explaining Qualification Motives**: Reduce friction by explaining *why* information (like email or phone number) is needed.
4. **De-escalation & Frustration Management**: Use empathy cushions when customers complain about pricing or past bad experiences.
5. **Graceful Redirection**: Politely redirect requests for unsupported services (e.g., custom props or external catering) while keeping the lead warm.
6. **ASR / Transcription Recovery**: Clarify name/spelling or slot selections when transcriptions appear garbled.
7. **Repetition Prevention**: Keep track of questions asked and fields already provided to avoid frustrating the customer with repeated questions.
8. **Resilient Flow Continuation**: Return to the booking funnel immediately after answering off-topic FAQs.

---

## 8.4.2 Conversational Quality Metrics

We track the following robustness metrics across simulated and live test suites:

* **Objection Recovery Rate**: % of sessions where the customer initially raises objections (e.g., pricing, safety, email sharing) but successfully completes or continues the booking.
* **Edge Case Recovery Rate**: % of sessions where the agent returns to the main task after handle interruptions, off-topic questions, or sarcasm.
* **Repetition Rate**: Average number of repeated questions per conversation (Target: < 0.2).
* **Customer Frustration Recovery Rate**: % of frustrated conversations that return to a neutral or positive sentiment after empathy is applied.
* **Clarification Efficiency**: Average number of clarifying turns needed before correct intent resolution.
* **Conversational Naturalness Score**: Human/LLM-evaluation rating of Empathy, Flow, and Sales effectiveness.

---

## 8.4.3 Red Team Scenario Suite

The agent is evaluated against 11 critical conversational edge cases:

### Scenario 1: Doesn't understand escape rooms
* **Customer**: "I have no idea what this even is."
* **Success Criteria**: Explains the escape room concept, builds excitement, and moves back to qualification.

### Scenario 2: Angry about pricing
* **Customer**: "Why is this so expensive?"
* **Success Criteria**: Acknowledges pricing concern, briefly explains the high production value/live hosting, and steers back toward booking.

### Scenario 3: Wants horror
* **Customer**: "I only want ghost rooms."
* **Success Criteria**: Offers alternatives or lists appropriate theme options (e.g., Zombie or haunted rooms) rather than a hard rejection.

### Scenario 4: Late arrival
* **Customer**: "We're thirty minutes away."
* **Success Criteria**: Shows empathy, explains policy (reducing room time to avoid impacting back-to-back bookings), and offers rescheduling options.

### Scenario 5: Refuses qualification
* **Customer**: "Why do you need my email?"
* **Success Criteria**: Explains that the email is for booking confirmation and game instructions, then proceeds with the flow.

### Scenario 6: Contradictory information
* **Customer**: "We're six people. Actually twelve. Maybe ten."
* **Success Criteria**: Stores the estimate (e.g., 10 players), accepts the uncertainty, and guides the customer forward.

### Scenario 7: Demanding discounts
* **Customer**: "Just give me a discount."
* **Success Criteria**: Mentions any available group rates or off-peak promotions, and avoids arguing or repeating boilerplate.

### Scenario 8: Sarcasm
* **Customer**: "So if we fail, you keep us locked forever?"
* **Success Criteria**: Recognizes the joke, reassures them they can exit at any time, and maintains a lighthearted tone.

### Scenario 9: Previous bad experience
* **Customer**: "Last time was terrible."
* **Success Criteria**: Apologizes, validates their experience, offers to collect details or route to the events team, and attempts to rebuild trust.

### Scenario 10: Non-linear conversation
* **Customer**: "We have ten people, maybe tomorrow, one person hates puzzles, and I don't know if we're coming."
* **Success Criteria**: Retains context (10 people, tomorrow), addresses concerns, and guides them step-by-step.

### Scenario 11: Extreme interruption test
* **Customer**: "Actually before that, do you have parking? Also my friend is vegetarian. Also can I bring a cake? Also are you open on Tuesday?"
* **Success Criteria**: Answers each question (parking, food, cake, hours) and returns to the active booking details (e.g., "By the way, you mentioned booking for ten people tomorrow. Shall we continue with that?").

---

## 8.4.4 Turn Evaluation Rubric

Every conversation turn is evaluated on a **14-point scale**:

| Metric | Score Range | Description |
| :--- | :--- | :--- |
| **Intent identified** | 0 - 2 | Correctly determines booking, inquiry, or FAQ intent. |
| **Empathy** | 0 - 2 | Validates customer emotions, objections, or concerns using appropriate cushions. |
| **Question answered** | 0 - 2 | Directly addresses policies or details cleanly and concisely. |
| **Context retained** | 0 - 2 | Remembers state information; does not ask repetitive questions for already-gathered fields. |
| **Naturalness** | 0 - 2 | Uses short, human-sounding turns (under 45 words) without robotic boilerplates. |
| **Flow advancement** | 0 - 2 | Ends with exactly one clear, active question or call to action to guide the customer. |
| **Policy compliance** | 0 - 2 | Adheres strictly to safety regulations, corporate routing, and pricing guidelines. |

**Target Quality Standard**: A minimum score of **12 / 14** points is required for a turn to be considered high quality.
