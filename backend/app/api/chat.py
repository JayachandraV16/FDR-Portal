"""FDR Copilot endpoint: a read-only assistant.

  POST /chat   any signed-in role (session, Origin and CSRF are enforced as for every unsafe request)

The reply is built from built-in help and from queries restricted to what the caller may already see
(app/chat/tools.py). The assistant has no write tools: it cannot approve, reject, release, upload or change
anything, and an instruction in a message has nothing to act on. An external language model is optional
(CHAT_PROVIDER); without one, or if it fails, the built-in answer is returned and labelled as such. Message
text is never logged and never stored; nothing about a conversation is kept on the server.
"""

import threading
import time
import uuid
from collections import deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_authenticated
from app.chat import i18n, knowledge, service
from app.chat.provider import ChatProvider, GeminiProvider, OpenAICompatibleProvider
from app.config import Settings, get_settings
from app.db import get_db

router = APIRouter(tags=["chat"])

RATE_LIMIT = 30  # messages per user per window
RATE_WINDOW_SECONDS = 300
Page = Literal[
    "overview", "fdrs", "fdr_new", "fdr_edit", "fdr_detail", "documents", "documents_detail", "verification", "verification_detail",
    "review", "review_detail", "alerts", "exceptions", "release", "release_detail", "reports", "calendar", "workload", "security",
    "import", "admin", "default",
]
Language = Literal["en", "hi", "mr"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    page: Page | None = None  # only used to choose suggestions
    fdr_id: uuid.UUID | None = None  # the record the user is looking at; access is still checked
    language: Language = "en"  # the language the user wants the answer in
    intent: Literal["message", "explain_page"] = "message"  # explain_page answers from curated page help; no AI involved


class ChatFact(BaseModel):
    label: str
    value: str


class ChatLink(BaseModel):
    label: str
    path: str


class ChatReply(BaseModel):
    reply: str
    source: Literal["built-in", "llm"]  # who worded the reply; the facts always come from the server
    kind: Literal["help", "records", "unknown"]
    notice: str | None
    provider: str | None = None  # which AI service worded the reply when source is "llm"; None for built-in answers
    language: Language = "en"  # the language the reply is actually in (English if no translation exists)
    facts: list[ChatFact]
    links: list[ChatLink]
    suggestions: list[str]


class RateLimiter:
    """A small per-user limit so the assistant (and any paid provider behind it) cannot be hammered."""

    def __init__(self, limit: int = RATE_LIMIT, window: int = RATE_WINDOW_SECONDS):
        self.limit, self.window = limit, window
        self._hits: dict[uuid.UUID, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: uuid.UUID) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(user_id, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


limiter = RateLimiter()


def get_chat_provider(settings: Settings = Depends(get_settings)) -> ChatProvider | None:
    """The configured AI provider, or None (built-in answers only) if none is configured or the configuration is
    unusable. A bad configuration never raises: the assistant keeps working without the provider."""
    name = (settings.chat_provider or "none").strip().lower()
    if name == "none" or settings.chat_provider_problem:
        return None
    key = settings.chat_api_key.get_secret_value().strip()
    if name == "gemini":
        return GeminiProvider(key, settings.chat_model, settings.chat_timeout_seconds, settings.chat_base_url)
    return OpenAICompatibleProvider(settings.chat_base_url, key, settings.chat_model, settings.chat_timeout_seconds)


@router.post("/chat", response_model=ChatReply)
def chat(
    body: ChatRequest,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
    settings: Settings = Depends(get_settings),
    provider: ChatProvider | None = Depends(get_chat_provider),
) -> ChatReply:
    response.headers["Cache-Control"] = "no-store"
    if len(body.message) > settings.chat_max_message_chars:
        raise HTTPException(status_code=422, detail=f"Please keep your question under {settings.chat_max_message_chars} characters.")
    if not limiter.allow(auth.user.id):
        raise HTTPException(status_code=429, detail="You are sending messages too quickly. Please wait a moment.")

    lang = body.language
    if body.intent == "explain_page":
        # Curated page help: complete without any provider, so none is called and no record data is sent anywhere.
        built_in = service.explain_page(db, auth.user, body.page, body.fdr_id, lang)
        return ChatReply(
            reply=built_in.text, source="built-in", kind="help", notice=None, provider=None, language=lang,
            facts=[], links=[], suggestions=[],
        )

    built_in = service.answer(db, auth.user, body.message, fdr_id=body.fdr_id, language=lang)
    reply, notice, source, provider_name = built_in.text, None, "built-in", None
    if provider is not None:
        worded, notice = service.reword(provider, body.message, built_in, lang)
        if worded is not None:
            reply, source, provider_name = worded, "llm", provider.name
    elif settings.chat_provider_problem:
        notice = i18n.notice(lang, "not_set_up")
    else:
        notice = i18n.notice(lang, "not_configured")
    shown_language = lang if source == "llm" else built_in.language
    if lang != "en" and shown_language != lang:  # asked for Hindi or Marathi, but the built-in answer exists only in English
        notice = f"{i18n.NOT_TRANSLATED[lang]} {notice}" if notice else i18n.NOT_TRANSLATED[lang]
    return ChatReply(
        reply=reply, source=source, kind=built_in.kind, notice=notice, provider=provider_name, language=shown_language,
        facts=[ChatFact(label=f.label, value=f.value) for f in built_in.facts],
        links=[ChatLink(label=l.label, path=l.path) for l in built_in.links],
        suggestions=knowledge.suggestions_for(body.page, lang) if built_in.kind == "unknown" else [],
    )


@router.get("/chat/suggestions")
def suggestions(page: Page | None = None, language: Language = "en", auth: AuthContext = Depends(require_authenticated)) -> dict:
    return {"suggestions": knowledge.suggestions_for(page, language), "capabilities": knowledge.capabilities(language)}
