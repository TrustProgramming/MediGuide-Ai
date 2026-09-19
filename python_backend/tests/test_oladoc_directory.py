"""Oladoc as the only source: name extraction and bookability.

MediGuide lists, schedules and books through Oladoc alone, so a listed doctor
must be one Playwright can actually open on oladoc.com. These tests pin the two
rules that guarantee it, without touching the network.
"""

from __future__ import annotations

import pytest

from app import doctor_identity as identity
from app import oladoc_provider as op


# ------------------------------------------------------- name from the listing

@pytest.mark.parametrize("label", sorted(op._NON_NAME_LABELS))
def test_call_to_action_text_is_never_taken_as_a_doctor_name(label):
    """A card's link text is sometimes the button, not the doctor."""
    assert not op._name_matches_slug(label, "hashir-amin-malik")


def test_real_name_matches_its_profile_slug():
    assert op._name_matches_slug("Dr. Hashir Amin Malik", "hashir-amin-malik")


def test_titles_and_honorifics_do_not_break_the_match():
    assert op._name_matches_slug("Assist. Prof. Dr. Madiha Malik", "madiha-malik")


def test_a_different_doctor_does_not_match():
    """The slug is part of the doctor's own URL, so it settles identity."""
    assert not op._name_matches_slug("Dr. Ayesha Ashfaq", "hashir-amin-malik")


def test_name_recovered_from_slug_when_the_card_text_is_useless():
    assert op._slug_to_name("hashir-amin-malik") == "Dr. Hashir Amin Malik"


def test_specialty_suffix_is_not_part_of_the_name():
    """Oladoc appends the specialty when two doctors share a name."""
    assert op._slug_to_name("syed-raza-hussain-neurologist", "neurologist") == (
        "Dr. Syed Raza Hussain"
    )


def test_slug_without_that_suffix_is_untouched():
    assert op._slug_to_name("maida-ayub", "neurologist") == "Dr. Maida Ayub"


# ----------------------------------------------------------- bookable on Oladoc

def test_real_oladoc_profile_is_bookable():
    doctor = {
        "oladoc_profile_url":
            "https://oladoc.com/pakistan/lahore/dr/neurologist/hashir-amin-malik/3637096",
        "provider_doctor_id": "3637096",
    }
    assert identity.is_bookable_on_oladoc(doctor)
    assert identity.booking_url_for(doctor).endswith("/3637096")


def test_specialty_landing_page_is_not_a_doctor_profile():
    """It names no one, so it can never be the page Playwright opens."""
    doctor = {"oladoc_profile_url": "https://oladoc.com/pakistan/lahore/general-physician"}
    assert identity.oladoc_profile_url(doctor) == ""
    assert not identity.is_bookable_on_oladoc(doctor)
    assert identity.booking_url_for(doctor) == ""


def test_a_non_oladoc_profile_is_never_used_for_booking():
    """A doctor listed on another site cannot be booked on Oladoc."""
    doctor = {
        "oladoc_profile_url": "",
        "profileUrl": "https://www.marham.pk/doctors/lahore/general-physician/dr-someone",
    }
    assert not identity.is_bookable_on_oladoc(doctor)
    assert identity.booking_url_for(doctor) == ""


def test_provider_id_alone_is_enough_to_be_bookable():
    assert identity.is_bookable_on_oladoc({"provider_doctor_id": "3637096"})


# ------------------------------------------------------------------- directory

def test_directory_specialties_are_all_real_oladoc_slugs():
    known = set(op.SPECIALTY_SLUGS.values()) | set(op.SPECIALTY_SYNONYMS.values())
    for slug in op.DIRECTORY_SLUGS:
        assert slug in known, f"{slug} is not a slug Oladoc actually uses"


def test_directory_raises_when_every_specialty_fails(monkeypatch):
    """No silent empty directory: an unreachable provider must be reported."""
    def always_fails(*args, **kwargs):
        raise op.ProviderError("unreachable")

    monkeypatch.setattr(op, "search_doctors", always_fails)
    with pytest.raises(op.ProviderError):
        op.directory_doctors(use_cache=False)


def test_directory_keeps_going_when_one_specialty_fails(monkeypatch):
    def sometimes(slug, city="Lahore", use_cache=True):
        if slug == "cardiologist":
            raise op.ProviderError("that one listing is down")
        return [{
            "provider_doctor_id": f"id-{slug}",
            "doctor_id": f"oladoc:id-{slug}",
            "doctor_name": f"Dr. {slug.title()}",
        }]

    monkeypatch.setattr(op, "search_doctors", sometimes)
    doctors = op.directory_doctors(use_cache=False)
    assert len(doctors) == len(op.DIRECTORY_SLUGS) - 1
    assert all("cardiologist" not in d["provider_doctor_id"] for d in doctors)


def test_the_same_doctor_is_not_listed_twice(monkeypatch):
    """One doctor can appear under more than one specialty listing."""
    def same_doctor(slug, city="Lahore", use_cache=True):
        return [{"provider_doctor_id": "999", "doctor_id": "oladoc:999",
                 "doctor_name": "Dr. Listed Twice"}]

    monkeypatch.setattr(op, "search_doctors", same_doctor)
    assert len(op.directory_doctors(use_cache=False)) == 1


# ------------------------------------------------- time is matched, not string-compared

@pytest.mark.parametrize(
    "mediguide, oladoc",
    [
        ("18:00", "06:00 PM"),
        ("18:20", "06:20 PM"),
        ("09:30", "09:30 AM"),
        ("15:30", "03:30 PM"),
        ("12:00", "12:00 PM"),   # noon is 12 PM, not 00
        ("00:15", "12:15 AM"),   # midnight is 12 AM, not 12
    ],
)
def test_the_same_moment_written_two_ways_is_one_time(mediguide, oladoc):
    """MediGuide holds 24-hour times; Oladoc publishes 12-hour ones.

    Comparing the written form made an available slot look unavailable, which
    stopped the booking right after the date had been selected.
    """
    from app.booking_worker import _minutes_of_day

    assert _minutes_of_day(mediguide) == _minutes_of_day(oladoc)


def test_different_times_do_not_collide():
    from app.booking_worker import _minutes_of_day

    assert _minutes_of_day("06:00 AM") != _minutes_of_day("06:00 PM")
    assert _minutes_of_day("18:00") != _minutes_of_day("18:30")


@pytest.mark.parametrize(
    "bad", ["", "sometime after lunch", "10:99", "13:00 PM", "25:00", "6 PM", None],
)
def test_unreadable_times_are_rejected_rather_than_guessed(bad):
    from app.booking_worker import _minutes_of_day

    assert _minutes_of_day(bad) is None
