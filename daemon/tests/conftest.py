import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


def _run_git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "-c",
            "user.email=bench@example.com",
            "-c",
            "user.name=Bench",
            *arguments,
        ),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repository() -> Callable[..., Path]:
    """Build a real one-commit git repository, so git context is never mocked."""

    def make(base: Path, name: str = "bench-project") -> Path:
        repository = base / name
        repository.mkdir(parents=True)
        _run_git(repository, "init", "--initial-branch=main")
        (repository / "notes.txt").write_text("bench notes")
        _run_git(repository, "add", "notes.txt")
        _run_git(repository, "commit", "-m", "initial")
        return repository

    return make
