"""S3 storage for candidate webcam recordings and session logs.

Recordings and raw Coding Assistant logs are kept alongside workspace archives
under ``s3://<bucket>/<prefix>/<interview-id>/``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from .. import config

logger = logging.getLogger(__name__)

_RECORDING_FILENAME = "recording.webm"
_PROCTORING_PREFIX = "proctoring"
_PRESIGNED_URL_TTL_SECONDS = 300  # 5 minutes — reduced from 1 hour
_LIFECYCLE_RULE_ID = "coderbit-candidate-artifacts-expiry"
_LIFECYCLE_EXPIRY_DAYS = int(os.getenv("S3_ARTIFACT_RETENTION_DAYS", "90"))


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


def proctoring_snapshot_s3_key(interview_id: str, alert_id: str) -> str:
    """Build the S3 object key for a proctoring snapshot image."""
    return f"{_artifact_prefix(interview_id)}{_PROCTORING_PREFIX}/{alert_id}.jpg"


def proctoring_snapshot_s3_uri(interview_id: str, alert_id: str) -> str:
    """Build the s3:// URI for a proctoring snapshot."""
    return (
        f"s3://{config.S3_CANDIDATE_LOGS_BUCKET}/"
        f"{proctoring_snapshot_s3_key(interview_id, alert_id)}"
    )


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
        ServerSideEncryption="AES256",
    )

    uri = f"s3://{bucket}/{key}"
    logger.info("Uploaded recording for interview %s to %s", interview_id, uri)
    return uri


def upload_proctoring_snapshot(
    interview_id: str, alert_id: str, content: bytes
) -> str:
    """Upload a proctoring snapshot JPEG to S3 and return the s3:// URI."""
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    key = proctoring_snapshot_s3_key(interview_id, alert_id)
    client = _get_s3_client()

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="image/jpeg",
        ServerSideEncryption="AES256",
    )

    uri = f"s3://{bucket}/{key}"
    logger.info(
        "Uploaded proctoring snapshot %s for interview %s to %s",
        alert_id,
        interview_id,
        uri,
    )
    return uri


def delete_interview_artifacts(interview_id: str) -> None:
    """Delete all S3 objects under the interview's artifact prefix.

    Called when an interview record is deleted so candidate PII/video does not
    accumulate indefinitely. Failures are logged but not re-raised so a missing
    bucket or permission error does not block the DB deletion.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    prefix = _artifact_prefix(interview_id)
    client = _get_s3_client()

    try:
        paginator = client.get_paginator("list_objects_v2")
        keys_to_delete: list[dict] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys_to_delete.append({"Key": obj["Key"]})

        if not keys_to_delete:
            logger.info("No S3 objects to delete for interview %s", interview_id)
            return

        # delete_objects accepts up to 1000 keys per call.
        for i in range(0, len(keys_to_delete), 1000):
            batch = keys_to_delete[i : i + 1000]
            client.delete_objects(
                Bucket=bucket, Delete={"Objects": batch, "Quiet": True}
            )

        logger.info(
            "Deleted %d S3 objects for interview %s under prefix %s",
            len(keys_to_delete),
            interview_id,
            prefix,
        )
    except ClientError:
        logger.exception(
            "Failed to delete S3 artifacts for interview %s", interview_id
        )


def ensure_lifecycle_rule() -> None:
    """Ensure the candidate-artifacts S3 lifecycle expiry rule exists.

    Creates or updates a lifecycle rule that expires all objects under the
    candidate logs prefix after S3_ARTIFACT_RETENTION_DAYS days (default 90).
    Safe to call at startup — it is idempotent.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    if not bucket:
        logger.warning("S3_CANDIDATE_LOGS_BUCKET not set — skipping lifecycle rule")
        return

    prefix = config.S3_CANDIDATE_LOGS_PREFIX.strip("/") + "/"
    client = _get_s3_client()

    new_rule = {
        "ID": _LIFECYCLE_RULE_ID,
        "Filter": {"Prefix": prefix},
        "Status": "Enabled",
        "Expiration": {"Days": _LIFECYCLE_EXPIRY_DAYS},
    }

    try:
        existing = client.get_bucket_lifecycle_configuration(Bucket=bucket)
        rules = existing.get("Rules", [])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
            rules = []
        else:
            logger.exception("Could not read lifecycle config for bucket %s", bucket)
            return

    # Replace existing rule with same ID, or append.
    rules = [r for r in rules if r.get("ID") != _LIFECYCLE_RULE_ID]
    rules.append(new_rule)

    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={"Rules": rules},
        )
        logger.info(
            "S3 lifecycle rule '%s' set: expire after %d days (prefix=%s bucket=%s)",
            _LIFECYCLE_RULE_ID,
            _LIFECYCLE_EXPIRY_DAYS,
            prefix,
            bucket,
        )
    except ClientError:
        logger.exception(
            "Failed to put lifecycle configuration on bucket %s", bucket
        )


def get_object_presigned_url(
    s3_uri: str, expires_in: int = _PRESIGNED_URL_TTL_SECONDS
) -> str | None:
    """Return a presigned HTTPS URL for any s3:// object."""
    if not s3_uri.startswith("s3://"):
        return None

    bucket, key = parse_s3_uri(s3_uri)
    client = _get_s3_client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except ClientError:
        logger.exception("Failed to generate presigned URL for %s", s3_uri)
        return None


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

    First tries the exact match ``<interview_id>.json``, then lists the
    artifact prefix and picks the first ``.json`` that is neither the context
    summary nor the recording. Returns ``None`` if no log JSON is found.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    prefix = _artifact_prefix(interview_id)

    # Fast path: exact-match patterns produced by the IDE extension.
    for candidate in (f"{prefix}{interview_id}.json",):
        try:
            client.head_object(Bucket=bucket, Key=candidate)
            logger.info("Found log key via exact match: %s", candidate)
            return candidate
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "404":
                logger.warning("Error checking S3 key %s: %s", candidate, exc)

    # Prefixed listing: finds <interview_id>_<anything>.json without needing
    # s3:ListBucket on the whole bucket — only s3:ListBucket on the prefix.
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
            logger.info("Found log key via prefix listing: %s", key)
            return key
        logger.warning(
            "No JSON log files found under prefix %s (contents: %s)",
            prefix,
            [c["Key"] for c in response.get("Contents", [])],
        )
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
