from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import LLM_API_KEY, LLM_API_URL, LLM_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a cautious healthcare navigation assistant. You do not diagnose. "
    "Use ONLY the retrieved WHO/NHS context supplied below plus the patient's structured "
    "symptom summary. If the retrieved context does not support an explanation, say the "
    "available information is insufficient rather than guessing. Never state a confirmed "
    "diagnosis. Prefer wording such as 'this may be consistent with' or 'possible causes "
    "include', and say that a clinician can confirm the diagnosis. Cite the sources you "
    "used by their title and source name."
)


def build_messages(
    normalized_symptoms: str,
    specialty: Optional[str],
    precautions: List[str],
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Assemble the exact chat payload, retrieved context included.

    Kept separate from the HTTP call so the composed prompt can be inspected and
    tested without contacting the provider.
    """
    context_blocks = []
    for index, item in enumerate(evidence or [], 1):
        context_blocks.append(
            f"[{index}] {item.get('source', '')}: {item.get('title', '')}\n"
            f"URL: {item.get('source_url', '')}\n"
            f"{item.get('text', '')[:1200]}"
        )
    context = "\n\n".join(context_blocks) if context_blocks else "(no retrieved context available)"

    user_content = (
        "Structured symptom summary:\n"
        f"{normalized_symptoms}\n\n"
        f"Care route under consideration: {specialty or 'not yet determined'}\n"
        f"Medically reviewed precautions already selected: {json.dumps(precautions)}\n\n"
        "Retrieved authoritative context:\n"
        f"{context}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def contextual_summary(
    symptoms: str,
    specialty: Optional[str],
    precautions: List[str],
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Ask the configured LLM to phrase the retrieved evidence cautiously.

    The retrieved WHO/NHS context is always part of the request, so the model is
    never asked to answer from the raw query alone.
    """
    if not LLM_API_URL:
        raise RuntimeError("LLM_API_URL is not configured. Set LLM_API_URL in .env.")
    if not LLM_API_KEY:
        raise RuntimeError("LLM API key is missing. Set LLM_API_KEY or OPENAI_API_KEY in .env.")

    messages = build_messages(symptoms, specialty, precautions, evidence)
    logger.info(
        "LLM request: model=%s evidence_chunks=%s context_chars=%s",
        LLM_MODEL, len(evidence or []), sum(len(m["content"]) for m in messages),
    )

    body = json.dumps({"model": LLM_MODEL, "temperature": 0.2, "messages": messages}).encode("utf-8")
    endpoint = LLM_API_URL if LLM_API_URL.endswith("/chat/completions") else f"{LLM_API_URL}/chat/completions"
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data: Dict[str, Any] = json.loads(response.read().decode("utf-8"))
        content = str(data["choices"][0]["message"]["content"]).strip()
        if not content:
            raise RuntimeError("The LLM returned an empty response.")
        return content
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"The LLM request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("The LLM request failed while generating the contextual summary.") from exc
