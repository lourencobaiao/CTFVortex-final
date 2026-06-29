from abc import ABC, abstractmethod
import os
import time
import requests
from typing import List, Dict, Optional

from ctf_agents.config import (
    DEFAULT_MODEL, FALLBACK_MODELS, OPENROUTER_BASE_URL,
    GROQ_BASE_URL, GROQ_DEFAULT_MODEL, GROQ_FALLBACK_MODELS,
)


class AIProvider(ABC):
    """Abstract base class for AI API providers"""

    @abstractmethod
    def generate_command(self, conversation_history: List[Dict[str, str]]) -> str:
        """Generate a command based on conversation history"""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name"""
        pass


class OpenRouterProvider(AIProvider):
    """OpenRouter API implementation with automatic model fallback.

    If a model returns 404 (removed) or exhausts retries on 429 (rate-limited),
    the provider automatically switches to the next model in the fallback chain.
    """

    def __init__(self, api_key: str, model: str = None,
                 fallback_models: List[str] = None):
        self.api_key = api_key
        self.base_url = OPENROUTER_BASE_URL
        self.request_delay = 3
        self.max_retries = 5
        self.retry_delay = 2

        # Build ordered model list: explicit model first, then fallbacks
        if model:
            self._models = [model]
        else:
            self._models = [DEFAULT_MODEL]
        for m in (fallback_models or FALLBACK_MODELS):
            if m not in self._models:
                self._models.append(m)

        self.model = self._models[0]
        self._model_index = 0

    def _switch_to_next_model(self) -> bool:
        """Try to switch to the next fallback model.
        Returns True if a fallback was available, False if exhausted."""
        self._model_index += 1
        if self._model_index < len(self._models):
            self.model = self._models[self._model_index]
            print(f"Switching to fallback model: {self.model}")
            return True
        return False

    def generate_command(self, conversation_history: List[Dict[str, str]]) -> str:
        """Generate command using OpenRouter API with retry + model fallback."""

        while True:
            for retry in range(self.max_retries):
                try:
                    response = requests.post(
                        url=self.base_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "messages": conversation_history,
                            "stream": False,
                        },
                        timeout=30,
                    )

                    # Provider-level error (404, 405, 5xx) → try fallback
                    if response.status_code in (404, 405, 500, 502, 503):
                        print(f"⚠️  Model {self.model} returned {response.status_code}. "
                              f"Provider error: {response.text[:120]}")
                        if self._switch_to_next_model():
                            break  # restart retry loop with new model
                        raise Exception(
                            f"All models exhausted. Last tried: {self.model}"
                        )

                    response.raise_for_status()
                    response_data = response.json()

                    ai_response = response_data["choices"][0]["message"]["content"]
                    command = (ai_response.strip().strip('`')
                               .replace('bash', '').replace('shell', '').strip())

                    time.sleep(self.request_delay)
                    return command

                except requests.exceptions.HTTPError as e:
                    if response.status_code == 429:
                        if retry < self.max_retries - 1:
                            wait_time = self.retry_delay * (2 ** retry)
                            print(f"Rate limited on {self.model}. "
                                  f"Waiting {wait_time}s (retry {retry + 1}/{self.max_retries})...")
                            time.sleep(wait_time)
                        else:
                            # Retries exhausted on this model → fallback
                            print(f"Rate limit retries exhausted for {self.model}.")
                            if self._switch_to_next_model():
                                break  # restart retry loop with new model
                            raise Exception(
                                "All models exhausted after rate limiting."
                            )
                    elif response.status_code in (404, 405, 500, 502, 503):
                        # Provider error — already handled above, but just in case
                        print(f"⚠️  Model {self.model} returned {response.status_code}.")
                        if self._switch_to_next_model():
                            break
                        raise Exception(
                            f"All models exhausted. Last tried: {self.model}"
                        )
                    else:
                        raise
                except requests.exceptions.HTTPError:
                    raise
                except Exception as e:
                    raise Exception(f"API Error: {str(e)}")
            else:
                # All retries used without break → we're done
                return ""

    def get_provider_name(self) -> str:
        all_models = ", ".join(self._models)
        return f"OpenRouter ({self.model}) [fallbacks: {all_models}]"


class GroqProvider(AIProvider):
    """Groq API implementation (OpenAI-compatible endpoint).

    Same retry + model-fallback pattern as OpenRouter. Groq's free tier
    has much higher RPM than OpenRouter's free models, so this is the
    preferred primary provider.
    """

    def __init__(self, api_key: str, model: Optional[str] = None,
                 fallback_models: Optional[List[str]] = None):
        self.api_key = api_key
        self.base_url = GROQ_BASE_URL
        self.request_delay = 1
        self.max_retries = 4
        self.retry_delay = 2

        if model:
            self._models = [model]
        else:
            self._models = [GROQ_DEFAULT_MODEL]
        for m in (fallback_models or GROQ_FALLBACK_MODELS):
            if m not in self._models:
                self._models.append(m)

        self.model = self._models[0]
        self._model_index = 0

    def _switch_to_next_model(self) -> bool:
        self._model_index += 1
        if self._model_index < len(self._models):
            self.model = self._models[self._model_index]
            print(f"Switching to Groq fallback model: {self.model}")
            return True
        return False

    def generate_command(self, conversation_history: List[Dict[str, str]]) -> str:
        while True:
            for retry in range(self.max_retries):
                try:
                    response = requests.post(
                        url=self.base_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "messages": conversation_history,
                            "stream": False,
                        },
                        timeout=30,
                    )

                    if response.status_code in (400, 404, 405, 500, 502, 503):
                        print(f"⚠️  Groq model {self.model} returned "
                              f"{response.status_code}: {response.text[:120]}")
                        if self._switch_to_next_model():
                            break
                        raise Exception(
                            f"All Groq models exhausted. Last tried: {self.model}"
                        )

                    response.raise_for_status()
                    response_data = response.json()

                    ai_response = response_data["choices"][0]["message"]["content"]
                    command = (ai_response.strip().strip('`')
                               .replace('bash', '').replace('shell', '').strip())

                    time.sleep(self.request_delay)
                    return command

                except requests.exceptions.HTTPError:
                    if response.status_code == 429:
                        if retry < self.max_retries - 1:
                            wait_time = self.retry_delay * (2 ** retry)
                            print(f"Groq rate limited on {self.model}. "
                                  f"Waiting {wait_time}s (retry {retry + 1}/{self.max_retries})...")
                            time.sleep(wait_time)
                        else:
                            print(f"Groq rate limit retries exhausted for {self.model}.")
                            if self._switch_to_next_model():
                                break
                            raise Exception(
                                "All Groq models exhausted after rate limiting."
                            )
                    elif response.status_code in (400, 404, 405, 500, 502, 503):
                        if self._switch_to_next_model():
                            break
                        raise Exception(
                            f"All Groq models exhausted. Last tried: {self.model}"
                        )
                    else:
                        raise
                except Exception as e:
                    raise Exception(f"Groq API Error: {str(e)}")
            else:
                return ""

    def get_provider_name(self) -> str:
        all_models = ", ".join(self._models)
        return f"Groq ({self.model}) [fallbacks: {all_models}]"


class FallbackProvider(AIProvider):
    """Composite provider that tries a list of providers in order.

    When one provider raises (e.g. all its internal model fallbacks are
    exhausted), the next provider is tried. Useful for pairing a primary
    provider (Groq) with a backup (OpenRouter).
    """

    def __init__(self, providers: List[AIProvider]):
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider.")
        self._providers = providers
        self._index = 0

    @property
    def _current(self) -> AIProvider:
        return self._providers[self._index]

    @property
    def model(self) -> str:
        return getattr(self._current, "model", "?")

    def generate_command(self, conversation_history: List[Dict[str, str]]) -> str:
        last_err: Optional[Exception] = None
        while self._index < len(self._providers):
            try:
                return self._current.generate_command(conversation_history)
            except Exception as e:
                last_err = e
                print(f"⚠️  Provider {self._current.get_provider_name()} failed: {e}")
                self._index += 1
                if self._index < len(self._providers):
                    print(f"→ Falling back to {self._current.get_provider_name()}")
        raise Exception(
            f"All providers exhausted. Last error: {last_err}"
        )

    def get_provider_name(self) -> str:
        names = [p.get_provider_name() for p in self._providers]
        return f"Fallback[{' → '.join(names)}] (active: {self._current.get_provider_name()})"


def _looks_like_openrouter_model(name: str) -> bool:
    """Heuristic: OpenRouter model names are ``vendor/model[:tag]`` while
    Groq model names are flat (``llama-3.3-70b-versatile``) or
    ``vendor/model`` without a ``:free`` tag."""
    if not name:
        return False
    return ":free" in name or ":paid" in name


def make_default_provider(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> AIProvider:
    """Factory: build the default provider chain (Groq → OpenRouter).

    Each provider reads its own env var (``GROQ_API_KEY``,
    ``OPENROUTER_API_KEY``). If neither is set but a legacy ``api_key``
    argument is passed, it's used for whichever provider has an empty
    env var — Groq first. At least one key must resolve to non-empty.

    ``model`` is routed to the provider it matches: OpenRouter-style
    names (with ``:free``) go to OpenRouter; other names go to Groq. This
    lets legacy call sites that pass a config-default OpenRouter model
    name keep working without forcing it onto Groq (which would reject it).
    """
    groq_key = os.getenv("GROQ_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    # Legacy compatibility: if neither env var is set and the caller passed
    # a key, use it for whichever provider makes sense given the model name.
    if not groq_key and not openrouter_key and api_key:
        if _looks_like_openrouter_model(model or ""):
            openrouter_key = api_key
        else:
            groq_key = api_key

    model_for_openrouter = model if _looks_like_openrouter_model(model or "") else None
    model_for_groq = None if model_for_openrouter else model

    chain: List[AIProvider] = []
    if groq_key:
        chain.append(GroqProvider(api_key=groq_key, model=model_for_groq))
    if openrouter_key:
        chain.append(OpenRouterProvider(
            api_key=openrouter_key,
            model=model_for_openrouter,
        ))

    if not chain:
        raise Exception(
            "No API keys configured. Set GROQ_API_KEY (primary) and/or "
            "OPENROUTER_API_KEY (fallback) in your environment."
        )

    if len(chain) == 1:
        return chain[0]
    return FallbackProvider(chain)
