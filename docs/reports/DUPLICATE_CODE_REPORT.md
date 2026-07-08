# Duplicate Code Report

**Generated:** 2026-07-01
**Audit Method:** Repository-wide reference search across all `.py` files
**Result: No files were removed. All files are referenced.**

---

## Audit Methodology

For every source file in `src/`, a grep was run across the entire repository (excluding `.venv` and `__pycache__`) to count external references. Files with zero external references were flagged for manual review.

```bash
find src/ -name "*.py" ! -name "__init__.py" | while read f; do
  base=$(basename "$f" .py)
  count=$(grep -rn "$base" --include="*.py" . | grep -v ".venv" | grep -v "__pycache__" | grep -v "$f" | wc -l)
  echo "$count $f"
done | sort -n
```

---

## Results: All Files Referenced

| References | File | Status |
|---|---|---|
| 1 | `src/memory/session_manager.py` | ✅ Used — imported by `src/memory/__init__.py` |
| 2 | `src/config/settings.py` | ✅ Used |
| 2 | `src/knowledge/knowledge_retriever.py` | ✅ Used |
| 2 | `src/logger/transcript_logger.py` | ✅ Used |
| 2 | `src/services/gpt_reasoner.py` | ✅ Used |
| 2 | `src/services/slot_filler.py` | ✅ Used |
| 2 | `src/voice/stt/voice_input.py` | ✅ Used |
| 3 | `src/config/constants.py` | ✅ Used |
| 4 | `src/agents/evaluation_agent.py` | ✅ Used |
| 4 | `src/agents/follow_up_agent.py` | ✅ Used |
| 4 | `src/services/question_classifier.py` | ✅ Used |
| 5 | `src/agents/conversation_intelligence_agent.py` | ✅ Used |
| 6 | `src/config/env_loader.py` | ✅ Used |
| 6 | `src/core/conversation_modes.py` | ✅ Used |
| 6 | `src/services/conversation_guard.py` | ✅ Used |
| 8 | `src/knowledge/demo_knowledge.py` | ✅ Used |
| 8 | `src/voice/tts/voice_output.py` | ✅ Used |
| 9 | `src/agents/handoff_summary_agent.py` | ✅ Used |
| 9 | `src/core/handoff_generator.py` | ✅ Used |
| 9 | `src/integrations/kreeda/breakout_booking_provider.py` | ✅ Used |
| 10 | `src/services/recommendation_engine.py` | ✅ Used |
| 10 | `src/services/venue_policy.py` | ✅ Used |
| 13 | `src/integrations/kreeda/agent_contract_provider.py` | ✅ Used |
| 14 | `src/core/agent_response.py` | ✅ Used |
| 14 | `src/orchestration/router.py` | ✅ Used |
| 15 | `src/orchestration/conversation_manager.py` | ✅ Used |
| 15 | `src/services/intent_detector.py` | ✅ Used |
| 16 | `src/agents/sentiment_agent.py` | ✅ Used |
| 17 | `src/integrations/langgraph/booking_node.py` | ✅ Used |
| 18 | `src/integrations/kreeda/breakout_api.py` | ✅ Used |

---

## Duplicate Utilities

**Result: None found.**

No duplicate utility functions exist across the codebase. Each service module has a distinct responsibility.

---

## Duplicate Prompts

**Result: None found in production code.**

The `prompts/` directory contains 4 files:
- `breakout_personality_prompt.txt` — active personality prompt loaded by `ResponseComposer`
- `conversation_playbook.txt` — active playbook loaded by `ResponseComposer`
- `inbound_prompt.txt` — active inbound prompt loaded by `InboundAgent`
- `transcript_examples.json` — active few-shot examples loaded at startup

Archived historical prompt drafts are in `docs/reports/archived/`:
- `FINAL_ESCALATION_PROMPT.md` — historical draft (not loaded by any code)
- `FINAL_HANDOFF_PROMPT.md` — historical draft (not loaded by any code)
- `FINAL_PERSONALITY_PROMPT.md` — historical draft (not loaded by any code)
- `FINAL_SCENARIO_PROMPT.md` — historical draft (not loaded by any code)

---

## Duplicate Services

**Result: None found.**

Each service has a distinct responsibility:
- `intent_detector.py` — intent classification
- `slot_filler.py` — entity extraction
- `recommendation_engine.py` — room recommendation
- `question_classifier.py` — question type classification
- `conversation_guard.py` — loop/abuse detection
- `gpt_reasoner.py` — GPT reasoning layer
- `wati_client.py` — WhatsApp message delivery
- `venue_policy.py` — venue rules and constraints

---

## Duplicate Integrations

**Finding: `integrations/langgraph_booking_node.py` vs `src/integrations/langgraph/booking_node.py`**

These two files are related but NOT identical:

| Aspect | `integrations/langgraph_booking_node.py` | `src/integrations/langgraph/booking_node.py` |
|---|---|---|
| Import style | Absolute (`from src.agents...`) | Relative (`from ...agents...`) |
| `booking_node_handler` logic | Simpler — no prerequisite validation | Full — validates required fields before booking |
| Purpose | Root-level module for test imports | Package-level canonical implementation |

**Decision: Both files are kept.**

Reason: `integrations/langgraph_booking_node.py` is imported by:
- `tests/test_langgraph_booking_node.py`
- `tests/test_booking_orchestration.py`

Removing it would break 2 test files. Moving it would require updating test imports (forbidden per task constraints).

**Recommendation for future:** Consolidate by updating test imports to use `src.integrations.langgraph.booking_node` and removing the root-level file.

---

## Duplicate Helper Functions

**Result: None found.**

All helper functions are localized to their respective modules.

---

## Unused Files

**Result: None found.**

All `.py` files have at least one external reference.

---

## Unused Configs

**Result: All config fields in `.env.example` are used.**

| Variable | Consumer |
|---|---|
| `OPENAI_API_KEY` | `ResponseComposer`, `InboundAgent` |
| `OPENAI_MODEL` | `ResponseComposer` |
| `BOOKING_API_KEY` | `BreakoutAPIProvider` |
| `BOOKING_BASE_URL` | `BreakoutAPIProvider` |
| `BOOKING_PROVIDER` | `env_loader.py` |
| `WATI_BASE_URL` | `WatiClient` |
| `API_VERSION` | `WatiClient` |
| `WATI_ACCESS_TOKEN` | `WatiClient` |
| `WATI_TIMEOUT_SECONDS` | `WatiClient` |
| `WATI_MAX_ATTEMPTS` | `WatiClient` |
| `WATI_SEND_PATH` | `WatiClient` |
| `WATI_TEMPLATE_ID` | `WatiClient` |
| `WATI_TEMPLATE_PATH` | `WatiClient` |
| `WATI_BROADCAST_NAME` | `WatiClient` |
| `WATI_SENDER_NUMBER` | `WatiClient` |
| `DEMO_CUSTOMER_NAME` | `scripts/demo_runner.py` |
| `DEMO_CUSTOMER_PHONE` | `scripts/demo_runner.py` |

---

## Summary

| Category | Finding |
|---|---|
| Dead Python files | 0 |
| Duplicate utilities | 0 |
| Duplicate services | 0 |
| Duplicate prompts (production) | 0 |
| Duplicate integrations | 1 (intentional — kept for test compatibility) |
| Unused config variables | 0 |
| Files removed | 0 |
| Files flagged for future consolidation | 1 (`integrations/langgraph_booking_node.py`) |
