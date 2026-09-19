"""Authoritative medical document ingestion into ChromaDB.

Only an explicit allowlist of WHO and NHS pages is fetched - this is not a
crawler and it does not follow links. Each page becomes a document, each
document is chunked, and every chunk keeps the source metadata needed to cite it
back to the reader.

Ingestion is idempotent: chunk ids are derived from the source URL and chunk
index, and a document whose content hash is unchanged is skipped.

    python -m python_backend.app.ingestion            # ingest the allowlist
    python -m python_backend.app.ingestion --verify   # report + semantic probe
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional

from .chroma_store import ChromaVectorStore
from .config import DATA_DIR

logger = logging.getLogger(__name__)

USER_AGENT = "MediGuide/1.0 (educational healthcare navigation project; contact via repository)"
REQUEST_TIMEOUT = 30
CHUNK_CHARS = 1100
CHUNK_OVERLAP = 150
MANIFEST_PATH = DATA_DIR / "rag-ingestion-manifest.json"

# Only these hosts are ever fetched.
ALLOWED_HOSTS = {"www.who.int", "who.int", "www.nhs.uk", "nhs.uk"}

# Curated allowlist. WHO fact sheets and NHS Health A-Z pages chosen to cover the
# symptom areas this application actually routes on.
SOURCES: List[Dict[str, str]] = [
    # --- WHO fact sheets ---
    {"source": "WHO", "document_type": "fact_sheet", "title": "Cardiovascular diseases (CVDs)",
     "url": "https://www.who.int/news-room/fact-sheets/detail/cardiovascular-diseases-(cvds)"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Hypertension",
     "url": "https://www.who.int/news-room/fact-sheets/detail/hypertension"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Diabetes",
     "url": "https://www.who.int/news-room/fact-sheets/detail/diabetes"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Asthma",
     "url": "https://www.who.int/news-room/fact-sheets/detail/asthma"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Depression",
     "url": "https://www.who.int/news-room/fact-sheets/detail/depression"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Headache disorders",
     "url": "https://www.who.int/news-room/fact-sheets/detail/headache-disorders"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Diarrhoeal disease",
     "url": "https://www.who.int/news-room/fact-sheets/detail/diarrhoeal-disease"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Food safety",
     "url": "https://www.who.int/news-room/fact-sheets/detail/food-safety"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Epilepsy",
     "url": "https://www.who.int/news-room/fact-sheets/detail/epilepsy"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Obesity and overweight",
     "url": "https://www.who.int/news-room/fact-sheets/detail/obesity-and-overweight"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Anaemia",
     "url": "https://www.who.int/news-room/fact-sheets/detail/anaemia"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Tuberculosis",
     "url": "https://www.who.int/news-room/fact-sheets/detail/tuberculosis"},
    {"source": "WHO", "document_type": "fact_sheet", "title": "Influenza (Seasonal)",
     "url": "https://www.who.int/news-room/fact-sheets/detail/influenza-(seasonal)"},

    # --- NHS Health A-Z ---
    {"source": "NHS", "document_type": "condition", "title": "Headaches",
     "url": "https://www.nhs.uk/conditions/headaches/"},
    {"source": "NHS", "document_type": "condition", "title": "Migraine",
     "url": "https://www.nhs.uk/conditions/migraine/"},
    {"source": "NHS", "document_type": "condition", "title": "Chest pain",
     "url": "https://www.nhs.uk/conditions/chest-pain/"},
    {"source": "NHS", "document_type": "condition", "title": "Heart attack",
     "url": "https://www.nhs.uk/conditions/heart-attack/"},
    {"source": "NHS", "document_type": "condition", "title": "Stroke: symptoms",
     "url": "https://www.nhs.uk/conditions/stroke/symptoms/"},
    {"source": "NHS", "document_type": "condition", "title": "Transient ischaemic attack (TIA)",
     "url": "https://www.nhs.uk/conditions/transient-ischaemic-attack-tia/"},
    {"source": "NHS", "document_type": "condition", "title": "Stomach ache",
     "url": "https://www.nhs.uk/conditions/stomach-ache/"},
    {"source": "NHS", "document_type": "condition", "title": "Diarrhoea and vomiting",
     "url": "https://www.nhs.uk/conditions/diarrhoea-and-vomiting/"},
    {"source": "NHS", "document_type": "condition", "title": "Heartburn and acid reflux",
     "url": "https://www.nhs.uk/conditions/heartburn-and-acid-reflux/"},
    {"source": "NHS", "document_type": "condition", "title": "Rashes in babies and children",
     "url": "https://www.nhs.uk/conditions/rashes-babies-and-children/"},
    {"source": "NHS", "document_type": "condition", "title": "Urinary tract infections (UTIs)",
     "url": "https://www.nhs.uk/conditions/urinary-tract-infections-utis/"},
    {"source": "NHS", "document_type": "condition", "title": "Kidney stones",
     "url": "https://www.nhs.uk/conditions/kidney-stones/"},
    {"source": "NHS", "document_type": "condition", "title": "Cough",
     "url": "https://www.nhs.uk/conditions/cough/"},
    {"source": "NHS", "document_type": "condition", "title": "Sore throat",
     "url": "https://www.nhs.uk/conditions/sore-throat/"},
    {"source": "NHS", "document_type": "condition", "title": "Shortness of breath",
     "url": "https://www.nhs.uk/conditions/shortness-of-breath/"},
    {"source": "NHS", "document_type": "condition", "title": "Anxiety, fear and panic",
     "url": "https://www.nhs.uk/mental-health/feelings-symptoms-behaviours/feelings-and-symptoms/anxiety-fear-panic/"},
    {"source": "NHS", "document_type": "condition", "title": "Insomnia",
     "url": "https://www.nhs.uk/conditions/insomnia/"},
    {"source": "NHS", "document_type": "condition", "title": "Back pain",
     "url": "https://www.nhs.uk/conditions/back-pain/"},
    {"source": "NHS", "document_type": "condition", "title": "Sprains and strains",
     "url": "https://www.nhs.uk/conditions/sprains-and-strains/"},
    {"source": "NHS", "document_type": "condition", "title": "Conjunctivitis",
     "url": "https://www.nhs.uk/conditions/conjunctivitis/"},
    {"source": "NHS", "document_type": "condition", "title": "Allergies",
     "url": "https://www.nhs.uk/conditions/allergies/"},
    {"source": "NHS", "document_type": "condition", "title": "Anaphylaxis",
     "url": "https://www.nhs.uk/conditions/anaphylaxis/"},
]


class _TextExtractor(HTMLParser):
    """Collect readable text, skipping chrome and non-content elements."""

    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "aside", "button"}
    BLOCK = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*", "\n\n", joined)
        return joined.strip()


def fetch(url: str) -> str:
    """Fetch one allowlisted page. Refuses any other host."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).netloc or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing to fetch a host that is not on the allowlist: {host}")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def fetch_rendered(url: str) -> str:
    """Render a JS-heavy page with the existing Playwright dependency.

    Used only when static extraction returns too little text, so the common
    case stays a cheap HTTP fetch.
    """
    from urllib.parse import urlsplit

    host = (urlsplit(url).netloc or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing to render a host that is not on the allowlist: {host}")

    from .oladoc_provider import browser_page

    with browser_page() as page:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        return page.content()


def extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def clean_text(text: str, title: str = "") -> str:
    """Drop boilerplate lines that carry no clinical information."""
    noise = (
        "cookie", "skip to main content", "accept all", "we use cookies",
        "sign up for", "subscribe", "follow us", "share this page",
        "print this page", "back to top", "was this page helpful",
        "page last reviewed", "next review due", "find out more about",
        "©", "all rights reserved",
    )
    lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 3:
            continue
        lowered = stripped.lower()
        if any(marker in lowered for marker in noise):
            continue
        if stripped.count("|") > 3:
            continue
        lines.append(stripped)
    cleaned = "\n".join(lines)
    return cleaned


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Paragraph-aware chunks with a small overlap so sentences are not orphaned."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = f"{tail} {paragraph}".strip()
        else:
            current = f"{current}\n{paragraph}".strip() if current else paragraph
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 120]


def document_id_for(url: str) -> str:
    return "doc-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _load_manifest() -> Dict[str, Any]:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Ingestion manifest unreadable; treating as empty.")
    return {}


def _save_manifest(manifest: Dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def build_chunks(entry: Dict[str, str], text: str) -> List[Dict[str, Any]]:
    document_id = document_id_for(entry["url"])
    retrieved_at = datetime.now(timezone.utc).isoformat()
    pieces = chunk_text(text)
    return [
        {
            # Stable id: same URL + same position => same id, so re-ingesting
            # updates in place instead of duplicating.
            "chunk_id": f"{document_id}-{index:04d}",
            "document_id": document_id,
            "text": piece,
            "source": entry["source"],
            "source_url": entry["url"],
            "title": entry["title"],
            "document_type": entry.get("document_type", "article"),
            "publication_date": entry.get("publication_date", ""),
            "retrieved_at": retrieved_at,
            "chunk_index": index,
            "chunk_count": len(pieces),
            "content_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
            "filename": f"{entry['source'].lower()}-{document_id}.txt",
            "page": 1,
        }
        for index, piece in enumerate(pieces)
    ]


def ingest(
    sources: Optional[Iterable[Dict[str, str]]] = None,
    *,
    store: Optional[ChromaVectorStore] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Fetch, clean, chunk, embed and store the allowlisted documents."""
    entries = list(sources if sources is not None else SOURCES)
    store = store or ChromaVectorStore.from_env()
    manifest = _load_manifest()

    report: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "documents_total": len(entries),
        "documents_ingested": 0,
        "documents_skipped_unchanged": 0,
        "documents_failed": 0,
        "chunks_written": 0,
        "failures": [],
        "by_source": {},
    }

    for entry in entries:
        url = entry["url"]
        try:
            html = fetch(url)
            text = clean_text(extract_text(html), entry.get("title", ""))
            if len(text) < 400:
                # Some WHO/NHS pages render their content with JavaScript.
                logger.info("Static extraction thin for %s; re-fetching with a rendered browser.", url)
                text = clean_text(extract_text(fetch_rendered(url)), entry.get("title", ""))
            if len(text) < 400:
                raise ValueError(f"Extracted only {len(text)} characters of usable text")
            document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            if not force and manifest.get(url, {}).get("document_hash") == document_hash:
                report["documents_skipped_unchanged"] += 1
                continue

            chunks = build_chunks(entry, text)
            if not chunks:
                raise ValueError("No chunk survived cleaning")
            written = store.upsert_documents(chunks)

            manifest[url] = {
                "document_id": chunks[0]["document_id"],
                "document_hash": document_hash,
                "source": entry["source"],
                "title": entry["title"],
                "chunks": written,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            report["documents_ingested"] += 1
            report["chunks_written"] += written
            report["by_source"][entry["source"]] = report["by_source"].get(entry["source"], 0) + written
            logger.info("Ingested %s (%s chunks) from %s", entry["title"], written, entry["source"])
            time.sleep(0.6)  # be polite to the source site
        except Exception as exc:
            report["documents_failed"] += 1
            report["failures"].append({"url": url, "title": entry.get("title", ""), "error": f"{type(exc).__name__}: {exc}"})
            logger.warning("Ingestion failed for %s: %s", url, exc)

    _save_manifest(manifest)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["collection_count"] = store.collection.count()
    return report


def verify(store: Optional[ChromaVectorStore] = None, *, probes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Prove the index is real: counts, metadata, no duplicate ids, live queries."""
    store = store or ChromaVectorStore.from_env()
    count = store.collection.count()
    sample = store.collection.get(limit=min(count, 500), include=["metadatas", "documents"])
    ids = sample.get("ids", [])
    metadatas = sample.get("metadatas", []) or []

    sources: Dict[str, int] = {}
    documents: set = set()
    with_embeddings = store.collection.get(limit=3, include=["embeddings"])
    embedding_dim = 0
    embeddings = with_embeddings.get("embeddings")
    if embeddings is not None and len(embeddings):
        embedding_dim = len(embeddings[0])

    for meta in metadatas:
        sources[str(meta.get("source", "unknown"))] = sources.get(str(meta.get("source", "unknown")), 0) + 1
        documents.add(str(meta.get("document_id", "")))

    result: Dict[str, Any] = {
        "collection": store.collection_name,
        "chunk_count": count,
        "sampled": len(ids),
        "duplicate_ids": len(ids) - len(set(ids)),
        "distinct_documents": len(documents),
        "chunks_by_source": sources,
        "embedding_dimension": embedding_dim,
        "metadata_complete": all(
            meta.get("source") and meta.get("source_url") and meta.get("title") for meta in metadatas
        ) if metadatas else False,
        "probes": [],
    }

    for query in (probes or [
        "persistent lower abdominal pain",
        "sudden crushing chest pain spreading to the arm",
        "throbbing headache with sensitivity to light",
        "burning feeling when passing urine",
    ]):
        hits = store.similarity_search(query, top_k=3)
        result["probes"].append({
            "query": query,
            "hits": [
                {
                    "title": hit.get("metadata", {}).get("title", ""),
                    "source": hit.get("metadata", {}).get("source", ""),
                    "distance": round(float(hit.get("distance", 0)), 4),
                    "excerpt": re.sub(r"\s+", " ", str(hit.get("text", "")))[:110],
                }
                for hit in hits
            ],
        })
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest WHO/NHS documents into ChromaDB")
    parser.add_argument("--verify", action="store_true", help="only report on the existing index")
    parser.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    parser.add_argument("--limit", type=int, default=0, help="ingest only the first N sources")
    args = parser.parse_args()

    store = ChromaVectorStore.from_env()
    if not args.verify:
        sources = SOURCES[: args.limit] if args.limit else SOURCES
        report = ingest(sources, store=store, force=args.force)
        print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=2))
        for failure in report["failures"]:
            print(f"  FAILED {failure['title']}: {failure['error']}")
    print(json.dumps(verify(store), indent=2))


if __name__ == "__main__":
    main()
