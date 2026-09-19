"""End-to-end coverage for: symptom intake -> RAG -> doctor -> booking.

The guarantee under test is that one canonical doctor identity survives every
stage, and that a provider showing a different doctor stops the flow instead of
booking the wrong person.
"""

from __future__ import annotations

import pytest

from app import doctor_identity as identity
from app import symptom_intake as si
from app.agents.qa import assess_structured
from app.playwright_booking import (
    DoctorIdentityMismatch,
    DoctorUnavailable,
    _canonicalize_results,
    book_with_canonical_doctor,
    extract_oladoc_doctor_identity,
    verify_page_doctor,
)

# --------------------------------------------------------------------------
# Live-search fixtures: three distinct doctors, plus a same-named pair.
# --------------------------------------------------------------------------

DR_A = {
    "name": "Dr. Ayesha Khan", "specialty": "Neurologist", "city": "Lahore",
    "rating": 4.9, "ratingLabel": "4.9/5",
    "oladocProfileUrl": "https://oladoc.com/pakistan/lahore/dr/neurologist/ayesha-khan/1111111",
    "oladocUrl": "https://oladoc.com/pakistan/lahore/neurologist",
}
DR_B = {
    "name": "Dr. Bilal Ahmed", "specialty": "Neurologist", "city": "Lahore",
    "rating": 4.8, "ratingLabel": "4.8/5",
    "oladocProfileUrl": "https://oladoc.com/pakistan/lahore/dr/neurologist/bilal-ahmed/2222222",
    "oladocUrl": "https://oladoc.com/pakistan/lahore/neurologist",
}
DR_C = {
    "name": "Dr. Cyrus Malik", "specialty": "Neurologist", "city": "Lahore",
    "rating": 4.7, "ratingLabel": "4.7/5",
    "oladocProfileUrl": "https://oladoc.com/pakistan/lahore/dr/neurologist/cyrus-malik/3333333",
    "oladocUrl": "https://oladoc.com/pakistan/lahore/neurologist",
}
# Same display name as DR_B, different person, different Oladoc id.
DR_B_TWIN = {
    "name": "Dr. Bilal Ahmed", "specialty": "Neurologist", "city": "Karachi",
    "rating": 4.1, "ratingLabel": "4.1/5",
    "oladocProfileUrl": "https://oladoc.com/pakistan/karachi/dr/neurologist/bilal-ahmed/9999999",
    "oladocUrl": "https://oladoc.com/pakistan/karachi/neurologist",
}

COMPLETE_RECORD = {
    "main_symptom": "headache",
    "location": "behind the eyes",
    "when_started": "2 days ago",
    "severity": 7,
    "other_symptoms": "nausea and sensitivity to light",
    "possible_trigger": "lack of sleep",
    "age": 29,
    "important_negatives": "no fever, no weakness, no loss of consciousness",
}


# --------------------------------------------------------------------------
# Fake Playwright surface
# --------------------------------------------------------------------------

class FakeLocator:
    def __init__(self, text: str = "", count: int = 0):
        self._text, self._count = text, count

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def count(self):
        return self._count

    def inner_text(self, timeout=0):
        return self._text

    def get_attribute(self, _name):
        return None

    def click(self, timeout=0):
        return None

    def fill(self, _text):
        return None

    def select_option(self, _text):
        return None


class FakePage:
    """Minimal page that reports whichever doctor the provider is 'showing'."""

    def __init__(self, shown_name: str, shown_specialty: str, shown_url: str):
        self.shown_name = shown_name
        self.shown_specialty = shown_specialty
        self.url = shown_url
        self.visited: list = []

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        return None

    def wait_for_load_state(self, *_a, **_k):
        return None

    def wait_for_timeout(self, *_a, **_k):
        return None

    def wait_for_function(self, *_a, **_k):
        return None

    def get_by_role(self, role, **kwargs):
        if role == "heading":
            return FakeLocator(self.shown_name, 1)
        return FakeLocator("Book appointment", 1)

    def get_by_text(self, *_a, **_k):
        return FakeLocator("", 0)

    def locator(self, selector):
        selector = str(selector)
        if selector == "h1":
            return FakeLocator(self.shown_name, 1)
        if selector in {"h2", "h1 + p", "[data-testid='doctor-specialty']", "[itemprop='medicalSpecialty']"}:
            return FakeLocator(self.shown_specialty, 1)
        if selector.startswith("button"):
            return FakeLocator("Book appointment", 1)
        if selector == "body":
            return FakeLocator("appointment page", 1)
        return FakeLocator("", 0)


class FakeBrowser:
    def __init__(self, page):
        self._page = page

    def new_page(self, **_kwargs):
        return self._page

    def close(self):
        return None


def fake_playwright(page):
    class Chromium:
        def launch(self, **_kwargs):
            return FakeBrowser(page)

    class Context:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    return lambda: Context()


# --------------------------------------------------------------------------
# 1. Symptom extraction
# --------------------------------------------------------------------------

def test_first_message_extracts_supplied_fields_and_asks_only_for_the_rest():
    record = si.apply_free_text(si.empty_record(), "I've had a severe headache behind my eyes for two days.")

    assert record["main_symptom"] == "headache"
    assert record["location"] == "behind the eyes"
    assert record["when_started"] == "two days"

    asked = [item["field"] for item in si.pending_questions(record)]
    assert "main_symptom" not in asked
    assert "location" not in asked
    assert "when_started" not in asked
    assert "severity" in asked and "age" in asked


def test_supplied_information_is_never_asked_again():
    record = si.apply_free_text(si.empty_record(), "I have severe stomach pain since yesterday.")
    asked = [item["field"] for item in si.pending_questions(record)]

    assert record["main_symptom"] == "stomach pain"
    assert record["when_started"] == "yesterday"
    assert "main_symptom" not in asked
    assert "when_started" not in asked


def test_qualitative_severity_is_not_turned_into_a_number():
    # "severe" must not be invented as a 0-10 score.
    record = si.apply_free_text(si.empty_record(), "I have a severe headache in my forehead.")
    assert record["severity"] is None
    assert "severity" in [item["field"] for item in si.pending_questions(record)]


# --------------------------------------------------------------------------
# 2. Follow-up questions build the structured object
# --------------------------------------------------------------------------

def test_answers_update_the_record_and_preserve_earlier_fields():
    record = si.apply_free_text(si.empty_record(), "I've had a headache behind my eyes for two days.")
    assert si.apply_answer(record, "severity", "about 7 out of 10") is None
    assert si.apply_answer(record, "age", "29") is None
    assert si.apply_answer(record, "other_symptoms", "nausea and sensitivity to light") is None
    assert si.apply_answer(record, "possible_trigger", "lack of sleep") is None
    assert si.apply_answer(record, "important_negatives", "none of these") is None

    assert record["main_symptom"] == "headache"          # preserved
    assert record["location"] == "behind the eyes"       # preserved
    assert record["severity"] == 7
    assert record["age"] == 29
    assert si.is_complete(record)


def test_invalid_severity_and_age_are_rejected():
    record = si.empty_record()
    assert "between 0 and 10" in (si.apply_answer(record, "severity", "42") or "")
    assert record["severity"] is None
    assert "number from 0 to 10" in (si.apply_answer(record, "severity", "very bad") or "")
    assert (si.apply_answer(record, "age", "900") or "").startswith("Enter an age")
    assert record["age"] is None


def test_negatives_are_only_recorded_when_explicitly_denied():
    flags = si.red_flags_for("headache")

    negatives, positives = si.parse_negatives("none of these", flags)
    assert set(negatives) == set(flags)
    assert positives == []

    negatives, positives = si.parse_negatives("no fever, but I do have vision loss", flags)
    assert "vision loss" in positives
    assert "vision loss" not in negatives

    # Silence is not a denial.
    negatives, positives = si.parse_negatives("I am not sure about any of that", flags)
    assert "confusion" not in positives


def test_affirmed_red_flag_moves_into_other_symptoms():
    record = si.apply_free_text(si.empty_record(), "headache in my forehead since yesterday")
    si.apply_answer(record, "other_symptoms", "none")
    si.apply_answer(record, "important_negatives", "no confusion, but I do have vision loss")

    assert "vision loss" in record["other_symptoms"]
    assert "no vision loss" not in record["important_negatives"]


# --------------------------------------------------------------------------
# 3. Exact RAG input format
# --------------------------------------------------------------------------

def test_rag_query_uses_the_exact_field_names_and_order():
    query = si.format_rag_query(COMPLETE_RECORD)

    assert query == (
        "Main symptom: headache\n"
        "Location: behind the eyes\n"
        "When it started: 2 days ago\n"
        "Severity from 0–10: 7\n"
        "Other symptoms: nausea and sensitivity to light\n"
        "Possible trigger: lack of sleep\n"
        "Age: 29\n"
        "Important negatives: no fever, no weakness, no loss of consciousness"
    )
    # The label carries an en dash, not a hyphen.
    assert "Severity from 0–10:" in query
    assert "Severity from 0-10:" not in query


def test_rag_format_does_not_change_with_user_wording():
    a = si.format_rag_query(COMPLETE_RECORD)
    b = si.format_rag_query({**COMPLETE_RECORD, "main_symptom": "bad head pain"})
    assert [line.split(":")[0] for line in a.splitlines()] == [line.split(":")[0] for line in b.splitlines()]


# --------------------------------------------------------------------------
# 4. The RAG layer receives that exact format
# --------------------------------------------------------------------------

def test_rag_receives_the_normalized_block(monkeypatch):
    seen = {}

    import app.agents.qa as qa

    real_assess = qa.assess

    def spy(text):
        seen["query"] = text
        return real_assess(text)

    monkeypatch.setattr(qa, "assess", spy)
    assess_structured(COMPLETE_RECORD)

    assert seen["query"] == si.format_rag_query(COMPLETE_RECORD)
    assert seen["query"].startswith("Main symptom: ")


def test_incomplete_record_never_reaches_rag(monkeypatch):
    import app.agents.qa as qa

    called = {"n": 0}
    monkeypatch.setattr(qa, "assess", lambda text: called.__setitem__("n", called["n"] + 1))

    with pytest.raises(ValueError):
        assess_structured({"main_symptom": "headache"})
    assert called["n"] == 0


def test_negated_red_flag_does_not_raise_an_emergency():
    record = {**COMPLETE_RECORD, "important_negatives": "no chest pain, no collapse, no seizure"}
    result = assess_structured(record)
    assert result["urgent"] is False


def test_assessment_language_stays_cautious():
    result = assess_structured(COMPLETE_RECORD)
    text = result["assessment"].lower()
    assert "may be consistent with" in text or "possible causes include" in text
    assert "not a diagnosis" in text
    for banned in ("you have ", "confirmed diagnosis", "diagnosed with", "definitely"):
        assert banned not in text


def test_assessment_returns_the_required_sections():
    result = assess_structured(COMPLETE_RECORD)
    for key in ("possibleConditions", "supportingSymptoms", "precautions", "redFlags", "recommendedSpecialty", "sources"):
        assert key in result
    assert result["recommendedSpecialty"] == "Neurology"
    assert result["supportingSymptoms"][0] == "headache"
    assert result["sources"], "sources must come from the indexed documents"


# --------------------------------------------------------------------------
# 5/6. Canonical doctor identity from live search
# --------------------------------------------------------------------------

def test_live_results_get_a_stable_provider_backed_id():
    doctors = _canonicalize_results([DR_A, DR_B, DR_C])
    assert [d["doctor_id"] for d in doctors] == ["oladoc:1111111", "oladoc:2222222", "oladoc:3333333"]
    assert all(d["identity_strength"] == "provider_doctor_id" for d in doctors)


def test_doctor_without_stable_identity_is_dropped_not_guessed():
    doctors = _canonicalize_results([DR_A, {"name": "Dr. Nameless", "specialty": "Neurologist", "oladocProfileUrl": "", "oladocUrl": ""}])
    assert len(doctors) == 1
    assert doctors[0]["doctor_id"] == "oladoc:1111111"


def test_selecting_dr_b_yields_dr_b_id():
    doctors = _canonicalize_results([DR_A, DR_B, DR_C])
    selected = doctors[1]
    assert selected["doctor_name"] == "Dr. Bilal Ahmed"
    assert selected["doctor_id"] == "oladoc:2222222"
    assert selected["doctor_id"] not in {doctors[0]["doctor_id"], doctors[2]["doctor_id"]}


# --------------------------------------------------------------------------
# 7. The appointment form carries the exact doctor
# --------------------------------------------------------------------------

def test_appointment_form_is_populated_with_the_selected_doctor():
    from app.ui import _resolve_booking_target

    doctors = _canonicalize_results([DR_A, DR_B, DR_C])
    selected = doctors[1]
    assert _resolve_booking_target(selected) == "/ui/book/oladoc%3A2222222"


def test_booking_target_is_never_guessed_from_a_name():
    """A bare name must not resolve to some other doctor in the directory."""
    from app.ui import _resolve_booking_target

    assert _resolve_booking_target({"name": "Dr. Hashir Amin Malik", "specialty": "Neurology"}) == ""
    assert _resolve_booking_target({"name": "Someone Else", "specialty": "Neurology"}) == ""


def test_registry_round_trips_the_canonical_record():
    from app.db import db

    selected = _canonicalize_results([DR_B])[0]
    db.upsert_doctor(selected)
    stored = db.find_doctor("oladoc:2222222")

    assert stored is not None
    assert stored["doctor_name"] == "Dr. Bilal Ahmed"
    assert stored["provider_doctor_id"] == "2222222"


# --------------------------------------------------------------------------
# 8. Playwright receives and verifies the exact doctor
# --------------------------------------------------------------------------

def test_playwright_opens_the_selected_doctors_own_profile(monkeypatch):
    import app.playwright_booking as pw

    selected = _canonicalize_results([DR_B])[0]
    page = FakePage("Dr. Bilal Ahmed", "Neurologist", DR_B["oladocProfileUrl"])
    monkeypatch.setattr(pw, "sync_playwright", fake_playwright(page))

    result = pw.book_with_canonical_doctor(selected, "2026-10-05", "10:30", "headache review", {"fullName": "P", "email": "p@example.com"})

    assert page.visited == [DR_B["oladocProfileUrl"]]
    assert result["doctorId"] == "oladoc:2222222"
    assert result["providerDoctorId"] == "2222222"
    assert result["success"] is True


def test_provider_showing_a_different_doctor_stops_the_booking(monkeypatch):
    import app.playwright_booking as pw

    selected = _canonicalize_results([DR_B])[0]
    # Oladoc navigated to Dr C instead.
    page = FakePage("Dr. Cyrus Malik", "Neurologist", DR_C["oladocProfileUrl"])
    monkeypatch.setattr(pw, "sync_playwright", fake_playwright(page))

    with pytest.raises(DoctorIdentityMismatch) as excinfo:
        pw.book_with_canonical_doctor(selected, "2026-10-05", "10:30", "headache review", {"fullName": "P"})

    payload = excinfo.value.to_dict()
    assert payload["success"] is False
    assert payload["error"] == "DOCTOR_IDENTITY_MISMATCH"
    assert payload["selectedDoctorId"] == "oladoc:2222222"


def test_missing_profile_url_stops_before_opening_a_browser():
    doctor = identity.canonical_doctor(
        name="Dr. Directory Only", specialty="Neurology",
        profile_url="", directory_url="", source="directory", directory_key="sp-neuro",
    )
    with pytest.raises(DoctorUnavailable):
        book_with_canonical_doctor(doctor, "2026-10-05", "10:30", "review", {"fullName": "P"})


def test_verification_reads_identity_from_the_page():
    page = FakePage("Dr. Bilal Ahmed", "Neurologist", DR_B["oladocProfileUrl"])
    observed = extract_oladoc_doctor_identity(page)

    assert observed["doctor_name"] == "Dr. Bilal Ahmed"
    assert observed["provider_doctor_id"] == "2222222"

    selected = _canonicalize_results([DR_B])[0]
    assert verify_page_doctor(page, selected, stage="test")["provider_doctor_id"] == "2222222"


# --------------------------------------------------------------------------
# 9. Duplicate names
# --------------------------------------------------------------------------

def test_two_doctors_with_the_same_name_are_distinct_identities():
    doctors = _canonicalize_results([DR_B, DR_B_TWIN])

    assert doctors[0]["doctor_name"] == doctors[1]["doctor_name"]
    assert doctors[0]["doctor_id"] != doctors[1]["doctor_id"]
    assert identity.find_duplicate_names(doctors) == {"bilal ahmed": ["oladoc:2222222", "oladoc:9999999"]}


def test_same_name_different_doctor_is_a_mismatch(monkeypatch):
    import app.playwright_booking as pw

    selected = _canonicalize_results([DR_B])[0]
    # Identical display name, but it is the other Bilal Ahmed.
    page = FakePage("Dr. Bilal Ahmed", "Neurologist", DR_B_TWIN["oladocProfileUrl"])
    monkeypatch.setattr(pw, "sync_playwright", fake_playwright(page))

    with pytest.raises(DoctorIdentityMismatch) as excinfo:
        pw.book_with_canonical_doctor(selected, "2026-10-05", "10:30", "review", {"fullName": "P"})

    assert "2222222" in str(excinfo.value.reasons)
    assert "9999999" in str(excinfo.value.reasons)


def test_name_only_match_is_rejected_without_a_distinguishing_specialty():
    selected = {"doctor_name": "Dr. Bilal Ahmed", "normalized_name": "bilal ahmed", "normalized_specialty": ""}
    observed = {"doctor_name": "Dr. Bilal Ahmed", "normalized_name": "bilal ahmed", "normalized_specialty": ""}
    assert identity.verify_identity(selected, observed)["match"] is False


def test_definitive_id_beats_a_matching_name():
    selected = {"doctor_name": "Dr. Bilal Ahmed", "normalized_name": "bilal ahmed", "provider_doctor_id": "2222222"}
    observed = {"doctor_name": "Dr. Bilal Ahmed", "normalized_name": "bilal ahmed", "provider_doctor_id": "9999999"}
    result = identity.verify_identity(selected, observed)

    assert result["match"] is False
    assert result["method"] == "provider_doctor_id"


# --------------------------------------------------------------------------
# 10. End to end
# --------------------------------------------------------------------------

def test_full_flow_keeps_one_doctor_identity(monkeypatch):
    import app.playwright_booking as pw
    from app.db import db
    from app.ui import _resolve_booking_target

    # 1. free text -> partial record
    record = si.apply_free_text(si.empty_record(), "I've had a headache behind my eyes for two days.")
    assert si.missing_fields(record)

    # 2. follow-ups complete it
    si.apply_answer(record, "severity", "7")
    si.apply_answer(record, "other_symptoms", "nausea and sensitivity to light")
    si.apply_answer(record, "possible_trigger", "lack of sleep")
    si.apply_answer(record, "age", "29")
    si.apply_answer(record, "important_negatives", "none of these")
    assert si.is_complete(record)

    # 3. exact normalized block -> RAG
    query = si.format_rag_query(record)
    assert query.startswith("Main symptom: headache\nLocation: behind the eyes\n")
    result = assess_structured(record)
    assert result["recommendedSpecialty"] == "Neurology"

    # 4. live search -> canonical doctors, Dr B selected
    doctors = _canonicalize_results([DR_A, DR_B, DR_C])
    db.upsert_doctors(doctors)
    selected = doctors[1]

    # 5. book appointment link carries Dr B's id
    assert _resolve_booking_target(selected).endswith("oladoc%3A2222222")

    # 6. backend resolves that id back to Dr B, server-side
    resolved = db.find_doctor("oladoc:2222222")
    assert resolved["doctor_name"] == "Dr. Bilal Ahmed"

    # 7. Playwright opens and verifies Dr B
    page = FakePage("Dr. Bilal Ahmed", "Neurologist", DR_B["oladocProfileUrl"])
    monkeypatch.setattr(pw, "sync_playwright", fake_playwright(page))
    booking = pw.book_with_canonical_doctor(resolved, "2026-10-05", "10:30", "headache review", {"fullName": "P"})

    assert booking["doctorId"] == selected["doctor_id"] == "oladoc:2222222"
    assert booking["doctorName"] == "Dr. Bilal Ahmed"
    assert page.visited == [DR_B["oladocProfileUrl"]]
    assert DR_A["oladocProfileUrl"] not in page.visited
    assert DR_C["oladocProfileUrl"] not in page.visited
