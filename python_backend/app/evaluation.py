from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .agents.appointment import validate_appointment
from .agents.medical_information import decrypt_details, encrypt_details
from .agents.qa import answer_symptoms
from .agents.training import train_and_evaluate
from .agents.kaggle_training import evaluate_model as evaluate_kaggle_model
from .rag import KNOWLEDGE, assess, retrieve


RAG_CASES = [
    ("throbbing headache with light sensitivity", "headache-migraine"),
    ("sudden facial droop and speech difficulty", "neurological-emergency"),
    ("stomach pain and vomiting", "digestive"),
    ("itchy red skin rash", "skin-rash"),
    ("stress and insomnia", "stress-sleep"),
    ("chest pressure and palpitations", "cardiac-emergency"),
    ("joint pain after a sprain", "joint-muscle"),
    ("cough and sore throat", "respiratory"),
    ("blurred vision and red eye", "eye-vision"),
    ("sneezing and food allergy", "allergy"),
    ("general persistent tiredness", "general"),
]


def run_evaluation() -> Dict[str, Any]:
    rag_results = [{"query": query, "expected": expected, "actual": retrieve(query)[0]["id"], "passed": retrieve(query)[0]["id"] == expected} for query, expected in RAG_CASES]
    patient_result = answer_symptoms("cough")
    patient_result.pop("internalLabel", None)
    qa_cases = [
        answer_symptoms("chest pain and severe breathing difficulty")["emergency"],
        "internalLabel" not in patient_result,
        answer_symptoms("sudden facial droop and speech difficulty")["recommendation"]["specialistIds"] == ["sp-neuro"],
    ]
    appointment_cases = [
        validate_appointment("2099-01-01", "10:00", "routine review", True)["valid"],
        not validate_appointment("2099-01-01", "10:00", "routine review", False)["valid"],
        not validate_appointment("2000-01-01", "10:00", "routine review", True)["valid"],
    ]
    encrypted = encrypt_details("private medical history")
    agent_results = {"qaAgent": {"passed": sum(qa_cases), "total": len(qa_cases)}, "appointmentAgent": {"passed": sum(appointment_cases), "total": len(appointment_cases)}, "medicalInformationAgent": {"passed": decrypt_details(encrypted) == "private medical history", "encryption": encrypted["encryption"]}}
    return {"modelInventory": {"ragDocuments": len(KNOWLEDGE), "classifier": "Kaggle weighted symptom-profile model"}, "kaggleModel": evaluate_kaggle_model(), "classifier": train_and_evaluate(), "rag": {"passed": sum(item["passed"] for item in rag_results), "total": len(rag_results), "accuracy": sum(item["passed"] for item in rag_results) / len(rag_results), "cases": rag_results}, "agents": agent_results, "limitations": ["The Python backend does not claim clinical accuracy.", "The Kaggle dataset is a public teaching dataset, not clinician-validated patient data.", "Evaluation cases are routing checks, not clinical validation."]}


def main() -> None:
    report = run_evaluation()
    output = Path(__file__).resolve().parents[1] / "data" / "python-evaluation-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
