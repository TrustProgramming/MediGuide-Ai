import importlib

from app.agents.qa import answer_symptoms
from app.ui import _doctor_cards, _resolve_booking_target


def test_doctor_card_has_playwright_booking_and_profile_actions():
    specialists = [{
        "id": "gen-doc",
        "name": "Dr. Test Doctor",
        "specialty": "General Physician",
        "city": "Lahore",
        "rating": "4.9/5",
        "ratingLabel": "4.9/5",
        "oladocProfileUrl": "https://oladoc.com/test-doctor",
        "oladocUrl": "https://oladoc.com/test-doctor",
    }]

    html = _doctor_cards(specialists)

    # Every doctor card exposes the same four actions, whatever its source.
    # The visible label is short so the 2x2 grid stays readable; the full
    # wording is kept in the tooltip and the accessible name.
    for action, visible in [
        ("Book with Playwright", "Book"),
        ("Check Live Slots", "Live slots"),
        ("View Live Profile", "Profile"),
        ("Open Oladoc", "Oladoc"),
    ]:
        assert f"title='{action}'" in html, f"missing action tooltip: {action}"
        assert f"<span>{visible}</span>" in html, f"missing visible label: {visible}"
    # The accessible name must still identify the doctor, not just the verb.
    assert "aria-label='Book an appointment with Dr. Test Doctor using Playwright'" in html
    # Links carry the canonical doctor id, not the raw directory key.
    assert "/ui/book/directory%3Agen-doc" in html
    assert "/ui/slots/directory%3Agen-doc" in html
    assert "data-doctor-id='directory:gen-doc'" in html


def test_resolve_booking_target_refuses_to_match_by_name():
    """A bare name must never resolve to a directory doctor.

    This previously mapped any live doctor onto a static specialist with the
    same specialty, so booking "Dr. X (Neurology)" silently opened the booking
    form for a completely different neurologist. Identity now comes from the
    canonical id only.
    """
    specialist = {"name": "Dr. Hashir Amin Malik", "specialty": "Neurology"}

    assert _resolve_booking_target(specialist) == ""


def test_resolve_booking_target_uses_the_canonical_id():
    from app import doctor_identity as identity
    from app.models import SPECIALISTS

    neuro = next(item for item in SPECIALISTS if item["id"] == "sp-neuro")
    expected = identity.from_specialist(neuro)["doctor_id"]

    target = _resolve_booking_target(neuro)
    assert target.startswith("/ui/book/")
    assert expected.replace(":", "%3A") in target


def test_llm_key_uses_openai_alias_when_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    import app.config as config
    importlib.reload(config)

    assert config.LLM_API_KEY == "test-key"


def test_answer_symptoms_handles_llm_failure_gracefully(monkeypatch):
    import app.agents.qa as qa

    monkeypatch.setattr(qa, "contextual_summary", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = qa.answer_symptoms("headache and nausea")

    assert result["contextualResponse"] is None
    assert "recommendation" in result
