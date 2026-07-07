"""Export candidate workspace codebases from the IDE container before teardown."""

import logging
import subprocess
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

CODEBASE_DIR = config.CODEBASE_DIR
REPO_ROOT = config._REPO_ROOT
WORKSPACE_DIR_IN_CONTAINER = "/home/coder/workspace"

# Exclude directories/files that are not source code. Keep this list conservative
# so we capture configuration files, readmes, and solution files.
_EXCLUDED_PATTERNS = [
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".coverage",
    ".venv",
    "venv",
    "env",
    "ENV",
    "dist",
    "build",
    ".next",
    "out",
    "coverage",
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.egg-info",
]


def _build_tar_exclude_args() -> list[str]:
    """Return --exclude arguments for tar for each excluded pattern."""
    args = []
    for pattern in _EXCLUDED_PATTERNS:
        args.extend(["--exclude", pattern])
    return args


def export_codebase_archive(
    container_id: str,
    interview_id: str,
    destination_dir: Path | None = None,
) -> str | None:
    """Archive the candidate's workspace from a running container.

    Returns a repo-relative POSIX path to the created .tar.gz file (e.g.
    ``code_bases/<interview_id>.tar.gz``), or None if the export fails. The
    archive is created by running tar inside the container so excluded files are
    never transferred and the result is already compressed.
    """
    if not container_id:
        logger.warning("No container_id provided for interview %s; skipping codebase export", interview_id)
        return None

    dest = destination_dir or CODEBASE_DIR
    dest.mkdir(parents=True, exist_ok=True)

    archive_name = f"{interview_id}.tar.gz"
    archive_path = dest / archive_name

    # Stream tar output directly into the host file. Using -C ensures the archive
    # entries are relative to the workspace root rather than /home/coder.
    tar_command = [
        "tar",
        "-czf",
        "-",
        *_build_tar_exclude_args(),
        "-C",
        WORKSPACE_DIR_IN_CONTAINER,
        ".",
    ]

    try:
        with archive_path.open("wb") as outfile:
            subprocess.run(
                ["docker", "exec", container_id, *tar_command],
                check=True,
                stdout=outfile,
                stderr=subprocess.PIPE,
            )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Failed to export codebase for interview %s: %s",
            interview_id,
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "unknown error",
        )
        # Clean up a partial archive so callers don't mistake it for success.
        if archive_path.exists():
            archive_path.unlink(missing_ok=True)
        return None
    except OSError as exc:
        logger.warning("Failed to write codebase archive for interview %s: %s", interview_id, exc)
        if archive_path.exists():
            archive_path.unlink(missing_ok=True)
        return None

    logger.info(
        "Exported codebase for interview %s to %s (%s bytes)",
        interview_id,
        archive_path,
        archive_path.stat().st_size,
    )

    # Store a repo-relative path so the value remains valid if the project is
    # moved or deployed on another host.
    try:
        return archive_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return archive_path.as_posix()
