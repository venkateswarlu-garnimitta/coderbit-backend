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

logger = logging.getLogger(__name__)

_COLLECT_ARTIFACTS_TIMEOUT_SECONDS = 180.0
_COLLECT_ARTIFACTS_MAX_RETRIES = 3


async def collect_and_upload_artifacts(interview: Interview) -> str | None:
    """Ask the candidate MicroVM to upload workspace + log to S3.

    Returns the S3 prefix where artifacts were stored (e.g.
    ``s3://coderbit/candidateLogs/<interview-id>/``) on success. Returns None
    (after logging the failure) if the MicroVM is unreachable, the collection
    endpoint fails, or either upload does not complete. The caller is expected
    to decide whether to proceed with termination after a failure.
    """
    if not interview.microvm_endpoint:
        logger.warning(
            "Cannot collect artifacts for interview %s: no MicroVM endpoint",
            interview.id,
        )
        return None

    candidate_email = ""
    try:
        if interview.candidate is not None:
            candidate_email = interview.candidate.email or ""
    except Exception:
        # The candidate relationship may not be loaded in async sessions.
        candidate_email = ""

    endpoint = interview.microvm_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    url = f"{endpoint}/collect-artifacts"
    payload = {
        "aws_access_key_id": config.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": config.AWS_SECRET_ACCESS_KEY,
        "aws_session_token": config.AWS_SESSION_TOKEN,
        "aws_region": config.AWS_DEFAULT_REGION,
        "bucket": config.S3_CANDIDATE_LOGS_BUCKET,
        "prefix": config.S3_CANDIDATE_LOGS_PREFIX,
        "interview_id": interview.id,
        "candidate_email": candidate_email,
    }
    headers: dict[str, str] = {
        "X-aws-proxy-auth": interview.auth_token or "",
        "X-aws-proxy-port": "9000",
        "Content-Type": "application/json",
    }

    last_error: str | None = None
    for attempt in range(1, _COLLECT_ARTIFACTS_MAX_RETRIES + 1):
        try:
            logger.info(
                "Collecting artifacts for interview %s from %s (attempt %s/%s)",
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

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    logger.error(
                        "collect-artifacts for interview %s returned non-JSON: %s",
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
                    logger.info(
                        "Artifacts uploaded for interview %s: %s",
                        interview.id,
                        data,
                    )
                    return s3_prefix

                logger.error(
                    "Artifact collection failed for interview %s: %s",
                    interview.id,
                    data,
                )
                last_error = data.get("error", "unknown error")
            else:
                logger.error(
                    "collect-artifacts for interview %s returned HTTP %s: %s",
                    interview.id,
                    resp.status_code,
                    resp.text[:500],
                )
                last_error = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            logger.warning(
                "Timeout collecting artifacts for interview %s (attempt %s/%s)",
                interview.id,
                attempt,
                _COLLECT_ARTIFACTS_MAX_RETRIES,
            )
            last_error = "timeout"
        except Exception:
            logger.exception(
                "Exception collecting artifacts for interview %s (attempt %s/%s)",
                interview.id,
                attempt,
                _COLLECT_ARTIFACTS_MAX_RETRIES,
            )
            last_error = "exception"

        if attempt < _COLLECT_ARTIFACTS_MAX_RETRIES:
            await asyncio.sleep(2 ** (attempt - 1))

    logger.error(
        "Giving up on artifact collection for interview %s after %s attempts. last_error=%s",
        interview.id,
        _COLLECT_ARTIFACTS_MAX_RETRIES,
        last_error,
    )
    return None
