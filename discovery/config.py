"""
Configuration management for Discovery Engine.

Stores API keys, provider preference, batch settings, and paths.
Config file: ~/.discovery/config.json
"""

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".discovery"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = CONFIG_DIR / "data"
BATCH_DIR = DATA_DIR / "batch"
PROGRESS_FILE = DATA_DIR / "progress.jsonl"

# Default GitHub repo (where PRs are submitted)
DEFAULT_REPO = "discovery-engine/discovery-engine"

# Default HuggingFace datasets
DEFAULT_HF_INDEX = "discovery-engine/paper-index"
DEFAULT_HF_RESULTS = "discovery-engine/results"

# Prompt file (relative to package root)
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "v_combined.txt"


def ensure_dirs():
    """Create config and data directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config from ~/.discovery/config.json."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    """Save config to ~/.discovery/config.json."""
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_api_key(provider: Optional[str] = None) -> Optional[str]:
    """
    Get API key for the specified provider.

    Resolution order:
    1. Environment variable (ANTHROPIC_API_KEY, OPENROUTER_API_KEY, etc.)
    2. Config file
    """
    config = load_config()
    provider = provider or config.get("provider", "anthropic")

    env_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "openai": "OPENAI_API_KEY",
    }

    # Try environment variable first
    env_var = env_vars.get(provider)
    if env_var:
        key = os.environ.get(env_var)
        if key:
            return key

    # Fall back to config file
    return config.get(f"{provider}_api_key")


def get_provider() -> str:
    """Get the configured LLM provider."""
    config = load_config()
    return config.get("provider", "anthropic")


def get_model() -> str:
    """Get the configured model name."""
    config = load_config()
    provider = config.get("provider", "anthropic")
    defaults = {
        "anthropic": "claude-sonnet-4-20250514",
        "openrouter": "deepseek/deepseek-chat",
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o",
    }
    return config.get("model", defaults.get(provider, "claude-sonnet-4-20250514"))


def get_batch_size() -> int:
    """How many papers to collect before submitting a PR."""
    config = load_config()
    return config.get("batch_size", 25)


def get_github_user() -> Optional[str]:
    """Get the configured GitHub username (for PR attribution)."""
    config = load_config()
    return config.get("github_user") or os.environ.get("GITHUB_USER")


def get_prompt_text() -> str:
    """Load the extraction prompt from disk."""
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Extraction prompt not found at {PROMPT_FILE}. "
            f"Make sure you cloned the full repository."
        )
    return PROMPT_FILE.read_text(encoding="utf-8")
