"""
Configuration settings for the Breakout Agent.
Loads environment variables and exposes configuration parameters.
"""

import os
from .constants import DEFAULT_BOOKING_BASE_URL, DEFAULT_OPENAI_MODEL

# API Keys
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID", "")
VAPI_TOOL_ID = os.environ.get("VAPI_TOOL_ID", "")
BOOKING_API_KEY = os.environ.get("BOOKING_API_KEY", "")

# Base URLs
BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", DEFAULT_BOOKING_BASE_URL)

# App Modes and configurations
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
BREAKOUT_DEBUG = os.environ.get("BREAKOUT_DEBUG", "false").lower() == "true"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
