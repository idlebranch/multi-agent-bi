"""Central configuration without process-wide environment side effects."""

from __future__ import annotations

import os
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Pure routing/database tests do not require dotenv.
    def load_dotenv() -> bool:
        return False


load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_MANIFEST = PROJECT_ROOT / "data" / "active_dataset.json"


def get_active_dataset_manifest() -> tuple[dict[str, Any], Path]:
    """Load the active local dataset manifest, if one has been generated."""
    configured = os.getenv("BI_DATASET_MANIFEST")
    path = Path(configured).expanduser() if configured else DEFAULT_DATASET_MANIFEST
    path = path.resolve()
    if not path.is_file():
        return {}, path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid dataset manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"dataset manifest must contain a JSON object: {path}")
    return payload, path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

DEFAULT_MAX_ITERATIONS = int(os.getenv("BI_MAX_ITERATIONS", "12"))
MAX_ALLOWED_ITERATIONS = int(os.getenv("BI_MAX_ALLOWED_ITERATIONS", "12"))
MAX_RESULT_ROWS = int(os.getenv("BI_MAX_RESULT_ROWS", "200"))
SQL_TIMEOUT_SECONDS = float(os.getenv("BI_SQL_TIMEOUT_SECONDS", "5"))
DB_MAX_CONCURRENCY = max(1, int(os.getenv("BI_DB_MAX_CONCURRENCY", "4")))
DB_QUEUE_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("BI_DB_QUEUE_TIMEOUT_SECONDS", "10")),
)
SCHEMA_MAX_TABLES = int(os.getenv("BI_SCHEMA_MAX_TABLES", "200"))
SCHEMA_DETAIL_MAX_TABLES = int(os.getenv("BI_SCHEMA_DETAIL_MAX_TABLES", "20"))
SCHEMA_SAMPLE_ROWS = int(os.getenv("BI_SCHEMA_SAMPLE_ROWS", "2"))
LLM_TIMEOUT_SECONDS = float(os.getenv("BI_LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.getenv("BI_LLM_MAX_RETRIES", "2"))
TRUST_ENV_PROXY = _env_bool("BI_TRUST_ENV_PROXY", False)


def get_database_url(override: str | None = None) -> str:
    """Return the PostgreSQL DSN without embedding credentials in source code."""
    value = (override or os.getenv("BI_DATABASE_URL", "")).strip()
    if not value:
        raise RuntimeError("BI_DATABASE_URL is not configured")
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("BI_DATABASE_URL must be a PostgreSQL connection URL")
    return value


def get_data_as_of_date() -> str:
    """Return the business clock used for relative-date questions."""
    configured = os.getenv("BI_DATA_AS_OF_DATE")
    if configured:
        try:
            return date.fromisoformat(configured).isoformat()
        except ValueError as exc:
            raise RuntimeError("BI_DATA_AS_OF_DATE must use YYYY-MM-DD format") from exc
    manifest, _ = get_active_dataset_manifest()
    manifest_date = manifest.get("as_of_date")
    if manifest_date:
        try:
            return date.fromisoformat(str(manifest_date)).isoformat()
        except ValueError as exc:
            raise RuntimeError("dataset manifest as_of_date must use YYYY-MM-DD format") from exc
    return date.today().isoformat()


@lru_cache(maxsize=4)
def get_llm(temperature: float = 0.0) -> Any:
    """Create the chat model lazily so offline tools/tests can import the project."""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    import httpx
    from langchain_openai import ChatOpenAI

    http_client = httpx.Client(
        trust_env=TRUST_ENV_PROXY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        http_client=http_client,
        temperature=temperature,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )
