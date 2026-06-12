from __future__ import annotations

import json
import os
from pathlib import Path

from .conversation_modes import ConversationMode


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
        playbook_file = Path(playbook_path) if playbook_path else prompt_file.with_name("conversation_playbook.txt")
        self.playbook = playbook_file.read_text(encoding="utf-8").strip() if playbook_file.exists() else ""
        if self.playbook:
            self.system_prompt = f"{self.system_prompt}\n\n{self.playbook}"
        self.model = model
        self.enabled = bool(os.environ.get("OPENAI_API_KEY")) if enabled is None else enabled
        self.last_error = ""

    def compose(
        self,
        draft: str,
        message: str,
        state: dict,
        intent: str,
        mode: ConversationMode,
        grounded_context: str = "",
    ) -> str:
        if not draft.strip() or not self.enabled:
            return draft

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            self.last_error = "missing_api_key"
            return draft

        safe_state = {
            key: state.get(key, "")
            for key in (
                "customer_name", "location", "participants", "age_group",
                "experience_level", "event_type", "preferred_date",
                "recommended_option", "current_workflow",
            )
        }
        user_prompt = (
            f"Conversation mode: {mode.value}\n"
            f"Intent: {intent}\n"
            f"Customer message: {message}\n"
            f"Structured state: {json.dumps(safe_state, ensure_ascii=False)}\n"
            f"Grounded context: {grounded_context or 'No additional context supplied.'}\n"
            f"Approved draft: {draft}\n\n"
            "Rewrite the approved draft in the Breakout voice. Preserve all facts and the single next question."
        )

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=15.0)
            response = client.responses.create(
                model=self.model,
                instructions=self.system_prompt,
                input=user_prompt,
                max_output_tokens=220,
            )
            composed = str(getattr(response, "output_text", "") or "").strip()
            if composed:
                self.last_error = ""
                return composed
            self.last_error = "empty_response"
        except Exception as exc:
            self.last_error = str(exc)
        return draft
