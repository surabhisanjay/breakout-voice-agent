# Test Results

Date: 2026-06-23

## New Regression Suite

Command:

```bash
.venv/bin/pytest -q tests/test_conversation_state_hardening.py
```

Result:

```text
35 passed in 0.15s
```

Coverage added:
- Date capture while location is missing
- Time capture while location is missing
- Persisted time reuse after location arrives
- Date, room, and slot correction false-positive escalation prevention
- FAQ interruptions for parking, cancellation, food, arrival, directions, dress code, age restrictions, and duration
- Repeated FAQ interruption during qualification
- Spoken phone parsing with `double`, `triple`, `oh`, and `zero`
- Name correction overwrite
- Qualification side-field persistence
- Slot invalidation on date and room correction

## Failed Subset Recheck

Command:

```bash
.venv/bin/pytest -q tests/test_final_scenario_matrix.py::test_50_faq_interruptions_answer_and_preserve_booking tests/test_hardening_pass.py::test_regression_modifications_and_audit_trail tests/test_mvp_stabilization.py::test_jp_nagar_booking_executes_cart_and_booking_end_to_end tests/test_mvp_stabilization.py::test_cart_is_reused_after_last_name_api_failure
```

Result:

```text
53 passed in 0.19s
```

## Full Suite

Command:

```bash
.venv/bin/pytest -q
```

Result:

```text
819 passed in 4.57s
```

