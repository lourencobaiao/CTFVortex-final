"""
Central configuration for the CTF Agents framework.

Change the model here once — every module picks it up automatically.
Values are read from environment variables, with optional .env file support.
"""

import os
from pathlib import Path

# Load .env file if python-dotenv is installed (optional dependency)
try:
    from dotenv import load_dotenv
    # Walk up from this file to find .env at the project root
    _project_root = Path(__file__).resolve().parent.parent
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

# ── LLM Model ────────────────────────────────────────────────────────
# Primary model used by all agents.
# Override at runtime with env var:  CTF_MODEL=google/gemma-3-27b-it:free
DEFAULT_MODEL = os.getenv(
    "CTF_MODEL",
    "meta-llama/llama-3.3-70b-instruct:free",
)

# Fallback chain: tried in order when the primary model fails (404 / rate-limit / 405)
FALLBACK_MODELS = [
    "google/gemma-3-27b-it:free",
    "qwen/qwen3-coder:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "stepfun/step-3.5-flash:free",
]

# ── OpenRouter API (fallback provider) ───────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Groq API (primary provider) ──────────────────────────────────────
# Groq's free tier is far more generous on RPM than OpenRouter's free
# models, so we try Groq first and fall back to OpenRouter on failure.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

# Primary Groq model. Override with env var CTF_GROQ_MODEL.
GROQ_DEFAULT_MODEL = os.getenv(
    "CTF_GROQ_MODEL",
    "llama-3.3-70b-versatile",
)

# Groq fallback chain (tried in order on 429/5xx/404).
GROQ_FALLBACK_MODELS = [
    "llama-3.1-8b-instant",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
]

# ── SSH / Docker defaults ────────────────────────────────────────────
SSH_HOST = "localhost"
SSH_PORT = 2222
SSH_USER = "sandboxuser"
SSH_PASS = "sandboxpass"

DOCKER_CONTAINER = "sandbox"
DOCKER_IMAGE = "sandbox:latest"

# ── Agent defaults ───────────────────────────────────────────────────
MAX_ITERATIONS = 30
FLAG_FORMAT = r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}"

# ── Docker Pivot scenario defaults ───────────────────────────────────
# Host-mapped SSH port for the DMZ entry container. Internal container
# is intentionally NOT host-mapped — its only reachable path is `ssh`
# from inside the DMZ host.
PIVOT_DMZ_SSH_PORT = int(os.getenv("PIVOT_DMZ_SSH_PORT", "2225"))
# Subnets the provider passes to `docker network create --subnet`.
# Picked outside Docker's default 172.16/12 range so they don't clash
# with the default bridge or the Diamond Vault comms net (10.55/24).
PIVOT_DMZ_SUBNET = os.getenv("PIVOT_DMZ_SUBNET", "10.88.0.0/24")
PIVOT_INTERNAL_SUBNET = os.getenv("PIVOT_INTERNAL_SUBNET", "10.99.0.0/24")
