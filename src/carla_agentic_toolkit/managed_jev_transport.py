"""The optional SDK is isolated to the trusted server process and fixed endpoint."""

from http import HTTPStatus
from typing import Protocol

from carla_agentic_toolkit.managed_jev_questions import (
    INSTRUCTIONS,
    MODEL,
    QUESTION_ID,
    JevReply,
)


class JevTransport(Protocol):
    """Retained async provider client; fakes require neither secrets nor network."""

    async def infer(self, state_json: str, criteria: dict[str, str]) -> JevReply:
        """Make exactly one HTTP attempt."""
        ...

    async def aclose(self) -> None:
        """Release retained provider connections."""
        ...


class ProviderFailure(Exception):  # noqa: N818
    """Sanitized provider failure, deliberately excluding response bodies and headers."""

    def __init__(self, category: str, *, retryable: bool, request_id: str | None = None) -> None:
        """Expose only explicitly safe trace fields."""
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.request_id = request_id


class TypeSafeTransport:
    """Use the official supported Choice API with hidden retries disabled."""

    def __init__(self, api_key: str, timeout_seconds: float) -> None:
        """Retain one SDK client, with endpoint and model fixed by reviewed code."""
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy  # noqa: PLC0415

        self._client: AsyncTypeSafeClient = AsyncTypeSafeClient(
            api_key=api_key,
            model=MODEL,
            base_url="https://api.typesafe.ai",
            retry=RetryPolicy(max_retries=0),
            timeout=timeout_seconds,
        )

    async def infer(self, state_json: str, criteria: dict[str, str]) -> JevReply:
        """Make one independent Choice request and normalize documented response fields."""
        from typesafe_sdk import (  # noqa: PLC0415
            Choice,
            TypeSafeAPIConnectionError,
            TypeSafeAPIError,
        )

        try:
            response = await self._client.system_one(
                state=state_json,
                questions={QUESTION_ID: Choice(instructions=INSTRUCTIONS, criteria=criteria)},
                model=MODEL,
            )
        except TypeSafeAPIError as error:
            raise _api_failure(error.status, error.request_id) from None
        except TypeSafeAPIConnectionError:
            category = "connection"
            raise ProviderFailure(category, retryable=True) from None
        answer = response.choices.get(QUESTION_ID)
        return JevReply(
            model=response.model,
            choice=answer.choice if answer else None,
            probabilities=answer.probabilities if answer else None,
            request_id=response.request_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def aclose(self) -> None:
        """Close the actual retained SDK client."""
        await self._client.aclose()


def _api_failure(status: int, request_id: str | None) -> ProviderFailure:
    category = {401: "authentication", 403: "authentication", 429: "throttled"}.get(
        status,
        "provider_error",
    )
    return ProviderFailure(
        category,
        retryable=status == HTTPStatus.TOO_MANY_REQUESTS
        or status >= HTTPStatus.INTERNAL_SERVER_ERROR,
        request_id=request_id,
    )
