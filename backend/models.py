"""Validated public request and response types for API contract version 1."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AskRequest(ContractModel):
    question: str = Field(max_length=2_000)
    code: str = Field(max_length=12_000)
    error: str = Field(max_length=8_000)

    @field_validator("question", "code", "error")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must contain non-whitespace text")
        return value


class Section(ContractModel):
    title: Literal["Meaning", "Cause", "Fix", "Reasoning"]
    text: str
    code: str | None = None

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Section text must be nonempty")
        return value

    @model_validator(mode="after")
    def restrict_code(self) -> Self:
        if "code" in self.model_fields_set:
            if self.title != "Fix" or self.code is None:
                raise ValueError("Only Fix may include code, which must be a string")
        return self


class Source(ContractModel):
    chunk_id: str
    file_name: str
    title: str | None = None

    @field_validator("chunk_id", "file_name", "title")
    @classmethod
    def require_nonblank(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("Source fields must be nonempty strings when supplied")
        return value

    @field_validator("file_name")
    @classmethod
    def require_basename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Source file_name must be a basename")
        return value


class Metrics(ContractModel):
    ttft_seconds: float = Field(default=0, ge=0)
    tokens_per_second: float = Field(default=0, ge=0)
    total_latency_seconds: float = Field(default=0, ge=0)
    tokens_generated: int = Field(default=0, ge=0)


Status = Literal[
    "ok", "mock", "unsupported", "insufficient_context", "invalid_json",
    "request_too_large", "unsupported_media_type", "invalid_request",
    "rate_limited", "invalid_ai_response", "unavailable", "timeout", "internal_error",
]

HTTP_STATUS: dict[Status, int] = {
    "ok": 200, "mock": 200, "unsupported": 200, "insufficient_context": 200,
    "invalid_json": 400, "request_too_large": 413, "unsupported_media_type": 415,
    "invalid_request": 422, "rate_limited": 429, "invalid_ai_response": 502,
    "unavailable": 503, "timeout": 504, "internal_error": 500,
}


class AskResponse(ContractModel):
    status: Status
    message: str
    sections: list[Section]
    sources: list[Source]
    metrics: Metrics

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status in {"ok", "mock"}:
            if [section.title for section in self.sections] != [
                "Meaning", "Cause", "Fix", "Reasoning"
            ]:
                raise ValueError("Answers require four ordered sections")
            if self.status == "ok" and not self.sources:
                raise ValueError("An ok answer requires retrieved context")
            if self.status == "mock" and (
                self.sources or self.metrics.model_fields_set
                or "no ai/retrieval ran" not in self.message.lower()
            ):
                raise ValueError("Mock answers require a notice and empty sources/metrics")
        elif self.sections or self.sources or not self.message.strip():
            raise ValueError("Non-answer outcomes require a message and no sections/sources")
        if len({source.chunk_id for source in self.sources}) != len(self.sources):
            raise ValueError("Sources must be deduplicated by chunk ID")
        return self
