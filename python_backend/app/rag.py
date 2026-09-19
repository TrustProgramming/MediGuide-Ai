from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List

from .chroma_store import ChromaVectorStore
from .symptom_intake import FIELD_LABELS, SEVERITY_LABEL

logger = logging.getLogger(__name__)

# Curated clinical education entries used for retrieval and conservative routing.
# These are not a diagnostic authority; the internal label is never returned to patients.
KNOWLEDGE = [
    {"id": "headache-migraine", "text": "headache migraine throbbing head pain nausea light sensitivity", "label": "headache or migraine pattern", "specialty": "Neurology", "specialist_ids": ["sp-neuro"], "precautions": ["Rest in a quiet, comfortable environment and track triggers.", "Avoid driving while severe symptoms or visual disturbance are active.", "Seek urgent care for a sudden worst-ever headache, weakness, confusion, or loss of consciousness."], "sources": ["NHS: migraine", "NHS: headaches"]},
    {"id": "neurological-emergency", "text": "sudden weakness numbness facial droop speech difficulty seizure collapse severe headache vision loss", "label": "possible neurological emergency", "specialty": "Neurology", "specialist_ids": ["sp-neuro"], "precautions": ["Note the time symptoms began and seek emergency assessment immediately.", "Do not drive yourself if sudden neurological symptoms are present.", "Call local emergency services for facial drooping, speech difficulty, seizure, collapse, or one-sided weakness."], "sources": ["NHS: stroke symptoms", "NHS: seizures"]},
    {"id": "digestive", "text": "stomach abdominal pain diarrhea constipation vomiting nausea heartburn bloating jaundice", "label": "gastrointestinal symptom pattern", "specialty": "Gastroenterology", "specialist_ids": ["sp-gastro"], "precautions": ["Take small sips of oral fluids and monitor for dehydration.", "Choose simple foods temporarily and avoid alcohol or foods that worsen symptoms.", "Seek urgent care for blood, severe persistent pain, fainting, or yellowing with confusion."], "sources": ["NHS: diarrhoea and vomiting", "NHS: abdominal pain"]},
    {"id": "skin-rash", "text": "skin rash itching redness irritation swelling hives dry skin", "label": "skin irritation pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Avoid new products that may have triggered the irritation.", "Do not scratch; use gentle, fragrance-free skin care.", "Seek urgent care for facial swelling or breathing difficulty."], "sources": ["NHS: rashes", "NHS: hives"]},
    {"id": "stress-sleep", "text": "stress anxiety worry panic sleep insomnia low mood", "label": "stress and sleep concern pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Keep a regular sleep schedule and reduce caffeine late in the day.", "Try slow breathing and speak with someone you trust.", "Seek urgent help for immediate danger or thoughts of self-harm."], "sources": ["NHS: anxiety", "NHS: sleep problems"]},
    {"id": "cardiac-emergency", "text": "chest pain pressure palpitations fast heartbeat shortness breath swelling legs dizziness", "label": "possible cardiovascular emergency", "specialty": "Cardiology & Emergency", "specialist_ids": ["sp-cardio"], "precautions": ["Stop strenuous activity and sit somewhere safe while arranging care.", "Do not drive yourself if symptoms are severe or sudden.", "Call local emergency services immediately for chest pressure, severe breathlessness, fainting, or pain spreading to the arm, jaw, or back."], "sources": ["NHS: heart attack symptoms", "American Heart Association: warning signs"]},
    {"id": "joint-muscle", "text": "joint pain muscle pain injury sprain swelling stiffness back pain", "label": "musculoskeletal symptom pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Rest the affected area and avoid activities that increase pain.", "Use a wrapped cold pack for short periods after a recent injury.", "Seek urgent care for severe deformity, loss of feeling, or inability to bear weight."], "sources": ["NHS: sprains and strains", "NHS: back pain"]},
    {"id": "respiratory", "text": "cough sore throat runny nose blocked nose congestion cold phlegm fever", "label": "upper respiratory symptom pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Rest and drink adequate fluids.", "Use a humidified environment and avoid smoke or other irritants.", "Seek urgent care for severe breathing difficulty, blue lips, confusion, or rapidly worsening symptoms."], "sources": ["NHS: common cold", "CDC: respiratory viruses"]},
    {"id": "eye-vision", "text": "eye pain red eye blurred vision vision loss discharge", "label": "eye symptom pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Avoid rubbing the eye and remove contact lenses if uncomfortable.", "Do not drive with blurred or lost vision.", "Seek urgent assessment for sudden vision loss, severe eye pain, or injury."], "sources": ["NHS: eye problems", "NHS: sudden vision loss"]},
    {"id": "allergy", "text": "allergy sneezing itching hives swelling food reaction wheezing", "label": "allergy symptom pattern", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Avoid the suspected trigger and record what was consumed or contacted.", "Do not ignore worsening swelling or wheezing.", "Call emergency services for throat swelling, severe breathing difficulty, faintness, or collapse."], "sources": ["NHS: allergies", "NHS: anaphylaxis"]},
    {"id": "general", "text": "general health concern tiredness symptoms persistent change", "label": "undifferentiated symptoms", "specialty": "Primary Care", "specialist_ids": ["sp-primary"], "precautions": ["Track when symptoms started, what changes them, and medicines taken.", "Arrange a primary-care assessment if symptoms persist, recur, or affect daily life.", "Seek urgent care if symptoms become sudden, severe, or associated with collapse or breathing difficulty."], "sources": ["NHS: when to seek medical help"]},
]

RED_FLAGS = ["chest pain", "chest pressure", "severe breathing", "blue lips", "facial droop", "speech difficulty", "one-sided weakness", "unconscious", "collapse", "seizure", "fits", "worst headache", "heavy bleeding", "throat swelling"]
STOP_WORDS = {"a", "an", "and", "are", "for", "has", "have", "i", "in", "is", "it", "my", "of", "on", "the", "to", "with"}
_BASE_STOP_WORDS = set(STOP_WORDS)


# The normalized RAG query is a labelled block. Two things must happen before it
# is scored: the field labels are not clinical content, and the "Important
# negatives" line states what the patient does NOT have - scoring it would pull
# retrieval towards exactly the conditions that were ruled out, and would trip
# the red-flag scan on phrases like "no chest pain".
_LABEL_TOKENS = {
    token
    for label in list(FIELD_LABELS.values()) + [SEVERITY_LABEL]
    for token in re.findall(r"[a-z0-9]+", label.lower())
}
_NEGATIVES_LABEL = FIELD_LABELS["important_negatives"].lower()


def retrieval_text(query: str) -> str:
    """Strip labels and the important-negatives line before scoring or red-flag scanning.

    The full normalized query is still what the RAG layer receives and what the
    LLM sees; this is only the text used for similarity and red-flag matching.
    """
    text = str(query or "")
    if ":" not in text:
        return text
    kept: List[str] = []
    for line in text.splitlines():
        label, separator, value = line.partition(":")
        if not separator:
            kept.append(line)
            continue
        if label.strip().lower() == _NEGATIVES_LABEL:
            continue
        value = value.strip()
        if value and value.lower() != "not provided":
            kept.append(value)
    return " ".join(kept) if kept else text


def _tokens(text: str) -> List[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in STOP_WORDS]


def _vector(text: str) -> Counter[str]:
    return Counter(_tokens(text))


def _cosine(left: Counter[str], right: Counter[str], idf: Dict[str, float]) -> float:
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] * idf.get(token, 1.0) ** 2 for token in shared)
    left_norm = math.sqrt(sum((value * idf.get(token, 1.0)) ** 2 for token, value in left.items()))
    right_norm = math.sqrt(sum((value * idf.get(token, 1.0)) ** 2 for token, value in right.items()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


# Document vectors are built before label tokens join the stop list, so the
# curated knowledge text keeps its own wording.
_DOCUMENT_VECTORS = [_vector(entry["text"]) for entry in KNOWLEDGE]
STOP_WORDS |= _LABEL_TOKENS
_DOCUMENT_FREQUENCY = Counter(token for vector in _DOCUMENT_VECTORS for token in vector)
_IDF = {token: math.log((1 + len(KNOWLEDGE)) / (1 + frequency)) + 1 for token, frequency in _DOCUMENT_FREQUENCY.items()}


def _knowledge_index_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
    source = entry.get("sources", ["internal-guidance"])[0] if entry.get("sources") else "internal-guidance"
    return {
        "id": entry["id"],
        "document_id": entry["id"],
        "text": entry["text"],
        "source": source,
        "filename": f"{entry['id']}.txt",
        "page": 1,
        "content_hash": entry["id"],
        "specialty": entry.get("specialty", ""),
        "label": entry.get("label", ""),
        "specialist_ids": entry.get("specialist_ids", []),
    }


def _get_vector_store() -> ChromaVectorStore | None:
    try:
        return ChromaVectorStore.from_env()
    except Exception:
        logger.warning("ChromaDB vector store unavailable; falling back to the curated in-memory knowledge index.", exc_info=True)
        return None


def index_knowledge_documents() -> int:
    store = _get_vector_store()
    if store is None:
        return len(KNOWLEDGE)
    payload = [_knowledge_index_payload(entry) for entry in KNOWLEDGE]
    return store.upsert_documents(payload)


def reindex_document(document_id: str) -> int:
    store = _get_vector_store()
    if store is None:
        return 0
    entry = next((item for item in KNOWLEDGE if item["id"] == document_id), None)
    if entry is None:
        return 0
    return store.upsert_documents([_knowledge_index_payload(entry)])


def delete_document_from_index(document_id: str) -> int:
    store = _get_vector_store()
    if store is None:
        return 0
    return store.delete_document(document_id)


def retrieve(symptoms: str, top_k: int = 3) -> List[Dict[str, Any]]:
    if not symptoms or not symptoms.strip():
        return []

    store = _get_vector_store()
    if store is not None:
        try:
            hits = store.similarity_search(symptoms, top_k=top_k)
            if hits:
                results: List[Dict[str, Any]] = []
                seen: set[str] = set()
                for hit in hits:
                    document_id = str(hit.get("document_id") or hit.get("chunk_id") or "")
                    if not document_id or document_id in seen:
                        continue
                    seen.add(document_id)
                    entry = next((item for item in KNOWLEDGE if item["id"] == document_id), None)
                    item = {"document_id": document_id, "id": document_id, "score": float(hit.get("distance", 0.0)) if hit.get("distance") is not None else 0.0}
                    if entry is not None:
                        item.update(entry)
                    item.setdefault("text", hit.get("text", ""))
                    item.setdefault("source", hit.get("source", ""))
                    item.setdefault("filename", hit.get("filename", ""))
                    item.setdefault("page", hit.get("page", 1))
                    results.append(item)
                if results:
                    return results
        except Exception:
            logger.warning("Chroma retrieval failed; falling back to the curated knowledge index.", exc_info=True)

    query = _vector(retrieval_text(symptoms))
    ranked = sorted(((round(_cosine(query, vector, _IDF), 6), entry) for entry, vector in zip(KNOWLEDGE, _DOCUMENT_VECTORS)), key=lambda item: item[0], reverse=True)
    relevant = [(score, entry) for score, entry in ranked if score > 0.05]
    if not relevant:
        relevant = [(0.0, KNOWLEDGE[-1])]
    return [{
        **entry,
        "document_id": entry["id"],
        "id": entry["id"],
        "score": round(score, 6),
    } for score, entry in relevant[:top_k]]


def retrieve_evidence(query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    """Authoritative WHO/NHS chunks for a normalized symptom block.

    Returns [] when the vector store is unavailable, so the caller can say the
    evidence layer is unavailable rather than invent supporting material.
    """
    store = _get_vector_store()
    if store is None:
        return []
    try:
        hits = store.similarity_search(retrieval_text(query), top_k=top_k)
    except Exception:
        logger.warning("Evidence retrieval from the vector store failed.", exc_info=True)
        return []

    evidence: List[Dict[str, Any]] = []
    for hit in hits:
        metadata = hit.get("metadata", {}) or {}
        source = str(metadata.get("source", ""))
        # Only cite the ingested authoritative corpus, not the in-repo topic notes.
        if source not in {"WHO", "NHS"}:
            continue
        evidence.append({
            "source": source,
            "title": str(metadata.get("title", "")),
            "source_url": str(metadata.get("source_url", "")),
            "document_type": str(metadata.get("document_type", "")),
            "retrieved_at": str(metadata.get("retrieved_at", "")),
            "chunk_id": str(metadata.get("chunk_id", hit.get("chunk_id", ""))),
            "document_id": str(metadata.get("document_id", "")),
            "distance": round(float(hit.get("distance", 0.0)), 4),
            "text": re.sub(r"\s+", " ", str(hit.get("text", ""))).strip(),
        })
    return evidence


def assess(symptoms: str) -> Dict[str, Any]:
    scored_text = retrieval_text(symptoms)
    query = _vector(scored_text)
    ranked = sorted(((round(_cosine(query, vector, _IDF), 6), entry) for entry, vector in zip(KNOWLEDGE, _DOCUMENT_VECTORS)), key=lambda item: item[0], reverse=True)
    entry, best_score = ranked[0][1], ranked[0][0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    retrieved_ids = [item[1]["id"] for item in ranked if item[0] > 0.05][:3] or ["general"]
    normalized = " ".join(_tokens(scored_text))
    emergency = any(flag in normalized or flag in scored_text.lower() for flag in RED_FLAGS)
    return {
        "recommendation": {"specialty": entry["specialty"], "specialistIds": entry["specialist_ids"]},
        "precautions": entry["precautions"],
        "sources": entry["sources"],
        "emergency": emergency,
        "retrievedDocuments": retrieved_ids,
        "internalLabel": entry["label"],
        "retrievalScore": best_score,
        "retrievalMargin": round(best_score - second_score, 6),
        "retrievalConfident": bool(best_score >= 0.34 and best_score - second_score >= 0.08),
    }
