"""Opt-in Qwen transport preserving provider reasoning across native tool calls.

The pinned LiteLLM OpenAI response converter drops reasoning_content. Use the
SDK response directly only when Qwen's preserve_thinking is explicitly enabled.
"""
from functools import lru_cache

import litellm
from openai import APIError, OpenAI


@lru_cache(maxsize=8)
def _client(api_key, base_url, timeout):
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


def completion(*, model, api_key, base_url, timeout, api_version=None,
               custom_llm_provider=None, drop_params=True, **kwargs):
    """Preserve raw optional message fields while retaining LiteLLM's event types."""
    if api_version is not None or custom_llm_provider not in (None, 'openai'):
        raise ValueError('Qwen reasoning transport requires the OpenAI-compatible endpoint')
    try:
        response = _client(api_key, base_url, timeout).chat.completions.create(
            model=model.removeprefix('openai/'), **kwargs)
    except APIError as error:
        # Keep the existing LLM retry policy and metrics/error path.
        raise litellm.APIError(status_code=getattr(error, 'status_code', 500) or 500,
                               message=str(error), llm_provider='openai', model=model) from error
    return litellm.ModelResponse(**response.model_dump())
