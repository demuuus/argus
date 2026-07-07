"""
llm.py — LLM client wrapper for ARGUS AI.

Sends a context-enriched prompt to the configured LLM server
(llama.cpp OpenAI-compatible endpoint by default).

Environment variable:
    LLM_URL   — full URL, default: http://192.168.0.26:8080/v1/chat/completions
"""

import logging
import os

import requests
from typing import Dict

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://192.168.0.26:8080/v1/chat/completions"

def complete(system_prompt: str, user_message: str, max_tokens: int = 512) -> Dict[str, str]:
    """
    Send a chat completion request to the LLM.

    Args:
        system_prompt (str): The system prompt.
        user_message (str): The user message.
        max_tokens (int, optional): The maximum number of tokens to generate. Defaults to 512.

    Returns:
        Dict[str, str]: A dictionary containing the answer and the number of tokens used.
    Raises:
        requests.exceptions.ConnectionError — LLM server is offline.
        Exception — any other failure.
    """
    url = os.environ.get("LLM_URL", _DEFAULT_URL)

    response = requests.post(
        url,
        json={
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()
    usage  = result.get("usage", {})
    answer = result["choices"][0]["message"]["content"].strip()
    return {
        "answer": answer,
        "tokens": usage.get("completion_tokens", 0),
    }

