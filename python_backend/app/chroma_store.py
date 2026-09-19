from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Sequence

try:
    import chromadb
except Exception as exc:  # pragma: no cover - optional dependency check only
    chromadb = None
    _CHROMADB_IMPORT_ERROR = exc
else:
    _CHROMADB_IMPORT_ERROR = None

CHROMADB_AVAILABLE = chromadb is not None

logger = logging.getLogger(__name__)


_LOCAL_EMBEDDER = None
_LOCAL_EMBEDDER_FAILED = False


def local_embedding_function():
    """ChromaDB's bundled MiniLM ONNX embedder.

    Real 384-dimension semantic embeddings that run locally, so retrieval does
    not depend on an API key or on network access at query time. The model is
    downloaded once and cached; loading is deferred until first use.
    """
    global _LOCAL_EMBEDDER, _LOCAL_EMBEDDER_FAILED
    if _LOCAL_EMBEDDER is not None or _LOCAL_EMBEDDER_FAILED:
        return _LOCAL_EMBEDDER
    try:
        from chromadb.utils import embedding_functions

        _LOCAL_EMBEDDER = embedding_functions.DefaultEmbeddingFunction()
        logger.info("Using the local MiniLM ONNX embedding function for RAG.")
    except Exception:
        _LOCAL_EMBEDDER_FAILED = True
        logger.warning("Local ONNX embedding model unavailable; falling back to lexical hashing.", exc_info=True)
    return _LOCAL_EMBEDDER


class OpenAICompatibleEmbedder:
    def __init__(self, api_url: str, api_key: str, model: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "text-embedding-3-small"

    def _fallback_embedding(self, text: str, dimensions: int = 384) -> List[float]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        vector = [0.0] * dimensions
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            index = int(digest, 16) % dimensions
            vector[index] += 1.0
        norm = (sum(value * value for value in vector)) ** 0.5
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        cleaned = [str(text or "").strip() for text in texts]
        if not cleaned or not any(cleaned):
            return [[0.0] * 384 for _ in cleaned]

        if self.api_key and self.api_url:
            endpoint = self.api_url if self.api_url.endswith("/embeddings") else f"{self.api_url}/embeddings"
            payload = json.dumps({"input": cleaned, "model": self.model}).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    data = json.loads(response.read().decode("utf-8"))
                vectors = []
                for item in data.get("data", []):
                    embedding = item.get("embedding") or []
                    if not embedding:
                        raise ValueError("Empty embedding returned by provider.")
                    vectors.append([float(value) for value in embedding])
                if len(vectors) == len(cleaned):
                    return vectors
            except (urllib.error.HTTPError, urllib.error.URLError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.warning("Embedding via the OpenAI-compatible API failed; using the local model instead. Error: %s", exc)

        # Real semantic embeddings without an API key. Only if even this is
        # unavailable do we drop to the lexical hash, which is a last resort.
        local = local_embedding_function()
        if local is not None:
            try:
                return [list(map(float, vector)) for vector in local(cleaned)]
            except Exception:
                logger.warning("Local embedding model failed; falling back to lexical hashing.", exc_info=True)
        return [self._fallback_embedding(text) for text in cleaned]


class ChromaVectorStore:
    def __init__(
        self,
        *,
        host: str = "",
        port: int = 8000,
        persist_directory: str = "",
        collection_name: str = "mediguide-docs",
        tenant: str = "",
        database: str = "",
        embedding_provider: str = "openai-compatible",
        embedding_model: str = "text-embedding-3-small",
        api_url: str = "",
        api_key: str = "",
    ):
        self.collection_name = collection_name or "mediguide-docs"
        self.embedding_provider = embedding_provider or "openai-compatible"
        self.embedding_model = embedding_model or "text-embedding-3-small"
        self.api_url = api_url or "https://api.openai.com/v1"
        self.api_key = api_key
        self.client = self._create_client(host=host, port=port, persist_directory=persist_directory, tenant=tenant, database=database)
        self.collection = self._get_or_create_collection(tenant=tenant, database=database)

    def _create_client(self, *, host: str, port: int, persist_directory: str, tenant: str, database: str):
        if chromadb is None:
            raise RuntimeError(f"chromadb is not installed. Original error: {_CHROMADB_IMPORT_ERROR}")
        if host:
            logger.info("Connecting to ChromaDB at %s:%s", host, port)
            params: Dict[str, Any] = {"host": host, "port": port}
            if tenant:
                params["tenant"] = tenant
            if database:
                params["database"] = database
            return chromadb.HttpClient(**params)

        persist_path = persist_directory or os.getenv("CHROMA_PERSIST_DIRECTORY", "./python_backend/data/chroma")
        logger.info("Using persistent ChromaDB storage at %s", persist_path)
        return chromadb.PersistentClient(path=persist_path)

    def _get_or_create_collection(self, *, tenant: str, database: str):
        params: Dict[str, Any] = {"name": self.collection_name, "metadata": {"hnsw:space": "cosine"}}
        if tenant:
            params["tenant"] = tenant
        if database:
            params["database"] = database
        return self.client.get_or_create_collection(**params)

    @classmethod
    def from_env(cls) -> "ChromaVectorStore":
        """Build from the environment, read at call time.

        Reading os.environ here rather than relying on import-time constants
        means a changed setting takes effect without reimporting the package.
        """
        from . import config

        def setting(name: str, default: Any) -> Any:
            value = os.getenv(name)
            return value.strip() if isinstance(value, str) and value.strip() else default

        port_value = os.getenv("CHROMA_PORT", "").strip()
        return cls(
            host=setting("CHROMA_HOST", config.CHROMA_HOST),
            port=int(port_value) if port_value.isdigit() else config.CHROMA_PORT,
            persist_directory=setting("CHROMA_PERSIST_DIRECTORY", config.CHROMA_PERSIST_DIRECTORY),
            collection_name=setting("CHROMA_COLLECTION", config.CHROMA_COLLECTION),
            tenant=setting("CHROMA_TENANT", config.CHROMA_TENANT),
            database=setting("CHROMA_DATABASE", config.CHROMA_DATABASE),
            embedding_provider=setting("EMBEDDING_PROVIDER", config.EMBEDDING_PROVIDER),
            embedding_model=setting("EMBEDDING_MODEL", config.EMBEDDING_MODEL),
            api_url=setting("LLM_API_URL", config.LLM_API_URL),
            api_key=setting("LLM_API_KEY", os.getenv("OPENAI_API_KEY", config.LLM_API_KEY)),
        )

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        provider = OpenAICompatibleEmbedder(self.api_url, self.api_key, self.embedding_model)
        return provider.embed_texts(texts)

    def upsert_documents(self, documents: Iterable[Dict[str, Any]]) -> int:
        prepared: List[Dict[str, Any]] = []
        for item in documents:
            if not item:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            document_id = str(item.get("document_id") or item.get("id") or "unknown-document")
            chunk_id = str(item.get("chunk_id") or f"{document_id}-chunk-{item.get('chunk_index', 0)}")
            metadata = {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "source": str(item.get("source") or "unknown"),
                "filename": str(item.get("filename") or ""),
                "page": int(item.get("page") or 1),
                "content_hash": str(item.get("content_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest()),
            }
            for key, value in item.items():
                if key in {"id", "document_id", "chunk_id", "text", "source", "filename", "page", "content_hash"}:
                    continue
                if value is None:
                    continue
                if isinstance(value, (dict, list, tuple)):
                    metadata[str(key)] = json.dumps(value, ensure_ascii=True)
                else:
                    metadata[str(key)] = str(value)
            prepared.append({"id": chunk_id, "document_id": document_id, "text": text, "metadata": metadata})

        if not prepared:
            return 0

        document_ids = {entry["document_id"] for entry in prepared}
        for document_id in sorted(document_ids):
            existing = self.collection.get(where={"document_id": document_id})
            if existing.get("ids"):
                self.collection.delete(ids=existing["ids"])

        ids = [entry["id"] for entry in prepared]
        texts = [entry["text"] for entry in prepared]
        embeddings = self.embed(texts)
        self.collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=[entry["metadata"] for entry in prepared])
        logger.info("Inserted %s chunks into Chroma collection %s", len(prepared), self.collection_name)
        return len(prepared)

    def similarity_search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        query_embedding = self.embed([query.strip()])[0]
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        hits: List[Dict[str, Any]] = []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = distances[index] if index < len(distances) else 0.0
            hits.append({
                "document_id": metadata.get("document_id", ""),
                "chunk_id": metadata.get("chunk_id", ""),
                "text": document,
                "source": metadata.get("source", ""),
                "filename": metadata.get("filename", ""),
                "page": metadata.get("page", 1),
                "distance": float(distance),
                "metadata": metadata,
            })
        return hits

    def delete_document(self, document_id: str) -> int:
        existing = self.collection.get(where={"document_id": document_id})
        ids = existing.get("ids", [])
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def health_check(self) -> Dict[str, Any]:
        try:
            count = self.collection.count()
            return {"status": "ok", "count": int(count), "collection": self.collection_name}
        except Exception as exc:  # pragma: no cover - runtime guard
            logger.exception("ChromaDB health check failed")
            return {"status": "error", "message": str(exc), "collection": self.collection_name}

    def close(self):
        try:
            self.client.clear_system_cache()
        except Exception:
            pass
