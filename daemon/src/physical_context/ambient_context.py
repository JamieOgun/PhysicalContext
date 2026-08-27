import logging
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 2.0

# rev-parse flags apply to the arguments that follow them, so --abbrev-ref has
# to sit between the two HEADs: put it first and the second HEAD resolves to
# the branch name again instead of the commit SHA.
GIT_CONTEXT_ARGUMENTS = ("rev-parse", "HEAD", "--abbrev-ref", "HEAD", "--show-toplevel")

_NO_GIT: tuple[None, None, None] = (None, None, None)


@dataclass(frozen=True, slots=True)
class AmbientContext:
    hostname: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    git_sha: str | None = None


class AmbientContextResolver:
    """Resolves the context surrounding a capture, and never raises.

    Ambient context is the only retrieval signal a capture keeps when
    captioning fails, so it is gathered opportunistically: any part that cannot
    be determined is recorded as null rather than blocking or guessing.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def resolve(self) -> AmbientContext:
        git_repo, git_branch, git_sha = self._resolve_git()
        return AmbientContext(
            hostname=self._resolve_hostname(),
            git_repo=git_repo,
            git_branch=git_branch,
            git_sha=git_sha,
        )

    def _resolve_hostname(self) -> str | None:
        try:
            hostname = socket.gethostname().strip()
        except OSError:
            logger.warning("hostname_unavailable")
            return None
        return hostname or None

    def _resolve_git(self) -> tuple[str | None, str | None, str | None]:
        # TODO(T-017): resolve the frontmost project automatically. Until that
        # exists an unset PCL_PROJECT_ROOT records no git context, because with
        # several repositories open there is no non-guessing way to pick one.
        if self.project_root is None:
            return _NO_GIT

        try:
            completed = subprocess.run(
                ("git", "-C", str(self.project_root), *GIT_CONTEXT_ARGUMENTS),
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("git_context_unavailable root=%s", self.project_root)
            return _NO_GIT

        if completed.returncode != 0:
            logger.warning(
                "git_context_unresolved root=%s detail=%s",
                self.project_root,
                completed.stderr.strip().splitlines()[:1],
            )
            return _NO_GIT

        lines = completed.stdout.strip().splitlines()
        if len(lines) != 3:
            logger.warning("git_context_malformed root=%s", self.project_root)
            return _NO_GIT

        sha, branch, toplevel = (line.strip() for line in lines)
        return (
            Path(toplevel).name or None,
            # A detached HEAD reports the literal string "HEAD", not a branch.
            None if branch in ("", "HEAD") else branch,
            sha or None,
        )
