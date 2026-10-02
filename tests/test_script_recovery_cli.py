"""Explicit local recovery CLI selects an endpoint, never arbitrary actor journal paths."""

from __future__ import annotations

import json

import pytest

from carla_agentic_toolkit import script_recovery_cli


@pytest.mark.parametrize("ok", [True, False])
def test_cli_emits_recovery_truth_and_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, ok: bool
) -> None:
    """CLI status derives from verified cleanup, with endpoint/deadline passed explicitly."""
    observed: list[tuple[str, int, float]] = []

    def recover(host: str, port: int, *, timeout_seconds: float) -> dict[str, object]:
        observed.append((host, port, timeout_seconds))
        return {"ok": ok, "recovery_required": not ok}

    monkeypatch.setattr(script_recovery_cli, "recover_script_ownership", recover)
    result = script_recovery_cli.main(["--host", "localhost", "--port", "3000"])
    assert result == (0 if ok else 1)
    assert json.loads(capsys.readouterr().out) == {"ok": ok, "recovery_required": not ok}
    assert observed == [("localhost", 3000, 10.0)]


def test_busy_endpoint_returns_json_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An active lease is not retried, expired, or cleared by recovery."""

    def busy(*_args: object, **_kwargs: object) -> dict[str, object]:
        message = "Simulator is owned by an active local run."
        raise RuntimeError(message)

    monkeypatch.setattr(script_recovery_cli, "recover_script_ownership", busy)
    assert script_recovery_cli.main([]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_type"] == "RuntimeError"
    assert payload["recovery_required"] is True


@pytest.mark.parametrize(
    "arguments", [["--port", "0"], ["--port", "65536"], ["--ownership-path", "/arbitrary"]]
)
def test_cli_rejects_invalid_endpoint_or_arbitrary_journal(arguments: list[str]) -> None:
    """Only the trusted endpoint lease can select recovery evidence."""
    with pytest.raises(SystemExit):
        script_recovery_cli.main(arguments)
