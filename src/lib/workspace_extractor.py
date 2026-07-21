"""Download and extract candidate workspace archives from S3.

Provides a helper to fetch a ZIP file from the configured S3 bucket,
unzip it into a persistent temporary directory, and return the folder
path so backend analysis functions can read the contents.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import zipfile
from pathlib import Path
from tempfile import mkdtemp

from botocore.exceptions import ClientError

from .. import config
from .s3_recording import _get_s3_client

logger = logging.getLogger(__name__)

# Track all temp dirs created in this process so they are cleaned up on exit.
_TEMP_DIRS: list[str] = []


def _cleanup_temp_dirs() -> None:
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_temp_dirs)


def _find_zip_key(client, interview_id: str) -> str | None:
    """Find the first .zip object under the interview artifact prefix.

    Returns ``None`` if no ZIP is found.
    """
    bucket = config.S3_CANDIDATE_LOGS_BUCKET
    prefix = f"{config.S3_CANDIDATE_LOGS_PREFIX.strip('/')}/{interview_id}/"

    try:
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".zip"):
                return key
    except ClientError:
        logger.exception("Failed to list S3 prefix %s", prefix)

    return None


def extract_workspace_from_s3(
    interview_id: str,
    s3_key: str | None = None,
) -> Path:
    """Download a workspace ZIP from S3 and extract it to a temp directory.

    Args:
        interview_id: The interview ID used to locate artifacts in S3.
        s3_key: Optional explicit S3 object key. If omitted, the function
            searches the standard artifact prefix for a ``.zip`` file.

    Returns:
        Path to the directory containing the unzipped workspace contents.

    Raises:
        FileNotFoundError: If the ZIP cannot be located in S3.
        RuntimeError: If the download or extraction fails.
    """
    client = _get_s3_client()
    bucket = config.S3_CANDIDATE_LOGS_BUCKET

    key = s3_key or _find_zip_key(client, interview_id)
    if key is None:
        raise FileNotFoundError(
            f"No workspace ZIP found in S3 for interview {interview_id}"
        )

    logger.info(
        "Downloading workspace ZIP for interview %s from s3://%s/%s",
        interview_id,
        bucket,
        key,
    )

    try:
        response = client.get_object(Bucket=bucket, Key=key)
        zip_bytes = response["Body"].read()
    except ClientError as exc:
        raise RuntimeError(
            f"Failed to download workspace ZIP for interview {interview_id}: {exc}"
        ) from exc

    extract_dir = Path(mkdtemp(prefix=f"workspace_{interview_id}_"))
    _TEMP_DIRS.append(str(extract_dir))
    logger.info(
        "Extracting workspace ZIP for interview %s to %s",
        interview_id,
        extract_dir,
    )

    try:
        zip_path = extract_dir / "workspace.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        zip_path.unlink()
    except (zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError(
            f"Failed to extract workspace ZIP for interview {interview_id}: {exc}"
        ) from exc

    return extract_dir
