from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.rety87nm.ru/v1").strip()
MODEL = os.getenv("OLLAMA_MODEL", "bonsai2:27b").strip()
API_KEY = os.getenv("OLLAMA_API_KEY", "").strip() or None
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
MAX_RETRIES = max(0, int(os.getenv("LLM_MAX_RETRIES", "2")))
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1600"))
