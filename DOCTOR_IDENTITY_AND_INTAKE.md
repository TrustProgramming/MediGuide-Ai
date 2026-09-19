# Symptom intake, RAG, and doctor identity

Written for: developers working on the MediGuide Python backend.

This document covers the flow from a patient's first message to a booking on
Oladoc, and the single rule that holds it together: **one canonical doctor
identity, created once and referenced everywhere.**

## The flow

```
free-text message
  -> structured symptom record        symptom_intake.apply_free_text
  -> follow-up questions (gaps only)  symptom_intake.pending_questions
  -> exact normalized block           symptom_intake.format_rag_query
  -> RAG retrieval                    rag.assess / rag.retrieve (Chroma, else TF-IDF)
  -> cautious assessment              agents.qa.assess_structured
  -> live doctor search               playwright_booking.search_live_oladoc_doctors
  -> CANONICAL DOCTOR OBJECT          doctor_identity.from_live_search_result
  -> doctor card (data-doctor-id)     ui._doctor_cards
  -> Book Appointment -> /ui/book/<doctor_id>
  -> appointment form (name readonly, id hidden)
  -> POST /ui/appointments/real       resolves the id server-side
  -> Playwright opens that doctor's own profile URL
  -> identity verified on arrival AND before confirmation
  -> booking proceeds, or stops with DOCTOR_IDENTITY_MISMATCH
```

## Symptom normalization

Eight fields, fixed order, fixed labels (`symptom_intake.FIELD_ORDER`):

| Field | Key | Validation |
| --- | --- | --- |
| Main symptom | `main_symptom` | non-empty |
| Location | `location` | non-empty |
| When it started | `when_started` | non-empty |
| Severity from 0–10 | `severity` | integer 0–10 |
| Other symptoms | `other_symptoms` | non-empty; "none" normalizes to `none reported` |
| Possible trigger | `possible_trigger` | non-empty; "none known" normalizes to `none identified` |
| Age | `age` | integer 0–120 |
| Important negatives | `important_negatives` | only explicitly denied red flags |

The severity label uses an **en dash** (U+2013), not a hyphen. It is written as
`"Severity from 0–10"` so the codepoint survives any encoding round trip.

### Incomplete input

`apply_free_text` fills only fields the patient actually supplied. Whatever is
still empty becomes a follow-up question, and **only** those fields are asked:

```
"I have severe stomach pain since yesterday."
  -> main_symptom = "stomach pain", when_started = "yesterday", location = "stomach"
  -> asks: severity, other symptoms, possible trigger, age, important negatives
  -> does NOT ask: main symptom, when it started
```

Answers arrive keyed by field (`answer_<field>`), so each answer is applied to
the question it answers rather than being re-parsed from loose text. The record
is persisted per conversation in `db.save_intake`, so fields survive turns.

Nothing is invented. "severe" is not converted into a severity number — the
patient is still asked for 0–10.

### Important negatives

A red flag is recorded as a negative **only** when the patient denies it. The
answer is split into clauses first, so a negation cannot bleed across the
sentence:

```
"no fever, but I do have vision loss"
  -> negatives: fever with a stiff neck
  -> positives: vision loss   (moved into Other symptoms)
```

Silence is never a denial. Red-flag prompts are chosen from the presenting
symptom (`symptom_intake.red_flag_specs`).

## RAG input

`format_rag_query` produces exactly:

```
Main symptom: headache
Location: behind the eyes
When it started: 2 days ago
Severity from 0–10: 7
Other symptoms: nausea and sensitivity to light
Possible trigger: lack of sleep
Age: 29
Important negatives: no fever, no weakness, no loss of consciousness
```

The format never varies with the patient's wording. An incomplete record raises
`ValueError` and never reaches retrieval.

### Two adjustments inside the RAG layer

`rag.retrieval_text()` derives the text used for **scoring** from the block:

1. Field labels are dropped — they are not clinical content.
2. The `Important negatives` line is dropped entirely.

Without (2), "no chest pain" would both pull retrieval toward the cardiac
document and trip the red-flag scan, i.e. a denial would be read as a symptom.
The full block is still what the RAG layer receives and what the LLM sees.

There is one pipeline: Chroma when available, the in-process TF-IDF index
otherwise.

## Medical safety

`assess_structured` returns six distinct parts: `possibleConditions`,
`supportingSymptoms`, `precautions`, `redFlags`, `recommendedSpecialty`,
`sources`. Wording stays hedged ("may be consistent with", "possible causes
include", "a clinician can confirm the diagnosis"). Possible conditions are the
retrieved **patterns** from the indexed documents, never a confirmed diagnosis,
and the Kaggle classifier label stays internal as before.

When a red flag is present, urgent-care guidance renders **above** the doctor
recommendations so booking never obscures it.

## Doctor identity

`doctor_identity.canonical_doctor()` builds one record:

```python
{
  "doctor_id": "oladoc:3637096",
  "doctor_name": "Dr. Hashir Amin Malik",
  "specialty": "Neurology",
  "location": "Lahore",
  "source": "live_search",
  "provider": "oladoc",
  "provider_doctor_id": "3637096",
  "oladoc_profile_url": "https://oladoc.com/.../hashir-amin-malik/3637096",
  "identity_strength": "provider_doctor_id",
  "booking_metadata": {...},
}
```

Identity strength, strongest first:

1. `provider_doctor_id` — Oladoc's own numeric id, parsed from the profile URL
2. `oladoc_profile_url` — the exact profile URL, hashed into the id
3. `name + specialty` — fallback only, **never name alone**

A doctor that cannot be given a stable identity is **dropped from results**
rather than offered for booking.

Doctors are registered in the `doctors` collection (`db.upsert_doctor`) the
moment they are listed, and every later stage resolves the id **server-side**.
A doctor name posted by a client is never used to look up a doctor.

## Appointment section

`/ui/book/<doctor_id>` resolves the id through the registry and renders the
doctor name into a **readonly** input alongside a hidden `doctorId`. On submit,
if the posted name disagrees with the stored record the request is refused with
`DOCTOR_IDENTITY_MISMATCH` — the displayed name and the stored id can never
drift apart.

## Playwright and Oladoc

`book_with_canonical_doctor()` receives the canonical record and:

1. Opens `oladoc_profile_url` directly. It never searches Oladoc by name and
   never clicks the first result.
2. **Gate 1** — reads the displayed identity and compares it before touching the form.
3. Fills reason, date/time, and patient details; selects pay-at-clinic.
4. **Gate 2** — re-verifies immediately before the confirmation step, in case the
   provider navigated elsewhere mid-flow.
5. Leaves OTP/CAPTCHA to the patient in a visible browser.

Identity is read with accessible locators (`get_by_role("heading", level=1)`,
stable `data-testid`/`itemprop` attributes, the live URL) rather than
positional selectors.

On failure:

```json
{
  "success": false,
  "error": "DOCTOR_IDENTITY_MISMATCH",
  "message": "The doctor displayed by the booking provider does not match the selected doctor.",
  "reasons": ["Provider doctor id mismatch: selected 2222222, provider showed 9999999."]
}
```

No alternative doctor is ever substituted.

### Duplicate names

Two doctors may share a display name. `verify_identity` treats a definitive
identifier that disagrees as a mismatch **even when the names match**, and a
name match with no distinguishing specialty is rejected outright.

## API contract

```
POST /appointments/playwright
{ "doctorId": "oladoc:3637096", "date": "2026-10-05", "time": "10:30", "reason": "..." }
```

- `404 DOCTOR_ID_MISSING` — the id resolves to no known doctor
- `409 DOCTOR_IDENTITY_MISMATCH` — the provider showed someone else
- `503 DOCTOR_UNAVAILABLE` — profile unreachable, or no bookable slots

`GET /doctors/<doctor_id>/canonical` returns the shared canonical record.

## Logging

Events on `mediguide.flow`: `SYMPTOM_NORMALIZED`, `RAG_QUERY_CREATED`,
`DOCTOR_SEARCH_COMPLETED`, `DOCTOR_SELECTED`, `APPOINTMENT_DOCTOR_SET`,
`OLADOC_DOCTOR_RESOLVED`, `OLADOC_DOCTOR_VERIFIED`, `BOOKING_STARTED`,
`BOOKING_COMPLETED`, `DOCTOR_IDENTITY_MISMATCH`, `BOOKING_FAILED`, `RAG_FAILED`.

Payloads carry identifiers, counts and outcomes only. Keys that look like
credentials, emails or phone numbers are redacted, and patient free text is
never logged. Level is set by `MEDIGUIDE_FLOW_LOG_LEVEL` (default `INFO`).

## Running it

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt
.venv\Scripts\python.exe -m playwright install chromium   # for live search/booking

$env:PYTHONPATH = (Get-Location).Path
.venv\Scripts\python.exe -c "from python_backend.app.rag import index_knowledge_documents; print(index_knowledge_documents())"
.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011
```

Tests:

```powershell
.venv\Scripts\python.exe -m pytest python_backend\tests -q
.venv\Scripts\python.exe -m pytest python_backend\tests\test_doctor_identity_flow.py -q
```

The Playwright tests stub the browser, so they need no network and no Oladoc
access. Chroma tests skip when `chromadb` is not installed.

## Troubleshooting doctor identity mismatches

| Symptom | Cause | Fix |
| --- | --- | --- |
| `DOCTOR_IDENTITY_MISMATCH` right after the profile loads | Oladoc redirected, or the profile URL is stale | Re-run the search so a fresh canonical record is stored |
| Mismatch with identical names | Two different doctors share a name | Expected. Check `provider_doctor_id` in the reason — they will differ |
| `DOCTOR_ID_MISSING` on submit | Form reached the POST without `doctorId` | Start from a doctor card; do not post the form directly |
| `DOCTOR_NOT_FOUND` on `/ui/book/<id>` | Registry has no such id | Search again — live results register on listing |
| Doctor missing from live results | No provider id and no profile URL | Intentional: unverifiable doctors are dropped, not guessed |
| `identity_strength` is `name_specialty` | Only a specialty directory URL is on file | Add the doctor's `/dr/.../<id>` profile URL to strengthen it |
| Flow events missing from logs | Level too high | Set `MEDIGUIDE_FLOW_LOG_LEVEL=INFO` |
