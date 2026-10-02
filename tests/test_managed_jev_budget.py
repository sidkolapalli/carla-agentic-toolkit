"""Account reservations persist across separate trusted worker processes."""

import subprocess
import sys
from pathlib import Path

from carla_agentic_toolkit.managed_jev_budget import PersistentAccountBudget
from carla_agentic_toolkit.managed_jev_config import TOKEN_RESERVATION


def test_new_worker_cannot_reset_account_budget(tmp_path: Path) -> None:
    """A new policy process sees earlier charges, including failed requests."""
    first = PersistentAccountBudget(tmp_path, 1, TOKEN_RESERVATION)
    assert first.reserve()
    second = PersistentAccountBudget(tmp_path, 200, 200 * TOKEN_RESERVATION)
    assert not second.reserve()


def test_corrupt_budget_fails_closed(tmp_path: Path) -> None:
    """Unreadable counters cannot turn into a fresh spending allowance."""
    budget = PersistentAccountBudget(tmp_path, 1, TOKEN_RESERVATION)
    (tmp_path / "jev-account.sqlite3").write_bytes(b"broken")
    assert not budget.reserve()


def test_separate_worker_observes_durable_charge(tmp_path: Path) -> None:
    """The account cap is shared by new OS processes, not only new Python objects."""
    budget = PersistentAccountBudget(tmp_path, 1, TOKEN_RESERVATION)
    assert budget.reserve()
    code = (
        "from pathlib import Path; import sys; "
        "from carla_agentic_toolkit.managed_jev_budget import PersistentAccountBudget; "
        "print(int(PersistentAccountBudget(Path(sys.argv[1])).reserve()))"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.strip() == "0"
