from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from ..config import DATA_DIR

MEDICAL_DIR = DATA_DIR / "medical"
TRAINING_FILE = MEDICAL_DIR / "Training.csv"
TESTING_FILE = MEDICAL_DIR / "Testing.csv"
MODEL_FILE = MEDICAL_DIR / "kaggle-disease-model.json"
ADDITIONAL_DIR = MEDICAL_DIR / "additional"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("_", " "))


def _terms(row: Dict[str, str]) -> List[str]:
    terms = []
    for key, value in row.items():
        if key.lower() in {"disease", "prognosis"}:
            continue
        if str(value).strip().lower() in {"1", "true", "yes"}:
            terms.append(_normalize(key))
    return terms


def _load(path: Path) -> List[Tuple[str, List[str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        records = []
        for row in csv.DictReader(handle):
            label = row.get("Disease") or row.get("prognosis") or ""
            if label:
                records.append((_normalize(label), _terms(row)))
        return records


def _load_additional(path: Path) -> List[Tuple[str, List[str]]]:
    """Load common disease/symptom CSV shapes without treating missing data as symptoms."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        records = []
        for row in csv.DictReader(handle):
            normalized = {str(key).strip().lower(): str(value or "").strip() for key, value in row.items()}
            label = normalized.get("disease") or normalized.get("diagnosis") or normalized.get("prognosis") or normalized.get("label")
            if not label:
                continue
            symptoms_text = normalized.get("symptoms") or normalized.get("symptom") or normalized.get("text") or ""
            terms = [_normalize(term) for term in re.split(r"[,;|]", symptoms_text) if _normalize(term)]
            if not terms:
                terms = [term for key, value in row.items() if str(key).lower() not in {"disease", "diagnosis", "prognosis", "label", "symptoms", "symptom", "text"} and str(value).strip().lower() in {"1", "true", "yes"} for term in [_normalize(key)]]
            if terms:
                records.append((_normalize(label), terms))
        return records


def _training_sources() -> List[Path]:
    additional = sorted(ADDITIONAL_DIR.glob("*.csv")) if ADDITIONAL_DIR.exists() else []
    if len(additional) > 3:
        raise ValueError("Use at most three additional medical CSV datasets in data/medical/additional.")
    return [TRAINING_FILE, *additional]


def train_model() -> Dict[str, Any]:
    if not TRAINING_FILE.exists() or not TESTING_FILE.exists():
        raise FileNotFoundError(f"Expected {TRAINING_FILE} and {TESTING_FILE}.")
    sources = _training_sources()
    rows = _load(TRAINING_FILE)
    for source in sources[1:]:
        rows.extend(_load_additional(source))
    profiles: Dict[str, Counter[str]] = defaultdict(Counter)
    for disease, symptoms in rows:
        profiles[disease].update(symptoms)
    vocabulary = sorted({symptom for _, symptoms in rows for symptom in symptoms})
    model = {"algorithm": "weighted symptom-profile overlap", "trainingRecords": len(rows), "diseases": sorted(profiles), "symptoms": vocabulary, "profiles": {disease: dict(values) for disease, values in profiles.items()}, "trainingSources": [str(source.relative_to(DATA_DIR)) for source in sources]}
    MEDICAL_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_FILE.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return model


def _predict_from_model(model: Dict[str, Any], symptoms: Iterable[str], top_k: int = 3) -> List[Tuple[str, float]]:
    query = set(_normalize(item) for item in symptoms if _normalize(item))
    scores = []
    for disease, profile in model["profiles"].items():
        matched = sum(1 for symptom in query if symptom in profile)
        weighted = sum(profile.get(symptom, 0) for symptom in query if symptom in profile)
        scores.append((matched + weighted / max(1, sum(profile.values())), disease))
    return [(disease, score) for score, disease in sorted(scores, reverse=True)[:top_k]]


def predict_disease(symptoms: str, model: Dict[str, Any] | None = None) -> Dict[str, Any]:
    model = model or (json.loads(MODEL_FILE.read_text(encoding="utf-8")) if MODEL_FILE.exists() else train_model())
    normalized_text = _normalize(symptoms)
    vocabulary_terms = [term for term in model.get("symptoms", []) if term and term in normalized_text]
    sentence_terms = [_normalize(part) for part in re.split(r",|;|\band\b", symptoms.lower()) if _normalize(part)]
    terms = list(dict.fromkeys([*vocabulary_terms, *sentence_terms]))
    predictions = _predict_from_model(model, terms)
    if not predictions or predictions[0][1] <= 0:
        return {"disease": "unknown", "topPredictions": []}
    return {"disease": predictions[0][0], "topPredictions": [{"disease": disease, "score": round(score, 6)} for disease, score in predictions]}


def evaluate_model(model: Dict[str, Any] | None = None) -> Dict[str, Any]:
    model = model or train_model()
    test_rows = _load(TESTING_FILE)
    labels = sorted(model["diseases"])
    matrix = {actual: {predicted: 0 for predicted in labels} for actual in labels}
    results = []
    top1 = top3 = 0
    for actual, symptoms in test_rows:
        predictions = _predict_from_model(model, symptoms)
        ranked = [disease for disease, _ in predictions]
        predicted = ranked[0] if ranked else "unknown"
        top1 += predicted == actual
        top3 += actual in ranked
        if actual in matrix and predicted in matrix[actual]:
            matrix[actual][predicted] += 1
        results.append({"actual": actual, "predicted": predicted, "top3": ranked, "passed": predicted == actual})
    precision = []
    recall = []
    f1 = []
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[actual][label] for actual in labels if actual != label)
        fn = sum(matrix[label][predicted] for predicted in labels if predicted != label)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        precision.append(p)
        recall.append(r)
        f1.append(2 * p * r / (p + r) if p + r else 0.0)
    return {"dataset": "Combined medical symptom datasets", "trainingSources": model.get("trainingSources", ["medical/Training.csv"]), "algorithm": model["algorithm"], "trainingRecords": model["trainingRecords"], "testRecords": len(test_rows), "diseases": len(model["diseases"]), "symptoms": len(model["symptoms"]), "top1Accuracy": top1 / len(test_rows), "top3Accuracy": top3 / len(test_rows), "macroPrecision": sum(precision) / len(precision), "macroRecall": sum(recall) / len(recall), "macroF1": sum(f1) / len(f1), "confusionMatrix": matrix, "cases": results, "limitations": ["These are teaching datasets, not clinical validation.", "The model routes symptom patterns and must not be presented as a diagnosis.", "Additional datasets must use a disease/diagnosis/prognosis/label column and a symptoms/symptom/text column or one-hot symptom columns."]}


def train_and_report() -> Dict[str, Any]:
    model = train_model()
    report = evaluate_model(model)
    report_file = MEDICAL_DIR / "kaggle-evaluation-report.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    report = train_and_report()
    print(json.dumps({key: report[key] for key in ("trainingRecords", "testRecords", "diseases", "symptoms", "top1Accuracy", "top3Accuracy", "macroPrecision", "macroRecall", "macroF1")}, indent=2))
