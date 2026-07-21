"""Generate short-lived STS credentials scoped to a single interview's S3 prefix.

These credentials are passed into the MicroVM for artifact upload instead of
the long-lived IAM keys stored in the backend .env file.
"""

from __future__ import annotations

import json
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from .. import config

logger = logging.getLogger(__name__)

_STS_DURATION_SECONDS = 900  # 15 minutes — enough for artifact upload


def _sts_policy(bucket: str, prefix: str) -> str:
    """Return a least-privilege IAM policy scoped to one interview's S3 prefix."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject", "s3:GetObject"],
                    "Resource": f"arn:aws:s3:::{bucket}/{prefix}/*",
                }
            ],
        }
    )


def get_scoped_upload_credentials(interview_id: str) -> dict:
    """Return short-lived STS credentials scoped to the interview's S3 prefix.

    The returned dict contains ``aws_access_key_id``, ``aws_secret_access_key``,
    ``aws_session_token``, and ``aws_region`` — the same keys the hook-server
    already expects in the collect-artifacts request body.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    prefix = f"{config.S3_CANDIDATE_LOGS_PREFIX}/{interview_id}"

    session_kwargs: dict = {}
    if config.AWS_DEFAULT_REGION:
        session_kwargs["region_name"] = config.AWS_DEFAULT_REGION
    if config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = config.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = config.AWS_SECRET_ACCESS_KEY
    if config.AWS_SESSION_TOKEN:
        session_kwargs["aws_session_token"] = config.AWS_SESSION_TOKEN

    if not config.MICROVM_UPLOAD_ROLE_ARN:
        logger.info(
            "MICROVM_UPLOAD_ROLE_ARN not configured — falling back to backend IAM credentials"
        )
        return {
            "aws_access_key_id": config.AWS_ACCESS_KEY_ID or "",
            "aws_secret_access_key": config.AWS_SECRET_ACCESS_KEY or "",
            "aws_session_token": config.AWS_SESSION_TOKEN or "",
            "aws_region": config.AWS_DEFAULT_REGION,
        }

    session = boto3.Session(**session_kwargs)
    sts = session.client("sts", config=Config(retries={"max_attempts": 3}))

    try:
        resp = sts.assume_role(
            RoleArn=config.MICROVM_UPLOAD_ROLE_ARN,
            RoleSessionName=f"coderbit-interview-{interview_id[:8]}",
            DurationSeconds=_STS_DURATION_SECONDS,
            Policy=_sts_policy(bucket, prefix),
        )
    except ClientError as exc:
        logger.exception(
            "Failed to assume upload role for interview %s", interview_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not generate scoped upload credentials: {exc}",
        ) from exc
    except ValueError as exc:
        logger.warning(
            "Upload role not configured for interview %s: %s", interview_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    creds = resp["Credentials"]
    logger.info(
        "Issued STS credentials for interview %s expiry=%s",
        interview_id,
        creds["Expiration"],
    )
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
        "aws_region": config.AWS_DEFAULT_REGION,
    }
