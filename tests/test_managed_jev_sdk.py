"""Optional SDK contract tests use a real HTTP serializer with local fake responses."""

from __future__ import annotations

import asyncio
import json

import pytest

from carla_agentic_toolkit.managed_jev_questions import MODEL, QUESTION_ID
from carla_agentic_toolkit.managed_jev_transport import ProviderFailure, TypeSafeTransport

httpx2 = pytest.importorskip("httpx2")
sdk = pytest.importorskip("typesafe_sdk")


@pytest.fixture
def http_responses(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Response]:
    """Keep actual SDK validation while intercepting all outgoing HTTP calls."""
    responses: list[httpx2.Response] = []
    client_class = sdk.AsyncTypeSafeClient

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url).startswith("https://api.typesafe.ai/")
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert set(body["questions"]) == {QUESTION_ID}
        assert set(body["questions"][QUESTION_ID]["criteria"]) == {"defer", "merge"}
        return responses.pop(0)

    def client(**kwargs: object) -> sdk.AsyncTypeSafeClient:
        assert kwargs["base_url"] == "https://api.typesafe.ai"
        retry = kwargs["retry"]
        assert isinstance(retry, sdk.RetryPolicy)
        assert retry.max_retries == 0
        kwargs["transport"] = httpx2.MockTransport(handle)
        return client_class(**kwargs)

    monkeypatch.setattr(sdk, "AsyncTypeSafeClient", client)
    return responses


def test_sdk_choice_wire_format_and_close(http_responses: list[httpx2.Response]) -> None:
    """Pinned SDK accepts the actual Choice schema and preserves absent usage."""
    http_responses.append(
        httpx2.Response(
            200,
            headers={"x-typesafe-request-id": "actual-header-id"},
            json={
                "model": MODEL,
                "usage": {},
                "answers": {
                    QUESTION_ID: {
                        "type": "choice",
                        "choice": "merge",
                        "confidence": 0.51,
                        "probabilities": {"defer": 0.49, "merge": 0.51},
                    },
                },
            },
        )
    )

    async def run() -> None:
        transport = TypeSafeTransport("synthetic-test-key", 1.0)
        result = await transport.infer('{"gap_m":25}', {"defer": "wait", "merge": "begin"})
        assert result.choice == "merge"
        assert result.input_tokens is None
        assert result.request_id == "actual-header-id"
        await transport.aclose()

    asyncio.run(run())
    assert not http_responses


def test_sdk_auth_error_does_not_expose_body_or_retry(
    http_responses: list[httpx2.Response],
) -> None:
    """A secret echoed in an error body cannot escape the transport boundary."""
    http_responses.append(httpx2.Response(401, json={"error": "private-credential"}))

    async def run() -> None:
        transport = TypeSafeTransport("synthetic-test-key", 1.0)
        try:
            with pytest.raises(ProviderFailure, match="authentication") as failure:
                await transport.infer("{}", {"defer": "wait", "merge": "begin"})
            assert "private-credential" not in str(failure.value)
            assert not failure.value.retryable
        finally:
            await transport.aclose()

    asyncio.run(run())
    assert not http_responses
