"""Optional language-model provider for FDR Copilot. Off by default.

The assistant works without any provider: it answers from built-in help and the caller's own records. A
provider only changes the WORDING of an answer whose facts were already established by the server, and it is
used only if CHAT_PROVIDER is configured. What it receives is limited by the caller (see app.chat.service):
the question and non-sensitive facts, never documents, amounts or names. The API key lives in server-side
settings and never reaches the browser. Any failure is turned into `ProviderError`, whose message is fixed
text, so nothing from the provider's response or the request can leak into logs or replies.

The transport is the standard library's urllib (no new dependency), called from the endpoint's worker thread.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

MAX_REPLY_CHARS = 2000
MAX_TOKENS = 400


class ProviderError(Exception):
    """The provider could not produce an answer. The text is fixed and safe to show. `code` lets the notice be
    shown in the user's language (see app.chat.i18n.PROVIDER_NOTICES); None means "use this English text"."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class ChatProvider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleProvider:
    """Any server that speaks the OpenAI chat-completions protocol (hosted or local)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._model = model
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self._model, "temperature": 0.2, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 (scheme is validated in settings)
                payload = json.loads(response.read(1_000_000))
            text = payload["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError("The AI provider did not respond.", "no_response") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("The AI provider sent an unusable reply.", "unusable") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("The AI provider sent an empty reply.", "empty")
        return text.strip()[:MAX_REPLY_CHARS]


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MAX_OUTPUT_TOKENS = 1024  # includes any "thinking" tokens a model spends before answering
QUOTA_MESSAGE = "The AI provider's usage limit has been reached for now."
_COOLDOWN_DEFAULT_SECONDS = 60
_COOLDOWN_MAX_SECONDS = 300

# After a quota or rate-limit refusal the provider is not asked again until the cool-down ends, so a spent free
# quota is not hammered by every question. It is process-wide because a provider object lives for one request.
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()


def _cooling_down(name: str) -> bool:
    with _cooldown_lock:
        return _cooldown_until.get(name, 0.0) > time.monotonic()


def _start_cooldown(name: str, retry_after: str | None) -> None:
    try:
        seconds = min(max(int(retry_after or ""), 1), _COOLDOWN_MAX_SECONDS)
    except ValueError:
        seconds = _COOLDOWN_DEFAULT_SECONDS
    with _cooldown_lock:
        _cooldown_until[name] = time.monotonic() + seconds


class GeminiProvider:
    """Google Gemini through its documented REST interface (models.generateContent).

    One attempt per question, no retries. The key travels only in the x-goog-api-key header (never in the URL,
    so it cannot end up in a proxy or access log). The model name comes from configuration. Every failure,
    including a quota refusal (HTTP 429), becomes a ProviderError with fixed text so nothing the service sent
    back, and nothing about the request, can reach logs or replies.
    """

    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float, base_url: str | None = None):
        self._url = f"{(base_url or GEMINI_BASE_URL).rstrip('/')}/models/{urllib.parse.quote(model, safe='.-_')}:generateContent"
        self._key = api_key
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        if _cooling_down(self.name):
            raise ProviderError(QUOTA_MESSAGE, "quota")
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS},
        }).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 (scheme is validated in settings)
                payload = json.loads(response.read(1_000_000))
        except urllib.error.HTTPError as err:
            if err.code == 429:
                _start_cooldown(self.name, err.headers.get("Retry-After") if err.headers else None)
                raise ProviderError(QUOTA_MESSAGE, "quota") from None
            if err.code in (400, 401, 403, 404):
                raise ProviderError("The AI provider rejected the request. Its configuration may need attention.", "rejected") from None
            raise ProviderError("The AI provider is temporarily unavailable.", "unavailable") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError("The AI provider did not respond.", "no_response") from None
        except ValueError:
            raise ProviderError("The AI provider sent an unusable reply.", "unusable") from None
        return self._text_of(payload)

    def _text_of(self, payload: object) -> str:
        try:
            if not isinstance(payload, dict):
                raise TypeError
            if (payload.get("promptFeedback") or {}).get("blockReason"):
                raise ProviderError("The AI provider declined to answer that.", "declined")
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("The AI provider sent an unusable reply.", "unusable") from None
        text = text.strip()
        if not text:
            raise ProviderError("The AI provider sent an empty reply.", "empty")
        if self._key in text:
            raise ProviderError("The AI provider sent an unusable reply.", "unusable")  # a credential must never be echoed back
        return text[:MAX_REPLY_CHARS]
