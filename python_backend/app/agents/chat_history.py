from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..db import db


def load_chat_context(user_id: str, conversation_id: str) -> Dict[str, Any]:
    records = [item for item in db.get_records("chat_sessions", user_id) if item.get("conversationId") == conversation_id]
    if not records:
        return {"conversationId": conversation_id, "symptoms": "", "turns": []}
    combined = " ".join(str(item.get("symptoms", "")).strip() for item in reversed(records) if item.get("symptoms"))
    question_answers = []
    for record in reversed(records):
        question_answers.extend(record.get("questionAnswers", []))
    return {"conversationId": conversation_id, "symptoms": combined, "turns": records, "pendingQuestions": records[0].get("followUpQuestions", []), "questionAnswers": question_answers}


def save_chat_turn(user_id: str, conversation_id: str, symptoms: str, result: Dict[str, Any], question_answers: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    disease_name = result.get("internalLabel") if result.get("confident") else "Assessment in progress"
    return db.create_record("chat_sessions", user_id, {"conversationId": conversation_id, "symptoms": symptoms, "internalAssessment": disease_name, "diseaseName": disease_name, "recommendation": result.get("recommendation"), "retrievedDocuments": result.get("retrievedDocuments"), "emergency": result.get("emergency", False), "followUpQuestions": result.get("followUpQuestions", []), "questionAnswers": question_answers or []})
