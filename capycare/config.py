"""Settings for Capy Care. Secrets come from the gitignored .env file in the repo root."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# This container exports AWS credentials for unrelated tooling; Cognee would treat them as an S3
# storage setup and try to import s3fs. Capy Care keeps all memory local, so drop them early.
for _key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
    os.environ.pop(_key, None)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding values already set."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing setting {name}; add it to .env")
    return value


# --- Superlinked SIE (serverless) -------------------------------------------------------------
SIE_BASE_URL = env("SIE_BASE_URL", "https://api.superlinked.com")
SIE_LLM = env("SIE_DEFAULT_LLM_ID", "Qwen/Qwen3.8-27B-FP8")
SIE_EMBED = "Qwen/Qwen3-Embedding-4B"
SIE_RERANK = "Qwen/Qwen3-Reranker-4B"
SIE_GLINER = "fastino/gliner2-large-v1"
SIE_OCR = "lightonai/LightOnOCR-2-1B"
SIE_ASR = "openai/whisper-large-v3-turbo"
SIE_DOCS = "docling"

# --- Band ------------------------------------------------------------------------------------
BAND_API = "https://app.band.ai/api/v1"
AGENT_KEYS = ("READER", "ELDER", "PHARMACIST", "GUARDIAN")

# --- Keenable --------------------------------------------------------------------------------
KEENABLE_API = "https://api.keenable.ai/v1"

# --- Demo household (fictional) --------------------------------------------------------------
PATIENT = "Eleanor Park"
PATIENT_SHORT = "Mom"
HUMAN_PERSONA = os.environ.get("CAPY_HUMAN_NAME", "Lily")
DATASET = os.environ.get("CAPY_DATASET", "eleanor_care")
VAULT_DIR = ROOT / ".vault"
WEB_DIR = ROOT / "web"
DEMO_DIR = ROOT / "demo" / "data"
