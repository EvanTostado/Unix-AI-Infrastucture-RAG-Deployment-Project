import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import Settings, create_app, mock_answer
from backend.models import AskRequest, AskResponse, Metrics, Section, Source


VALID = {
    "question": "Why does this raise TypeError?",
    "code": '"Age: " + 25',
    "error": "TypeError: can only concatenate str to str",
}


@pytest.fixture
def client():
    with TestClient(create_app(Settings(mock_mode=True))) as client:
        yield client


def assert_failure(response, status_code, status):
    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert set(payload) == {"status", "message", "sections", "sources", "metrics"}
    assert payload["status"] == status
    assert payload["message"]
    assert payload["sections"] == payload["sources"] == []
    assert payload["metrics"] == {}


def test_mock_is_explicit_fixed_fixture(client):
    response = client.post("/api/ask", json=VALID)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "mock"
    assert "no AI/retrieval ran" in payload["message"]
    assert [s["title"] for s in payload["sections"]] == ["Meaning", "Cause", "Fix", "Reasoning"]
    assert all("code" not in s for s in payload["sections"] if s["title"] != "Fix")
    assert payload["sources"] == []
    assert payload["metrics"] == {}


def test_live_mode_does_not_fall_back_to_mock(monkeypatch):
    monkeypatch.delenv("API_MOCK_MODE", raising=False)
    with TestClient(create_app()) as client:
        assert_failure(client.post("/api/ask", json=VALID), 503, "unavailable")


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("API_MOCK_MODE", "true")
    monkeypatch.setenv("API_MAX_BODY_BYTES", "64")
    with TestClient(create_app()) as client:
        assert_failure(client.post("/api/ask", json=VALID), 413, "request_too_large")
    monkeypatch.setenv("API_MOCK_MODE", "yes")
    with pytest.raises(ValueError):
        create_app()


@pytest.mark.parametrize("field", VALID)
@pytest.mark.parametrize("value", [None, 42, 1.5, True, [], {}, "", " \n\t\r", "\u2003"])
def test_invalid_fields(client, field, value):
    assert_failure(client.post("/api/ask", json={**VALID, field: value}), 422, "invalid_request")


@pytest.mark.parametrize("field", VALID)
def test_missing_fields(client, field):
    assert_failure(client.post("/api/ask", json={k: v for k, v in VALID.items() if k != field}), 422, "invalid_request")


@pytest.mark.parametrize("body", [None, [], "text", 5, True, {**VALID, "extra": "no"}])
def test_invalid_root_and_unknown_fields(client, body):
    # Explicit serialization also sends JSON null instead of an empty body.
    assert_failure(client.post("/api/ask", content=json.dumps(body), headers={"Content-Type": "application/json"}), 422, "invalid_request")


@pytest.mark.parametrize("field,limit", [("question", 2000), ("code", 12000), ("error", 8000)])
def test_character_boundaries(client, field, limit):
    assert client.post("/api/ask", json={**VALID, field: "é" * limit}).status_code == 200
    assert_failure(client.post("/api/ask", json={**VALID, field: "é" * (limit + 1)}), 422, "invalid_request")


def test_request_preserves_indentation_and_traceback():
    payload = {"question": "  Why?\n", "code": "def f():\n    return 1\n\n", "error": "Traceback:\n  File example.py\nTypeError\n"}
    assert AskRequest.model_validate(payload).model_dump() == payload


@pytest.mark.parametrize("body", [b"", b"{", b'{"question":}', b'{} trailing', b'{"x":NaN}', b'{"x":Infinity}', b'"\xff"'])
def test_malformed_json(client, body):
    assert_failure(client.post("/api/ask", content=body, headers={"Content-Type": "application/json"}), 400, "invalid_json")


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "application/problem+json"])
def test_unsupported_media_type(client, content_type):
    headers = {} if content_type is None else {"Content-Type": content_type}
    assert_failure(client.post("/api/ask", content=json.dumps(VALID), headers=headers), 415, "unsupported_media_type")


def test_json_content_type_parameters(client):
    assert client.post("/api/ask", content=json.dumps(VALID), headers={"Content-Type": "Application/JSON; charset=utf-8"}).status_code == 200


def test_complete_body_byte_limit(client):
    body = json.dumps(VALID).encode()
    # Whitespace is valid JSON padding and counts toward the byte limit.
    padded = body + b" " * (128 * 1024 - len(body))
    assert client.post("/api/ask", content=padded, headers={"Content-Type": "application/json"}).status_code == 200
    assert_failure(client.post("/api/ask", content=padded + b" ", headers={"Content-Type": "application/json"}), 413, "request_too_large")


def test_chunked_body_enforces_actual_limit():
    import asyncio

    async def send():
        app = create_app(Settings(mock_mode=True, max_body_bytes=64))

        async def chunks():
            yield b" " * 40
            yield b" " * 40

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/ask", content=chunks(), headers={"Content-Type": "application/json"})

    assert_failure(asyncio.run(send()), 413, "request_too_large")


def test_invalid_request_never_reaches_mock(monkeypatch, client):
    def forbidden():
        pytest.fail("Invalid input reached answer generation")

    monkeypatch.setattr("backend.app.mock_answer", forbidden)
    assert_failure(client.post("/api/ask", json={}), 422, "invalid_request")


def test_submitted_code_is_only_text(client, tmp_path):
    marker = tmp_path / "executed"
    code = f"from pathlib import Path\nPath({str(marker)!r}).touch()"
    assert client.post("/api/ask", json={**VALID, "code": code}).status_code == 200
    assert not marker.exists()


def test_unexpected_failure_has_safe_envelope(monkeypatch):
    def broken():
        raise RuntimeError("secret internal path /private/config")

    monkeypatch.setattr("backend.app.mock_answer", broken)
    with TestClient(create_app(Settings(mock_mode=True)), raise_server_exceptions=False) as client:
        response = client.post("/api/ask", json=VALID)
    assert_failure(response, 500, "internal_error")
    assert "private" not in response.text


def test_invalid_outgoing_response_has_safe_envelope(monkeypatch, client):
    monkeypatch.setattr("backend.app.mock_answer", lambda: {"status": "mock"})
    assert_failure(client.post("/api/ask", json=VALID), 500, "internal_error")


def test_openapi_describes_contract(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/ask"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["required"]) == set(VALID)
    assert schema["additionalProperties"] is False
    assert "503" in operation["responses"]


@pytest.mark.parametrize("change", [
    {"sections": []},
    {"sections": list(reversed(mock_answer().model_dump(exclude_unset=True)["sections"]))},
    {"status": "ok"},
    {"message": "A real answer"},
    {"metrics": {"tokens_generated": 0}},
    {"status": "unavailable"},
    {"unknown": "value"},
])
def test_response_rejects_contract_violations(change):
    with pytest.raises(ValidationError):
        AskResponse.model_validate({**mock_answer().model_dump(exclude_unset=True), **change})


def test_live_response_and_missing_metrics():
    payload = mock_answer().model_dump(exclude_unset=True)
    payload.update(status="ok", message="", sources=[{"chunk_id": "chunk-1", "file_name": "python.pdf"}], metrics={"tokens_generated": 10})
    result = AskResponse.model_validate(payload).model_dump(exclude_unset=True)
    assert result == payload
    payload["sources"] *= 2
    with pytest.raises(ValidationError):
        AskResponse.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"title": "Cause", "text": "text", "code": "pass"},
    {"title": "Fix", "text": "text", "code": None},
    {"title": "Fix", "text": " \n"},
    {"title": "Other", "text": "text"},
])
def test_invalid_sections(payload):
    with pytest.raises(ValidationError):
        Section.model_validate(payload)


@pytest.mark.parametrize("change", [{"chunk_id": ""}, {"file_name": "/internal/python.pdf"}, {"file_name": "C:\\internal\\python.pdf"}, {"title": " "}, {"title": None}])
def test_invalid_sources(change):
    with pytest.raises(ValidationError):
        Source.model_validate({"chunk_id": "chunk-1", "file_name": "python.pdf", **change})


@pytest.mark.parametrize("metrics", [{"tokens_generated": 1.5}, {"tokens_generated": True}, {"tokens_generated": -1}, {"ttft_seconds": -1}, {"tokens_per_second": float("inf")}, {"total_latency_seconds": float("nan")}, {"status": "ok"}])
def test_invalid_metrics(metrics):
    with pytest.raises(ValidationError):
        Metrics.model_validate(metrics)
