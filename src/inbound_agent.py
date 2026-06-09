from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from pathlib import Path

from .conversation_memory import ConversationMemory
from .handoff_generator import HandoffGenerator
from .intent_detector import IntentDetector
from .knowledge_loader import KnowledgeBase
from .knowledge_retriever import KnowledgeRetriever
from .qualification_agent import QualificationAgent
from .recommendation_engine import RecommendationEngine
from .router import Router


class InboundAgent:
    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        memory: ConversationMemory,
        prompt_path: str | Path,
        model: str = "qwen3:8b",
        use_ollama: bool = True,
    ):
        self.knowledge_base = knowledge_base
        self.memory = memory
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8")
        self.model = model
        self.use_ollama = use_ollama
        self.intent_detector = IntentDetector()
        self.recommender = RecommendationEngine()
        self.router = Router()
        self.handoff_generator = HandoffGenerator()
        self.retriever = KnowledgeRetriever(knowledge_base)
        self.qualification_agent = QualificationAgent(memory)
        self.last_ollama_error = ""

    def handle_message(self, message: str) -> dict:
        normalized_message = self.memory.normalize_number_words(message)
        if self._is_partial_transcript(normalized_message):
            response = "Sorry, I didn't catch that. Could you repeat it?"
            return {
                "response": response,
                "intent": self.memory.data.get("intent", "general_faq") or "general_faq",
                "intent_confidence": 0.2,
                "recommendation": {"option": "", "reason": ""},
                "missing_fields": [],
                "route": {
                    "next_agent": "inbound_agent",
                    "reason": "Partial transcript needs clarification.",
                    "should_handoff": False,
                },
                "qualification": {
                    "qualified": False,
                    "missing_fields": [],
                    "next_question": "",
                    "summary": {},
                },
                "handoff_summary": None,
            }

        self.memory.add_turn("customer", message)

        intent_result = self.intent_detector.detect(normalized_message, self.memory.data.get("intent", ""))
        # Run generic memory extraction for intent/event_type/location/participants etc.
        self.memory.update_from_message(normalized_message, intent_result.intent)

        recommendation = self.recommender.recommend(normalized_message, self.memory.data)
        if recommendation.option:
            self.memory.update_from_message(normalized_message, intent_result.intent, recommendation.option)

        # Run the qualification agent — this validates + captures the current expected field
        # and returns the next question (or completion).
        qual_result = self.qualification_agent.update_and_qualify(normalized_message, intent_result.intent)

        handoff_ready = self.memory.handoff_ready(intent_result.intent)
        route = self.router.route(intent_result.intent, qual_result.qualified)
        response = self._generate_response(
            normalized_message, intent_result.intent, recommendation, handoff_ready, qual_result
        )
        response = self._clean_response(response)

        self.memory.add_turn("agent", response)
        handoff = self.handoff_generator.generate(self.memory.data) if handoff_ready else None

        return {
            "response": response,
            "intent": intent_result.intent,
            "intent_confidence": intent_result.confidence,
            "recommendation": {
                "option": recommendation.option,
                "reason": recommendation.reason,
            },
            "missing_fields": qual_result.missing_fields,
            "route": {
                "next_agent": route.next_agent,
                "reason": route.reason,
                "should_handoff": route.should_handoff,
            },
            "qualification": {
                "qualified": qual_result.qualified,
                "missing_fields": qual_result.missing_fields,
                "next_question": qual_result.next_question,
                "summary": qual_result.summary,
            },
            "handoff_summary": handoff,
        }

    def _generate_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None) -> str:
        response = self._fallback_response(message, intent, recommendation, should_handoff, qual_result)
        if response:
            return response

        retrieved_context = self.retriever.search(
            " ".join(
                [
                    message,
                    intent,
                    self.memory.data.get("event_type", ""),
                    self.memory.data.get("location", ""),
                    self.memory.data.get("recommended_option", ""),
                ]
            )
        )

        if self.use_ollama and retrieved_context:
            prompt = self.prompt_template.format(
                knowledge=retrieved_context,
                memory=self.memory.as_prompt_context(),
                intent=intent,
                recommendation=recommendation.option,
                message=message,
            )
            ollama_response = self._call_ollama(prompt)
            if ollama_response:
                return ollama_response

        return "I don't currently have that information, but I can connect you with the appropriate team."

    def _call_ollama(self, prompt: str) -> str:
        process = None
        try:
            process = subprocess.Popen(
                ["ollama", "run", self.model, prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, _stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self.last_ollama_error = "timeout"
            if process is not None:
                self._terminate_ollama_process(process)
            return ""
        except Exception as exc:
            self.last_ollama_error = str(exc)
            if process is not None:
                self._terminate_ollama_process(process)
            return ""

        if process.returncode != 0:
            self.last_ollama_error = f"returncode:{process.returncode}"
            return ""
        self.last_ollama_error = ""
        return stdout.strip()

    @staticmethod
    def _terminate_ollama_process(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _fallback_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None) -> str:
        lowered = message.lower()

        if self._is_breakout_greeting(lowered):
            return "Yes, this is Breakout Escape Rooms. How may I help you today?"

        if self._is_plain_greeting(lowered) and intent == "general_faq":
            return "Hi, this is Breakout Escape Rooms. How may I help you today?"

        if self._is_yes_to_compare(lowered):
            comparison = self._comparison_response(self.memory.data.get("recommended_option", ""))
            if comparison:
                return comparison

        faq_answer = self._answer_faq(lowered)
        if faq_answer and (intent == "general_faq" or self._is_direct_question(lowered)):
            return faq_answer

        explicit_answer = self._answer_explicit_intent_question(lowered, intent)
        if explicit_answer:
            return explicit_answer

        if intent == "escape_room_inquiry" and self._asks_for_more_details(lowered):
            details = self._comparison_response(self.memory.data.get("recommended_option", ""))
            if not details:
                details = self._room_details_response(self.memory.data.get("recommended_option", ""))
            if details:
                return details

        if intent == "general_faq":
            return ""

        if should_handoff:
            route = self.router.route(intent, True)
            return f"Perfect. I have the details I need. I will connect you with our {route.next_agent.replace('_', ' ')}."

        guided_response = self._guided_response(message, intent, recommendation)
        if guided_response:
            return guided_response

        # ------------------------------------------------------------------ #
        # QUALIFICATION GUARD                                                  #
        # After _guided_response returns empty (no contextual response),       #
        # delegate fully to the QualificationAgent when it is in control.      #
        # This prevents Bug 1 (response mixing) and Bug 5 (recommendation leak)#
        # Only fires when the QA is actively waiting for a specific field.     #
        # ------------------------------------------------------------------ #
        # The QA guard only fires for fields that the FLOW_FIELDS mechanism does
        # NOT cover (e.g. food_required, budget_range, customer_name, phone).
        # For standard fields (location, participants, preferred_date, age_group)
        # the existing guided_response / flow_missing path provides richer messages.
        _flow_fields = set(self.memory.FLOW_FIELDS.get(intent, []))
        if (
            qual_result is not None
            and not qual_result.qualified
            and qual_result.response
            and self.qualification_agent._waiting_for
            and self.qualification_agent._waiting_for not in _flow_fields
        ):
            route_check = self.router.route(intent, False)
            if route_check.next_agent == "qualification_agent":
                return qual_result.response

        flow_missing = self.memory.missing_fields(intent=intent, include_contact=False)
        if intent == "escape_room_inquiry" and flow_missing == ["location"] and recommendation.option:
            return self._recommendation_response(intent, recommendation.option, recommendation.reason)

        if flow_missing:
            if (
                flow_missing[0] == "preferred_date"
                and not self._message_supplied_location(lowered)
                and self._invalid_date_answer(lowered)
            ):
                return "Sorry, I didn't understand the date. Could you provide a date like 18 June or June 18?"
            return self._ask_for_field(intent, flow_missing[0])

        if recommendation.option:
            if intent == "escape_room_inquiry":
                return self._recommendation_response(intent, recommendation.option, recommendation.reason)

            # For non-escape-room intents after flow fields are collected, if
            # qualification is still running (name/phone pending) return the
            # qual response, not a recommendation blurb.
            if qual_result is not None and not qual_result.qualified and qual_result.response:
                return qual_result.response

            contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
            if contact_missing and self._message_contains_contact_detail(message):
                return self._ask_for_field(intent, contact_missing[0])

            details = self._recommendation_response(intent, recommendation.option, recommendation.reason)
            if contact_missing:
                return f"{details} {self._ask_for_field(intent, contact_missing[0])}"
            return details

        contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
        if contact_missing:
            return self._ask_for_field(intent, contact_missing[0])

        return ""

    def _answer_faq(self, lowered: str) -> str:
        if "is this" in lowered or "breakout" in lowered:
            return "Yes, this is Breakout Escape Rooms. How may I help you today?"
        if "what escape rooms" in lowered or "rooms are available" in lowered or "games are available" in lowered:
            if "whitefield" in lowered:
                return "Sure. At Whitefield, you can choose from Murder Mystery, Hostage, Bomb Defusal, and Undercover. If you want something more intense, Bomb Defusal is the one I'd narrow in on."
            if "koramangala" in lowered:
                return "Sure. Koramangala has Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Undercover, The Wizarding Championship, and The Forbidden Forest. If you're new, I'd start with Murder Mystery or Hostage."
            if "jp nagar" in lowered or "jp" in lowered:
                return "Sure. JP Nagar has Murder Mystery, Hostage, and Prison Break. If you want something more mission-style, Prison Break is the stronger pick."
            return "Sure. Breakout has Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Bomb Defusal, Undercover, Prison Break, The Wizarding Championship, and The Forbidden Forest. I can narrow that down based on age group and challenge level."
        if "beginner" in lowered or "first time" in lowered or "first-time" in lowered:
            return "For beginners, Murder Mystery and Hostage are good options. They are available at Koramangala, Whitefield, and JP Nagar."
        if "whitefield" in lowered and ("visiting" in lowered or "coming" in lowered):
            return "Great. At Whitefield, Murder Mystery and Hostage are better for lighter investigation-style play, while Bomb Defusal and Undercover are stronger if you want more intensity. What age group is the team?"
        if "koramangala" in lowered and ("visiting" in lowered or "coming" in lowered):
            return "Great. Koramangala gives you the widest mix, from Murder Mystery and Hostage to more challenging rooms like Classified and Undercover. What age group is the team?"
        if ("jp nagar" in lowered or "jp" in lowered) and ("visiting" in lowered or "coming" in lowered):
            return "Great. JP Nagar has Murder Mystery, Hostage, and Prison Break. Murder Mystery and Hostage are easier to start with, while Prison Break is more mission-style. What age group is the team?"
        if "hostage" in lowered and ("age" in lowered or "limit" in lowered):
            return "Hostage is listed for ages 9 and above."
        if "food" in lowered:
            return "Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events."
        if "birthday" in lowered and ("package" in lowered or "activities" in lowered):
            return "Birthday parties include Escape Room, Showstopper, Karaoke, and Scavenger Hunt. Capacity is 50-80 people at Koramangala and 35-40 people at JP Nagar or Whitefield."
        if "corporate" in lowered and ("package" in lowered or "activities" in lowered or "event" in lowered):
            return "Corporate events include Escape Room, Let Loose, Detective Job, Scavenger Hunt Activity, The Unwind Challenge, and virtual event options."
        if "what is an escape room" in lowered or "escape room" in lowered:
            return "Sure. An escape room is a team game where players enter a themed room, find clues, solve puzzles, and complete a mission before time runs out."
        if "how long" in lowered or "duration" in lowered:
            return "Each game runs for around 50 minutes. Players should arrive 20 minutes before the slot time."
        if "children" in lowered or "kids" in lowered:
            return "Yes. Kid-only and kid-friendly games are available with staff supervision."
        if "parking" in lowered:
            return "Koramangala has basement and street parking, Whitefield has basement car parking with dedicated car spots and general parking, and JP Nagar has street parking."
        if "location" in lowered or "where" in lowered:
            return "Breakout has locations in Koramangala, Whitefield, and JP Nagar."
        if "cancel" in lowered or "refund" in lowered:
            return "Cancellation charges depend on how far in advance the cancellation is made. I can connect you with the appropriate team for cancellation or refund handling."
        if "walk in" in lowered:
            return "Walk-ins are allowed only if slots are available. Slots cannot be held without advance payment."
        if "advance booking" in lowered or "booking required" in lowered:
            return "Yes. All bookings are done online."
        return ""

    def _ask_for_field(self, intent: str, field: str) -> str:
        if field == "location":
            return "Sure. Which location would you prefer: Koramangala, Whitefield, or JP Nagar?"
        if field == "participants":
            if intent == "birthday_party":
                return "Got it. Approximately how many guests will attend?"
            return "Got it. How many people will be joining?"
        if field == "age_group":
            return "Sure. What is the age group of the players?"
        if field == "preferred_date":
            if intent == "birthday_party":
                location = str(self.memory.data.get("location", ""))
                capacity = self._capacity_for_location(location)
                if capacity:
                    return f"Great. {location} can accommodate approximately {capacity}. What date are you considering?"
            return "Perfect. What date are you planning for?"
        if field == "company_size":
            return "Sure. What is the approximate company or team size?"
        if field == "customer_name":
            return "May I have your name?"
        if field == "phone":
            return "Thank you. Could I have your phone number so we can share the event details?"
        if field == "food_required":
            return "Would you require food and beverages for the event?"
        if field == "budget_range":
            return "What budget range are you considering? We have Basic, Standard, and Premium options."
        return "Could you share one more detail?"

    def _recommendation_response(self, intent: str, option: str, reason: str) -> str:
        if intent == "birthday_party":
            location = self.memory.data.get("location", "")
            if str(location).lower() == "whitefield":
                return "Great. Whitefield can accommodate approximately 35-40 guests. The birthday package can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper. What date are you considering?"
            return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper. What date are you considering?"
        if intent == "corporate_event":
            return "For a corporate team, I'd suggest combining Escape Rooms with Scavenger Hunt or Let Loose activities. Escape Rooms work well for teamwork and problem-solving, while Scavenger Hunt helps larger groups participate together. Which location are you considering?"
        if intent == "escape_room_inquiry":
            if option == "The Wizarding Championship":
                prefix = self._escape_room_context_prefix("children aged 5 to 8")
                follow_up = "Would you like more details about that room?" if self.memory.data.get("location") else "Which location are you planning to visit?"
                return f"{prefix}I'd recommend The Wizarding Championship. It is designed for that age group and gives younger players a magical, activity-led escape room experience. If the group is closer to 9 and above, Murder Mystery or Hostage would be the next step up. {follow_up}"
            if option == "Murder Mystery and Hostage":
                prefix = self._escape_room_context_prefix("that age group")
                follow_up = "Would you like more details about either room?" if self.memory.data.get("location") else "Which location are you planning to visit?"
                return f"{prefix}Murder Mystery and Hostage are both good choices. Murder Mystery focuses more on investigation and clue solving, while Hostage adds more urgency with a rescue-style story. {follow_up}"
            if option == "Murder Mystery or Hostage":
                prefix = self._beginner_context_prefix()
                follow_up = "Would you like me to compare those two?" if self.memory.data.get("location") else "Which location are you planning to visit?"
                return f"{prefix}I'd start with Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling overwhelming. If you want something slightly more exciting, Hostage is a good alternative. {follow_up}"
            return self._adult_recommendation_response()
        return f"Sure. {option} would be a good fit. {reason}"

    def _guided_response(self, message: str, intent: str, recommendation) -> str:
        lowered = message.lower()

        if intent == "birthday_party":
            if "birthday" in lowered and not self._message_has_count(lowered):
                return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper, depending on the package. Approximately how many guests are you expecting?"
            if not self.memory.data.get("participants"):
                return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper, depending on the package. Approximately how many guests are you expecting?"
            if not self.memory.data.get("location"):
                participants = self.memory.data.get("participants")
                return f"Great. For around {participants} guests, we can narrow the package by location first. Which location would you prefer: Koramangala, Whitefield, or JP Nagar?"

        if intent == "corporate_event":
            if self.memory.data.get("company_size") and (
                not self.memory.data.get("location") or self._is_fresh_corporate_inquiry_without_location(lowered)
            ):
                size = self.memory.data.get("company_size")
                return f"For a team of {size}, I'd suggest a combination of Escape Rooms and Scavenger Hunt activities. Escape Rooms help with teamwork and problem-solving, while Scavenger Hunt lets larger groups participate together. Which location are you considering?"
            if not self.memory.data.get("company_size"):
                return "Absolutely. Corporate events can include Escape Rooms, Let Loose, Detective Job, Scavenger Hunt Activity, and The Unwind Challenge. How many employees are you planning for?"

        if intent == "couple_event":
            return "A lot of couples choose Murder Mystery because it is focused on teamwork, clue solving, and the story. If you want something more intense, Prison Break or Undercover can add more challenge. Are you looking for something relaxed or more challenging?"

        if intent == "escape_room_inquiry":
            if self._is_beginner_context(lowered):
                participants = self.memory.data.get("participants")
                location = self.memory.data.get("location")
                if participants and location:
                    return self._recommendation_response(intent, "Murder Mystery or Hostage", "Murder Mystery is easier to start with, while Hostage adds a bit more urgency.")
                if participants:
                    return "If it's your first escape room, I'd usually recommend Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling too heavy. If you'd like something slightly more exciting, Hostage is another good option. Which location are you planning to visit?"
                return "If it's your first escape room, I'd usually recommend Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling too heavy. If you'd like something slightly more exciting, Hostage is another good option. How many people will be joining?"
            if recommendation.option:
                return self._recommendation_response(intent, recommendation.option, recommendation.reason)

        return ""

    def _adult_recommendation_response(self) -> str:
        participants = self.memory.data.get("participants")
        location = str(self.memory.data.get("location", ""))

        group_phrase = f"For a group of {participants} adults" if participants else "For adults"
        if location:
            group_phrase += f" visiting {location}"

        if location.lower() == "whitefield":
            return f"{group_phrase}, I'd recommend Bomb Defusal or Undercover. Bomb Defusal is better if you want something intense and time-pressured, while Undercover works well if you enjoy a mystery-style investigation. Are you looking for something challenging or more story-driven?"
        if location.lower() == "koramangala":
            return f"{group_phrase}, I'd recommend Classified or Undercover. Classified is great if you enjoy investigation-style challenges, while Undercover gives you a more tense bunker-style mystery. Are you looking for something challenging or more beginner-friendly?"
        if location.lower() == "jp nagar":
            return f"{group_phrase}, I'd recommend Prison Break. It has a stronger mission-style setup, and Murder Mystery is a lighter alternative if you want more clue solving. Are you looking for something intense or more relaxed?"

        return f"{group_phrase}, I'd recommend Classified or Bomb Defusal. Classified is great if you enjoy investigation-style challenges and solving a mystery together. Bomb Defusal is better if you're looking for something more intense and fast-paced. Are you looking for something challenging or something more beginner-friendly?"

    def _escape_room_context_prefix(self, age_phrase: str) -> str:
        participants = self.memory.data.get("participants")
        location = self.memory.data.get("location")
        stored_age = str(self.memory.data.get("age_group", ""))
        group_word = "players"
        if stored_age and stored_age != "adults" and re.search(r"\d+", stored_age):
            age_match = re.search(r"\d+", stored_age)
            if age_match:
                age_phrase = f"kids aged {age_match.group(0)}"
                group_word = "kids"
        elif stored_age == "kids" and age_phrase == "that age group":
            age_phrase = "kids"
            group_word = "kids"
        if participants and location:
            if group_word == "kids" and age_phrase.startswith("kids aged"):
                return f"For {participants} {age_phrase} visiting {location}, "
            return f"For {participants} {group_word} in {age_phrase} visiting {location}, "
        if participants:
            if group_word == "kids" and age_phrase.startswith("kids aged"):
                return f"For {participants} {age_phrase}, "
            return f"For {participants} {group_word} in {age_phrase}, "
        if location:
            return f"For {age_phrase} visiting {location}, "
        return ""

    def _beginner_context_prefix(self) -> str:
        participants = self.memory.data.get("participants")
        location = self.memory.data.get("location")
        age_group = str(self.memory.data.get("age_group", ""))
        if participants and age_group == "adults":
            prefix = f"For a group of {participants} adults"
            if location:
                prefix += f" visiting {location}"
            return f"{prefix}, since you're first-time players, "
        return self._escape_room_context_prefix("first-time players")

    def _room_details_response(self, recommended_option: str) -> str:
        option = recommended_option.lower()
        if "wizarding" in option:
            return "The Wizarding Championship is at Koramangala. It is for ages 5 to 8, with a maximum capacity of 8 players and minimum 4 players."
        if "murder mystery" in option and "hostage" in option:
            return "Murder Mystery and Hostage are available at Koramangala, Whitefield, and JP Nagar. Each has a capacity of 7 players and a minimum of 2 players."
        if "classified" in option or "undercover" in option or "prison break" in option or "bomb defusal" in option:
            return "Classified is at Koramangala, Undercover is at Koramangala and Whitefield, Prison Break is at JP Nagar, and Bomb Defusal is at Whitefield."
        return ""

    def _comparison_response(self, recommended_option: str) -> str:
        option = recommended_option.lower()
        if "murder mystery" in option and "hostage" in option:
            return "Sure. Murder Mystery is the better starting point if you want clue solving and an investigation-style story. Hostage is a good alternative if you want a little more urgency and a rescue-style setup. Would you prefer something calmer or slightly more exciting?"
        if "bomb defusal" in option and "undercover" in option:
            return "Sure. Bomb Defusal is stronger if you want pressure and a faster-paced challenge. Undercover is better if you prefer a mystery-style investigation. Are you leaning toward intensity or story?"
        if "classified" in option and "undercover" in option:
            return "Sure. Classified is better for investigation-style challenges, while Undercover has a tense bunker-style mystery. Are you looking for difficult puzzles or a stronger story setup?"
        return ""

    def _answer_explicit_intent_question(self, lowered: str, intent: str) -> str:
        if intent == "corporate_event" and self._asks_for_room_suggestion(lowered):
            size = self.memory.data.get("company_size") or self.memory.data.get("participants")
            group = f" for a team of {size}" if size else ""
            return f"For a corporate group{group}, I'd suggest combining Escape Rooms with Scavenger Hunt or Let Loose activities. Escape Rooms handle the teamwork and problem-solving part, while Scavenger Hunt helps larger groups participate together. Which location are you considering?"
        return ""

    @staticmethod
    def _capacity_for_location(location: str) -> str:
        lowered = location.lower()
        if lowered == "koramangala":
            return "50-80 guests"
        if lowered in {"whitefield", "jp nagar"}:
            return "35-40 guests"
        return ""

    @staticmethod
    def _is_breakout_greeting(lowered: str) -> bool:
        return "is this" in lowered and "breakout" in lowered

    @staticmethod
    def _is_plain_greeting(lowered: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        return normalized in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}

    @staticmethod
    def _asks_for_more_details(lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "more information",
                "more info",
                "tell me more",
                "about those rooms",
                "details",
                "compare these two",
                "compare these 2",
                "compare those two",
                "compare those 2",
                "compare them",
            )
        )

    @staticmethod
    def _is_yes_to_compare(lowered: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        return normalized in {"yes", "yes please", "yeah", "sure", "please compare", "compare"}

    @staticmethod
    def _asks_for_room_suggestion(lowered: str) -> bool:
        return any(term in lowered for term in ("which room", "what room", "suggest", "recommend"))

    @staticmethod
    def _is_fresh_corporate_inquiry_without_location(lowered: str) -> bool:
        has_corporate_signal = any(term in lowered for term in ("corporate", "employees", "team event", "event for"))
        has_location = any(term in lowered for term in ("whitefield", "koramangala", "jp nagar"))
        return has_corporate_signal and not has_location

    @staticmethod
    def _is_partial_transcript(message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        partial_phrases = {
            "we have never",
            "never done",
            "event do you suggest for",
            "what if i ask people what do you suggest",
        }
        if lowered in partial_phrases:
            return True
        if len(lowered.split()) <= 4 and lowered.endswith(("never", "for", "suggest")):
            return True
        return False

    def _invalid_date_answer(self, lowered: str) -> bool:
        if self._is_direct_question(lowered):
            return False
        if self.memory._extract_preferred_date(lowered):
            return False
        if re.fullmatch(r"[a-z]\d+", lowered.strip()):
            return True
        meaningful = re.findall(r"[a-z0-9]+", lowered)
        if len(meaningful) < 2:
            return True
        return any(phrase in lowered for phrase in ("google", "don't know", "not sure"))

    @staticmethod
    def _message_supplied_location(lowered: str) -> bool:
        return any(location in lowered for location in ("whitefield", "koramangala", "jp nagar")) or ("jp" in lowered and "nagar" in lowered)

    @staticmethod
    def _is_beginner_context(lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "beginner",
                "first time",
                "first-time",
                "never done",
                "new to escape",
            )
        )

    @staticmethod
    def _is_direct_question(lowered: str) -> bool:
        stripped = lowered.strip()
        return stripped.endswith("?") or stripped.startswith(
            (
                "what ",
                "where ",
                "which ",
                "how ",
                "can ",
                "do ",
                "does ",
                "tell me ",
            )
        )

    @staticmethod
    def _message_contains_contact_detail(message: str) -> bool:
        lowered = message.lower()
        has_name = any(phrase in lowered for phrase in ("my name is", "i am", "i'm", "this is"))
        has_phone = bool(re.search(r"(?:(?:\+91[\s-]?)|0)?[6-9]\d{9}\b", message.replace(" ", "")))
        return has_name or has_phone

    @staticmethod
    def _message_has_count(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b\d{1,4}\s*(people|persons|guests|kids|children|adults|participants|players|members|employees)?\b",
                lowered,
            )
        )

    @staticmethod
    def _clean_response(response: str) -> str:
        banned = [
            "I deeply apologize",
            "I completely understand your frustration",
        ]
        cleaned = response.strip()
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
        cleaned = re.sub(r"(?im)^\s*thinking\s*\.{0,3}\s*$", "", cleaned).strip()
        cleaned = re.sub(r"(?im)^\s*okay,\s*the customer.*$", "", cleaned).strip()
        cleaned = re.sub(r"(?im)^\s*(analysis|reasoning)\s*:.*$", "", cleaned).strip()
        for phrase in banned:
            cleaned = cleaned.replace(phrase, "I understand")
        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                parsed = json.loads(cleaned)
                cleaned = parsed.get("response", cleaned)
            except json.JSONDecodeError:
                pass
        cleaned = re.sub(r"(?im)^(intent|confidence|route|handoff|debug|analysis|reasoning)\s*:.*$", "", cleaned)
        cleaned = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return cleaned
