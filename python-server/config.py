"""アプリケーション設定の一元管理."""

import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ── サーバー設定 ──
PORT = int(os.getenv("PORT", 3000))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEBUG_ENABLED = LOG_LEVEL == "DEBUG"

# ── CORS ──
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")
CORS_ALLOW_HEADERS = os.getenv(
    "CORS_ALLOW_HEADERS",
    "Content-Type, Authorization, ngrok-skip-browser-warning",
)

# ── OpenAI ──
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_REALTIME = os.getenv("MODEL_REALTIME", "gpt-realtime-mini-2025-12-15")
MODEL_EVAL = os.getenv("MODEL_EVAL", "gpt-4o-mini")
MODEL_TRANSCRIBE = os.getenv("MODEL_TRANSCRIBE", "gpt-4o-mini-transcribe")

# ── Google Drive プロンプト ──
INSTRUCTION_DOC_ID = os.getenv(
    "INSTRUCTION_DOC_ID", "1cQSHjpoijqEkbvU8h5ZlMzk3qIdy6u4gjL4qXM4BA9w"
)
COMPLIANCE_INSTRUCTION_DOC_ID = os.getenv(
    "COMPLIANCE_INSTRUCTION_DOC_ID", "17X_7fQzE14K6FFYWj9PQTPFLCHzsfVG8-phoPH-f37g"
)

# ── Recall.ai ──
RECALL_TOKEN = os.getenv("RECALL_TOKEN")
RECALL_API_BASE = os.getenv("RECALL_API_BASE", "https://ap-northeast-1.recall.ai/api/v1")
DISCONNECT_DELAY = int(os.getenv("DISCONNECT_DELAY", "8"))

# ── パス ──
BASE_DIR = Path(__file__).parent
SA_CREDENTIALS_PATH = BASE_DIR / "ame-ai-agent.json"
LOG_FILE_PATH = BASE_DIR / "server.log"
