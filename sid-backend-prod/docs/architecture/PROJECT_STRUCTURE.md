# Project Structure — Breakout Escape Rooms MVP

This document describes the production-grade codebase structure of the Breakout Agent project.

```
Breakout-Agent/
├── main.py                          # CLI runner for text and voice loops
├── AGENTS.md                        # Documentation of agent definitions
├── requirements.txt                 # Project dependencies
├── README.md                        # General developer setup guide
├── prompts/                         # System prompts, playbooks, and training examples
├── memory/                          # Session memory storage directory
├── logs/                            # Local transcripts and conversation logs
├── tests/                           # Unit and integration test suite
└── src/                             # Source root
    ├── __init__.py
    ├── response_composer.py         # Humanizer post-processor
    ├── agents/                      # Dialogue agents
    │   ├── __init__.py
    │   ├── inbound_agent.py         # Front-desk and FAQ agent
    │   ├── booking_agent.py         # Slot confirm/selection agent
    │   └── qualification_agent.py   # Intake form filler agent
    ├── config/                      # Configuration layer
    │   ├── __init__.py
    │   ├── settings.py              # Environment variables
    │   └── constants.py             # Default constants
    ├── core/                        # Common models and base classes
    │   ├── __init__.py
    │   ├── agent_response.py        # Shared data contract for agent replies
    │   ├── booking_provider.py      # Abstract scheduling provider contract
    │   ├── conversation_modes.py    # Enumerated modes (sales, faq, rescue, etc.)
    │   └── handoff_generator.py     # Schema packaging for down-stream nodes
    ├── integrations/                # External platforms and workflows
    │   ├── __init__.py
    │   ├── kreeda/                  # Kreeda booking API integration
    │   │   ├── __init__.py
    │   │   ├── breakout_api.py      # HTTP client layer
    │   │   ├── agent_contract.py    # Advanced contract API
    │   │   └── booking_provider.py  # Production BookingProvider implementation
    │   └── langgraph/               # LangGraph workflow integration
    │       ├── __init__.py
    │       └── booking_node.py      # LangGraph booking node handler
    ├── memory/                      # Conversational state storage
    │   ├── __init__.py
    │   ├── conversation_memory.py   # JSON state repository
    │   └── session_manager.py       # Session lifetime manager
    ├── orchestration/               # Multi-agent flows & business routing
    │   ├── __init__.py
    │   ├── booking_orchestrator.py  # Orchestrates live/simulator backend
    │   ├── conversation_manager.py  # Intent-driven routing coordinator
    │   └── router.py                # High-level state transitions router
    ├── services/                    # Business domain intelligence
    │   ├── __init__.py
    │   ├── intent_detector.py       # Entity and intent classifier
    │   ├── recommendation_engine.py # Game options classifier
    │   ├── slot_filler.py           # Dialogue slot filler
    │   └── conversation_intel.py    # General conversation layer helper
    ├── knowledge/                   # Local static files knowledge base
    │   ├── __init__.py
    │   ├── knowledge_loader.py      # Knowledge documents file loader
    │   ├── knowledge_retriever.py   # Context semantic matcher
    │   └── demo_knowledge.py        # Grounded faq maps
    ├── tools/                       # Reusable agent tool interfaces
    │   ├── __init__.py
    │   ├── availability_tool.py     # Wraps check_availability
    │   └── booking_tool.py          # Wraps prepare_booking
    ├── voice/                       # Audio pipeline integration
    │   ├── __init__.py
    │   ├── stt/                     # Speech-to-Text
    │   │   └── voice_input.py       # Microphone transcription (Whisper)
    │   └── tts/                     # Text-to-Speech
    │       └── voice_output.py      # Spoken output (pyttsx3)
    └── logger/                      # Diagnostics & audits
        ├── __init__.py
        └── transcript_logger.py     # Transcripts audit logger
```
