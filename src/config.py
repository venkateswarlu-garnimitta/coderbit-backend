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
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///data/interview.db",
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

# Push AWS credentials into os.environ so boto3's default credential chain picks
# them up when they are provided via the backend .env file. We assign directly
# (not setdefault) so values in backend/.env override any stale system env vars.
if AWS_DEFAULT_REGION:
    os.environ["AWS_DEFAULT_REGION"] = AWS_DEFAULT_REGION
if AWS_ACCESS_KEY_ID:
    os.environ["AWS_ACCESS_KEY_ID"] = AWS_ACCESS_KEY_ID
if AWS_SECRET_ACCESS_KEY:
    os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET_ACCESS_KEY
if AWS_SESSION_TOKEN:
    os.environ["AWS_SESSION_TOKEN"] = AWS_SESSION_TOKEN

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

# S3 location where candidate workspace archives and Coding Assistant logs are
# uploaded before an interview session is terminated.
S3_CANDIDATE_LOGS_BUCKET = os.getenv("S3_CANDIDATE_LOGS_BUCKET", "coderbit")
S3_CANDIDATE_LOGS_PREFIX = os.getenv("S3_CANDIDATE_LOGS_PREFIX", "candidateLogs")
# Host directory where candidate webcam recordings are stored.
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", str(_REPO_ROOT / "recordings")))
# ── LLM Gateway ──
GATEWAY_RATE_LIMIT_RPM = int(os.getenv("GATEWAY_RATE_LIMIT_RPM", "60"))
GATEWAY_JWT_ISSUER = os.getenv("GATEWAY_JWT_ISSUER", "")
