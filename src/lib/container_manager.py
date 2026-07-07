import os
import secrets
import subprocess
import time

import httpx
from fastapi import HTTPException

from .. import config
from ..middleware.auth import create_access_token

DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "")
WORKSPACE_VOLUME_SOURCE = config.WORKSPACE_DIR
WORKSPACE_VOLUME_TARGET = config.WORKSPACE_VOLUME_TARGET


def _run_docker(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Docker command failed: {exc.stderr or exc.stdout or 'unknown error'}",
        ) from exc


def _escape_single_quotes(value: str) -> str:
    return value.replace("'", "'\\''")


def start_container(
    interview_id: str,
    candidate_email: str,
    markdown_content: str,
) -> dict:
    password = secrets.token_urlsafe(12)
    candidate_jwt = create_access_token({
        "sub": candidate_email,
        "email": candidate_email,
        "role": "candidate",
    })

    # Ensure the host log directory exists so the bind mount lands there
    # instead of Docker auto-creating a path inside its VM.
    os.makedirs(WORKSPACE_VOLUME_SOURCE, exist_ok=True)

    result = _run_docker(
        [
            "docker",
            "run",
            "-d",
            "--name",
            f"interview-{interview_id}",
            "-p",
            "0:8080",
            "-e",
            f"CANDIDATE_NAME={candidate_email}",
            "-e",
            f"CANDIDATE_PASSWORD={password}",
            "-e",
            f"INTERVIEW_ID={interview_id}",
            "-e",
            f"CANDIDATE_JWT={candidate_jwt}",
            "-e",
            "GATEWAY_BASE_URL=http://host.docker.internal:3001/v1",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{WORKSPACE_VOLUME_SOURCE}:{WORKSPACE_VOLUME_TARGET}",
            "--memory=512m",
            "interview-ide",
        ]
    )
    container_id = result.stdout.strip()

    safe_markdown = _escape_single_quotes(markdown_content)
    _run_docker(
        [
            "docker",
            "exec",
            container_id,
            "bash",
            "-c",
            (
                "mkdir -p /home/coder/workspace && "
                f"printf '%s' '{safe_markdown}' > /home/coder/workspace/question.md"
            ),
        ]
    )

    # Get the dynamically allocated host port.
    port = int(
        _run_docker(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}',
            ]
        ).stdout.strip()
    )

    # Wait for code-server inside the container to become reachable so callers
    # don't receive a connection-refused error when they open the IDE.
    _wait_for_ide(port)

    return {
        "container_id": container_id,
        "port": port,
        "ide_url": f"http://localhost:{port}",
    }


def _wait_for_ide(port: int, timeout: float = 30.0, interval: float = 0.5) -> None:
    deadline = time.time() + timeout
    url = f"http://localhost:{port}/healthz"
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(interval)
    # Don't fail the whole request if the IDE is slow; the caller can retry/poll.


def stop_container(container_id: str) -> None:
    if not container_id:
        raise HTTPException(
            status_code=500, detail="Interview has no associated container"
        )

    _run_docker(["docker", "stop", container_id])
    _run_docker(["docker", "rm", container_id])
