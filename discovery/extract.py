"""
LLM extraction — multi-provider support.

Sends a paper's text through the combined extraction prompt and returns
structured JSON. Supports Anthropic, OpenRouter, Gemini, and OpenAI.

Usage:
    from discovery.extract import extract_paper
    result = extract_paper(paper_text, provider="anthropic", model="claude-sonnet-4-20250514")
"""

import json
import time
import re
import logging
from typing import Optional

from . import config

logger = logging.getLogger("discovery.extract")


def extract_paper(
    paper_text: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    prompt_text: Optional[str] = None,
    max_retries: int = 2,
) -> dict:
    """
    Extract structured data from a paper using an LLM.

    Args:
        paper_text: The full text (or abstract) of the paper
        provider: LLM provider (anthropic, openrouter, gemini, openai)
        model: Model name (provider-specific)
        api_key: API key (or use config/env)
        prompt_text: Custom prompt (or use default v_combined.txt)
        max_retries: Number of retries on failure

    Returns:
        Parsed JSON extraction result (dict)

    Raises:
        ExtractionError: If extraction fails after retries
    """
    provider = provider or config.get_provider()
    model = model or config.get_model()
    api_key = api_key or config.get_api_key(provider)
    prompt_text = prompt_text or config.get_prompt_text()

    if not api_key:
        raise ExtractionError(
            f"No API key found for provider '{provider}'. "
            f"Run: discovery config --provider {provider} --api-key YOUR_KEY"
        )

    if not paper_text or len(paper_text.strip()) < 50:
        raise ExtractionError("Paper text too short (need 50+ characters)")

    # Build the full prompt with paper text appended
    full_prompt = prompt_text + "\n\n---\n\n# PAPER TEXT\n\n" + paper_text

    # Dispatch to provider
    extractors = {
        "anthropic": _extract_anthropic,
        "openrouter": _extract_openrouter,
        "gemini": _extract_gemini,
        "openai": _extract_openai,
    }

    extractor = extractors.get(provider)
    if not extractor:
        raise ExtractionError(f"Unknown provider: {provider}. Supported: {list(extractors.keys())}")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            t0 = time.time()
            raw_response = extractor(full_prompt, model, api_key)
            elapsed = time.time() - t0

            # Parse JSON from response
            result = _parse_json_response(raw_response)

            # Add extraction metadata
            result["_meta"] = result.get("_meta", {})
            result["_meta"]["model"] = model
            result["_meta"]["provider"] = provider
            result["_meta"]["extraction_seconds"] = round(elapsed, 1)
            result["_meta"]["prompt_version"] = "v_combined"

            logger.info(f"Extraction succeeded in {elapsed:.1f}s (attempt {attempt})")
            return result

        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}"
            logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
        except ExtractionError as e:
            last_error = str(e)
            logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")

        if attempt < max_retries:
            time.sleep(2 * attempt)  # backoff

    raise ExtractionError(f"Extraction failed after {max_retries} attempts. Last error: {last_error}")


# ── Provider implementations ──────────────────────────────────────────


def _extract_anthropic(prompt: str, model: str, api_key: str) -> str:
    """Extract using Anthropic's Claude API."""
    try:
        import anthropic
    except ImportError:
        raise ExtractionError("Install anthropic: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _extract_openrouter(prompt: str, model: str, api_key: str) -> str:
    """Extract using OpenRouter (supports DeepSeek, Llama, Qwen, etc.)."""
    try:
        import httpx
    except ImportError:
        raise ExtractionError("Install httpx: pip install httpx")

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16384,
            "temperature": 0.1,
        },
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        raise ExtractionError(f"OpenRouter error: {data['error']}")

    return data["choices"][0]["message"]["content"]


def _extract_gemini(prompt: str, model: str, api_key: str) -> str:
    """Extract using Google Gemini API."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise ExtractionError("Install google-generativeai: pip install google-generativeai")

    genai.configure(api_key=api_key)
    gen_model = genai.GenerativeModel(model)
    response = gen_model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=16384,
            temperature=0.1,
        ),
    )
    return response.text


def _extract_openai(prompt: str, model: str, api_key: str) -> str:
    """Extract using OpenAI-compatible API."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ExtractionError("Install openai: pip install openai")

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=16384,
        temperature=0.1,
    )
    return response.choices[0].message.content


# ── JSON parsing ──────────────────────────────────────────────────────


def _parse_json_response(text: str) -> dict:
    """
    Parse JSON from LLM response, handling common formatting issues.

    LLMs sometimes wrap JSON in markdown code blocks or add commentary.
    This function extracts the JSON object robustly.
    """
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    # Match ```json ... ``` or ``` ... ```
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the outermost { ... } pair
    first_brace = text.find("{")
    if first_brace >= 0:
        # Find matching closing brace
        depth = 0
        in_string = False
        escape = False
        for i in range(first_brace, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[first_brace : i + 1])
                    except json.JSONDecodeError:
                        break

    raise json.JSONDecodeError("Could not find valid JSON in LLM response", text, 0)


class ExtractionError(Exception):
    """Raised when paper extraction fails."""
    pass
