from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.conversation_memory import ConversationMemory
from src.inbound_agent import InboundAgent
from src.knowledge_loader import KnowledgeLoader
from src.transcript_logger import TranscriptLogger
from src.voice_input import VoiceInput
from src.voice_output import VoiceOutput


BASE_DIR = Path(__file__).resolve().parent
EXIT_COMMANDS = {"quit", "exit", "goodbye", "bye", "stop"}
POST_TTS_COOLDOWN_SECONDS = 0.75


def is_exit_command(message: str) -> bool:
    return message.lower().strip().rstrip(".!?") in EXIT_COMMANDS


def should_process_transcript(message: str) -> bool:
    return bool(message.strip())


def speak_then_resume_listening(voice_out: VoiceOutput, text: str, debug: bool = False) -> None:
    try:
        voice_out.speak(text)
    except Exception as exc:
        if debug:
            print(f"TTS ERROR: {exc}")
    time.sleep(POST_TTS_COOLDOWN_SECONDS)


def build_agent(args: argparse.Namespace) -> InboundAgent:
    knowledge = KnowledgeLoader(BASE_DIR / "knowledge").load()
    memory = ConversationMemory(BASE_DIR / "memory" / "session.json")
    if args.reset_memory:
        memory.reset()

    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=BASE_DIR / "prompts" / "inbound_prompt.txt",
        model=args.model,
        use_ollama=not args.no_ollama,
    )


def print_result(result: dict, show_debug: bool) -> None:
    print(f"\nAgent: {result['response']}")
    if show_debug:
        print("\n--- Debug ---")
        print(f"Intent: {result['intent']} ({result['intent_confidence']:.2f})")
        print(f"Recommendation: {result['recommendation']['option']}")
        print(f"Missing fields: {', '.join(result['missing_fields']) or 'none'}")
        print(f"Route: {result['route']['next_agent']} | handoff={result['route']['should_handoff']}")
        print("Handoff summary:")
        print(result["handoff_summary"])


def run_text_loop(args: argparse.Namespace) -> None:
    agent = build_agent(args)
    voice = VoiceOutput(enabled=args.speak)
    logger = TranscriptLogger(BASE_DIR / "logs" / "conversations")

    print("Breakout Inbound Agent is running locally.")
    print("Type a customer message. Commands: /handoff, /show_memory, /reset, /quit")

    while True:
        try:
            message = input("\nCustomer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not message:
            continue
        if message == "/quit":
            print("Goodbye.")
            break
        if message == "/reset":
            agent.memory.reset()
            print("Session memory reset.")
            continue
        if message == "/handoff":
            print(agent.handoff_generator.to_json(agent.memory.data))
            continue
        if message == "/show_memory":
            print(json.dumps(agent.memory.data, indent=2, ensure_ascii=False))
            continue

        result = agent.handle_message(message)
        logger.log_turn(message, result, agent.memory.data)
        print_result(result, args.debug)
        voice.speak(result["response"])


def run_voice_loop(args: argparse.Namespace) -> None:
    agent = build_agent(args)
    agent.memory.reset()
    voice_in = VoiceInput(model_name=args.whisper_model, language="en", debug=args.debug)
    voice_out = VoiceOutput(enabled=True, debug=args.debug)
    logger = TranscriptLogger(BASE_DIR / "logs" / "conversations")

    print("Voice mode is running. Press Ctrl+C to stop.")
    print("Speak after the recording prompt. Say 'quit' or 'goodbye' to stop.")
    greeting = "Hello, thank you for calling Breakout Escape Rooms. How may I help you today?"
    print(f"\nAgent: {greeting}")
    speak_then_resume_listening(voice_out, greeting, debug=args.debug)
    while True:
        try:
            message = voice_in.record_and_transcribe(seconds=args.record_seconds)
        except KeyboardInterrupt:
            print("\nGoodbye.")
            break
        if args.debug and message:
            print(f"WHISPER TRANSCRIPT: {message}")
        if not should_process_transcript(message):
            if voice_in.last_status == "unclear":
                print("\nAgent: Sorry, I didn't catch that. Could you repeat it?")
                speak_then_resume_listening(
                    voice_out,
                    "Sorry, I didn't catch that. Could you repeat it?",
                    debug=args.debug,
                )
            continue
        print(f"\nCustomer: {message}")
        if message == "/show_memory":
            print(json.dumps(agent.memory.data, indent=2, ensure_ascii=False))
            continue
        if is_exit_command(message):
            farewell = "Thank you for contacting Breakout. Have a great day."
            print(farewell)
            speak_then_resume_listening(voice_out, farewell, debug=args.debug)
            break
        result = agent.handle_message(message)
        logger.log_turn(message, result, agent.memory.data)
        print_result(result, args.debug)
        speak_then_resume_listening(voice_out, result["response"], debug=args.debug)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Breakout Escape Rooms Inbound Agent")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model name")
    parser.add_argument("--no-ollama", action="store_true", help="Use deterministic fallback responses only")
    parser.add_argument("--reset-memory", action="store_true", help="Clear memory/session.json at startup")
    parser.add_argument("--debug", action="store_true", help="Print intent, route, and handoff details")
    parser.add_argument("--speak", action="store_true", help="Enable pyttsx3 text-to-speech in text mode")
    parser.add_argument("--voice", action="store_true", help="Enable Whisper microphone input and pyttsx3 output")
    parser.add_argument("--whisper-model", default="small", help="Whisper model for voice mode")
    parser.add_argument("--record-seconds", type=int, default=12, help="Seconds to record per voice turn")
    return parser.parse_args()


if __name__ == "__main__":
    parsed_args = parse_args()
    if parsed_args.voice:
        run_voice_loop(parsed_args)
    else:
        run_text_loop(parsed_args)
