"""GitFlow sends contributor work and release work to the intended branches."""

from __future__ import annotations

import pytest

from scripts import check_branch_policy


@pytest.mark.parametrize(
    ("base", "head", "same_repository"),
    [
        ("develop", "feature/42-cameras", False),
        ("develop", "fix/96-walker", False),
        ("develop", "docs/setup", False),
        ("develop", "chore/dependencies", True),
        ("develop", "dependabot/uv/mcp-2.2.0", True),
        ("develop", "WIP/existing-work", True),
        ("develop", "main", True),
        ("develop", "release/0.1.0", True),
        ("develop", "hotfix/cleanup", True),
        ("main", "release/0.1.0", True),
        ("main", "hotfix/cleanup", False),
        ("main", "WIP/release/gitflow-policy", True),
        ("main", "WIP/hotfix/cleanup", True),
    ],
)
def test_supported_gitflow_routes(base: str, head: str, *, same_repository: bool) -> None:
    """Fork contributions, release PRs, and upstream back-merges are accepted."""
    assert check_branch_policy.policy_error(base, head, same_repository=same_repository) is None


@pytest.mark.parametrize(
    ("base", "head", "same_repository"),
    [
        ("main", "feature/new-api", True),
        ("main", "fix/walker", False),
        ("main", "develop", True),
        ("main", "dependabot/uv/mcp-2.2.0", True),
        ("main", "WIP/unclassified-change", True),
        ("main", "release/", True),
        ("main", "release-lookalike", True),
        ("develop", "main", False),
        ("develop", "develop", False),
        ("develop", "unclassified-change", True),
        ("develop", "feature/", True),
        ("unprotected-target", "feature/new-api", True),
        ("", "", False),
    ],
)
def test_unsupported_gitflow_routes(base: str, head: str, *, same_repository: bool) -> None:
    """Wrong targets and misleading branch names fail with actionable guidance."""
    error = check_branch_policy.policy_error(base, head, same_repository=same_repository)
    assert error is not None
    assert "develop" in error


@pytest.mark.parametrize(("base", "expected"), [("develop", 0), ("main", 1)])
def test_workflow_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], base: str, expected: int
) -> None:
    """The required Actions job fails when a fork feature targets main."""
    monkeypatch.setenv("GITHUB_BASE_REF", base)
    monkeypatch.setenv("GITHUB_HEAD_REF", "feature/test-policy")
    monkeypatch.setenv("GITHUB_HEAD_REPOSITORY", "contributor/toolkit")
    monkeypatch.setenv("GITHUB_REPOSITORY", "sidkolapalli/carla-agentic-toolkit")
    assert check_branch_policy.main() == expected
    assert capsys.readouterr().out


@pytest.mark.parametrize(
    ("head", "same_repository", "author", "accepted"),
    [
        ("dependabot/uv/security-fix", True, "dependabot[bot]", True),
        ("dependabot/uv/security-fix", False, "dependabot[bot]", False),
        ("dependabot/uv/security-fix", True, "contributor", False),
        ("feature/security-fix", True, "dependabot[bot]", False),
    ],
)
def test_dependabot_default_branch_exception(
    head: str, author: str, *, same_repository: bool, accepted: bool
) -> None:
    """GitHub security updates work without allowing contributor bot impersonation."""
    error = check_branch_policy.policy_error(
        "main", head, same_repository=same_repository, author=author
    )
    assert (error is None) is accepted
