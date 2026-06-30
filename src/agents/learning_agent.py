from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.agent_response import AgentResponse
    from ..memory.conversation_memory import ConversationMemory
    from .scoring_agent import ScoreResult
    from .sentiment_agent import SentimentResult


@dataclass(frozen=True)
class LearningSnapshot:
    metrics: dict[str, Any]
    insights: list[str]
    report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentEvaluation:
    intent_identified: int
    empathy: int
    question_answered: int
    context_retained: int
    naturalness: int
    flow_advancement: int
    policy_compliance: int
    total_score: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentEvaluator:
    @staticmethod
    def evaluate(
        customer_message: str,
        agent_response: str,
        intent: str,
        missing_fields: list[str],
        next_agent: str,
        should_handoff: bool,
        sentiment: str,
        state: dict[str, Any]
    ) -> AgentEvaluation:
        # 1. Try LLM-based evaluation if API key is present
        llm_eval = AgentEvaluator._evaluate_via_llm(customer_message, agent_response, intent, state)
        if llm_eval is not None:
            return llm_eval

        # 2. Fallback to Heuristic rule-based evaluation
        user_lower = customer_message.lower().strip()
        agent_lower = agent_response.lower().strip()

        # Intent Identified (0-2)
        intent_identified = 2 if intent and intent != "unknown" else 0

        # Empathy Score (0-2)
        has_negative_sentiment = sentiment in {"frustrated", "angry", "confused", "hesitant"}
        has_negative_keywords = any(kw in user_lower for kw in [
            "expensive", "price", "pricey", "charge", "cost", "cheap", "traffic", "late", "delay", 
            "ruined", "bad", "worst", "fail", "trap", "lock", "safety", "spam", "privacy", "email"
        ])
        
        empathy_cushions = [
            "understand", "no worries", "don't worry", "worry", "sorry", "perfect", "great", "awesome",
            "completely get", "fair question", "love to", "happy to", "that's a fair", "exciting",
            "nice", "apologize", "we'd love"
        ]
        has_empathy_cushion = any(c in agent_lower for c in empathy_cushions)
        is_callback_text = "don't have that detail to hand" in agent_lower or "name and number" in agent_lower
        
        if is_callback_text:
            empathy = 0
        elif (has_negative_sentiment or has_negative_keywords) and has_empathy_cushion:
            empathy = 2
        elif has_negative_sentiment or has_negative_keywords:
            empathy = 1
        elif has_empathy_cushion:
            empathy = 2
        else:
            empathy = 1

        # Question Answered Score (0-2)
        is_question = "?" in user_lower or any(user_lower.startswith(w) for w in ["what", "how", "why", "where", "when", "can", "is", "do", "are", "will"])
        is_not_sure = "not sure" in agent_lower or "don't have that detail" in agent_lower
        
        is_price_query = "price" in user_lower or "cost" in user_lower or "expensive" in user_lower or "cheap" in user_lower
        has_price_info = "depend" in agent_lower or "quote" in agent_lower or "estimate" in agent_lower or "price" in agent_lower or "pricing" in agent_lower or "cost" in agent_lower or "offer" in agent_lower
        
        is_safety_query = "lock" in user_lower or "trap" in user_lower or "safe" in user_lower
        has_safety_info = "not actually locked" in agent_lower or "monitors" in agent_lower or "safety" in agent_lower or "exit" in agent_lower or "open" in agent_lower
        
        is_late_query = "late" in user_lower or "traffic" in user_lower or "delay" in user_lower
        has_late_info = "back to back" in agent_lower or "reduces the time" in agent_lower or "how late" in agent_lower or "flexibility" in agent_lower
        
        if is_question and is_not_sure:
            question_answered = 0
        elif is_price_query and has_price_info:
            question_answered = 2
        elif is_safety_query and has_safety_info:
            question_answered = 2
        elif is_late_query and has_late_info:
            question_answered = 2
        elif is_question and not is_not_sure:
            question_answered = 2
        elif not is_question:
            question_answered = 2
        else:
            question_answered = 1

        # Context Retained Score (0-2)
        context_retained = 2
        repetition_detected = False
        if (state.get("participants") or state.get("company_size")) and any(w in agent_lower for w in ["how many players", "how many people", "size of your group"]):
            repetition_detected = True
        if state.get("location") and any(w in agent_lower for w in ["which location", "choose a location", "what location"]):
            repetition_detected = True
        if state.get("preferred_date") and any(w in agent_lower for w in ["what date", "which date", "what day"]):
            repetition_detected = True
            
        if repetition_detected:
            context_retained = 1

        # Naturalness Score (0-2)
        is_robotic_phrase = any(phrase in agent_lower for phrase in [
            "don't have that detail to hand", "not sure about that", "our team would be the best",
            "may i take your name and number"
        ])
        word_count = len(agent_response.split())
        
        if is_robotic_phrase:
            naturalness = 0
        elif word_count <= 45:
            naturalness = 2
        else:
            naturalness = 1

        # Flow Advancement Score (0-2)
        num_questions = agent_response.count("?")
        booking_keywords = ["player", "people", "adult", "kid", "mix", "join", "location", "date", "time", "slot", "room", "name", "phone", "email", "book", "reserve", "play"]
        advances = any(k in agent_lower for k in booking_keywords)
        
        if is_callback_text:
            flow_advancement = 0
        elif num_questions == 1 and (next_agent == "booking_agent" or should_handoff or advances):
            flow_advancement = 2
        elif num_questions == 0 and (should_handoff or next_agent in {"events_team", "escalation_agent"} or "thank you" in agent_lower or "goodbye" in agent_lower):
            flow_advancement = 2
        else:
            flow_advancement = 1

        # Policy Compliance Score (0-2)
        policy_compliance = 2
        if intent == "corporate_event" and should_handoff and next_agent != "events_team":
            policy_compliance = 1

        total_score = intent_identified + empathy + question_answered + context_retained + naturalness + flow_advancement + policy_compliance

        reasons = []
        if intent_identified == 0:
            reasons.append("Intent not resolved or unknown.")
        elif intent_identified == 2:
            reasons.append("Intent successfully identified.")
            
        if empathy == 0:
            reasons.append("Lacked empathy response when customer expressed issues or objections.")
        elif empathy == 2:
            reasons.append("Excellent empathy cushion used to acknowledge the customer's state.")
            
        if question_answered == 0:
            reasons.append("Failed to answer customer question directly.")
        elif question_answered == 2:
            reasons.append("Answered the customer's question directly with correct details.")
            
        if context_retained == 0 or context_retained == 1:
            reasons.append("Lost context, repeated questioning for already provided information.")
        elif context_retained == 2:
            reasons.append("Successfully retained context and avoided repetitive questioning.")
            
        if naturalness == 0:
            reasons.append("Response sounded robotic with repeated boilerplate phrases.")
        elif naturalness == 2:
            reasons.append("Short, natural, and highly human-sounding response.")
            
        if flow_advancement == 0 or flow_advancement == 1:
            reasons.append("Flow advancement could be improved; missing or multiple questions asked.")
        elif flow_advancement == 2:
            reasons.append("Great flow advancement with a clear, singular next step.")

        if not reasons:
            reasons.append("Response met standard agent quality requirements.")

        return AgentEvaluation(
            intent_identified=intent_identified,
            empathy=empathy,
            question_answered=question_answered,
            context_retained=context_retained,
            naturalness=naturalness,
            flow_advancement=flow_advancement,
            policy_compliance=policy_compliance,
            total_score=total_score,
            reasons=reasons[:4]
        )

    @staticmethod
    def _evaluate_via_llm(
        customer_message: str,
        agent_response: str,
        intent: str,
        state: dict[str, Any]
    ) -> AgentEvaluation | None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        
        prompt = f"""You are an independent Quality Assurance Evaluator for Breakout Escape Rooms voice agent.
Evaluate the AI Agent's response to the customer's message.

Customer Message: "{customer_message}"
Agent Response: "{agent_response}"
Conversation State: {json.dumps(state)}

Score the agent response on these 7 metrics (0 = Poor/Robotic, 1 = Adequate, 2 = Great/Empathetic/Conversational):
1. Intent Identified (Correctly identified customer's booking or inquiry intent)
2. Empathy (Acknowledge and validate customer's state/emotion/objection)
3. Question Answered (Explain the policy or answer the question directly and cleanly)
4. Context Retained (Does not ask repetitive questions for info already provided, handles multi-statement context)
5. Naturalness (Natural wording, under 45 words, no robotic clichés)
6. Flow Advancement (End with exactly one question, guide toward next booking slot or qualification step)
7. Policy Compliance (Follow safety, booking rules, and event routing guidelines)

Return your evaluation EXACTLY in the following JSON format:
{{
  "intent_identified": <score 0-2>,
  "empathy": <score 0-2>,
  "question_answered": <score 0-2>,
  "context_retained": <score 0-2>,
  "naturalness": <score 0-2>,
  "flow_advancement": <score 0-2>,
  "policy_compliance": <score 0-2>,
  "total_score": <sum of scores, 0-14>,
  "reasons": ["reason 1", "reason 2"]
}}"""
        try:
            import openai
            OpenAI = getattr(openai, "OpenAI", None)
            if OpenAI is None:
                return None
            client = OpenAI(api_key=api_key, timeout=2.0, max_retries=0)
            model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content or "{}")
            return AgentEvaluation(
                intent_identified=int(data.get("intent_identified", 2)),
                empathy=int(data.get("empathy", 1)),
                question_answered=int(data.get("question_answered", 1)),
                context_retained=int(data.get("context_retained", 2)),
                naturalness=int(data.get("naturalness", 1)),
                flow_advancement=int(data.get("flow_advancement", 1)),
                policy_compliance=int(data.get("policy_compliance", 2)),
                total_score=int(data.get("total_score", 10)),
                reasons=data.get("reasons", ["LLM Evaluation completed successfully."])
            )
        except Exception:
            return None


class LearningAgent:
    """Maintains session-level learning metrics and a compact report."""

    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory

    def observe_turn(
        self,
        message: str,
        result: AgentResponse,
        sentiment: SentimentResult,
        score: ScoreResult,
    ) -> LearningSnapshot:
        metrics = self._metrics()
        metrics["turns"] = int(metrics.get("turns", 0)) + 1

        intent = str(result.intent or self.memory.data.get("intent") or "unknown")
        self._increment(metrics.setdefault("intent_counts", {}), intent)
        self._increment(metrics.setdefault("sentiment_counts", {}), sentiment.sentiment)

        if result.should_handoff:
            metrics["handoff_count"] = int(metrics.get("handoff_count", 0)) + 1
        booking_result = result.booking_result or result.booking or {}
        if isinstance(booking_result, dict) and booking_result.get("confirmed"):
            metrics["booking_confirmed_count"] = int(metrics.get("booking_confirmed_count", 0)) + 1
        whatsapp = self.memory.data.get("whatsapp_confirmation", {})
        if isinstance(whatsapp, dict) and whatsapp.get("sent"):
            metrics["whatsapp_sent_count"] = int(metrics.get("whatsapp_sent_count", 0)) + 1

        # Evaluate the agent response
        eval_result = AgentEvaluator.evaluate(
            customer_message=message,
            agent_response=result.response,
            intent=intent,
            missing_fields=result.missing_fields,
            next_agent=result.next_agent,
            should_handoff=result.should_handoff,
            sentiment=sentiment.sentiment,
            state=self.memory.data
        )
        metrics["latest_agent_evaluation"] = eval_result.to_dict()

        # Append agent evaluation to history
        eval_history = self.memory.data.setdefault("agent_eval_history", [])
        eval_history.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "customer_message": message,
            "agent_response": result.response,
            **eval_result.to_dict()
        })
        self.memory.data["agent_eval_history"] = eval_history[-50:]

        insights = self._build_insights(result, sentiment, score)
        metrics["last_insights"] = insights
        metrics["last_updated_at"] = datetime.now().isoformat(timespec="seconds")

        report = self.build_report(score.to_dict(), sentiment.to_dict(), insights)
        self.memory.data["learning_metrics"] = metrics
        self.memory.data["latest_metrics_report"] = report
        self.memory.save()
        return LearningSnapshot(metrics=metrics, insights=insights, report=report)

    def build_report(
        self,
        scoring: dict[str, Any] | None = None,
        sentiment: dict[str, Any] | None = None,
        insights: list[str] | None = None,
    ) -> dict[str, Any]:
        state = self.memory.data
        metrics = self._metrics()
        score_history = state.get("score_history", [])
        latest_score = scoring or (score_history[-1] if score_history else {})
        latest_sentiment = sentiment or {
            "sentiment": state.get("sentiment", "neutral"),
            "confidence": state.get("sentiment_confidence", 0.0),
            "reason": state.get("sentiment_reason", ""),
        }
        funnel = {
            "intent": state.get("intent", ""),
            "event_type": state.get("event_type", ""),
            "room": state.get("room") or state.get("recommended_option", ""),
            "location": state.get("location", ""),
            "participants": state.get("participants") or state.get("company_size", ""),
            "preferred_date": state.get("preferred_date", ""),
            "selected_slot": state.get("selected_slot", ""),
            "booking_confirmed": bool(state.get("booking_id") and state.get("booking_ref")),
            "whatsapp_confirmation": state.get("whatsapp_confirmation", {}),
        }
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "turns": metrics.get("turns", 0),
                "handoffs": metrics.get("handoff_count", 0),
                "confirmed_bookings": metrics.get("booking_confirmed_count", 0),
                "whatsapp_confirmations_sent": metrics.get("whatsapp_sent_count", 0),
            },
            "latest_sentiment": latest_sentiment,
            "latest_scoring": latest_score,
            "latest_agent_evaluation": metrics.get("latest_agent_evaluation") or {},
            "funnel": funnel,
            "distributions": {
                "intent_counts": metrics.get("intent_counts", {}),
                "sentiment_counts": metrics.get("sentiment_counts", {}),
            },
            "insights": insights if insights is not None else metrics.get("last_insights", []),
        }

    def _metrics(self) -> dict[str, Any]:
        metrics = self.memory.data.get("learning_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        metrics.setdefault("turns", 0)
        metrics.setdefault("intent_counts", {})
        metrics.setdefault("sentiment_counts", {})
        metrics.setdefault("handoff_count", 0)
        metrics.setdefault("booking_confirmed_count", 0)
        metrics.setdefault("whatsapp_sent_count", 0)
        metrics.setdefault("last_insights", [])
        return metrics

    @staticmethod
    def _increment(bucket: dict[str, int], key: str) -> None:
        bucket[key] = int(bucket.get(key, 0)) + 1

    @staticmethod
    def _build_insights(
        result: AgentResponse,
        sentiment: SentimentResult,
        score: ScoreResult,
    ) -> list[str]:
        insights: list[str] = []
        if score.booking_readiness >= 80 and not result.should_handoff:
            insights.append("High booking readiness; keep the next turn focused on availability or slot choice.")
        if score.escalation_risk >= 60:
            insights.append("Escalation risk is elevated; reduce qualification pressure and offer human support.")
        if sentiment.sentiment == "hesitant":
            insights.append("Customer is hesitant; offer a comparison or pause instead of pushing confirmation.")
        if score.lead_score >= 75:
            insights.append("Lead score is strong; customer is likely close to booking.")
        if not insights:
            insights.append("Conversation is stable; continue with the next missing detail.")
        return insights[:4]
