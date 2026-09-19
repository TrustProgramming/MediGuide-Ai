"""Structured symptom intake.

Free-form patient text is progressively normalized into the exact field set the
RAG layer expects. Nothing here invents clinical information: a field is filled
only when the patient actually supplied it, and "important negatives" are only
recorded when the patient explicitly denies a red flag.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The RAG contract uses an en dash in the severity label. Written as an escape so
# the exact codepoint survives any source-encoding round trip.
SEVERITY_LABEL = "Severity from 0–10"

FIELD_ORDER: List[Tuple[str, str]] = [
    ("main_symptom", "Main symptom"),
    ("location", "Location"),
    ("when_started", "When it started"),
    ("severity", SEVERITY_LABEL),
    ("other_symptoms", "Other symptoms"),
    ("possible_trigger", "Possible trigger"),
    ("age", "Age"),
    ("important_negatives", "Important negatives"),
]
FIELD_KEYS = [key for key, _ in FIELD_ORDER]
FIELD_LABELS = dict(FIELD_ORDER)

MIN_AGE = 0
MAX_AGE = 120
MIN_SEVERITY = 0
MAX_SEVERITY = 10

NONE_ANSWERS = {
    "no", "none", "nope", "nothing", "n/a", "na", "none known", "not known",
    "no others", "nothing else", "none that i know", "none that i know of",
    "no other symptoms", "nothing known", "no trigger", "unknown", "not sure",
    "none of these", "no to all", "none of them", "no none", "neither",
}

# Longest-first so "stomach pain" wins over "pain".
SYMPTOM_TERMS = [
    "shortness of breath", "difficulty breathing", "sensitivity to light",
    "light sensitivity", "blurred vision", "vision loss", "burning urination",
    "stomach pain", "abdominal pain", "chest pain", "back pain", "joint pain",
    "muscle pain", "neck pain", "sore throat", "runny nose", "blocked nose",
    "loss of appetite", "night sweats", "ear pain", "eye pain", "red eye",
    "headache", "migraine", "toothache", "earache", "cough", "fever",
    "nausea", "vomiting", "diarrhea", "diarrhoea", "constipation",
    "heartburn", "bloating", "rash", "itching", "hives", "swelling",
    "dizziness", "fatigue", "tiredness", "insomnia", "anxiety",
    "palpitations", "sneezing", "wheezing", "numbness", "weakness",
    "seizure", "fainting", "bleeding", "cramps", "chills",
]

LOCATION_TERMS = [
    "behind the eyes", "behind my eyes", "behind the eye", "upper abdomen",
    "lower abdomen", "lower back", "upper back", "left side", "right side",
    "back of the head", "front of the head", "forehead", "temple", "temples",
    "abdomen", "stomach", "chest", "throat", "shoulder", "ankle", "wrist",
    "knee", "elbow", "neck", "head", "back", "arm", "arms", "leg", "legs",
    "eye", "eyes", "ear", "ears", "face", "hand", "hands", "foot", "feet",
    "hip", "jaw", "scalp", "sinus", "sinuses",
]

# Longest first so multi-word locations win over their own substrings
# ("behind the eyes" must beat "eye").
LOCATION_TERMS.sort(key=len, reverse=True)
SYMPTOM_TERMS.sort(key=len, reverse=True)

_TIME_WORDS = r"(?:second|minute|hour|day|week|month|year)s?"
_NUM_WORDS = r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|several|couple\s+of)"
_WHEN_PATTERNS = [
    r"\bfor\s+(?:the\s+)?(?:past\s+|last\s+)?(" + _NUM_WORDS + r"\s+" + _TIME_WORDS + r")",
    r"\b(" + _NUM_WORDS + r"\s+" + _TIME_WORDS + r")\s+ago\b",
    r"\bsince\s+((?:yesterday|last\s+night|this\s+morning|this\s+afternoon|this\s+evening|last\s+week|last\s+month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    r"\b(yesterday|today|this\s+morning|this\s+afternoon|this\s+evening|last\s+night|tonight|last\s+week)\b",
]

_TRIGGER_TAIL = r"([a-z0-9 ,\'-]{3,50})"
_TRIGGER_PATTERNS = [
    r"\b(?:triggered\s+by|brought\s+on\s+by|caused\s+by|due\s+to|because\s+of)\s+" + _TRIGGER_TAIL,
    r"\bafter\s+((?!\d)[a-z0-9 ,\'-]{3,50})",
    r"\bfollowing\s+" + _TRIGGER_TAIL,
]

# Red-flag prompts are chosen from the presenting symptom so the questions stay
# clinically relevant. These are questions only; nothing is recorded unless the
# patient answers.
RED_FLAG_SETS: Dict[str, List[Dict[str, Any]]] = {
    "head": [
        {"label": "sudden extremely severe onset", "terms": ["sudden", "worst", "thunderclap", "out of nowhere"]},
        {"label": "weakness or numbness", "terms": ["weakness", "weak", "numbness", "numb"]},
        {"label": "confusion", "terms": ["confusion", "confused", "disoriented"]},
        {"label": "loss of consciousness", "terms": ["consciousness", "passed out", "blacked out", "fainted", "fainting"]},
        {"label": "vision loss", "terms": ["vision", "eyesight", "blind"]},
        {"label": "fever with a stiff neck", "terms": ["stiff neck", "fever", "temperature"]},
        {"label": "recent significant head injury", "terms": ["head injury", "hit my head", "banged my head", "concussion"]},
    ],
    "abdomen": [
        {"label": "blood in vomit or stool", "terms": ["blood", "bleeding", "bloody"]},
        {"label": "black tarry stool", "terms": ["black stool", "tarry", "dark stool"]},
        {"label": "fainting", "terms": ["fainting", "fainted", "passed out", "blacked out"]},
        {"label": "yellowing of the skin or eyes", "terms": ["yellow", "jaundice", "yellowing"]},
        {"label": "fever", "terms": ["fever", "temperature"]},
        {"label": "pain so severe you cannot stand upright", "terms": ["cannot stand", "can't stand", "doubled over", "bent over"]},
    ],
    "chest": [
        {"label": "pain spreading to the arm, jaw or back", "terms": ["spreading", "radiating", "into my arm", "jaw"]},
        {"label": "severe breathlessness", "terms": ["breathless", "short of breath", "shortness of breath", "cannot breathe"]},
        {"label": "fainting", "terms": ["fainting", "fainted", "passed out", "blacked out"]},
        {"label": "cold sweat", "terms": ["cold sweat", "sweating", "clammy"]},
        {"label": "an irregular or racing heartbeat", "terms": ["irregular", "racing", "palpitation", "heartbeat", "fluttering"]},
    ],
    "respiratory": [
        {"label": "severe breathing difficulty", "terms": ["breathing", "breathe", "breathless", "short of breath"]},
        {"label": "blue lips", "terms": ["blue lips", "blue", "bluish"]},
        {"label": "coughing up blood", "terms": ["coughing blood", "coughing up blood", "blood"]},
        {"label": "chest pain", "terms": ["chest"]},
        {"label": "confusion", "terms": ["confusion", "confused", "disoriented"]},
        {"label": "a high fever", "terms": ["fever", "temperature"]},
    ],
    "skin": [
        {"label": "facial or throat swelling", "terms": ["swelling", "swollen", "throat", "face swelling"]},
        {"label": "breathing difficulty", "terms": ["breathing", "breathe", "wheez"]},
        {"label": "blistering or peeling skin", "terms": ["blister", "peeling", "peel"]},
        {"label": "fever", "terms": ["fever", "temperature"]},
        {"label": "rapidly spreading redness", "terms": ["spreading", "redness", "red streaks"]},
    ],
    "default": [
        {"label": "fever", "terms": ["fever", "temperature"]},
        {"label": "chest pain", "terms": ["chest"]},
        {"label": "shortness of breath", "terms": ["breath", "breathing", "breathless"]},
        {"label": "fainting or loss of consciousness", "terms": ["fainting", "fainted", "consciousness", "passed out"]},
        {"label": "weakness or numbness", "terms": ["weakness", "weak", "numbness", "numb"]},
        {"label": "uncontrolled bleeding", "terms": ["bleeding", "blood"]},
    ],
}

# label -> match terms, across every bucket.
_RED_FLAG_TERMS: Dict[str, List[str]] = {
    spec["label"]: spec["terms"] for specs in RED_FLAG_SETS.values() for spec in specs
}

_RED_FLAG_ROUTES = [
    ("head", ("headache", "migraine", "head", "dizziness", "vision", "eye")),
    ("abdomen", ("stomach", "abdomen", "abdominal", "nausea", "vomiting", "diarrhea", "diarrhoea", "constipation", "heartburn")),
    ("chest", ("chest", "palpitation", "heart")),
    ("respiratory", ("cough", "throat", "breath", "wheez", "nose", "cold", "flu")),
    ("skin", ("rash", "itch", "hives", "skin", "swelling")),
]


def empty_record() -> Dict[str, Any]:
    """A blank structured symptom object. `asked` tracks what we already put to the patient."""
    return {
        "main_symptom": "",
        "location": "",
        "when_started": "",
        "severity": None,
        "other_symptoms": "",
        "possible_trigger": "",
        "age": None,
        "important_negatives": "",
        "asked": [],
    }


def normalize_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce a stored/partial record into the canonical shape without inventing values."""
    base = empty_record()
    for key, value in (record or {}).items():
        if key in base:
            base[key] = value
    base["asked"] = [item for item in (base.get("asked") or []) if item in FIELD_KEYS]
    return base


def red_flag_specs(main_symptom: str) -> List[Dict[str, Any]]:
    """The red-flag definitions relevant to the presenting symptom."""
    text = str(main_symptom or "").lower()
    for bucket, markers in _RED_FLAG_ROUTES:
        if any(marker in text for marker in markers):
            return RED_FLAG_SETS[bucket]
    return RED_FLAG_SETS["default"]


def red_flags_for(main_symptom: str) -> List[str]:
    """Human-readable red-flag labels for the presenting symptom."""
    return [spec["label"] for spec in red_flag_specs(main_symptom)]


def _is_none_answer(text: str) -> bool:
    cleaned = re.sub(r"[^a-z0-9 /]", "", str(text or "").strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned in NONE_ANSWERS


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" .,;:!?")).strip()


def _find_symptoms(text: str) -> List[str]:
    lowered = str(text or "").lower()
    found: List[str] = []
    for term in SYMPTOM_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lowered) and term not in found:
            found.append(term)
    return found


def _find_location(text: str) -> str:
    lowered = str(text or "").lower()
    phrase = re.search(
        r"\b(in|on|at|behind|around|under|near|across|along)\s+(?:my|the|his|her|their)?\s*([a-z ]{3,30})",
        lowered,
    )
    if phrase:
        preposition = phrase.group(1)
        candidate = _clean(phrase.group(2))
        for term in LOCATION_TERMS:
            if term in candidate:
                if preposition in {"behind", "around", "under", "near", "across", "along"}:
                    return _clean(f"{preposition} the {term}")
                return term
    for term in LOCATION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            return term
    return ""


def _find_when(text: str) -> str:
    lowered = str(text or "").lower()
    for pattern in _WHEN_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            value = _clean(match.group(1))
            if "ago" in match.group(0) and "ago" not in value:
                value = f"{value} ago"
            return value
    return ""


def _find_severity(text: str) -> Optional[int]:
    lowered = str(text or "").lower()
    match = re.search(r"\b(\d{1,2})\s*(?:/|out\s+of)\s*10\b", lowered)
    if not match:
        match = re.search(r"\bseverity\s*(?:is|of|:)?\s*(\d{1,2})\b", lowered)
    if match:
        value = int(match.group(1))
        if MIN_SEVERITY <= value <= MAX_SEVERITY:
            return value
    return None


def _find_age(text: str) -> Optional[int]:
    lowered = str(text or "").lower()
    patterns = [
        r"\b(\d{1,3})\s*(?:years?\s*old|yrs?\s*old|y/?o\b|yo\b)",
        r"\baged?\s*(?:is|:)?\s*(\d{1,3})\b",
        r"\bi\s*(?:’m|\'m|\s+am)\s+(\d{1,3})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            value = int(match.group(1))
            if MIN_AGE <= value <= MAX_AGE:
                return value
    return None


def _find_trigger(text: str) -> str:
    lowered = str(text or "").lower()
    for pattern in _TRIGGER_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            candidate = _clean(match.group(1))
            # "after two days" is a duration, not a trigger.
            if re.match(r"^" + _NUM_WORDS + r"\s+" + _TIME_WORDS, candidate):
                continue
            if candidate and not _is_none_answer(candidate):
                return candidate
    return ""


def apply_free_text(record: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Fill any still-empty field that the patient's own words clearly supply."""
    record = normalize_record(record)
    symptoms = _find_symptoms(text)

    if not record["main_symptom"] and symptoms:
        record["main_symptom"] = symptoms[0]
    if not record["location"]:
        record["location"] = _find_location(text)
    if not record["when_started"]:
        record["when_started"] = _find_when(text)
    if record["severity"] is None:
        record["severity"] = _find_severity(text)
    if record["age"] is None:
        record["age"] = _find_age(text)
    if not record["possible_trigger"]:
        record["possible_trigger"] = _find_trigger(text)
    if not record["other_symptoms"]:
        others = [item for item in symptoms if item != record["main_symptom"]]
        if others:
            record["other_symptoms"] = ", ".join(others)
    return record


def parse_negatives(answer: str, red_flags: List[str]) -> Tuple[List[str], List[str]]:
    """Split a red-flag answer into explicitly denied and explicitly affirmed items.

    The answer is split into clauses first, so a negation in one clause cannot
    bleed into the next: in "no fever, but I do have vision loss", the fever is
    denied and the vision loss is affirmed. A flag the patient never mentioned
    stays unestablished - silence is not a denial.
    """
    text = str(answer or "").lower()
    clauses = [
        part for part in re.split(r"[,;.]|\band\b|\bbut\b|\bhowever\b|\bthough\b", text) if part.strip()
    ]

    blanket_no = bool(re.search(
        r"\b(none of (these|them|those)|no to all|not any of|none at all|nothing like that)\b", text
    )) or _is_none_answer(answer)

    positives: List[str] = []
    negatives: List[str] = []
    for flag in red_flags:
        terms = _RED_FLAG_TERMS.get(flag) or [word for word in re.findall(r"[a-z]{4,}", flag.lower())]
        for clause in clauses:
            if not any(term in clause for term in terms):
                continue
            negated = bool(re.search(r"\b(no|not|never|none|denies|without|dont|doesn't|don't)\b", clause))
            (negatives if negated else positives).append(flag)
            break

    if blanket_no:
        negatives = [flag for flag in red_flags if flag not in positives]

    return negatives, positives


def apply_answer(record: Dict[str, Any], field: str, text: str) -> Optional[str]:
    """Apply a direct answer to one field. Returns an error message, or None on success.

    Answers are attributed to the field whose question was asked, so there is no
    guesswork about what the patient is responding to.
    """
    if field not in FIELD_KEYS:
        return f"Unknown field: {field}"
    for key, value in normalize_record(record).items():
        record.setdefault(key, value)
    answer = _clean(text)
    if not answer:
        return None

    if field not in record.get("asked", []):
        record.setdefault("asked", []).append(field)

    if field == "severity":
        match = re.search(r"\b(\d{1,2})\b", answer)
        if not match:
            return "Enter the severity as a number from 0 to 10."
        value = int(match.group(1))
        if not (MIN_SEVERITY <= value <= MAX_SEVERITY):
            return f"Severity must be between {MIN_SEVERITY} and {MAX_SEVERITY}."
        record["severity"] = value
        return None

    if field == "age":
        match = re.search(r"\b(\d{1,3})\b", answer)
        if not match:
            return "Enter the patient's age in years."
        value = int(match.group(1))
        if not (MIN_AGE <= value <= MAX_AGE):
            return f"Enter an age between {MIN_AGE} and {MAX_AGE}."
        record["age"] = value
        return None

    if field == "important_negatives":
        negatives, positives = parse_negatives(answer, red_flags_for(record.get("main_symptom", "")))
        record["important_negatives"] = (
            ", ".join(f"no {item}" for item in negatives) if negatives
            else "none explicitly confirmed by the patient"
        )
        if positives:
            existing = [
                item.strip() for item in str(record.get("other_symptoms") or "").split(",")
                if item.strip() and not _is_none_answer(item)
            ]
            for item in positives:
                if item not in existing:
                    existing.append(item)
            record["other_symptoms"] = ", ".join(existing)
        return None

    if field == "other_symptoms":
        record["other_symptoms"] = "none reported" if _is_none_answer(answer) else answer
        return None

    if field == "possible_trigger":
        record["possible_trigger"] = "none identified" if _is_none_answer(answer) else answer
        return None

    if _is_none_answer(answer) and field in {"location", "when_started"}:
        return f"Please answer the question about {FIELD_LABELS[field].lower()}."

    record[field] = answer
    return None


def is_filled(record: Dict[str, Any], field: str) -> bool:
    value = normalize_record(record).get(field)
    if field in {"severity", "age"}:
        return isinstance(value, int)
    return bool(str(value or "").strip())


def missing_fields(record: Dict[str, Any]) -> List[str]:
    return [field for field in FIELD_KEYS if not is_filled(record, field)]


def is_complete(record: Dict[str, Any]) -> bool:
    return not missing_fields(record)


def question_for(record: Dict[str, Any], field: str) -> str:
    if field == "important_negatives":
        listed = "; ".join(red_flags_for(record.get("main_symptom", "")))
        return (
            "To rule out urgent causes, which of these do you have and which do you NOT have? "
            f"({listed}). Answer 'none of these' if you have none of them."
        )
    return {
        "main_symptom": "What is the main symptom that brought you here today?",
        "location": "Where exactly is it? Name the body area or side.",
        "when_started": "When did it start, and has it got better, worse, or stayed the same?",
        "severity": "How severe is it right now on a scale of 0 to 10?",
        "other_symptoms": "What other symptoms have come with it? Reply 'none' if there are none.",
        "possible_trigger": "Did anything seem to bring it on, such as food, medicine, injury, activity, or stress? Reply 'none known' if nothing stands out.",
        "age": "How old is the patient?",
    }[field]


def pending_questions(record: Dict[str, Any]) -> List[Dict[str, str]]:
    """Concise questions for the missing fields only. Answered fields are never re-asked."""
    return [{"field": field, "question": question_for(record, field)} for field in missing_fields(record)]


def validate(record: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    record = normalize_record(record)
    severity = record.get("severity")
    if severity is not None and (not isinstance(severity, int) or not (MIN_SEVERITY <= severity <= MAX_SEVERITY)):
        errors.append(f"Severity must be a whole number between {MIN_SEVERITY} and {MAX_SEVERITY}.")
    age = record.get("age")
    if age is not None and (not isinstance(age, int) or not (MIN_AGE <= age <= MAX_AGE)):
        errors.append(f"Age must be a whole number between {MIN_AGE} and {MAX_AGE}.")
    return errors


def display_value(record: Dict[str, Any], field: str) -> str:
    value = normalize_record(record).get(field)
    if value is None:
        return "not provided"
    text = str(value).strip()
    return text or "not provided"


def format_rag_query(record: Dict[str, Any]) -> str:
    """The exact normalized representation handed to the RAG layer.

    Field labels and order are fixed by contract and never vary with the
    patient's wording.
    """
    return "\n".join(f"{label}: {display_value(record, key)}" for key, label in FIELD_ORDER)


def log_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    """Non-identifying shape summary for logs - never the free-text clinical content."""
    record = normalize_record(record)
    return {
        "fields_present": [field for field in FIELD_KEYS if is_filled(record, field)],
        "fields_missing": missing_fields(record),
        "severity": record.get("severity"),
    }
