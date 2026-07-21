"""Coordinate artifact collection and S3 upload from a candidate MicroVM.

The candidate's workspace and Coding Assistant logs live inside the Lambda
MicroVM. Before the environment is terminated, the backend asks the MicroVM to
archive the workspace, locate the JSON log, and upload both to S3. This module
contains the helper that calls the MicroVM-side collection endpoint with retries.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .. import config
from ..models.interview import Interview
from . import microvm_manager
from .sts_credentials import get_scoped_upload_credentials

logger = logging.getLogger(__name__)

_COLLECT_ARTIFACTS_TIMEOUT_SECONDS = 180.0
_COLLECT_ARTIFACTS_MAX_RETRIES = 3


async def collect_and_upload_artifacts(
    interview: Interview,
) -> dict[str, str] | None:
    """Ask the candidate MicroVM to upload workspace + log to S3.

    Returns a dict with ``prefix`` (the S3 prefix where artifacts were stored,
    e.g. ``s3://coderbit/candidateLogs/<interview-id>/``) and ``log_key`` (the
    S3 object key of the raw candidate log JSON) on success. Returns None
    (after logging the failure) if the MicroVM is unreachable, the collection
    endpoint fails, or either upload does not complete. The caller is expected
    to decide whether to proceed with termination after a failure.
    """
    logger.info("[END_SESSION] Collecting artifacts for interview %s", interview.id)
    if not interview.microvm_endpoint:
        logger.warning(
            "[END_SESSION] Cannot collect artifacts for interview %s: no MicroVM endpoint",
            interview.id,
        )
        return None

    candidate_email = ""
    try:
        if interview.candidate is not None:
            candidate_email = interview.candidate.email or ""
            logger.info("[END_SESSION] Candidate email for interview %s: %s", interview.id, candidate_email)
    except Exception:
        # The candidate relationship may not be loaded in async sessions.
        logger.warning("[END_SESSION] Could not load candidate email for interview %s", interview.id)
        candidate_email = ""

    endpoint = interview.microvm_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    url = f"{endpoint}/collect-artifacts"
    logger.info("[END_SESSION] Artifact collection URL for interview %s: %s", interview.id, url)

    # Tokens created before the hook-server port was added to allowedPorts only
    # authorize port 8080. Refresh the token now so the request to port 9000
    # is not rejected with 403 "Access to port denied".
    auth_token = interview.auth_token or ""
    logger.info("[END_SESSION] Stored auth token for interview %s: length=%s prefix=%s", interview.id, len(auth_token), auth_token[:8] if auth_token else "")
    if interview.microvm_id:
        try:
            logger.info("[END_SESSION] Refreshing auth token for interview %s (MicroVM %s)", interview.id, interview.microvm_id)
            token_data = await asyncio.to_thread(
                microvm_manager.refresh_token, interview.microvm_id
            )
            new_token = token_data.get("auth_token") or auth_token
            logger.info(
                "[END_SESSION] Refreshed MicroVM auth token for interview %s: length=%s prefix=%s",
                interview.id,
                len(new_token),
                new_token[:8] if new_token else "",
            )
            auth_token = new_token
        except Exception:
            logger.exception(
                "[END_SESSION] Failed to refresh MicroVM auth token for interview %s; "
                "falling back to stored token",
                interview.id,
            )

    # Use short-lived STS credentials scoped to this interview's S3 prefix
    # instead of passing long-lived IAM keys into the candidate-controlled VM.
    scoped_creds = None
    try:
        logger.info("[END_SESSION] Getting scoped upload credentials for interview %s", interview.id)
        scoped_creds = get_scoped_upload_credentials(str(interview.id))
        logger.info("[END_SESSION] Scoped upload credentials obtained for interview %s", interview.id)
    except Exception:
        logger.exception(
            "[END_SESSION] Failed to get scoped upload credentials for interview %s; "
            "falling back to no credentials (hook-server will use instance profile)",
            interview.id,
        )
        scoped_creds = {
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_session_token": "",
            "aws_region": config.AWS_DEFAULT_REGION,
        }

    payload = {
        **scoped_creds,
        "bucket": config.S3_CANDIDATE_LOGS_BUCKET,
        "prefix": config.S3_CANDIDATE_LOGS_PREFIX,
        "interview_id": interview.id,
        "candidate_email": candidate_email,
    }
    headers: dict[str, str] = {
        "X-aws-proxy-auth": auth_token,
        "X-aws-proxy-port": "9000",
        "Content-Type": "application/json",
    }
    logger.info(
        "[END_SESSION] Sending collect-artifacts request for interview %s to %s "
        "(auth_token_len=%s bucket=%s prefix=%s)",
        interview.id,
        url,
        len(auth_token),
        config.S3_CANDIDATE_LOGS_BUCKET,
        config.S3_CANDIDATE_LOGS_PREFIX,
    )

    last_error: str | None = None
    for attempt in range(1, _COLLECT_ARTIFACTS_MAX_RETRIES + 1):
        try:
            logger.info(
                "[END_SESSION] Collecting artifacts for interview %s from %s (attempt %s/%s)",
                interview.id,
                url,
                attempt,
                _COLLECT_ARTIFACTS_MAX_RETRIES,
            )
            async with httpx.AsyncClient(
                timeout=_COLLECT_ARTIFACTS_TIMEOUT_SECONDS,
                follow_redirects=False,
                http1=True,
                http2=False,
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)

            logger.info(
                "[END_SESSION] collect-artifacts response for interview %s: HTTP %s",
                interview.id,
                resp.status_code,
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    logger.error(
                        "[END_SESSION] collect-artifacts for interview %s returned non-JSON: %s",
                        interview.id,
                        resp.text[:500],
                    )
                    last_error = "non-JSON response"
                    continue

                if data.get("success"):
                    s3_prefix = (
                        f"s3://{config.S3_CANDIDATE_LOGS_BUCKET}/"
                        f"{config.S3_CANDIDATE_LOGS_PREFIX}/{interview.id}/"
                    )
                    log_key = data.get("log_key")
                    log_found = data.get("log_found", False)
                    logger.info(
                        "[END_SESSION] Artifacts uploaded for interview %s: "
                        "prefix=%s log_key=%s log_found=%s response=%s",
                        interview.id,
                        s3_prefix,
                        log_key,
                        log_found,
                        data,
                    )
                    if not log_found:
                        logger.warning(
                            "[END_SESSION] Hook server reported log not found for interview %s; "
                            "workspace was still uploaded",
                            interview.id,
                        )
                    return {"prefix": s3_prefix, "log_key": log_key, "log_found": log_found}

                logger.error(
                    "[END_SESSION] Artifact collection FAILED for interview %s: "
                    "hook-server returned success=false response=%s",
                    interview.id,
                    data,
                )
                last_error = data.get("error", "unknown error")
            else:
                logger.error(
                    "[END_SESSION] collect-artifacts for interview %s returned HTTP %s: "
                    "body=%s headers=%s",
                    interview.id,
                    resp.status_code,
                    resp.text[:500],
                    dict(resp.headers),
                )
                last_error = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            logger.warning(
                "[END_SESSION] Timeout collecting artifacts for interview %s (attempt %s/%s)",
                interview.id,
                attempt,
                _COLLECT_ARTIFACTS_MAX_RETRIES,
            )
            last_error = "timeout"
        except Exception:
            logger.exception(
                "[END_SESSION] Exception collecting artifacts for interview %s (attempt %s/%s)",
                interview.id,
                attempt,
                _COLLECT_ARTIFACTS_MAX_RETRIES,
            )
            last_error = "exception"

        if attempt < _COLLECT_ARTIFACTS_MAX_RETRIES:
            await asyncio.sleep(2 ** (attempt - 1))

    logger.error(
        "[END_SESSION] Giving up on artifact collection for interview %s after %s attempts. last_error=%s",
        interview.id,
        _COLLECT_ARTIFACTS_MAX_RETRIES,
        last_error,
    )
    return None
