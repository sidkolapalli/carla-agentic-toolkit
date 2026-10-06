"""Check the GitFlow target of a pull request without credentials or network access."""

from __future__ import annotations

import os

_RELEASE_TOPICS = ("release", "hotfix")
_DEVELOP_TOPICS = (
    "feature",
    "fix",
    "docs",
    "chore",
    "refactor",
    "test",
    "perf",
    "ci",
    "build",
    "dependabot",
    "WIP",
    *_RELEASE_TOPICS,
)
_MAIN_GUIDANCE = (
    "Target develop for normal work; main accepts release/* or hotfix/* PRs, "
    "plus upstream Dependabot updates."
)
_DEVELOP_GUIDANCE = (
    "Target develop from a named feature/*, fix/*, docs/*, chore/*, refactor/*, test/*, "
    "perf/*, ci/*, build/*, dependabot/*, WIP/*, release/*, or hotfix/* branch. "
    "Only this repository's main can be back-merged into develop."
)


def _is_topic(branch: str, topics: tuple[str, ...]) -> bool:
    kind, separator, description = branch.partition("/")
    return bool(separator and description and kind in topics)


def policy_error(base: str, head: str, *, same_repository: bool, author: str = "") -> str | None:
    """Return guidance for a rejected route, or None for an allowed pull request."""
    if base == "main":
        release = _is_topic(head.removeprefix("WIP/"), _RELEASE_TOPICS)
        dependency_update = (
            same_repository and author == "dependabot[bot]" and _is_topic(head, ("dependabot",))
        )
        return None if release or dependency_update else _MAIN_GUIDANCE
    if base != "develop":
        return "Pull requests must target develop or main. " + _MAIN_GUIDANCE
    if head == "main":
        return None if same_repository else _DEVELOP_GUIDANCE
    return None if _is_topic(head, _DEVELOP_TOPICS) else _DEVELOP_GUIDANCE


def main() -> int:
    """Read Actions metadata as data and return the required check's exit status."""
    error = policy_error(
        os.environ["GITHUB_BASE_REF"],
        os.environ["GITHUB_HEAD_REF"],
        same_repository=os.environ["GITHUB_HEAD_REPOSITORY"] == os.environ["GITHUB_REPOSITORY"],
        author=os.environ.get("GITHUB_PR_AUTHOR", ""),
    )
    print(error or "GitFlow branch policy passed.")  # noqa: T201
    return int(error is not None)


if __name__ == "__main__":
    raise SystemExit(main())
