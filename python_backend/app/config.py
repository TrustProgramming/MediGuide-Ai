import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
# Resolved through secrets_manager: the environment, or a generated machine-local
# key. There is deliberately no hardcoded fallback - see secrets_manager.py.
from .secrets_manager import resolve_secret  # noqa: E402

SECRET_KEY = resolve_secret()
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
PORT = int(os.getenv("PORT", "8011"))
CAL_API_KEY = os.getenv("CAL_API_KEY", "").strip()
CAL_API_URL = os.getenv("CAL_API_URL", "https://api.cal.com/v2").rstrip("/")
CAL_EVENT_TYPE_ID = os.getenv("CAL_EVENT_TYPE_ID", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")

# ChromaDB vector store configuration.
CHROMA_HOST = os.getenv("CHROMA_HOST", "").strip()
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_PERSIST_DIRECTORY = os.getenv("CHROMA_PERSIST_DIRECTORY", str(DATA_DIR / "chroma")).strip()
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "mediguide-docs").strip()
CHROMA_TENANT = os.getenv("CHROMA_TENANT", "").strip()
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE", "").strip()

# Embeddings provider configuration.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai-compatible").strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()

LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1").strip().rstrip("/")
LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
