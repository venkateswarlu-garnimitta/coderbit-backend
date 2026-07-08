"""S3 storage for candidate webcam recordings and session logs.

Recordings and raw Coding Assistant logs are kept alongside workspace archives
under ``s3://<bucket>/<prefix>/<interview-id>/``.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from .. import config

logger = logging.getLogger(__name__)

_RECORDING_FILENAME = "recording.webm"
_PRESIGNED_URL_TTL_SECONDS = 3600


def _get_s3_client():
    """Return a boto3 S3 client using the backend's configured credentials."""
    return boto3.client(
        "s3",
        region_name=config.AWS_DEFAULT_REGION or None,
        aws_access_key_id=config.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY or None,
        aws_session_token=config.AWS_SESSION_TOKEN or None,
    )


def _artifact_prefix(interview_id: str) -> str:
    """Build the S3 prefix for an interview's artifacts."""
    prefix = config.S3_CANDIDATE_LOGS_PREFIX.strip("/")
    return f"{prefix}/{interview_id}/"


def recording_s3_key(interview_id: str) -> str:
    """Build the S3 object key for an interview recording."""
    return f"{_artifact_prefix(interview_id)}{_RECORDING_FILENAME}"


def recording_s3_uri(interview_id: str) -> str:
    """Build the S3 URI stored in the database for an interview recording."""
    return f"s3://{config.S3_CANDIDATE_LOGS_BUCKET}/{recording_s3_key(interview_id)}"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an s3:// URI into (bucket, key)."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def upload_recording(interview_id: str, content: bytes) -> str:
    """Upload recording bytes to S3 and return the s3:// URI.

    Raises:
        ClientError: if the S3 upload fails.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    key = recording_s3_key(interview_id)
    client = _get_s3_client()

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="video/webm",
    )

    uri = f"s3://{bucket}/{key}"
    logger.info("Uploaded recording for interview %s to %s", interview_id, uri)
    return uri


def get_recording_download_url(
    recording_path: str, expires_in: int = _PRESIGNED_URL_TTL_SECONDS
) -> str | None:
    """Return a presigned HTTPS URL for a recording stored in S3.

    Returns ``None`` if the recording_path is not an S3 URI or if presigning
    fails (e.g. due to missing credentials).
    """
    if not recording_path.startswith("s3://"):
        return None

    bucket, key = parse_s3_uri(recording_path)
    client = _get_s3_client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except ClientError:
        logger.exception("Failed to generate presigned URL for %s", recording_path)
        return None


def _find_log_key(client, interview_id: str) -> str | None:
    """Find the raw candidate log JSON key under the interview artifact prefix.

    First tries any stored/pattern keys, then falls back to listing the prefix.
    Returns ``None`` if no log JSON is found.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    prefix = _artifact_prefix(interview_id)

    # Fast path: common log file names produced by the IDE extension.
    candidate_keys = [
        f"{prefix}{interview_id}.json",
        f"{prefix}{interview_id}_*.json",
    ]
    for key in candidate_keys:
        if "*" in key:
            continue
        try:
            client.head_object(Bucket=bucket, Key=key)
            return key
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "404":
                logger.warning("Error checking S3 key %s: %s", key, exc)

    # Fallback: list the prefix and pick the first .json that is not the context
    # summary or the recording.
    try:
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            if key.endswith("interview-context-summary.json"):
                continue
            if key.endswith(_RECORDING_FILENAME):
                continue
            return key
    except ClientError:
        logger.exception("Failed to list S3 prefix %s", prefix)

    return None


def get_raw_log_json(
    interview_id: str, log_s3_key: str | None = None
) -> Any | None:
    """Download and parse the raw candidate log JSON from S3.

    If ``log_s3_key`` is provided it is used directly; otherwise the artifact
    prefix is searched for a matching JSON object. Returns ``None`` if the log
    cannot be found or parsed.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    client = _get_s3_client()

    key = log_s3_key or _find_log_key(client, interview_id)
    if key is None:
        logger.warning("No raw log JSON found for interview %s", interview_id)
        return None

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        return json.loads(body.decode("utf-8"))
    except (ClientError, json.JSONDecodeError, UnicodeDecodeError):
        logger.exception("Failed to read raw log JSON for interview %s", interview_id)
        return None
