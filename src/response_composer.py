from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .core.conversation_modes import ConversationMode


class ResponseComposer:
    def __init__(
        self,
        prompt_path: str | Path,
        model: str = "gpt-4.1-mini",
        enabled: bool | None = None,
        playbook_path: str | Path | None = None,
    ) -> None:
        prompt_file = Path(prompt_path)
        self.system_prompt = prompt_file.read_text(encoding="utf-8").strip()
        demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
        if self.system_prompt and not demo_mode:
            print("personality_prompt_loaded=True")
        playbook_file = Path(playbook_path) if playbook_path else prompt_file.with_name("conversation_playbook.txt")
        self.playbook = playbook_file.read_text(encoding="utf-8").strip() if playbook_file.exists() else ""
        if self.playbook:
            self.system_prompt = f"{self.system_prompt}\n\n{self.playbook}"
        self.model = model
        self.enabled = bool(os.environ.get("OPENAI_API_KEY")) if enabled is None else enabled
        if "PYTEST_CURRENT_TEST" in os.environ and enabled is None:
            self.enabled = False
        self.last_error = ""
        self.last_latency = 0.0

        examples_file = prompt_file.with_name("transcript_examples.json")
        self.few_shot_examples = []
        if examples_file.exists():
            try:
                self.few_shot_examples = json.loads(examples_file.read_text(encoding="utf-8"))
                if not demo_mode:
                    print(f"Loaded {len(self.few_shot_examples)} examples from transcript_examples.json")
            except Exception:
                pass

    def compose(
        self,
        draft: str,
        message: str,
        state: dict,
        intent: str,
        mode: ConversationMode,
        grounded_context: str = "",
    ) -> str:
        self.last_latency = 0.0
        api_key = os.environ.get("OPENAI_API_KEY", "")
        enabled = bool(api_key) and self.enabled
        debug = os.environ.get("BREAKOUT_DEBUG", "false").lower() == "true"
        if debug:
            print(f"OPENAI_ENABLED={enabled}")
            print(f"MODEL={self.model}")

        if (
            state.get("booking_started")
            and not self._explicit_room_information_request(message)
            and self._looks_like_room_description(draft)
        ):
            return self._booking_progress_response(state)

        if not draft.strip() or not enabled:
            return draft

        safe_state = {
            key: state.get(key, "")
            for key in (
                "customer_name", "location", "participants", "age_group",
                "experience_level", "event_type", "preferred_date",
                "recommended_option", "current_workflow", "selected_slot",
                "customer_name", "first_name", "last_name", "phone",
                "booking_started", "booking_id", "booking_ref",
            )
        }

        few_shot_block = ""
        if self.few_shot_examples:
            print(f"Injected {len(self.few_shot_examples)} examples into prompt")
            few_shot_block = "Here are examples of how to humanize the approved draft dialogues using the structured layout and known state:\n\n"
            for ex in self.few_shot_examples:
                ex_state = {
                    key: ex.get("state", {}).get(key, "")
                    for key in (
                        "customer_name", "location", "participants", "age_group",
                        "experience_level", "event_type", "preferred_date",
                        "recommended_option", "current_workflow",
                    )
                }
                for k, v in ex.get("state", {}).items():
                    if k in ex_state:
                        ex_state[k] = v
                few_shot_block += (
                    f"Example Input:\n"
                    f"Conversation mode: {ex.get('state', {}).get('conversation_mode', 'recommendation')}\n"
                    f"Intent: {ex.get('state', {}).get('intent', 'escape_room_inquiry')}\n"
                    f"Customer message: {ex['customer_message']}\n"
                    f"Structured state: {json.dumps(ex_state, ensure_ascii=False)}\n"
                    f"Approved draft: {ex['approved_draft']}\n"
                    f"Example Output (Humanized Response):\n"
                    f"{ex['composed_response']}\n\n"
                    "--- \n\n"
                )

        user_prompt = (
            f"{few_shot_block}"
            f"Now rewrite the approved draft as a short, natural spoken turn using the state constraints:\n\n"
            f"Conversation mode: {mode.value}\n"
            f"Intent: {intent}\n"
            f"Customer message: {message}\n"
            f"Structured state: {json.dumps(safe_state, ensure_ascii=False)}\n"
            f"Grounded context: {grounded_context or 'No additional context supplied.'}\n"
            f"Approved draft: {draft}\n\n"
            "Rewrite the approved draft in the Breakout voice. Preserve all facts and the single next question. "
            "Never ask for information already present in the structured state. "
            "Use 1-3 short sentences, usually under 45 words. For recommendations, use one recommendation, "
            "one reason, and one question. Do not expand the draft. Never say 'I can help with that', "
            "'I'd be happy to assist', 'It gives you', 'Would you like me to', 'Good question', "
            "or 'Coming back to the event planning'."
        )

        start_time = time.perf_counter()
        try:
            try:
                import openai
                OpenAI = getattr(openai, "OpenAI", None)
                APITimeoutError = getattr(openai, "APITimeoutError", Exception)
                APIConnectionError = getattr(openai, "APIConnectionError", Exception)
                RateLimitError = getattr(openai, "RateLimitError", Exception)
                AuthenticationError = getattr(openai, "AuthenticationError", Exception)
            except (ImportError, AttributeError):
                OpenAI = None
                APITimeoutError = Exception
                APIConnectionError = Exception
                RateLimitError = Exception
                AuthenticationError = Exception

            if OpenAI is None:
                raise RuntimeError("openai package not installed")

            client = OpenAI(api_key=api_key, timeout=2.0, max_retries=0)
            if hasattr(client, "chat") and hasattr(client.chat, "completions"):
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=100,
                )
                composed = (response.choices[0].message.content or "").strip()
            elif hasattr(client, "responses"):
                response = client.responses.create(
                    model=self.model,
                    input=user_prompt,
                    instructions=self.system_prompt,
                )
                composed = getattr(response, "output_text", "").strip()
            else:
                raise AttributeError("Mock OpenAI client has neither chat.completions nor responses.")
            self.last_latency = time.perf_counter() - start_time
            if debug:
                print(f"OpenAI latency per request: {self.last_latency:.4f}s")
                print("response_source=openai")
            if composed:
                if self._has_unverified_booking_claim(composed, state):
                    self.last_error = "unverified_booking_claim_blocked"
                    return draft
                self.last_error = ""
                return composed
            self.last_error = "empty_response"
        except (APITimeoutError, APIConnectionError, RateLimitError, AuthenticationError) as exc:
            self.last_latency = time.perf_counter() - start_time
            self.last_error = f"openai_failure: {exc}"
            self.enabled = False
            if debug:
                print(f"OpenAI error: {type(exc).__name__}: {exc}")
                print(f"OpenAI latency per request: {self.last_latency:.4f}s")
                print("response_source=fallback")
        except Exception as exc:
            self.last_latency = time.perf_counter() - start_time
            self.last_error = f"openai_failure: {exc}"
            self.enabled = False
            if debug:
                print(f"OpenAI error: {type(exc).__name__}: {exc}")
                print(f"OpenAI latency per request: {self.last_latency:.4f}s")
                print("response_source=fallback")
        return draft

    @staticmethod
    def _has_unverified_booking_claim(response: str, state: dict) -> bool:
        if state.get("booking_id") and state.get("booking_ref"):
            return False
        lowered = response.lower()
        claim_patterns = (
            r"\b(?:your\s+)?booking\b.{0,40}\b(?:confirmed|booked|finali[sz]ed|complete|completed|all set|is set)\b",
            r"\b(?:confirmed|booked|finali[sz]ed)\b.{0,25}\b(?:booking|reservation)\b",
            r"\bbooking details are all set\b",
        )
        return any(re.search(pattern, lowered) for pattern in claim_patterns)

    @staticmethod
    def _explicit_room_information_request(message: str) -> bool:
        lowered = message.lower()
        return bool(re.search(r"\b(?:tell|explain|describe|details?|about)\b", lowered))

    @staticmethod
    def _looks_like_room_description(response: str) -> bool:
        lowered = response.lower()
        return any(
            phrase in lowered
            for phrase in (
                "investigation-style escape room", "spy-themed thriller", "is offered at",
                "rescue-style escape room", "high-stakes game", "classic escape room",
            )
        )

    @staticmethod
    def _booking_progress_response(state: dict) -> str:
        prompts = (
            ("room", "Which escape room would you like to book?"),
            ("location", "Which Breakout location would you like to visit?"),
            ("preferred_date", "What date would you like to visit?"),
            ("selected_slot", "Which available time slot would you like?"),
            ("first_name", "What is your first name?"),
            ("last_name", "What is your last name?"),
            ("phone", "What is the best phone number for the booking?"),
        )
        for field, prompt in prompts:
            if not state.get(field):
                return prompt
        return "I have the required booking details and will continue with confirmation."
