import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Force backend/.env to take precedence over any AWS_* env vars already
# exported in the parent shell (e.g. from ~/.aws/credentials or system env).
_dotenv_path = find_dotenv()
load_dotenv(_dotenv_path, override=True)

# Repo root: <root>/backend/src/config.py -> parents[2] == <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "1440"))

_MIN_JWT_SECRET_LENGTH = 32


def _validate_jwt_secret() -> None:
    """Fail fast if JWT_SECRET is missing or too short to be secure."""
    if not JWT_SECRET or len(JWT_SECRET) < _MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET must be set and at least {_MIN_JWT_SECRET_LENGTH} characters long. "
            "Set a strong random value in backend/.env before starting the server."
        )


_validate_jwt_secret()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/interview_db",
)

CODEVECTOR_BASE_URL = os.getenv(
    "CODEVECTOR_BASE_URL",
    "https://coding-gateway.fissionlabs.com/gateway/openai/v1",
)
CODEVECTOR_API_KEY = os.getenv("CODEVECTOR_API_KEY", "")
CODEVECTOR_MODEL = os.getenv("CODEVECTOR_MODEL", "kimi-k2.6")
_cv_temp = os.getenv("CODEVECTOR_TEMPERATURE")
CODEVECTOR_TEMPERATURE = float(_cv_temp) if _cv_temp is not None else 1.0
_cv_max_tokens = os.getenv("CODEVECTOR_MAX_TOKENS")
CODEVECTOR_MAX_TOKENS = int(_cv_max_tokens) if _cv_max_tokens is not None else None
CODEVECTOR_TIMEOUT_SECONDS = float(os.getenv("CODEVECTOR_TIMEOUT_SECONDS", "120"))

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# AWS Lambda MicroVMs configuration used to launch candidate IDE instances.
MICROVM_IMAGE_ARN = os.getenv("MICROVM_IMAGE_ARN", "")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "")

# NOTE: AWS credentials are intentionally NOT pushed into os.environ.
# microvm_manager._get_client() reads them from this config module and passes
# them explicitly via boto3.Session(...) kwargs, so they are never exposed
# to subprocesses or environment-sniffing libraries.

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}

# IANA timezone used to display interview times in emails (e.g. "Asia/Kolkata").
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", "Asia/Kolkata")

# Host directory where the IDE container writes candidate session logs and the
# backend reads them back for scoring. Defaults to <repo>/web-ide/workspaces so
# it works on any host without manual setup; override with WORKSPACE_DIR.
WORKSPACE_DIR = os.getenv(
    "WORKSPACE_DIR",
    str(_REPO_ROOT / "web-ide" / "workspaces"),
)
# Path inside the container that WORKSPACE_DIR is bind-mounted to.
WORKSPACE_VOLUME_TARGET = os.getenv(
    "WORKSPACE_VOLUME_TARGET",
    "/home/coder/interview-logs",
)

# Host directory where candidate codebases are archived before the IDE container
# is removed. Defaults to <repo>/code_bases. Override with CODEBASE_DIR env var.
CODEBASE_DIR = Path(os.getenv("CODEBASE_DIR", str(_REPO_ROOT / "code_bases")))

# IAM Role ARN that the backend assumes via STS to generate short-lived
# upload credentials for MicroVMs. The role must allow s3:PutObject/GetObject
# on the S3_CANDIDATE_LOGS_BUCKET. Required when MICROVM_IMAGE_ARN is set.
MICROVM_UPLOAD_ROLE_ARN = os.getenv("MICROVM_UPLOAD_ROLE_ARN", "")

# ── MicroVM resource limits ──────────────────────────────────────────────────
# Memory cap set on the image via UpdateMicrovmImage resources[].minimumMemoryInMiB.
# AWS uses this as both the minimum and effective allocation for Firecracker VMs.
MICROVM_MEMORY_MIB = int(os.getenv("MICROVM_MEMORY_MIB", "2048"))

# Hard wall-clock cap passed to run_microvm as maximumDurationInSeconds.
# AWS terminates the MicroVM after this many seconds regardless of activity.
# Default: 4 hours (14400s). Set to the longest interview duration + buffer.
MICROVM_MAX_DURATION_SECONDS = int(os.getenv("MICROVM_MAX_DURATION_SECONDS", "14400"))

# Disk write quota enforced inside the MicroVM via a sparse sentinel file
# created by entrypoint.sh. Unit: MiB. Default: 2048 MiB (2 GB).
MICROVM_DISK_QUOTA_MB = int(os.getenv("MICROVM_DISK_QUOTA_MB", "2048"))

# Maximum number of processes/threads the candidate user may create inside
# the MicroVM, enforced via cgroup pids.max. Default: 256.
MICROVM_MAX_PIDS = int(os.getenv("MICROVM_MAX_PIDS", "256"))

# S3 location where candidate workspace archives and Coding Assistant logs are
# uploaded before an interview session is terminated.
S3_CANDIDATE_LOGS_BUCKET = os.getenv("S3_CANDIDATE_LOGS_BUCKET", "coderbit")
S3_CANDIDATE_LOGS_PREFIX = os.getenv("S3_CANDIDATE_LOGS_PREFIX", "candidateLogs")

# Host directory where candidate webcam recordings are stored.
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", str(_REPO_ROOT / "recordings")))
# ── LLM Gateway ──
GATEWAY_RATE_LIMIT_RPM = int(os.getenv("GATEWAY_RATE_LIMIT_RPM", "60"))
GATEWAY_JWT_ISSUER = os.getenv("GATEWAY_JWT_ISSUER", "")

# ── Scoring LLM ──────────────────────────────────────────────────────────────
# Credentials for the LLM used by the multi-judge scoring pipeline.
# Defaults fall back to the CodeVector config so existing deployments keep
# working without any env change.
SCORING_BASE_URL = os.getenv("SCORING_BASE_URL", CODEVECTOR_BASE_URL)
SCORING_API_KEY = os.getenv("SCORING_API_KEY", CODEVECTOR_API_KEY)
SCORING_MODEL = os.getenv("SCORING_MODEL", CODEVECTOR_MODEL)
