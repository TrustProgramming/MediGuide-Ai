from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

from ..rag import KNOWLEDGE


TRAINING_CASES = [(f"{entry['text']} {variant}", entry['id']) for entry in KNOWLEDGE for variant in ("symptoms", "concern", "problem")]
STOP_WORDS = {"a", "an", "and", "are", "for", "has", "have", "i", "in", "is", "it", "my", "of", "on", "the", "to", "with"}


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


def train_and_evaluate() -> Dict[str, Any]:
    train = [case for index, case in enumerate(TRAINING_CASES) if index % 3 != 2]
    test = [case for index, case in enumerate(TRAINING_CASES) if index % 3 == 2]
    vectors = [_vector(text) for text, _ in train]
    frequency = Counter(token for vector in vectors for token in vector)
    idf = {token: math.log((1 + len(train)) / (1 + count)) + 1 for token, count in frequency.items()}
    grouped: Dict[str, List[Counter[str]]] = defaultdict(list)
    for vector, (_, label) in zip(vectors, train):
        grouped[label].append(vector)
    centroids = {label: Counter({token: sum(vector[token] for vector in group) / len(group) for token in set().union(*group)}) for label, group in grouped.items()}
    predictions = []
    for text, expected in test:
        query = _vector(text)
        predicted = max(centroids, key=lambda label: _cosine(query, centroids[label], idf))
        predictions.append({"expected": expected, "predicted": predicted, "passed": predicted == expected})
    labels = sorted(grouped)
    matrix = {expected: {predicted: 0 for predicted in labels} for expected in labels}
    for item in predictions:
        matrix[item["expected"]][item["predicted"]] += 1
    passed = sum(item["passed"] for item in predictions)
    return {"algorithm": "TF-IDF nearest-centroid classifier", "trainingRecords": len(train), "heldOutRecords": len(test), "labels": labels, "accuracy": passed / len(test), "correct": passed, "total": len(test), "confusionMatrix": matrix, "cases": predictions, "limitations": ["Training examples are repository-generated topic examples, not clinical records.", "This metric measures topic routing on the local benchmark, not diagnosis or clinical safety."]}


def save_report(output: Path) -> Dict[str, Any]:
    report = train_and_evaluate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
