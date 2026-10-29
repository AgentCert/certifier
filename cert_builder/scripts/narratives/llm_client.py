"""
Phase 3 LLM Client Utility.

Provides a thin wrapper around Azure OpenAI for all Phase 3 LLM calls.
Handles: client init, retry with exponential backoff,
token tracking, and structured JSON schema enforcement.

Environment variables (AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, etc.)
must be set before running — e.g. via VS Code debug profile or shell env.
"""

import copy
import json
import os
import time
import urllib.error
import urllib.request
import warnings
from typing import Type

from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel

# Azure returns these fragments when a reasoning model rejects legacy params.
_UNSUPPORTED_PARAM_CODE = "unsupported_parameter"
_MAX_TOKENS_UNSUPPORTED = "max_tokens"
_TEMPERATURE_UNSUPPORTED = "temperature"

_AZURE_PLACEHOLDER = "YOUR_RESOURCE"


def _local_endpoint_reachable(base_url: str, timeout: float = 2.0) -> bool:
    """True when *base_url* answers on the network within *timeout* seconds.

    Any HTTP response (even 4xx/5xx) counts as reachable — it proves the
    server is running. Only network-level failures (connection refused,
    DNS error, timeout) return False.
    """
    try:
        urllib.request.urlopen(base_url.rstrip("/"), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # server responded with an HTTP error code — it is up
    except Exception:
        return False


def get_client() -> AzureOpenAI | OpenAI:
    """Create and return an LLM client from env vars.

    Priority order:

    1. Azure credentials present (AZURE_OPENAI_ENDPOINT set and not the
       ``YOUR_RESOURCE`` placeholder) — returns AzureOpenAI.

    2. No Azure credentials — auto-falls back to a local OpenAI-compatible
       endpoint (default ``http://127.0.0.1:11434/v1``, override via
       ``OPENAI_COMPATIBLE_BASE_URL``). Emits a ``UserWarning`` so the
       fallback is visible in logs without being fatal.

    3. Neither Azure credentials nor a reachable local endpoint — raises
       ``RuntimeError`` with a clear diagnosis rather than letting the
       caller discover the problem on the first LLM call.
    """
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if azure_endpoint and _AZURE_PLACEHOLDER not in azure_endpoint:
        return AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=azure_endpoint,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        )

    local_base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:11434/v1")

    if not _local_endpoint_reachable(local_base_url):
        raise RuntimeError(
            f"AZURE_OPENAI_ENDPOINT is not configured (or still contains the "
            f"'{_AZURE_PLACEHOLDER}' placeholder) and the local fallback endpoint "
            f"is not reachable at {local_base_url}. "
            f"Either set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY, or ensure "
            f"Ollama is running at that address "
            f"(override the address via OPENAI_COMPATIBLE_BASE_URL)."
        )

    warnings.warn(
        f"AZURE_OPENAI_ENDPOINT is not configured — falling back to local "
        f"OpenAI-compatible endpoint at {local_base_url}. "
        f"Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY to use Azure OpenAI instead.",
        UserWarning,
        stacklevel=2,
    )
    return OpenAI(
        base_url=local_base_url,
        api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY", "ollama"),
    )


def _prepare_strict_schema(schema: dict) -> dict:
    """Recursively patch a Pydantic JSON schema for OpenAI strict mode."""
    schema = copy.deepcopy(schema)

    def process(obj):
        if not isinstance(obj, dict):
            return obj
        if "$ref" in obj:
            return {"$ref": obj["$ref"]}
        if "properties" in obj:
            obj["required"] = list(obj["properties"].keys())
            obj["additionalProperties"] = False
            for key in obj["properties"]:
                obj["properties"][key] = process(obj["properties"][key])
        if "$defs" in obj:
            for name in obj["$defs"]:
                obj["$defs"][name] = process(obj["$defs"][name])
        if "items" in obj:
            obj["items"] = process(obj["items"])
        for key in ("anyOf", "oneOf", "allOf"):
            if key in obj:
                obj[key] = [process(item) for item in obj[key]]
        return obj

    return process(schema)


def call_llm(
    client: AzureOpenAI,
    system_prompt: str,
    user_prompt: str,
    *,
    deployment: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    retries: int = 3,
    expect_json: bool = True,
    response_schema: Type[BaseModel] | None = None,
    is_reasoning_model: bool = False,
) -> dict:
    """
    Send a prompt to Azure OpenAI and return the result.

    Args:
        client: AzureOpenAI client instance.
        system_prompt: System message setting the LLM role.
        user_prompt: User message with context and task.
        deployment: Azure deployment name (defaults to env var).
        temperature: Sampling temperature.
        max_tokens: Max response tokens.
        retries: Number of retry attempts on failure.
        expect_json: If True, parse response as JSON.
        response_schema: If provided, enforce this Pydantic model as a
            strict JSON schema via OpenAI structured output.
        is_reasoning_model: If True, omit temperature and max_tokens
            (reasoning models like o-series/GPT-5 do not support them).

    Returns:
        {
            "content": <validated Pydantic model if response_schema, else parsed JSON or raw string>,
            "model": <model name>,
            "tokens_used": <total tokens>,
            "raw_response": <raw response text>,
        }

    Raises:
        RuntimeError: If all retries are exhausted.
    """
    if deployment is None:
        if isinstance(client, AzureOpenAI):
            deployment = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4o")
        else:
            # Local OpenAI-compatible client -- use Ollama's real model name,
            # matching configs/configs.json's "gpt-4o" entry's model_id.
            deployment = os.getenv("OPENAI_COMPATIBLE_MODEL_ID", "qwen2.5:7b-instruct")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Build response_format
    if response_schema is not None:
        schema = _prepare_strict_schema(response_schema.model_json_schema())
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "schema": schema,
                "strict": True,
            },
        }
    elif expect_json:
        response_format = {"type": "json_object"}
    else:
        response_format = None

        # Starts from caller's hint; auto-flips to True on 400 'unsupported_parameter'.
    detected_reasoning = is_reasoning_model

    last_error = None
    for attempt in range(retries):
        try:
            gen_kwargs: dict = {}
            if detected_reasoning:
                # Reasoning models (o-series, GPT-5): use max_completion_tokens,
                # no temperature.
                gen_kwargs["max_completion_tokens"] = max_tokens
            else:
                gen_kwargs["temperature"] = temperature
                # Try max_completion_tokens first (required for gpt-4o / o-series models)
                # Fall back to max_tokens if not supported
                gen_kwargs["max_completion_tokens"] = max_tokens

            response = client.chat.completions.create(
                model=deployment,
                messages=messages,
                response_format=response_format,
                **gen_kwargs,
            )

            raw_text = response.choices[0].message.content.strip()
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0
            total_tokens = response.usage.total_tokens if response.usage else 0
            model_name = response.model or deployment

            if response_schema is not None:
                content = response_schema.model_validate(json.loads(raw_text))
            elif expect_json:
                content = json.loads(raw_text)
            else:
                content = raw_text

            return {
                "content": content,
                "model": model_name,
                "tokens_used": total_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "raw_response": raw_text,
            }

        except Exception as e:
            last_error = e

            # Auto-fallback: if Azure reports max_tokens/temperature unsupported
            # for this deployment, switch to reasoning-model params and retry
            # immediately (without consuming the backoff).
            err_str = str(e).lower()
            if (
                not detected_reasoning
                and _UNSUPPORTED_PARAM_CODE in err_str
                and (_MAX_TOKENS_UNSUPPORTED in err_str or _TEMPERATURE_UNSUPPORTED in err_str)
            ):
                print(
                    f"[llm_client] Deployment '{deployment}' rejected max_tokens/temperature; "
                    f"switching to reasoning-model parameters (max_completion_tokens)."
                )
                detected_reasoning = True
                continue

            if attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                time.sleep(wait)

    raise RuntimeError(
        f"LLM call failed after {retries} attempts: {last_error}"
    )