"""Step 1 API: strict validation and an explicitly enabled development fixture."""

import json
import logging
import os
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from backend.models import AskRequest, AskResponse, HTTP_STATUS, Status

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    mock_mode: bool = False
    max_body_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if type(self.max_body_bytes) is not int or self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be a positive integer")

    @classmethod
    def from_env(cls) -> "Settings":
        mock_mode = os.getenv("API_MOCK_MODE", "false").lower()
        if mock_mode not in {"true", "false"}:
            raise ValueError("API_MOCK_MODE must be true or false")
        return cls(
            mock_mode=mock_mode == "true",
            max_body_bytes=int(os.getenv("API_MAX_BODY_BYTES", str(128 * 1024))),
        )


def outcome(status: Status, message: str) -> JSONResponse:
    payload = AskResponse(
        status=status, message=message, sections=[], sources=[], metrics={}
    )
    return JSONResponse(
        status_code=HTTP_STATUS[status], content=payload.model_dump(exclude_unset=True)
    )


def mock_answer() -> AskResponse:
    return AskResponse.model_validate({
        "status": "mock",
        "message": (
            "Development mock: no AI/retrieval ran. This fixed TypeError example "
            "does not diagnose your submission."
        ),
        "sections": [
            {"title": "Meaning", "text": "The example combines incompatible types."},
            {"title": "Cause", "text": "The example concatenates a string and an integer."},
            {"title": "Fix", "text": "Convert the integer to a string.",
             "code": '"Age: " + str(25)'},
            {"title": "Reasoning", "text": "Both operands are now strings."},
        ],
        "sources": [],
        "metrics": {},
    })


def reject_json_constant(value: str) -> None:
    raise ValueError("Nonstandard JSON constant")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(title="Python Troubleshooting API", version="1")
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return outcome("invalid_request", "Provide valid question, code, and error strings.")

    @app.exception_handler(ResponseValidationError)
    async def invalid_response(request: Request, exc: ResponseValidationError) -> JSONResponse:
        return outcome("internal_error", "The server could not prepare a valid response.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # Always select the HTTP code through the documented status mapping.
        status = next(
            (status for status, code in HTTP_STATUS.items() if code == exc.status_code and code >= 400),
            "internal_error",
        )
        messages = {
            "not_found": "The requested route was not found.",
            "method_not_allowed": "The HTTP method is not supported for this route.",
        }
        response = outcome(status, messages.get(status, "The request could not be completed."))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unexpected API failure (%s)", type(exc).__name__)
        return outcome("internal_error", "The server could not complete your request.")

    @app.post(
        "/api/ask",
        response_model=AskResponse,
        response_model_exclude_unset=True,
        responses={code: {"model": AskResponse} for code in set(HTTP_STATUS.values()) if code != 200},
        openapi_extra={"requestBody": {
            "required": True,
            "content": {"application/json": {"schema": AskRequest.model_json_schema()}},
        }},
    )
    async def ask(request: Request) -> AskResponse | JSONResponse:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return outcome("unsupported_media_type", "Send the request as application/json.")

        # Count actual bytes even when Content-Length is absent or inaccurate.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings.max_body_bytes:
                return outcome("request_too_large", "The request body exceeds the configured byte limit.")
            body.extend(chunk)
        try:
            data = json.loads(body.decode("utf-8"), parse_constant=reject_json_constant)
        except (ValueError, RecursionError):
            return outcome("invalid_json", "Send a valid UTF-8 JSON request body.")
        try:
            # Validation checks whitespace but retains every original character.
            AskRequest.model_validate(data)
        except ValidationError:
            return outcome(
                "invalid_request",
                "Provide only question, code, and error as nonblank strings "
                "of at most 2000, 12000, and 8000 characters respectively.",
            )

        if settings.mock_mode:
            return mock_answer()
        return outcome("unavailable", "The live AI/retrieval service is not implemented yet.")

    return app


app = create_app()
