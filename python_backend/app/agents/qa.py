from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ..rag import KNOWLEDGE, assess, retrieve, retrieve_evidence
from ..llm import contextual_summary
from .. import flow_log
from .. import symptom_intake
from .kaggle_training import predict_disease


DISEASE_CATEGORIES = {
    "headache-migraine": {"migraine", "headache", "tension headache"},
    "digestive": {"gastroenteritis", "gerd", "peptic ulcer disease", "hepatitis", "constipation", "diarrhoea"},
    "skin-rash": {"acne", "dermatitis", "eczema", "fungal infection", "psoriasis", "impetigo", "drug reaction"},
    "stress-sleep": {"anxiety", "depression", "insomnia", "panic disorder"},
    "cardiac-emergency": {"heart attack", "hypertension", "cardiac arrhythmia"},
    "joint-muscle": {"arthritis", "osteoarthritis", "muscle pain", "back pain", "sprain"},
    "respiratory": {"common cold", "flu", "pneumonia", "bronchitis", "tuberculosis"},
    "eye-vision": {"conjunctivitis", "cataract", "glaucoma"},
    "allergy": {"allergy", "anaphylaxis"},
}

CATEGORY_ENTRIES = {
    "headache-migraine": "headache-migraine",
    "digestive": "digestive",
    "skin-rash": "skin-rash",
    "stress-sleep": "stress-sleep",
    "cardiac-emergency": "cardiac-emergency",
    "joint-muscle": "joint-muscle",
    "respiratory": "respiratory",
    "eye-vision": "eye-vision",
    "allergy": "allergy",
}


def _question_is_answered(question: str, text: str) -> bool:
    normalized = text.lower()
    if "when did this start" in question.lower():
        return bool(re.search(r"\b(today|yesterday|\d+\s*(?:day|days|hour|hours|week|weeks|month|months)|since|started|began)\b", normalized))
    if "where exactly" in question.lower():
        return bool(re.search(r"\b(\d+\s*(?:/\s*10|out of 10)|mild|moderate|severe|intense|left|right|upper|lower|chest|stomach|head|back|neck|arm|leg)\b", normalized))
    if "what other symptoms" in question.lower():
        return bool(re.search(r"\b(no|none|nothing|fever|dizziness|dizzy|blood|fainting|faint|breathing|breath|shortness|vomiting|vomit|nausea|cough|rash|swelling|symptoms)\b", normalized))
    if "did this follow" in question.lower():
        return bool(re.search(r"\b(no|none|nothing|food|medicine|medication|soap|product|bite|trigger|allergy|exposure)\b", normalized))
    if "patient's age" in question.lower():
        return bool(re.search(r"\b\d{1,3}\s*(?:years?|y/o|yr)|adult|child|baby|infant|pregnan|not pregnant|unknown\b", normalized))
    return bool(normalized.strip())


def _follow_up_questions(symptoms: str, result: Dict[str, Any], context: Optional[Dict[str, Any]]) -> list[str]:
    text = symptoms.lower()
    previous = " ".join(str(turn.get("symptoms", "")) for turn in (context or {}).get("turns", []))
    questions: list[str] = []
    if not any(marker in text for marker in ("today", "yesterday", "week", "month", "day", "since", "hours")):
        questions.append("When did this start, and is it getting better, worse, or staying the same?")
    if any(word in text for word in ("pain", "ache", "hurt", "pressure")) and not any(word in text for word in ("mild", "moderate", "severe", "10/10", "intense")):
        questions.append("Where exactly is the discomfort, how severe is it from 0 to 10, and what makes it better or worse?")
    if any(word in text for word in ("cough", "breath", "breathing", "fever", "vomit", "diarrhea", "rash", "swelling")) and not any(word in text for word in ("temperature", "degrees", "blood", "faint", "dizzy")):
        questions.append("What other symptoms came with it, such as fever, dizziness, blood, fainting, or breathing difficulty?")
    if any(word in text for word in ("rash", "itch", "hives", "swelling")) and not any(word in text for word in ("new soap", "food", "medicine", "allergy", "trigger")):
        questions.append("Did this follow a new food, medicine, product, insect bite, or other possible trigger?")
    if not any(word in text for word in ("age", "pregnant", "pregnancy", "child", "baby")):
        questions.append("What is the patient's age, and is pregnancy possible if relevant?")
    return [question for question in questions if not _question_is_answered(question, combined_text := f"{previous} {text}")]


def _strong_category_evidence(category: str | None, symptoms: str) -> bool:
    text = symptoms.lower()
    anchors = {
        "digestive": ("stomach", "abdominal", "vomit", "diarrhea", "constipation", "heartburn", "nausea"),
        "headache-migraine": ("headache", "migraine", "throbbing", "light sensitivity", "nausea"),
        "respiratory": ("cough", "sore throat", "runny nose", "congestion", "phlegm"),
        "skin-rash": ("rash", "itch", "redness", "hives", "skin"),
        "allergy": ("allergy", "sneezing", "hives", "wheezing", "swelling"),
    }
    matches = sum(anchor in text for anchor in anchors.get(category or "", ()))
    return matches >= 2


def answer_symptoms(symptoms: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    combined = " ".join(filter(None, [str((context or {}).get("symptoms", "")), symptoms.strip()]))
    result = assess(combined)
    model_prediction = predict_disease(combined)
    if model_prediction["disease"] != "unknown":
        result["internalLabel"] = model_prediction["disease"]
    answered_questions = {
        str(item.get("question", "")).strip()
        for item in (context or {}).get("questionAnswers", [])
        if str(item.get("answer", "")).strip()
    }
    active_questions = list(dict.fromkeys((context or {}).get("pendingQuestions", [])))
    if result["emergency"]:
        remaining_questions = []
    elif active_questions:
        remaining_questions = [question for question in active_questions if question not in answered_questions]
    else:
        remaining_questions = _follow_up_questions(combined, result, context)
    model_confident = model_prediction["disease"] != "unknown"
    top_prediction = str(model_prediction.get("disease", "")).replace("_", " ").lower()
    retrieved_categories = set(result.get("retrievedDocuments", []))
    disease_category = next((category for category, diseases in DISEASE_CATEGORIES.items() if top_prediction in diseases), None)
    strong_evidence = _strong_category_evidence(disease_category, combined)
    evidence_agrees = bool(disease_category and (disease_category in retrieved_categories or strong_evidence))
    category_entry = next((entry for entry in KNOWLEDGE if entry["id"] == CATEGORY_ENTRIES.get(disease_category)), None)
    if category_entry and evidence_agrees:
        result["internalLabel"] = model_prediction["disease"]
        result["recommendation"] = {"specialty": category_entry["specialty"], "specialistIds": category_entry["specialist_ids"]}
        result["precautions"] = category_entry["precautions"]
        result["sources"] = category_entry["sources"]
    result["diseaseCategory"] = disease_category
    retrieval_ready = bool(result.get("retrievalConfident") or strong_evidence)
    result["confident"] = bool(result["emergency"] or (retrieval_ready and model_confident and evidence_agrees and not remaining_questions))
    if not result["confident"]:
        all_questions = [question for question in dict.fromkeys([*(context or {}).get("pendingQuestions", []), *remaining_questions]) if question not in answered_questions]
        result["followUpQuestions"] = all_questions
        result["needsClinicianReview"] = not bool(all_questions)
        result["recommendation"] = {"specialty": None, "specialistIds": []}
        result["precautions"] = []
        result["sources"] = []
    else:
        result["followUpQuestions"] = []
    result["message"] = (
        "Some symptoms may need urgent assessment. Contact local emergency services now if you are in immediate danger."
        if result["emergency"]
        else "This is general health information for care routing, not a diagnosis."
    )
    try:
        result["contextualResponse"] = contextual_summary(combined, result["recommendation"].get("specialty"), result["precautions"])
    except Exception:
        result["contextualResponse"] = None
    return result


# ---------------------------------------------------------------------------
# Structured assessment
#
# The patient's free text is normalized into the fixed field block first (see
# symptom_intake), and only the completed block reaches the RAG layer. The
# response separates what was retrieved from what is merely possible, and never
# presents a retrieved pattern as a confirmed diagnosis.
# ---------------------------------------------------------------------------

# Phrases in the curated precautions that mark urgent-care guidance rather than
# ordinary self-care.
_URGENT_MARKERS = ("seek urgent", "seek emergency", "call local emergency", "call emergency", "seek immediate")


def _split_precautions(precautions: List[str]) -> tuple[list[str], list[str]]:
    """Separate ordinary precautions from red-flag/urgent-care guidance."""
    ordinary: List[str] = []
    red_flags: List[str] = []
    for item in precautions:
        (red_flags if any(marker in item.lower() for marker in _URGENT_MARKERS) else ordinary).append(item)
    return ordinary, red_flags


def _supporting_symptoms(record: Dict[str, Any]) -> List[str]:
    """The patient-reported findings that support the retrieved pattern. Never invented."""
    found: List[str] = []
    main = str(record.get("main_symptom") or "").strip()
    if main:
        found.append(main)
    for part in re.split(r",|;| and ", str(record.get("other_symptoms") or "")):
        cleaned = part.strip()
        if cleaned and cleaned.lower() not in {"none", "none reported", ""} and cleaned not in found:
            found.append(cleaned)
    return found


def assess_structured(record: Dict[str, Any]) -> Dict[str, Any]:
    """Run the completed structured record through the existing RAG pipeline.

    Raises ValueError when the record is incomplete - an incomplete record must
    never reach retrieval, because the missing fields are exactly the ones that
    change the answer.
    """
    missing = symptom_intake.missing_fields(record)
    if missing:
        raise ValueError(
            "Structured symptom information is incomplete: " + ", ".join(symptom_intake.FIELD_LABELS[f] for f in missing)
        )
    errors = symptom_intake.validate(record)
    if errors:
        raise ValueError(" ".join(errors))

    normalized_query = symptom_intake.format_rag_query(record)
    flow_log.event(flow_log.SYMPTOM_NORMALIZED, **symptom_intake.log_summary(record))
    flow_log.event(
        flow_log.RAG_QUERY_CREATED,
        field_count=len(symptom_intake.FIELD_ORDER),
        query_chars=len(normalized_query),
    )

    # One pipeline: the same assess()/retrieve() the rest of the app uses, plus
    # the authoritative WHO/NHS chunks from the vector store.
    result = assess(normalized_query)
    retrieved = retrieve(normalized_query, top_k=3)
    evidence = retrieve_evidence(normalized_query, top_k=4)
    flow_log.event(
        "RAG_EVIDENCE_RETRIEVED",
        chunk_ids=[item["chunk_id"] for item in evidence],
        sources=sorted({item["source"] for item in evidence}),
        best_distance=(evidence[0]["distance"] if evidence else None),
    )

    entry = next((item for item in KNOWLEDGE if item["id"] == (result.get("retrievedDocuments") or ["general"])[0]), None)
    ordinary, red_flags = _split_precautions(list(result.get("precautions") or []))

    possible_conditions = [
        {
            "pattern": item.get("label", ""),
            "specialty": item.get("specialty", ""),
            "document_id": item.get("document_id") or item.get("id", ""),
            "confidence": "possible",
        }
        for item in retrieved
        if item.get("label")
    ]

    urgent = bool(result.get("emergency"))
    if urgent:
        assessment = (
            "Some of the information you gave can be associated with conditions that need urgent assessment. "
            "This is not a diagnosis, and it does not confirm what is causing your symptoms."
        )
    elif possible_conditions:
        leading = possible_conditions[0]["pattern"]
        assessment = (
            f"The information you provided may be consistent with a {leading}. "
            "Possible causes include others with overlapping symptoms, so this is not a diagnosis. "
            "A clinician can confirm the diagnosis after examining you."
        )
    else:
        assessment = (
            "The information you provided does not point clearly to one reviewed pattern. "
            "A clinician can assess this properly."
        )

    payload = {
        "structuredRecord": symptom_intake.normalize_record(record),
        "normalizedQuery": normalized_query,
        "assessment": assessment,
        "possibleConditions": possible_conditions,
        "supportingSymptoms": _supporting_symptoms(record),
        "precautions": ordinary,
        "redFlags": red_flags,
        "urgent": urgent,
        "recommendedSpecialty": (result.get("recommendation") or {}).get("specialty") or (entry or {}).get("specialty", ""),
        "specialistIds": (result.get("recommendation") or {}).get("specialistIds") or [],
        "sources": list(result.get("sources") or []),
        "evidence": [
            {
                "source": item["source"],
                "title": item["title"],
                "sourceUrl": item["source_url"],
                "documentType": item["document_type"],
                "excerpt": item["text"][:320],
                "distance": item["distance"],
            }
            for item in evidence
        ],
        "citations": list(dict.fromkeys(
            f"{item['source']}: {item['title']}" for item in evidence if item.get("title")
        )),
        "evidenceAvailable": bool(evidence),
        "retrievedDocuments": list(result.get("retrievedDocuments") or []),
        "retrievalScore": result.get("retrievalScore"),
    }

    try:
        payload["contextualResponse"] = contextual_summary(
            normalized_query, payload["recommendedSpecialty"], payload["precautions"], evidence
        )
        payload["llmStatus"] = "ok"
    except Exception as exc:
        # The retrieved evidence and curated precautions stand on their own; the
        # LLM only adds wording, so its absence is reported, never papered over.
        payload["contextualResponse"] = None
        payload["llmStatus"] = f"unavailable: {type(exc).__name__}"
        flow_log.event("LLM_UNAVAILABLE", error=type(exc).__name__)

    return payload
