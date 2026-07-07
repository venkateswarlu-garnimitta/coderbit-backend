"""AWS Lambda MicroVM lifecycle management.

This module replaces the local Docker container orchestration used for
candidate IDE provisioning. Each interview gets its own Firecracker-based
MicroVM created from a single shared image ARN.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from .. import config

logger = logging.getLogger(__name__)

TARGET_PORT = 8080
_TOKEN_LEAD_TIME_MINUTES = 5
_MICROVM_STARTUP_TIMEOUT_SECONDS = 180
_MICROVM_STARTUP_POLL_INTERVAL_SECONDS = 2


def _get_client():
    """Return a configured lambda-microvms boto3 client.

    If AWS credentials are present in the backend .env file, pass them
    explicitly via a fresh boto3 Session so boto3 never falls back to local
    ~/.aws/credentials or an AWS_PROFILE set in the environment.
    """
    import boto3

    session_kwargs: dict = {}
    if config.AWS_DEFAULT_REGION:
        session_kwargs["region_name"] = config.AWS_DEFAULT_REGION
    if config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = config.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = config.AWS_SECRET_ACCESS_KEY
    if config.AWS_SESSION_TOKEN:
        session_kwargs["aws_session_token"] = config.AWS_SESSION_TOKEN

    session = boto3.Session(**session_kwargs)
    return session.client(
        "lambda-microvms",
        config=Config(retries={"max_attempts": 3}),
    )


def _is_run_hook_disabled_error(exc: ClientError) -> bool:
    """Return True if the error means the image does not have run hooks enabled."""
    error = exc.response.get("Error", {})
    return (
        error.get("Code") == "ValidationException"
        and "run hook" in (error.get("Message") or "").lower()
    )


def ensure_run_hook_enabled(image_arn: str | None = None) -> dict:
    """Attempt to enable the run hook on the configured MicroVM image.

    AWS Lambda MicroVMs requires run hooks to be enabled on the image before
    `runHookPayload` can be passed to `run_microvm`. This helper fetches the
    latest active image version, then calls `update_microvm_image` to create
    a new version with the run hook configured.

    If the API is unavailable or the caller lacks permission, it raises an
    HTTPException so the caller can decide whether to proceed without run hooks.
    """
    import uuid

    arn = image_arn or config.MICROVM_IMAGE_ARN
    if not arn:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MICROVM_IMAGE_ARN is not configured",
        )

    client = _get_client()
    logger.info("Ensuring run hook is enabled for image %s", arn)

    try:
        image_summary = client.get_microvm_image(imageIdentifier=arn)
    except ClientError as exc:
        logger.exception("Failed to get MicroVM image %s", arn)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not read MicroVM image details: {exc}",
        ) from exc

    latest_version = image_summary.get("latestActiveImageVersion")
    if not latest_version:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MicroVM image {arn} has no active version",
        )

    try:
        version_details = client.get_microvm_image_version(
            imageIdentifier=arn,
            imageVersion=latest_version,
        )
    except ClientError as exc:
        logger.exception(
            "Failed to get MicroVM image version %s for %s", latest_version, arn
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not read MicroVM image version details: {exc}",
        ) from exc

    # Copy the existing version configuration so we only change the hook.
    # update_microvm_image expects baseImageVersion as a single major version
    # number (e.g. "1"), while get_microvm_image_version may return a full
    # version string (e.g. "1.2"). Extract the major component.
    base_image_version = version_details.get("baseImageVersion") or ""
    if "." in base_image_version:
        base_image_version = base_image_version.split(".")[0]

    update_args: dict = {
        "imageIdentifier": arn,
        "baseImageArn": version_details.get("baseImageArn"),
        "baseImageVersion": base_image_version,
        "buildRoleArn": version_details.get("buildRoleArn"),
        "codeArtifact": version_details.get("codeArtifact"),
        "clientToken": str(uuid.uuid4()),
    }

    # Preserve optional fields if they exist.
    for field in (
        "description",
        "logging",
        "egressNetworkConnectors",
        "cpuConfigurations",
        "resources",
        "additionalOsCapabilities",
        "environmentVariables",
    ):
        if field in version_details and version_details[field] is not None:
            update_args[field] = version_details[field]

    existing_hooks = version_details.get("hooks") or {}
    microvm_hooks = existing_hooks.get("microvmHooks") or {}
    # The service model declares run/resume/suspend/terminate hooks as
    # ENABLED/DISABLED enums; the actual hook path is the image convention
    # (/var/task/runhook or the HTTP hook on hooks.port).
    microvm_hooks["run"] = "ENABLED"
    microvm_hooks["runTimeoutInSeconds"] = 60
    existing_hooks["microvmHooks"] = microvm_hooks
    # The HTTP hook server listens on 9000, so advertise that port in the
    # image settings. Keeping the configured port consistent with the server
    # avoids silent delivery failures if the service ever honors this field.
    existing_hooks["port"] = 9000
    update_args["hooks"] = existing_hooks

    try:
        resp = client.update_microvm_image(**update_args)
    except ClientError as exc:
        logger.exception(
            "Failed to update MicroVM image %s with run hook", arn
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not update MicroVM image with run hook: {exc}",
        ) from exc

    logger.info(
        "Run hook enabled for image %s (new version %s)",
        arn,
        resp.get("imageVersion", "unknown"),
    )
    return {
        "image_arn": arn,
        "previous_version": latest_version,
        "new_version": resp.get("imageVersion"),
        "response": resp,
    }


def _extract_auth_token(resp: dict) -> str | None:
    """Extract the X-aws-proxy-auth token from a Lambda MicroVMs response.

    Per the AWS Lambda MicroVMs documentation, the canonical response shape is:
        {"authToken": {"X-aws-proxy-auth": "..."}}

    This function only uses the response object local to the current call —
    no shared global state — so it is safe under concurrent token refreshes.
    """
    logger.debug("Extracting auth token from response keys: %s", list(resp.keys()))

    # The service model declares authToken as a MapShape of string keys to
    # string values. boto3 typically surfaces it as a dict.
    auth_token_obj = resp.get("authToken")
    if isinstance(auth_token_obj, dict):
        token = auth_token_obj.get("X-aws-proxy-auth")
        if token:
            return str(token).strip()
    elif isinstance(auth_token_obj, str):
        # Some SDK versions may return the token as a plain string.
        return auth_token_obj.strip()

    # Fallback: some SDK versions may flatten the map key to the top level.
    token = resp.get("X-aws-proxy-auth")
    if token:
        return str(token).strip()

    # Last resort: the value may only exist in the raw HTTP response headers
    # attached to ResponseMetadata. We read from the *local* resp only.
    headers = resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    for key in list(headers.keys()):
        if key.lower() == "x-aws-proxy-auth":
            return str(headers[key]).strip()

    return None


def _create_token(microvm_id: str) -> dict:
    """Create a fresh auth token scoped to TARGET_PORT.

    Tokens are valid for 60 minutes (AWS maximum). We record an expiry 5
    minutes early so the proxy layer refreshes them before they become invalid.
    """
    client = _get_client()
    logger.info(
        "Creating auth token for MicroVM %s (region=%s, key_prefix=%s)",
        microvm_id,
        config.AWS_DEFAULT_REGION,
        config.AWS_ACCESS_KEY_ID[:8] if config.AWS_ACCESS_KEY_ID else "",
    )
    resp = client.create_microvm_auth_token(
        microvmIdentifier=microvm_id,
        expirationInMinutes=60,
        allowedPorts=[{"allPorts": {}}],
    )
    logger.info("create_microvm_auth_token response: %s", resp)
    token = _extract_auth_token(resp)
    if token:
        logger.info(
            "Extracted auth token for MicroVM %s: length=%s prefix=%s",
            microvm_id,
            len(token),
            token[:8],
        )
    else:
        logger.error(
            "create_microvm_auth_token response did not contain a token. "
            "Response keys: %s. ResponseMetadata headers: %s",
            list(resp.keys()),
            resp.get("ResponseMetadata", {}).get("HTTPHeaders", {}),
        )
        raise RuntimeError(
            "create_microvm_auth_token did not return a token. "
            f"Response: {resp}"
        )

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=60 - _TOKEN_LEAD_TIME_MINUTES
    )
    return {
        "auth_token": token,
        "token_expires_at": expires_at,
    }


def _normalize_endpoint(endpoint: str | None) -> str:
    """Ensure the MicroVM endpoint has an HTTP/HTTPS protocol prefix.

    AWS Lambda MicroVMs sometimes returns endpoints without a scheme. We
    default to https:// because MicroVM traffic is TLS-terminated at the
    service edge.
    """
    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="run_microvm response missing endpoint",
        )
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"https://{endpoint}"


def _wait_for_running(microvm_id: str) -> None:
    """Poll GetMicrovm until the MicroVM reaches the RUNNING state.

    run_microvm returns as soon as the service accepts the request, but the
    MicroVM may still be PENDING. Auth tokens created against a MicroVM that is
    not yet RUNNING can be rejected with 403 "Token authentication failed".
    """
    client = _get_client()
    deadline = time.monotonic() + _MICROVM_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            resp = client.get_microvm(microvmIdentifier=microvm_id)
            state = resp.get("state")
            logger.info("MicroVM %s state: %s", microvm_id, state)
            if state == "RUNNING":
                return
            if state in {"TERMINATING", "TERMINATED"}:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"MicroVM {microvm_id} is {state}",
                )
        except ClientError as exc:
            logger.warning(
                "GetMicrovm failed for %s: %s", microvm_id, exc
            )
        time.sleep(_MICROVM_STARTUP_POLL_INTERVAL_SECONDS)

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"MicroVM {microvm_id} did not reach RUNNING state within {_MICROVM_STARTUP_TIMEOUT_SECONDS}s",
    )


def _try_enable_run_hook(image_arn: str) -> bool:
    """Attempt to enable the run hook on the image without raising.

    Returns True if the hook was enabled successfully, False otherwise.
    """
    try:
        ensure_run_hook_enabled(image_arn)
        return True
    except Exception as exc:
        logger.warning(
            "Automatic run hook enable failed for image %s: %s",
            image_arn,
            exc,
        )
        return False


def start_microvm(
    interview_id: str,
    candidate_email: str,
    problem_markdown: str,
    candidate_jwt: str,
) -> dict:
    """Launch a new MicroVM for an interview and return connection details."""
    if not config.MICROVM_IMAGE_ARN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MICROVM_IMAGE_ARN is not configured",
        )

    client = _get_client()
    payload = json.dumps(
        {
            "interview_id": interview_id,
            "candidate_email": candidate_email,
            "problem_markdown": problem_markdown,
            "candidate_jwt": candidate_jwt,
        }
    )

    logger.info(
        "Starting MicroVM for interview %s: candidate_email=%s "
        "problem_markdown_length=%s payload_length=%s candidate_jwt_prefix=%s",
        interview_id,
        candidate_email,
        len(problem_markdown),
        len(payload),
        candidate_jwt[:8] if candidate_jwt else "",
    )
    run_resp: dict | None = None
    try:
        run_resp = client.run_microvm(
            imageIdentifier=config.MICROVM_IMAGE_ARN,
            runHookPayload=payload,
            idlePolicy={
                "autoResumeEnabled": True,
                "maxIdleDurationSeconds": 3600,
                "suspendedDurationSeconds": 7200,
            },
        )
    except ClientError as exc:
        if _is_run_hook_disabled_error(exc):
            logger.warning(
                "Run hook not enabled on image %s for interview %s; "
                "attempting to enable automatically.",
                config.MICROVM_IMAGE_ARN,
                interview_id,
            )
            if _try_enable_run_hook(config.MICROVM_IMAGE_ARN):
                logger.info(
                    "Run hook enabled automatically for image %s; "
                    "retrying run_microvm with payload for interview %s",
                    config.MICROVM_IMAGE_ARN,
                    interview_id,
                )
                try:
                    run_resp = client.run_microvm(
                        imageIdentifier=config.MICROVM_IMAGE_ARN,
                        runHookPayload=payload,
                        idlePolicy={
                            "autoResumeEnabled": True,
                            "maxIdleDurationSeconds": 3600,
                            "suspendedDurationSeconds": 7200,
                        },
                    )
                except ClientError as retry_exc:
                    logger.exception(
                        "run_microvm retry failed for interview %s after enabling run hook",
                        interview_id,
                    )
                    error_msg = retry_exc.response.get("Error", {}).get("Message", str(retry_exc))
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to start IDE environment: {error_msg}",
                    ) from retry_exc
            else:
                logger.warning(
                    "Could not enable run hook automatically for image %s; "
                    "retrying without runHookPayload. The IDE will fall back "
                    "to a generated interview ID.",
                    config.MICROVM_IMAGE_ARN,
                )
                try:
                    run_resp = client.run_microvm(
                        imageIdentifier=config.MICROVM_IMAGE_ARN,
                    )
                except ClientError as retry_exc:
                    logger.exception("run_microvm retry failed for interview %s", interview_id)
                    error_msg = retry_exc.response.get("Error", {}).get("Message", str(retry_exc))
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=f"Failed to start IDE environment: {error_msg}",
                    ) from retry_exc
        else:
            logger.exception("run_microvm failed for interview %s", interview_id)
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to start IDE environment: {error_msg}",
            ) from exc
    except Exception as exc:
        logger.exception("Unexpected error starting MicroVM for interview %s", interview_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start IDE environment: {exc}",
        ) from exc

    microvm_id = run_resp.get("microvmId")
    endpoint = _normalize_endpoint(run_resp.get("endpoint"))
    if not microvm_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="run_microvm response missing microvmId",
        )

    logger.info("MicroVM %s started for interview %s", microvm_id, interview_id)
    _wait_for_running(microvm_id)
    token_data = _create_token(microvm_id)

    return {
        "microvm_id": microvm_id,
        "endpoint": endpoint,
        "auth_token": token_data["auth_token"],
        "token_expires_at": token_data["token_expires_at"],
    }


def refresh_token(microvm_id: str) -> dict:
    """Refresh the auth token for a running MicroVM."""
    logger.info("Refreshing auth token for MicroVM %s", microvm_id)
    token_data = _create_token(microvm_id)
    logger.info("Auth token refreshed for MicroVM %s", microvm_id)
    return {
        "microvm_id": microvm_id,
        **token_data,
    }


def terminate_microvm(microvm_id: str) -> None:
    """Terminate a MicroVM and release its resources."""
    if not microvm_id:
        logger.warning("terminate_microvm called with empty microvm_id")
        return

    client = _get_client()
    logger.info("Terminating MicroVM %s", microvm_id)
    try:
        client.terminate_microvm(microvmIdentifier=microvm_id)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "ResourceNotFoundException":
            logger.info("MicroVM %s already terminated", microvm_id)
            return
        logger.exception("Failed to terminate MicroVM %s", microvm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to terminate IDE environment: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error terminating MicroVM %s", microvm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to terminate IDE environment: {exc}",
        ) from exc
