import pytest

from app.chroma_store import CHROMADB_AVAILABLE, ChromaVectorStore
from app.rag import index_knowledge_documents, retrieve

requires_chromadb = pytest.mark.skipif(not CHROMADB_AVAILABLE, reason="chromadb is not installed")


@pytest.fixture
def chroma_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_HOST", "")
    monkeypatch.setenv("CHROMA_PORT", "")
    monkeypatch.setenv("CHROMA_PERSIST_DIRECTORY", str(tmp_path / "chroma-data"))
    monkeypatch.setenv("CHROMA_COLLECTION", "test-mediguide")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_URL", "https://api.openai.com/v1")
    store = ChromaVectorStore.from_env()
    yield store
    store.close()


@requires_chromadb
def test_chroma_store_creates_persistent_collection(chroma_store):
    assert chroma_store.collection_name == "test-mediguide"
    assert chroma_store.collection is not None
    assert chroma_store.collection.count() == 0


@requires_chromadb
def test_document_indexing_and_similarity_search(chroma_store):
    documents = [
        {
            "id": "doc-headache",
            "text": "Headache migraine throbbing pain nausea light sensitivity",
            "specialty": "Neurology",
            "source": "NHS: migraine",
            "filename": "migraine.txt",
        }
    ]

    inserted = chroma_store.upsert_documents(documents)
    assert inserted == 1
    assert chroma_store.collection.count() == 1

    hits = chroma_store.similarity_search("migraine headache with nausea", top_k=3)
    assert hits
    assert any(hit["document_id"] == "doc-headache" for hit in hits)


def test_index_all_knowledge_documents():
    count = index_knowledge_documents()
    assert count > 0
    result = retrieve("migraine headache and nausea", top_k=1)
    assert result[0]["document_id"]
