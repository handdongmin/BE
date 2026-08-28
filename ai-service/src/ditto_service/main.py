from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from openai import APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel, Field

from ditto_agent import configure, get, resume, start
from ditto_agent.llm.client import LLMClient
from ditto_agent.schema import DraftContext, StartResult

_invoke_lock = threading.RLock()
_checkpoint_connection: sqlite3.Connection | None = None


class UserContext(BaseModel):
    user_id: str
    name: str | None = None
    time_zone: str
    language: str | None = None


class WorkContext(BaseModel):
    start: str = "09:00"
    end: str = "18:00"
    days: list[str] = Field(
        default_factory=lambda: ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    )


class AttachmentContext(BaseModel):
    attachment_id: str
    file_name: str
    extracted_text: str | None = None


class StartSessionRequest(BaseModel):
    review_id: str | None = None
    draft: str = Field(min_length=1, max_length=4000)
    sender: UserContext
    receiver: UserContext
    receiver_work: WorkContext = Field(default_factory=WorkContext)
    recent_messages: list[str] = Field(default_factory=list, max_length=10)
    attachments: list[AttachmentContext] = Field(default_factory=list, max_length=10)
    now_iso: str | None = None


class ResumeSessionRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)


class TranslationRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    source_language: str = Field(min_length=2, max_length=10)
    target_language: str = Field(min_length=2, max_length=10)


class TranslationResponse(BaseModel):
    translated_content: str
    source_language: str
    target_language: str


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("DITTO_INTERNAL_API_KEY", "")
    if expected and (
        x_internal_api_key is None
        or not hmac.compare_digest(expected, x_internal_api_key)
    ):
        raise PermissionError("invalid internal API key")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _checkpoint_connection
    checkpoint_path = Path(
        os.getenv("DITTO_CHECKPOINT_DB", "/app/data/ditto_checkpoints.db")
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _checkpoint_connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    configure(checkpointer=SqliteSaver(_checkpoint_connection))
    try:
        yield
    finally:
        _checkpoint_connection.close()
        _checkpoint_connection = None


app = FastAPI(
    title="Ditto AI Internal API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.exception_handler(PermissionError)
async def permission_error_handler(_: Request, exc: PermissionError) -> JSONResponse:
    return _error(401, "INVALID_INTERNAL_API_KEY", str(exc))


@app.exception_handler(LookupError)
async def lookup_error_handler(_: Request, exc: LookupError) -> JSONResponse:
    return _error(404, "THREAD_NOT_FOUND", str(exc))


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return _error(400, "INVALID_REQUEST", str(exc))


@app.exception_handler(RateLimitError)
async def rate_limit_handler(_: Request, __: RateLimitError) -> JSONResponse:
    return _error(429, "OPENAI_RATE_LIMITED", "OpenAI request limit exceeded")


@app.exception_handler(APITimeoutError)
async def timeout_handler(_: Request, __: APITimeoutError) -> JSONResponse:
    return _error(504, "AI_TIMEOUT", "OpenAI request timed out")


@app.exception_handler(APIError)
async def api_error_handler(_: Request, __: APIError) -> JSONResponse:
    return _error(502, "AI_PROCESSING_FAILED", "OpenAI request failed")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "llmMode": os.getenv("DITTO_LLM_MODE", "live"),
        "model": os.getenv("DITTO_OPENAI_MODEL", "o3-mini"),
        "translationModel": os.getenv("DITTO_TRANSLATION_MODEL", "gpt-4o-mini"),
    }


@app.post(
    "/internal/v1/sessions",
    response_model=StartResult,
    dependencies=[Depends(require_internal_key)],
)
def start_session(body: StartSessionRequest) -> StartResult:
    attachment_contexts = [
        f"{item.file_name}: {item.extracted_text or '텍스트 추출 없음'}"
        for item in body.attachments
    ]
    context = DraftContext(
        sender_id=body.sender.user_id,
        sender_name=body.sender.name,
        sender_tz=body.sender.time_zone,
        sender_lang=body.sender.language,
        receiver_id=body.receiver.user_id,
        receiver_name=body.receiver.name,
        receiver_tz=body.receiver.time_zone,
        receiver_lang=body.receiver.language,
        now_iso=body.now_iso,
        receiver_work_start=body.receiver_work.start,
        receiver_work_end=body.receiver_work.end,
        receiver_work_days=body.receiver_work.days,
        recent_messages=body.recent_messages,
        attachment_contexts=attachment_contexts,
    )
    with _invoke_lock:
        return start(body.draft.strip(), context)


@app.get(
    "/internal/v1/sessions/{thread_id}",
    response_model=StartResult,
    dependencies=[Depends(require_internal_key)],
)
def get_session(thread_id: str) -> StartResult:
    with _invoke_lock:
        return get(thread_id)


@app.post(
    "/internal/v1/sessions/{thread_id}/answers",
    response_model=StartResult,
    dependencies=[Depends(require_internal_key)],
)
def answer_session(thread_id: str, body: ResumeSessionRequest) -> StartResult:
    with _invoke_lock:
        return resume(thread_id, body.answer.strip())


@app.post(
    "/internal/v1/translations",
    response_model=TranslationResponse,
    dependencies=[Depends(require_internal_key)],
)
def translate_message(body: TranslationRequest) -> TranslationResponse:
    source_language = body.source_language.strip().lower()
    target_language = body.target_language.strip().lower()
    if source_language == target_language:
        return TranslationResponse(
            translated_content=body.content,
            source_language=source_language,
            target_language=target_language,
        )
    with _invoke_lock:
        result = LLMClient().translate_text(
            body.content.strip(), source_language, target_language
        )
    return TranslationResponse(
        translated_content=result.translated_content,
        source_language=source_language,
        target_language=target_language,
    )
