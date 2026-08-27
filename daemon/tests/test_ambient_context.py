import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from physical_context.ambient_context import AmbientContextResolver


def test_hostname_is_recorded_for_every_capture() -> None:
    context = AmbientContextResolver().resolve()

    assert context.hostname
    assert context.hostname.strip() == context.hostname


def test_git_repo_branch_and_sha_are_resolved_from_the_project_root(
    tmp_path: Path, git_repository: Callable[..., Path]
) -> None:
    repository = git_repository(tmp_path)

    context = AmbientContextResolver(repository).resolve()

    assert context.git_repo == "bench-project"
    assert context.git_branch == "main"
    assert context.git_sha is not None
    assert len(context.git_sha) == 40
    assert context.hostname


def test_context_is_resolved_from_a_subdirectory_of_the_repository(
    tmp_path: Path, git_repository: Callable[..., Path]
) -> None:
    repository = git_repository(tmp_path)
    nested = repository / "firmware" / "src"
    nested.mkdir(parents=True)

    context = AmbientContextResolver(nested).resolve()

    assert context.git_repo == "bench-project"
    assert context.git_branch == "main"


def test_a_detached_head_records_the_sha_but_no_branch(
    tmp_path: Path, git_repository: Callable[..., Path]
) -> None:
    repository = git_repository(tmp_path)
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ("git", "-C", str(repository), "checkout", "--detach", head),
        check=True,
        capture_output=True,
    )

    context = AmbientContextResolver(repository).resolve()

    assert context.git_branch is None
    assert context.git_sha == head
    assert context.git_repo == "bench-project"


def test_an_unset_project_root_records_no_git_context(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("git must not be invoked without a configured project root")

    monkeypatch.setattr(subprocess, "run", fail)

    context = AmbientContextResolver().resolve()

    assert (context.git_repo, context.git_branch, context.git_sha) == (None, None, None)
    assert context.hostname


def test_a_directory_that_is_not_a_repository_resolves_to_no_git_context(
    tmp_path: Path, caplog
) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    with caplog.at_level(logging.WARNING, logger="physical_context.ambient_context"):
        context = AmbientContextResolver(plain).resolve()

    assert (context.git_repo, context.git_branch, context.git_sha) == (None, None, None)
    assert context.hostname
    assert caplog.records


def test_a_missing_project_root_never_raises(tmp_path: Path) -> None:
    context = AmbientContextResolver(tmp_path / "gone").resolve()

    assert (context.git_repo, context.git_branch, context.git_sha) == (None, None, None)
    assert context.hostname


def test_an_unavailable_git_binary_never_raises(
    tmp_path: Path, monkeypatch, git_repository
) -> None:
    repository = git_repository(tmp_path)

    def unavailable(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", unavailable)

    context = AmbientContextResolver(repository).resolve()

    assert (context.git_repo, context.git_branch, context.git_sha) == (None, None, None)


def test_a_hanging_git_call_never_raises(tmp_path: Path, monkeypatch, git_repository) -> None:
    repository = git_repository(tmp_path)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=2.0)

    monkeypatch.setattr(subprocess, "run", timeout)

    context = AmbientContextResolver(repository).resolve()

    assert (context.git_repo, context.git_branch, context.git_sha) == (None, None, None)
