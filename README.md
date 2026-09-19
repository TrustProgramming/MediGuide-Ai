# MediGuide

MediGuide is a healthcare navigation web application. A patient describes what
they are feeling in their own words, and MediGuide helps them work out what kind
of doctor to see and then book a real appointment with that exact doctor.

It does **not** diagnose illness. It gives general health information taken from
official sources (the World Health Organization and the UK National Health
Service), points out warning signs that need urgent care, suggests the right
*kind* of specialist, and then helps the patient book with a real doctor on
[oladoc.com](https://oladoc.com) — a Pakistani doctor directory.

**Who it is for**

| Person | What they get |
| --- | --- |
| A patient | Describe symptoms, get cautious information, find a real doctor, book a real appointment |
| A developer | A small, well-separated Python codebase with a real database, tests, and migrations |
| A reviewer | Every claim in this file is checked against the code; limitations are stated openly |

**The problem it solves.** People often do not know which specialist to see. They
search the internet, find low-quality advice, and either panic or delay. MediGuide
replaces that with information from WHO and NHS pages, a conservative tone, and a
direct path to booking a specific, verified doctor.

**The one rule the whole system is built around:** the doctor the patient picks
must be the exact doctor who gets booked. Not a doctor with the same name, not
another doctor in the same specialty. Much of the design below exists to enforce
that.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Current Project Status](#current-project-status)
- [Why This Technology Stack](#why-this-technology-stack)
- [Main Features](#main-features)
- [User Roles and Permissions](#user-roles-and-permissions)
- [User Journeys](#user-journeys)
- [Application Architecture](#application-architecture)
- [Architecture Principles](#architecture-principles)
- [Project Folder Structure](#project-folder-structure)
- [Technology Stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Configuration Files](#configuration-files)
- [Database](#database)
- [Database Schema](#database-schema)
- [Database Relationships](#database-relationships)
- [Database Migrations](#database-migrations)
- [JSON-to-Database Migration](#json-to-database-migration)
- [Backend](#backend)
- [API Reference](#api-reference)
- [Frontend](#frontend)
- [Pages and Screens](#pages-and-screens)
- [MediGuide Branding and Icon System](#mediguide-branding-and-icon-system)
- [UI Design System](#ui-design-system)
- [Accessibility](#accessibility)
- [Responsive Design](#responsive-design)
- [Deterministic Modules](#deterministic-modules)
- [Important Business Logic](#important-business-logic)
- [Data Flows](#data-flows)
- [Authentication](#authentication)
- [Authorization](#authorization)
- [Validation](#validation)
- [Error Handling](#error-handling)
- [Loading, Empty and Error States](#loading-empty-and-error-states)
- [RAG and the Medical Knowledge Base](#rag-and-the-medical-knowledge-base)
- [Live Doctor Search and Booking](#live-doctor-search-and-booking)
- [Performance](#performance)
- [Caching](#caching)
- [Security](#security)
- [Privacy and Sensitive Data](#privacy-and-sensitive-data)
- [External Services](#external-services)
- [Dependencies](#dependencies)
- [Commands](#commands)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Logging](#logging)
- [Build, Deployment, Docker and CI](#build-deployment-docker-and-ci)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Important Files](#important-files)
- [Where to Make Changes](#where-to-make-changes)
- [Adding a New Feature](#adding-a-new-feature)
- [Changing the Database Safely](#changing-the-database-safely)
- [Code Quality Rules](#code-quality-rules)
- [Known Limitations](#known-limitations)
- [Glossary](#glossary)
- [Complete Command Reference](#complete-command-reference)
- [Documentation Map](#documentation-map)

---

## Project Overview

MediGuide is one Python application. A single FastAPI process serves two things:

1. **A website** (server-rendered HTML) at `/ui` — this is what patients use.
2. **A JSON API** at the root — the same features, for programs instead of people.

There is no separate frontend project, no Node.js, and no JavaScript build step.

### The main workflow

```
Patient types symptoms
   ↓
MediGuide asks only for the details that are still missing
   ↓
The completed details are turned into a fixed, structured block of text
   ↓
That block is searched against WHO/NHS documents stored in a vector database
   ↓
The patient sees: possible patterns, precautions, red flags, and a specialty
   ↓
MediGuide searches oladoc.com live for real doctors in that specialty
   ↓
The patient picks one doctor and sees that doctor's real free appointment times
   ↓
Playwright opens that doctor's own booking page and checks it is the right doctor
   ↓
The patient enters the provider's verification code (OTP)
   ↓
If the provider confirms, MediGuide saves the appointment and emails the patient
```

### What makes this project unusual

- **Live data, not sample data.** Doctor names, clinics and appointment times are
  read from oladoc.com at the moment you ask. If a time is not on their page, it
  is not shown.
- **Identity is enforced, not assumed.** Every doctor carries a stable ID taken
  from the provider. Before booking, the system re-checks the doctor on the
  provider's page and stops if it does not match.
- **The app refuses to pretend.** If the knowledge base is unavailable, it says
  so instead of inventing medical text. If the provider never confirmed a
  booking, the appointment is labelled "Selected", not "Confirmed".

---

## Current Project Status

| Area | Status | Notes |
| --- | --- | --- |
| Accounts, login, sessions | **Working** | Verified end to end |
| Symptom intake and follow-up questions | **Working** | 29 tests |
| RAG over WHO/NHS documents | **Working** | 347 WHO/NHS chunks indexed, real semantic search |
| LLM phrasing layer | **Not working here** | The configured API key has no credits (see [Known Limitations](#known-limitations)) |
| Live doctor search (oladoc.com) | **Working** | Verified against the live site |
| Live appointment slots | **Working** | Verified: UI times equal what Playwright read |
| Booking up to the provider's confirm button | **Working** | Verified against the live site |
| Final booking submission | **Deliberately disabled** | `OLADOC_SUBMIT_MODE=disabled` by default |
| OTP handoff | **Built, partly unverified** | Logic tested with a stub; never seen a real OTP challenge |
| Confirmation email (SMTP) | **Working** | Verified against a real SMTP server, message captured and read back |
| Confirmation email (Cal.com) | **Working** | Verified against the live Cal.com v2 API: booking created, read back, then cancelled |
| Doctor directory | **Working** | Read live from Oladoc; verified 48 doctors, all with real profiles |
| Playwright redirect to Oladoc | **Working** | Verified a visible window landing on `oladoc.com/appointment/{clinic}/{doctorId}` for the selected doctor's physical clinic |
| Provider window hand-off | **Working** | Verified the window stays open after the run and closes on answer (+7/-7 browser processes) |
| Time applied on Oladoc | **Working** | Verified `18:20` selects the page's `06:20 PM` slot |
| Time-picker focus effect | **Working** | Verified on the live page; screenshot reviewed |
| Live schedule | **Working** | Verified reading real published slots from a doctor's Oladoc page |
| Database + migrations | **Working** | SQLite verified; PostgreSQL supported but untested here |
| Deployment / Docker / CI | **Does not exist** | Not configured in this repository |

---

## Why This Technology Stack

This section explains **why each choice was made and what was rejected**. If you
are wondering "why not React?" or "why not PostgreSQL?", the answers are here.

### Decision 1 — Python + FastAPI for the backend

**Chosen:** FastAPI.

**Why.** The project began as a Python teaching project, and the whole domain
layer is Python. FastAPI gives automatic request validation through Pydantic,
generates OpenAPI documentation for free (visit `/docs`), and can serve HTML and
JSON from the same process.

**What was rejected and what would have happened:**

| Alternative | What would have changed | Why not |
| --- | --- | --- |
| Django | Would have brought its own ORM, admin, templates and auth. Much of this project's code would be replaced by Django conventions. | Too heavy for ~10k lines, and it would have forced a rewrite of working code |
| Flask | Very similar outcome, slightly less built in. | No automatic validation or OpenAPI; we would have hand-written what FastAPI gives free |
| Node.js / Express | The whole Python domain layer, the Kaggle model and the RAG code would need rewriting in JavaScript. | The valuable part of this project is Python. Rewriting it buys nothing |

### Decision 2 — Server-rendered HTML instead of a JavaScript framework

**Chosen:** HTML generated in Python ([`ui.py`](python_backend/app/ui.py)).

**Why.** There is one user type and no offline or real-time requirement. Every
page is a form submit or a link. Adding React would mean a second project, a
build step, a bundler, a dev server, an API client layer and CORS — for pages
that are fundamentally forms.

**What was rejected:**

| Alternative | What would have happened | Why not |
| --- | --- | --- |
| React / Vue / Next.js | Two processes, a `package.json`, a build pipeline, and client state duplicating server state | The app has no interactivity that needs it. Cost far exceeds benefit |
| **Streamlit** | Business logic would survive, but `ui.py` (~2,000 lines) would be rewritten | Evaluated and rejected. See below |
| Jinja2 templates | HTML would move out of Python strings into template files | A genuine improvement and a reasonable future step. Not done yet |

**Why Streamlit specifically was rejected.** This was considered seriously, because
an earlier Streamlit prototype existed in this repository. Four problems decided it:

1. **The OTP flow fights Streamlit's model.** Booking runs a background thread
   holding a live browser, and the page polls for `OTP_REQUIRED`. Streamlit re-runs
   the entire script on every interaction, and background threads cannot safely
   touch Streamlit state.
2. **Session security gets worse.** Today the session token lives in an
   `httponly` cookie that JavaScript cannot read. Streamlit would put it in
   server memory keyed by browser session — a downgrade.
3. **Real URLs are lost.** `/ui/confirmation/{id}` would become
   `/?page=confirmation&id=…`. Confirmation *emails link to those pages*, so
   shareable links would degrade.
4. **The design system would not survive.** Custom cards, the brand mark and
   dark mode would fall back to Streamlit theming plus fragile HTML injection.

Streamlit remains a good fit for a data dashboard. It is a poor fit for a
multi-page transactional app with browser automation and emailed links.

### Decision 3 — SQLAlchemy + Alembic, SQLite by default, PostgreSQL supported

**Chosen:** SQLAlchemy 2.x ORM, Alembic migrations, connection chosen by
`DATABASE_URL`.

**Why not "just SQLite" and why not "just PostgreSQL"?** Both were checked
against reality rather than assumed:

- The machine this runs on has **no database server installed at all** — no
  PostgreSQL, no Docker, no MySQL, nothing listening on port 5432.
- It is launched by double-clicking [`start-mediguide.bat`](start-mediguide.bat).
- The real data was small: 7 users, 76 chat turns, 4 notifications, 2 appointments.

Requiring PostgreSQL would have made the application **unable to start** on the
only computer it runs on. So the decision was to make the *storage layer*
portable rather than to pick one database and hard-code it:

- One ORM, one schema, one set of migrations.
- `DATABASE_URL` unset → a local SQLite file, no installation needed.
- `DATABASE_URL=postgresql+psycopg://…` → PostgreSQL, with no code change.
- Column types were deliberately chosen to mean the same thing on both.

**Honest caveat:** the PostgreSQL path is written to be portable but **has not
been executed**, because there is no server here to test against.

| Alternative | Why not |
| --- | --- |
| Raw `sqlite3` + hand-written SQL | No migrations, no portability, and every query becomes a string to get wrong |
| MongoDB | The data is clearly relational: users own appointments which own checklists. Documents would re-create joins in application code |
| Keeping JSON files | This is what was replaced. See [JSON-to-Database Migration](#json-to-database-migration) |
| Prisma / Drizzle | JavaScript tools. Wrong language for this project |

### Decision 4 — ChromaDB with a local embedding model

**Chosen:** ChromaDB 1.5.9, using the MiniLM ONNX model it bundles.

**Why.** The knowledge base must find *meaning*, not matching words: a patient
writing "burning when I pee" should reach the NHS page on urinary tract
infections even though none of those words appear in it. That needs embeddings.

**Why the bundled local model rather than OpenAI embeddings.** The configured
OpenAI key has no credits. Rather than leave retrieval broken, the store falls
back to ChromaDB's bundled MiniLM model, which runs on this machine, needs no
key and no network at query time. Measured quality:

```
"persistent lower abdominal pain" vs "stomach ache low in the belly"  →  0.721 similar
"persistent lower abdominal pain" vs "migraine sensitive to light"    →  0.171 similar
```

**Why version 1.5.9 specifically.** The project previously pinned `chromadb==0.5.23`,
which has **no wheel for Python 3.13+**. On this machine that meant the package
could not install, and because `rag.py` imported it at module level, **the whole
application failed to start**. Version 1.5.9 ships `cp39-abi3` wheels that work on
Python 3.14. The import is now also optional, so a missing ChromaDB degrades to a
simpler word-based search instead of breaking the app.

| Alternative | Why not |
| --- | --- |
| Pinecone / Weaviate (cloud) | Needs an account, a key and network access at query time |
| FAISS | A library, not a store — we would have to write persistence and metadata handling ourselves |
| Plain keyword search | Already present as the fallback. It cannot match meaning, only words |

### Decision 5 — Playwright for the provider integration

**Chosen:** Playwright (already a project dependency).

**Why.** oladoc.com publishes **no public API**. Doctor lists, clinics and
appointment times are rendered by JavaScript in the browser, so a plain HTTP
request returns a page with no doctors in it. A real browser is the only way to
read them.

| Alternative | Why not |
| --- | --- |
| `requests` + BeautifulSoup | The content is rendered client-side; there is nothing to parse in the raw HTML |
| Selenium | Would work, but Playwright was already installed. Adding a second browser tool would be duplication |
| An official API | Does not exist |

### Decision 6 — JWT in an httpOnly cookie for sessions

**Chosen:** A signed JSON Web Token stored in an `httponly` cookie.

**Why.** The token is self-contained, so no session table lookup is needed, and
`httponly` means page JavaScript cannot read it — which blunts token theft
through cross-site scripting.

| Alternative | Why not |
| --- | --- |
| Server-side session table | More correct for instant logout, but adds a table and a query on every request for a single-user app |
| Token in `localStorage` | Readable by any script on the page. Strictly worse |

### Decision 7 — scrypt for password hashing

**Chosen:** `hashlib.scrypt` with `n=16384, r=8, p=1`, a per-user random salt,
and constant-time comparison.

**Why.** scrypt is deliberately slow and memory-hard, which makes guessing
passwords expensive. It is in the Python standard library, so it adds no
dependency. Comparison uses `hmac.compare_digest` so that the time taken does not
leak information about the password.

| Alternative | Why not |
| --- | --- |
| bcrypt / argon2 | Both are good — argon2 is arguably better — but each adds a dependency for no gain here |
| SHA-256 alone | Far too fast. Modern hardware guesses billions per second |
| Plain text | Never |

### Decision 8 — AES-256-GCM for medical records

**Chosen:** AES-256-GCM, key derived from the application secret.

**Why.** Medical history is the most sensitive data here. GCM is *authenticated*
encryption: it detects tampering as well as hiding content. The key is derived
with SHA-256 from the application secret, so there is one secret to manage.

**Trade-off, stated plainly:** because the key comes from the application secret,
**changing the secret makes existing encrypted records unreadable.** This is
documented in [`secrets_manager.py`](python_backend/app/secrets_manager.py).

---

## Main Features

### 1. Accounts and sign-in

Patients create an account with a name, email and password, then sign in.

- **Who can use it:** anyone, no invitation needed.
- **How it works:** the password is hashed with scrypt and never stored in
  readable form. Signing in returns a signed token placed in an `httponly` cookie.
- **Code:** [`auth.py`](python_backend/app/auth.py), routes `ui_register`,
  `ui_login`, `ui_logout` in [`ui.py`](python_backend/app/ui.py)
- **API:** `POST /auth/register`, `POST /auth/login`

### 2. Structured symptom intake

This is the feature that makes the medical part trustworthy. Instead of sending
whatever the patient typed to a search engine, MediGuide first collects **eight
specific pieces of information** and only then searches.

- **Who can use it:** signed-in patients.
- **How it works:** the first message is read for whatever it already contains.
  Whatever is missing becomes a short follow-up question. Questions that were
  already answered are never asked again.
- **Code:** [`symptom_intake.py`](python_backend/app/symptom_intake.py)
- **Pages:** `/ui/chat`
- **API:** `POST /ai/health-chat`

Example:

> Patient: *"I have severe stomach pain since yesterday."*
>
> MediGuide records **main symptom** = stomach pain, **location** = stomach,
> **when it started** = yesterday — and asks only about severity, other symptoms,
> triggers, age and warning signs. It does **not** ask "what is your main symptom?"

### 3. Medical information from WHO and NHS

- **Who can use it:** signed-in patients, after intake is complete.
- **How it works:** the structured block is converted to numbers (an embedding)
  and compared against 347 pieces of WHO and NHS pages stored in ChromaDB. The
  closest pieces are shown with their source and a link.
- **Code:** [`rag.py`](python_backend/app/rag.py),
  [`chroma_store.py`](python_backend/app/chroma_store.py),
  [`ingestion.py`](python_backend/app/ingestion.py),
  [`agents/qa.py`](python_backend/app/agents/qa.py)

The answer is always split into six parts: possible patterns, supporting
symptoms, precautions, red flags, recommended specialty, and sources.

### 4. Red-flag (urgent care) detection

- **How it works:** if the symptoms match known emergency patterns, urgent-care
  advice is shown **above** the doctor list, so booking never hides it.
- **Important detail:** denials are not treated as symptoms. If a patient says
  "no chest pain", that must not trigger the cardiac warning. The
  `Important negatives` line is removed before the red-flag scan for exactly this
  reason. This is tested.
- **Code:** `RED_FLAGS` and `retrieval_text()` in [`rag.py`](python_backend/app/rag.py)

### 5. Live doctor search

- **How it works:** Playwright opens the oladoc.com listing page for the city and
  specialty, scrolls until the list stops growing, and reads every doctor link.
- **Why it is filtered by URL:** oladoc.com puts laboratories under the same
  `/dr/` path as doctors. The previous code filtered with `"/lab/" in href`, which
  **never matches** `/radiology-lab/`, so searching "urologist" returned
  diagnostic labs from other cities. The fix reads the specialty out of the URL
  itself and keeps only entries matching the requested specialty.
- **Code:** [`oladoc_provider.py`](python_backend/app/oladoc_provider.py)
- **Pages:** `/ui/doctors` · **API:** `POST /doctors/search`

### 6. Live appointment slots

- **How it works:** each doctor's profile lists clinics, each clinic has a
  booking page, and that page has one chip per bookable day and one element per
  free time. MediGuide clicks the requested date and reads the times.
- **Guarantee:** the times shown are exactly the times read from the provider.
  Verified by comparing the page output against a separate extraction run.
- **Pages:** `/ui/slots/{doctor_id}`

### 7. Booking with doctor verification

- **How it works:** the canonical doctor record (including the provider's own ID)
  is passed to Playwright, which opens **that doctor's own booking page** — it
  never searches by name. Identity is checked on arrival and again immediately
  before confirming.
- **If it does not match:** the flow stops with `DOCTOR_IDENTITY_MISMATCH`. No
  other doctor is ever booked instead.
- **Code:** [`booking_worker.py`](python_backend/app/booking_worker.py),
  [`doctor_identity.py`](python_backend/app/doctor_identity.py)

### 8. OTP handoff

- **How it works:** when the provider asks for a verification code, the browser
  pauses and the MediGuide page shows an input. The patient types the code they
  received; it is passed straight to the provider's field and then discarded.
- **What MediGuide never does:** read your messages, read your email, guess a
  code, or store one. There is a test asserting the code does not remain in
  session state.

### 9. Appointment records, checklist and confirmation card

- The card clearly separates **Selected** (you chose it) from **Confirmed** (the
  provider confirmed it). A reference number is shown only if the provider
  actually returned one.
- **Code:** [`appointments.py`](python_backend/app/appointments.py)
- **Pages:** `/ui/confirmation/{appointment_id}`

### 10. Confirmation email

- Sent **only** after the provider confirms.
- **Important behaviour:** if the email fails, the appointment stays
  `CONFIRMED` and only `emailStatus` becomes `FAILED`. A retry button is offered.
- **Code:** [`email_service.py`](python_backend/app/email_service.py)

### 11. Supporting features

| Feature | Page | Code |
| --- | --- | --- |
| Encrypted medical information | `/ui/medical-information` | [`agents/medical_information.py`](python_backend/app/agents/medical_information.py) |
| Complaints | `/ui/complaints` | repository in [`repositories.py`](python_backend/app/database/repositories.py) |
| Notification log | `/ui/notifications` | [`agents/notifications.py`](python_backend/app/agents/notifications.py) |
| Conversation history | `/ui/history` | [`agents/chat_history.py`](python_backend/app/agents/chat_history.py) |
| Payments (Stripe test mode only) | `/ui/payments` | [`agents/payments.py`](python_backend/app/agents/payments.py) |
| Disease model training | command line | [`agents/kaggle_training.py`](python_backend/app/agents/kaggle_training.py) |

---

## User Roles and Permissions

**MediGuide has exactly one role: the signed-in patient.** There is no admin
role, no doctor login and no staff area. This is a deliberate simplification, not
an omission — the application never needs to act on behalf of a clinic.

| Capability | Signed out | Signed-in patient |
| --- | --- | --- |
| Landing page | Yes | Yes |
| Register / log in | Yes | — |
| Symptom chat, doctor search, live slots | No | Yes |
| Book appointments, view own appointments | No | Yes |
| View **another** patient's data | No | **No** |

**How isolation is enforced.** Every query is filtered by the signed-in user's
ID at the repository level, not in the page. For example
`find_appointment_for_user(appointment_id, user_id)` requires both, so guessing
another patient's appointment ID returns nothing. There is a test for this
(`test_appointment_update_is_scoped_to_its_owner`).

---

## User Journeys

### Journey A — From symptoms to a booked appointment

```
 1. Open http://localhost:8011/ui
 2. Create an account (name, email, password ≥ 8 characters)
 3. Open "AI health chat"
 4. Describe the problem in your own words
 5. Answer only the follow-up questions shown
 6. Read: possible patterns, precautions, red flags, recommended specialty, sources
 7. Review the live doctors listed for that specialty
 8. Choose a doctor → "Check Live Slots"
 9. Pick a date; real free times appear
10. Click a time → the booking session starts
11. Playwright opens that doctor's booking page and verifies the doctor
12. Enter the provider's verification code when asked
13. If the provider confirms: appointment saved, confirmation card, email sent
```

**Note on step 13:** with the default settings the flow stops just before the
provider's confirm button and reports `SUBMIT_DISABLED`. This is a safety gate,
described in [Live Doctor Search and Booking](#live-doctor-search-and-booking).

### Journey B — Browse doctors directly

```
1. Sign in → "Find a doctor"
2. Search a specialty ("urologist", "heart specialist", "skin doctor")
3. Each result offers the same four actions
4. Continue from step 8 of Journey A
```

### Journey C — Urgent symptoms

```
1. Describe symptoms that match an emergency pattern
2. Urgent-care advice appears ABOVE everything else
3. Follow-up questions are skipped — nothing delays the warning
4. Doctor booking is still offered, but never in place of the warning
```

---

## Application Architecture

```
                         Patient's browser
                                 │
                    ┌────────────┴────────────┐
                    │                         │
              HTML pages (/ui)          JSON API (/…)
                    │                         │
                    └────────────┬────────────┘
                                 │
                    ui.py                  main.py
              (pages, forms)         (API routes, validation)
                                 │
                                 ▼
                      Domain / business logic
        symptom_intake · doctor_identity · appointments ·
        booking_session · rag · availability · auth
                                 │
                                 ▼
                     db.py  (facade, keeps old call sites working)
                                 │
                                 ▼
                    database/repositories.py
                        (the only SQL in the app)
                                 │
                                 ▼
                  SQLAlchemy  →  SQLite or PostgreSQL

        Side systems, reached only from the domain layer:
        oladoc_provider / booking_worker  →  Playwright  →  oladoc.com
        chroma_store                      →  ChromaDB    →  WHO/NHS text
        email_service                     →  SMTP
        llm.py                            →  OpenAI-compatible API
```

**Layer by layer**

| Layer | File(s) | Responsibility | Must not do |
| --- | --- | --- | --- |
| Pages | [`ui.py`](python_backend/app/ui.py) | Render HTML, read forms, redirect | Contain business rules or SQL |
| API | [`main.py`](python_backend/app/main.py) | Validate requests, map errors to status codes | Contain business rules or SQL |
| Domain | `symptom_intake`, `doctor_identity`, `appointments`, `booking_session`, `rag` | The actual rules | Touch the database directly |
| Facade | [`db.py`](python_backend/app/db.py) | Keep the old `db.method()` call style working | Contain SQL |
| Repositories | [`repositories.py`](python_backend/app/database/repositories.py) | All SQL | Contain business rules |
| Storage | SQLAlchemy + Alembic | Tables, constraints, migrations | — |

Dependencies point **downwards only**. Pages call the domain; the domain calls
repositories; repositories call the database. Nothing calls back upwards.

---

## Architecture Principles

**1. One responsibility per module.** [`doctor_identity.py`](python_backend/app/doctor_identity.py)
only answers "are these two doctors the same person?". It does not render HTML,
call the database or open a browser.

**2. Business logic is deterministic.** The same input produces the same output.
For example, `format_rag_query()` always produces the same eight labelled lines
in the same order. This makes behaviour testable without a database or a network.

> **Deterministic** simply means: give the function the same input, get the same
> answer, every time. Nothing hidden, nothing random.

**3. Pure logic is separated from side effects.** Text parsing, validation and
identity comparison are pure functions. Browsers, email and the database are
called from separate places. That is why the booking state machine can be tested
with a fake provider.

**4. All SQL lives in one place.** Only
[`repositories.py`](python_backend/app/database/repositories.py) imports SQLAlchemy.

**5. Validate at the edge.** Requests are validated by Pydantic before reaching
the domain. The domain revalidates anything safety-critical.

**6. Errors are handled centrally.** One module,
[`errors.py`](python_backend/app/errors.py), decides what every failure looks like.

**7. Never invent data.** If the provider published no times, show none. If the
knowledge base is unavailable, say so.

---

## Project Folder Structure

```text
mediguide-ai-final/
├── README.md                     ← this file: the main documentation
├── start-mediguide.bat           ← double-click launcher (migrations + server)
├── alembic.ini                   ← migration tool configuration
├── conftest.py                   ← makes `pytest` work from the repo root
├── .env                          ← YOUR real settings and secrets (never committed)
├── .env.example                  ← template listing every setting
├── .gitignore
│
├── DOCTOR_IDENTITY_AND_INTAKE.md ← deep dive: symptom fields + doctor identity
├── AGENT_ARCHITECTURE.md         ← older design notes (partly out of date)
├── CHANGES.md                    ← historical change log (partly out of date)
├── RAG_AND_WEBSITE_EVALUATION.txt← older evaluation notes (partly out of date)
│
└── python_backend/
    ├── requirements.txt          ← Python packages and why each is present
    │
    ├── app/                      ← the entire application
    │   ├── main.py               ← FastAPI app, JSON API routes, middleware
    │   ├── ui.py                 ← every HTML page and the CSS design system
    │   ├── config.py             ← reads settings from the environment
    │   ├── secrets_manager.py    ← resolves the application secret safely
    │   ├── errors.py             ← central error handling
    │   ├── flow_log.py           ← structured event logging
    │   ├── branding.py           ← MediGuide logo, favicon, SVG icon set
    │   │
    │   ├── db.py                 ← facade over the repositories
    │   ├── database/
    │   │   ├── models.py         ← the tables (SQLAlchemy models)
    │   │   ├── engine.py         ← connection, pooling, SQLite pragmas
    │   │   ├── repositories.py   ← all SQL queries
    │   │   └── migrate_json.py   ← one-off import from the old JSON files
    │   │
    │   ├── auth.py               ← password hashing, tokens
    │   ├── models.py             ← the built-in specialist directory (static data)
    │   ├── availability.py       ← clinic opening hours and slot conflicts
    │   ├── appointments.py       ← appointment records and statuses
    │   ├── booking_session.py    ← booking state machine
    │   ├── booking_worker.py     ← runs the browser booking, handles OTP
    │   ├── oladoc_provider.py    ← live doctor search and live slots
    │   ├── playwright_booking.py ← older booking helpers, still used
    │   ├── doctor_identity.py    ← canonical doctor identity and verification
    │   ├── symptom_intake.py     ← the eight symptom fields
    │   ├── rag.py                ← retrieval and red-flag detection
    │   ├── chroma_store.py       ← vector database access and embeddings
    │   ├── ingestion.py          ← downloads and indexes WHO/NHS pages
    │   ├── llm.py                ← optional language-model phrasing
    │   ├── email_service.py      ← confirmation email
    │   ├── booking.py            ← builds referral links to provider profiles
    │   ├── calcom.py             ← optional Cal.com integration
    │   ├── evaluation.py         ← command-line evaluation report
    │   │
    │   └── agents/               ← focused single-purpose services
    │       ├── qa.py             ← turns intake + retrieval into an answer
    │       ├── appointment.py    ← appointment validation rules
    │       ├── chat_history.py   ← saves and loads conversation turns
    │       ├── medical_information.py ← AES-256-GCM encryption
    │       ├── notifications.py  ← email / WhatsApp notification log
    │       ├── payments.py       ← Stripe test-mode payment intents
    │       ├── kaggle_training.py← trains the symptom→disease model
    │       └── training.py       ← evaluates the topic classifier
    │
    ├── migrations/               ← Alembic
    │   ├── env.py                ← reads DATABASE_URL from the app config
    │   ├── script.py.mako        ← template for new migrations
    │   └── versions/             ← the migration files themselves
    │
    ├── tests/                    ← 117 tests
    │   ├── conftest.py           ← gives every test its own database
    │   └── test_*.py
    │
    └── data/                     ← generated data (not source code)
        ├── mediguide.db          ← the SQLite database
        ├── .mediguide-secret     ← generated local secret (git-ignored)
        ├── chroma/               ← the vector index
        ├── rag-ingestion-manifest.json ← which pages were indexed
        ├── python-evaluation-report.json
        └── medical/              ← Kaggle CSVs and the trained model
```

### Why the folders are named and arranged this way

- **`python_backend/app/`** — everything runnable lives under one package so
  imports are unambiguous (`python_backend.app.main`).
- **`database/` as a sub-package** — storage is the one concern that must not
  leak. Putting models, engine and repositories together makes it obvious that
  SQL belongs there and nowhere else.
- **`agents/`** — a name inherited from the project's earlier design. Each file
  is a small service with one job. The name is kept because it is used throughout
  the code and in [`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md).
- **`data/` is generated, never edited by hand** — the database, the vector
  index, the trained model and the secret all live here so one folder can be
  deleted to reset the application.
- **`migrations/versions/` filenames** carry a timestamp
  (`20260918_0334_initial_schema.py`) so they sort chronologically, which is the
  order they must run in.

---

## Technology Stack

### Core

| Technology | Version | Purpose | Why this one |
| --- | --- | --- | --- |
| Python | 3.14 (3.11+ works) | The language | The whole domain layer is Python |
| FastAPI | 0.115.0 | Web framework | Validation and OpenAPI for free; serves HTML and JSON together |
| Uvicorn | 0.30.6 | Web server | The standard ASGI server for FastAPI |

### Data

| Technology | Version | Purpose | Why this one |
| --- | --- | --- | --- |
| SQLAlchemy | 2.0.54 | ORM / query builder | Lets the same code run on SQLite and PostgreSQL |
| Alembic | 1.20.0 | Database migrations | Version-controlled, repeatable schema changes |
| SQLite | built in | Default database | No server to install; correct for a single-user desktop app |
| PostgreSQL | optional | Production database | Supported through `DATABASE_URL`; **not tested here** |
| ChromaDB | 1.5.9 | Vector database | Stores WHO/NHS text as numbers for meaning-based search |

### Security

| Technology | Purpose | Why this one |
| --- | --- | --- |
| PyJWT 2.9.0 | Session tokens | Small, focused, does one job |
| `hashlib.scrypt` | Password hashing | Slow and memory-hard by design; standard library |
| cryptography 43.0.1 | AES-256-GCM | Authenticated encryption for medical records |

### Integrations

| Technology | Purpose | Why this one |
| --- | --- | --- |
| Playwright 1.48.0 | Browser automation | oladoc.com renders content with JavaScript; only a real browser can read it |
| `smtplib` | Email | Standard library; no dependency needed |
| Stripe 11.1.0 | Payments (test mode only) | Official SDK; the code refuses any key not starting `sk_test_` |

### Testing

| Technology | Purpose |
| --- | --- |
| pytest | Test runner — 117 tests |
| `requests` | Used by end-to-end checks against a running server |

### Not used, and deliberately so

Node.js · npm · React · Vue · Webpack · Vite · Tailwind · Docker · Redis ·
Celery · an external message queue. Each was considered and rejected; see
[Why This Technology Stack](#why-this-technology-stack).

---

## Prerequisites

- [ ] **Windows** — the launcher is a `.bat` file. The Python itself is
      cross-platform; only `start-mediguide.bat` is Windows-specific.
- [ ] **Python 3.11 or newer** (3.14 is what this was developed on).
      Check with `py --version`.
- [ ] **About 1 GB of free disk space** — mostly Playwright browsers (~400 MB)
      and the ChromaDB embedding model (~80 MB).
- [ ] **An internet connection** — required for live doctor search, live slots
      and the first ChromaDB model download. Not required afterwards for normal
      browsing.

Optional:

- [ ] An SMTP account, for confirmation emails.
- [ ] A PostgreSQL server, if you do not want SQLite.
- [ ] An OpenAI-compatible API key, for the optional phrasing layer.

---

## Installation

### Step 1 — Create the virtual environment

```powershell
cd "path\to\mediguide-ai-final"
py -m venv .venv
```

> A virtual environment is a private folder of Python packages, so this project's
> packages do not clash with anything else on your computer.

### Step 2 — Install the packages

```powershell
.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt
```

### Step 3 — Install the browsers Playwright needs

```powershell
.venv\Scripts\python.exe -m playwright install chromium
```

Needed for live doctor search, live slots and booking. Skip it and those
features return a clear error; the rest of the app still works.

### Step 4 — Create your settings file

```powershell
copy .env.example .env
```

Then open `.env` and set at minimum:

```ini
MEDIGUIDE_SECRET=<paste a long random value here>
```

Generate one with:

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

> **Why this matters.** That value signs your session tokens *and* produces the
> key that encrypts medical records. If it is left unset, MediGuide generates a
> random one into `python_backend/data/.mediguide-secret` so local development
> still works — but it will never fall back to a value written in the source
> code, because a secret in source code is not a secret.

### Step 5 — Create the database tables

```powershell
$env:PYTHONPATH = (Get-Location).Path
.venv\Scripts\python.exe -m alembic upgrade head
```

This creates `python_backend/data/mediguide.db` with all 13 tables.

### Step 6 — Fill the medical knowledge base (optional but recommended)

```powershell
.venv\Scripts\python.exe -m python_backend.app.ingestion
```

Downloads ~35 WHO and NHS pages and indexes them (a few minutes; the embedding
model downloads once). Without this the app still runs, using a small built-in
set of topic notes instead.

### Step 7 — Start it

```powershell
.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011
```

Or simply **double-click `start-mediguide.bat`**, which loads `.env`, runs
migrations, starts the server and opens the browser.

Then open <http://localhost:8011/ui>.

---

## Quick Start

For someone who just wants it running:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
$env:PYTHONPATH = (Get-Location).Path
$env:MEDIGUIDE_SECRET = (.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011
```

---

## Environment Variables

Every variable below is genuinely read somewhere in the code. Nothing here is
aspirational.

### Core

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `MEDIGUIDE_SECRET` | **Yes in production** | Signs session tokens and derives the medical-record encryption key. Minimum 32 characters. Known placeholders are rejected | `kJ8x…48-random-chars` |
| `DATABASE_URL` | No | Database connection. Unset → local SQLite file | `postgresql+psycopg://user:pw@host:5432/mediguide` |
| `PORT` | No | Port used when running `main.py` directly (default `8011`) | `8011` |
| `ALLOWED_ORIGIN` | No | Which website may call the API from a browser. `*` means any | `http://127.0.0.1:8011` |
| `APP_BASE_URL` | No | The app's own address, used to build return links | `http://127.0.0.1:8011` |

### Database tuning

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `DB_POOL_SIZE` | No | Kept-open connections (PostgreSQL only) | `5` |
| `DB_MAX_OVERFLOW` | No | Extra connections under load (PostgreSQL only) | `10` |
| `DB_ECHO` | No | Log every SQL statement. Development only — very noisy | `false` |

### Knowledge base and language model

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `LLM_API_URL` | No | OpenAI-compatible endpoint | `https://api.openai.com/v1` |
| `LLM_API_KEY` | No | Key for the above | `sk-…` |
| `OPENAI_API_KEY` | No | Alternative name for `LLM_API_KEY` | `sk-…` |
| `LLM_MODEL` | No | Chat model name | `gpt-4o-mini` |
| `EMBEDDING_PROVIDER` | No | Embedding source | `openai-compatible` |
| `EMBEDDING_MODEL` | No | Embedding model name | `text-embedding-3-small` |
| `CHROMA_HOST` | No | Remote ChromaDB host. Unset → local file | `localhost` |
| `CHROMA_PORT` | No | Remote ChromaDB port | `8000` |
| `CHROMA_PERSIST_DIRECTORY` | No | Where the local index is stored | `./python_backend/data/chroma` |
| `CHROMA_COLLECTION` | No | Collection name | `mediguide-docs` |
| `CHROMA_TENANT` | No | ChromaDB tenant (server mode) | *(empty)* |
| `CHROMA_DATABASE` | No | ChromaDB database (server mode) | *(empty)* |

> **If no key is set,** embeddings are produced by the local MiniLM model that
> ChromaDB bundles. Retrieval works; only the optional phrasing layer is lost.

### Live provider and booking

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `OLADOC_SUBMIT_MODE` | No | **Safety gate.** `disabled` (default) stops before the provider's confirm button. `live` allows real bookings | `disabled` |
| `OLADOC_BOOKING_HEADLESS` | No | Hide the booking browser. Default `false` so the patient can answer the provider's challenge | `false` |
| `OLADOC_OTP_WAIT_SECONDS` | No | How long to wait for the patient's code | `300` |
| `OLADOC_BOOKING_URL` | No | Override the booking page | *(empty)* |
| `OLADOC_SEARCH_SUBMIT_SELECTOR` | No | Optional CSS selector override | *(empty)* |
| `OLADOC_CONTINUE_SELECTOR` | No | Optional CSS selector override | *(empty)* |
| `OLADOC_SUCCESS_URL_CONTAINS` | No | Words marking a success page | `confirmation\|success\|thank` |
| `OLADOC_PATIENT_WAIT_SECONDS` | No | Legacy wait used by the older booking helper | `300` |
| `CLINIC_NAME_SELECTOR` | No | Selector for the patient-name field on a non-Oladoc clinic portal | *(empty)* |
| `CLINIC_EMAIL_SELECTOR` | No | Selector for the email field | *(empty)* |
| `CLINIC_PHONE_SELECTOR` | No | Selector for the phone field | *(empty)* |
| `CLINIC_CITY_SELECTOR` | No | Selector for the city field | *(empty)* |
| `CLINIC_CONTACT_SELECTOR` | No | Selector for the contact-preference field | *(empty)* |
| `CLINIC_REASON_SELECTOR` | No | Selector for the reason-for-visit field | *(empty)* |
| `CLINIC_DATE_SELECTOR` | No | Selector for the date field | *(empty)* |
| `CLINIC_TIME_SELECTOR` | No | Selector for the time field | *(empty)* |
| `CLINIC_SUBMIT_SELECTOR` | No | Selector for the submit button | *(empty)* |

> These nine are only needed if you point MediGuide at a clinic portal other
> than Oladoc. Left empty, the built-in selectors are used. They are never
> applied to OTP or CAPTCHA fields — see `_is_patient_verification_selector`
> in [`playwright_booking.py`](python_backend/app/playwright_booking.py).

### Email

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `SMTP_HOST` | For email | Mail server address | `smtp.gmail.com` |
| `SMTP_PORT` | No | Port. `465` uses SSL, anything else uses STARTTLS | `587` |
| `SMTP_USER` | For email | Mail username | `you@example.com` |
| `SMTP_PASSWORD` | For email | Mail password or app password | *(secret)* |
| `SMTP_FROM` | No | "From" address; defaults to `SMTP_USER` | `noreply@example.com` |

### Optional extras

| Variable | Required | Purpose |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | No | Stripe **test** key. The code rejects anything not starting `sk_test_` |
| `CAL_API_KEY` | No | Cal.com API key. Also the fallback email channel (see below) |
| `CAL_EVENT_TYPE_ID` | No | **Numeric** Cal.com event type id. A non-numeric value disables the channel rather than crashing |
| `CAL_API_URL`, `CAL_EMBED_URL` | No | Cal.com endpoints. v2 is the only supported API; v1 is decommissioned |
| `CONFIRMATION_CHANNEL` | No | `auto` (default), `smtp`, `calcom` or `both`. See *How the confirmation reaches the patient* |
| `MEDIGUIDE_FLOW_LOG_LEVEL` | No | Detail level for flow events. Default `INFO` |

### Date and time are applied the same way

The patient's date and time are both written into the booking form and both
applied on Oladoc: MediGuide clicks the provider's own date chip, then the
provider's own time slot, so the page ends up showing exactly what was
recorded.

**Why the time used to be ignored.** The two were not actually treated the
same. The date was matched against the ISO date in the chip's class, but the
time was compared as *written text* - and MediGuide holds `18:00` where Oladoc
publishes `06:00 PM`. The strings never matched, so an available slot looked
unavailable and the run stopped immediately after the date was selected. Both
sides are now reduced to minutes since midnight before they are compared, so
the two spellings of one moment are recognised as the same time. (Noon and
midnight are the cases that catch a naive converter: 12 PM is 12:00 and 12 AM
is 00:00, not the other way round.)

**Focusing the time picker.** While the time is being chosen, the provider page
is given a soft sepia blur and the time-slot box alone is lifted out of it -
white, sharp, and ringed - so the one remaining decision is obvious. The effect
is removed as soon as a time is selected, and it stays up when the requested
minute is gone so the patient is already looking at the alternatives.

The veil is **purely visual and never swallows clicks** (`pointer-events:none`).
It would be easy to make the rest of the page inert to force attention, but the
patient may still need to reach a cookie banner, a CAPTCHA or an OTP field on
that same page, and blocking those would break the booking outright.

---

### The provider window is the patient's to finish in

When a booking starts, Playwright opens a **real, visible Chromium window** on
that doctor's own Oladoc booking page (`/appointment/{clinicId}/{doctorId}`),
choosing the doctor's **physical** clinic rather than an online-video one,
because payment is pay at clinic.

**Why it looked like nothing opened.** The worker used to close the browser in
a `finally:` block, so it closed the moment automation stopped. Automation
almost always stops *before* the booking is done - the patient still has to
pick a time the provider will accept, clear a CAPTCHA, or enter an OTP. The
window therefore flashed open for about five seconds and vanished, which from
the patient's side is indistinguishable from never having opened.

**What happens now.** The window stays open on the doctor's page and the
MediGuide page says so, telling the patient to finish there. It is closed only
when keeping it open would be wrong or pointless:

| Situation | Window |
| --- | --- |
| Automation stopped, patient can still finish | **stays open** |
| Provider displayed a different doctor | closed at once - never leave a patient on the wrong doctor's page |
| Provider confirmed the booking | closed - nothing left to do |
| Internal error, or the session expired | closed |
| Patient answered "did Oladoc confirm your appointment?" | closed - they are done with it |
| `OLADOC_PATIENT_WINDOW_SECONDS` elapsed (default 900) | closed, so windows cannot leak |

`OLADOC_BOOKING_HEADLESS` must stay `false` in normal use; `true` exists only
so automated tests do not spawn windows. A headless run is why the patient
would see no browser at all.

---

### Why every doctor comes from Oladoc

MediGuide reads doctors, live schedules and clinics from Oladoc, and Playwright
opens Oladoc pages only. Payment is always pay at clinic; no online payment is
ever selected.

**What was wrong before.** The browse page was populated from a hard-coded list
of ten doctors sourced from **Marham**, each carrying an `oladocUrl` that
pointed at a *specialty landing page* (`/pakistan/lahore/general-physician`)
rather than that doctor's profile. The consequences ran right through the flow:

* "View Live Profile" opened marham.pk while the UI called it the provider profile.
* "Open Oladoc" opened a listing page that named no doctor at all.
* Playwright had no Oladoc profile to open, so booking died at
  `NO_CLINIC_PUBLISHED` before any redirect happened.
* "View live schedule" failed, because there was no Oladoc page to read.

Checked against Oladoc, those Marham doctors **do not exist there**, so there
was no profile to redirect to and no schedule to read.

**Why not match them by name.** Searching Oladoc for a Marham doctor's name and
booking whoever comes back would mean booking a *different person*. Matching on
a name alone is the one thing the identity rules forbid, so the local list was
removed instead.

**What replaced it.** `oladoc_provider.directory_doctors()` reads real doctors
from Oladoc listings across several specialties. Every card therefore has a
real profile URL and provider id, which is exactly what Playwright needs. The
specialties are fetched in parallel (each is an independent page load) and
cached for 15 minutes; a background warm-up at startup means the first visitor
gets the cached copy rather than waiting. A specialty that fails is skipped; if
all fail the page says Oladoc is unreachable rather than showing an empty
directory or substituting local data.

**Two guards keep it honest.**

* `identity.oladoc_profile_url()` accepts only a real profile
  (`/pakistan/{city}/dr/{specialty}/{name}/{id}`). A specialty landing page
  names nobody, so it is never treated as a profile, and
  `booking_url_for()` returns `""` rather than handing Playwright a page for
  the wrong doctor - or a different provider's site.
* The listing scraper no longer trusts a card's link text as the doctor's name.
  Some cards expose the call to action instead, which is how a doctor once came
  back named **"View Profile"**. The profile slug is part of the doctor's own
  URL, so it decides: a scraped name that shares no token with the slug is
  rejected and the name is recovered from the slug instead.

**Appointments made before this change** still reference a Marham doctor. Their
schedule genuinely cannot be read, so the page says the doctor is not on Oladoc
and offers the live directory, rather than showing a retry that can never work.

---

### How the confirmation reaches the patient

There are two delivery channels, chosen by `CONFIRMATION_CHANNEL`.

**Why two at all.** An SMTP account is the normal way to send mail, but not
every deployment has one. Cal.com was already a dependency for scheduling, and
it will email a patient on our behalf — so it doubles as a fallback rather than
adding a third-party mail service (SendGrid, Mailgun, SES) that would need its
own account, its own key and its own domain verification.

**What Cal.com actually does, and what it does not.** Cal.com has *no*
general "send an email" endpoint. It is a scheduling service. The only mail it
sends for us is the booking confirmation and calendar invite it delivers to the
attendee when a booking is created against an event type. So the `calcom`
channel does not merely email — it also books the slot on the practice
calendar. That side effect is the reason `auto` prefers SMTP: a plain
confirmation should not silently create a second calendar entry.

| Value | Behaviour |
| --- | --- |
| `auto` *(default)* | SMTP if configured, otherwise Cal.com |
| `smtp` | SMTP only |
| `calcom` | Cal.com only — books the slot **and** emails the attendee |
| `both` | Send over both; succeeds if either does |

**Two faults fixed in the Cal.com client.** It previously sent no
`User-Agent`, so Cloudflare rejected every request with `403` (error 1010)
before it reached the API; and it targeted API v1, which is decommissioned. The
client now sets a User-Agent and the required `cal-api-version` header.

**When the email is sent.** Not at the moment a booking is confirmed, but when
the patient confirms the on-site checklist — that is where the clinic and
contact details become final. A booking the provider confirmed itself has
already been emailed, so the checklist step only sends when nothing has gone
out yet.

**Honesty in the wording.** An appointment the patient confirmed themselves is
not the same as one the provider verified. The email says so: its subject is
"Appointment **recorded**" rather than "Appointment **confirmed**", and the body
states that MediGuide did not see the provider confirm it. This is the same
rule the confirmation card follows — nothing is presented as verified that was
not actually observed.

**A note on timezones.** Cal.com takes the start time in UTC, so an
appointment's local clinic time is converted before sending. Windows ships no
system timezone database, which is why `tzdata` is a dependency; without it the
code falls back to a fixed offset and logs a warning rather than failing to
send.

---

## Configuration Files

| File | Purpose | Safe to change? |
| --- | --- | --- |
| [`.env`](.env) | Your real settings and secrets | Yes — **never commit it** |
| [`.env.example`](.env.example) | Template documenting every setting | Yes — keep it in step with the code |
| [`alembic.ini`](alembic.ini) | Migration tool settings | Rarely. The database URL is **not** here on purpose — it is read from the app so no credentials sit in a committed file |
| [`conftest.py`](conftest.py) | Puts the project on Python's import path for tests | No |
| [`python_backend/requirements.txt`](python_backend/requirements.txt) | Packages and why each exists | Yes, with care |
| [`start-mediguide.bat`](start-mediguide.bat) | Loads `.env`, runs migrations, starts the server | Yes |
| [`.gitignore`](.gitignore) | Keeps secrets and generated files out of version control | Yes |

---

## Database

### What and why

MediGuide stores its data in a **relational database** — data organised into
tables that reference each other. Previously it used JSON files; see
[JSON-to-Database Migration](#json-to-database-migration).

The connection is decided by `DATABASE_URL`:

| Setting | Result | When to use |
| --- | --- | --- |
| Not set | SQLite file at `python_backend/data/mediguide.db` | Local use. Nothing to install |
| `postgresql+psycopg://…` | PostgreSQL | A server deployment with several users |

**Why the default is SQLite.** There is no database server installed on the
target machine, and the app is launched by double-clicking a `.bat` file.
Requiring PostgreSQL would stop it from starting at all.

**Why PostgreSQL is still supported.** So that moving to a server needs a
settings change, not a rewrite. Column types were chosen to mean the same thing
on both. **This path has not been tested** — no PostgreSQL server was available.

### SQLite settings, and why each one

Set in [`engine.py`](python_backend/app/database/engine.py):

| Setting | Value | Why |
| --- | --- | --- |
| `journal_mode` | `WAL` | Lets readers work while a writer is active. Without it, concurrent requests would block each other |
| `synchronous` | `NORMAL` | A sensible balance of durability and speed for WAL |
| `foreign_keys` | `ON` | SQLite ignores foreign keys **by default**. Without this line the relationships would be decoration |
| `busy_timeout` | `30000` ms | Wait for a lock rather than failing instantly |
| `check_same_thread` | `False` | The server handles requests on several threads; sessions are short-lived and never shared |

A test (`test_concurrent_writes_all_land`) starts 8 threads writing at once and
checks all 8 rows arrive.

---

## Database Schema

13 tables. `alembic_version` is a 14th, owned by the migration tool.

| Table | Purpose |
| --- | --- |
| `users` | Patient accounts |
| `doctors` | Canonical doctor identities from live search or the built-in directory |
| `appointments` | Bookings, with snapshots of the doctor and clinic |
| `conversations` | One AI health-chat conversation |
| `conversation_turns` | One message within a conversation |
| `symptom_intakes` | The eight structured symptom fields for a conversation |
| `checklists` | Pre-visit checklist attached to an appointment |
| `checklist_items` | One tickable item |
| `notifications` | Record of every email/WhatsApp attempt |
| `medical_information` | Patient records, encrypted |
| `complaints` | Patient complaints |
| `payments` | Stripe test-mode payment records |
| `redirect_handoffs` | Audit trail of referral links generated |

### `users` (10 columns)

| Field | Type | Purpose | Rules |
| --- | --- | --- | --- |
| `id` | String(32) | Unique account ID | Primary key |
| `full_name` | String(160) | Display name | Required |
| `email` | String(320) | Sign-in address | **Unique**, indexed |
| `password_hash` | String(255) | scrypt hash as `salt:hash` | Required, never readable |
| `phone` | String(40) | Contact number | Optional |
| `city` | String(120) | Used to search doctors nearby | Defaults `Lahore` |
| `preferred_contact` | String(16) | Notification channel | Must be `email`, `whatsapp`, `phone` or `both` |
| `profile_picture` | Text | Image URL | Optional |
| `created_at` / `updated_at` | DateTime | Timestamps | Set automatically |

> 320 characters is the maximum length an email address can be by standard.

### `doctors` (17 columns)

| Field | Type | Purpose | Rules |
| --- | --- | --- | --- |
| `doctor_id` | String(128) | Canonical ID, e.g. `oladoc:3637096` | Primary key |
| `doctor_name` | String(200) | Display name | Required |
| `normalized_name` | String(200) | Lower-case, title-stripped, for comparison | Indexed, **not unique** |
| `specialty` / `normalized_specialty` | String(120) | Field of medicine | — |
| `location`, `clinic` | String | Where they practise | — |
| `provider` | String(40) | Which directory, e.g. `oladoc` | — |
| `provider_doctor_id` | String(64) | The provider's own ID — the strongest identity | Indexed |
| `oladoc_profile_url` | Text | That doctor's own page | — |
| `identity_strength` | String(32) | `provider_doctor_id`, `profile_url` or `name_specialty` | — |
| `booking_metadata` | JSON | Extra provider details | — |

> **Why `normalized_name` is indexed but not unique:** two different doctors
> genuinely can share a name. Making it unique would corrupt real data.

### `appointments` (35 columns)

Grouped by purpose:

| Group | Fields | Why |
| --- | --- | --- |
| Identity | `id`, `user_id`, `doctor_id`, `provider_doctor_id`, `booking_session_id`, `slot_id` | Links the booking to a person and a specific doctor |
| Snapshots | `specialist_name`, `specialist_specialty`, `clinic_name`, `clinic_address`, `clinic_fee` | Copies taken at booking time so old records still read correctly if the doctor's profile later changes |
| When | `appointment_date`, `appointment_time`, `time_label`, `timezone_name` | Date and time as the provider published them |
| Status | `booking_status`, `status`, `email_status` | Booking progress and email outcome, kept separate |
| Provider | `provider_appointment_id`, `confirmation_reference`, `provider_id`, `provider_name` | Only filled if the provider returned them |
| Other | `reason`, `booking_type`, `payment_method`, `cancelled_at`, `extra` | — |

Constraints:

- `booking_status` must be `SELECTED`, `IN_PROGRESS`, `AWAITING_OTP`,
  `CONFIRMED`, `FAILED` or `CANCELLED`.
- `email_status` must be `PENDING`, `SENT`, `FAILED` or `NOT_CONFIGURED`.

> **Why `extra` (JSON) exists:** if code adds a field the schema does not know
> about, it is preserved here rather than silently dropped.

> **Why `booking_status` and `email_status` are separate:** a confirmed
> appointment whose email failed is still confirmed. Merging them would let an
> email problem look like a booking failure.

### `conversations` and `conversation_turns`

| Table | Key fields | Notes |
| --- | --- | --- |
| `conversations` | `id`, `user_id`, `conversation_key` | `(user_id, conversation_key)` is unique — one conversation per key per person |
| `conversation_turns` | `id`, `conversation_id`, `position`, `symptoms`, `emergency`, `recommendation` (JSON), `retrieved_documents` (JSON), `follow_up_questions` (JSON), `question_answers` (JSON) | `position` keeps messages in order |

> **Why two tables?** The old JSON file stored one row per message and repeated
> the conversation ID on every row. That is a one-to-many relationship written
> flat. Splitting it removes the repetition and makes "all messages in this
> conversation, in order" a simple indexed query.

> **Why the four JSON columns were *not* split into tables:** they are always
> read together with their message and are never searched by content. Splitting
> them would add joins and buy nothing — unnecessary normalization.

### `checklists` and `checklist_items`

| Table | Key fields | Notes |
| --- | --- | --- |
| `checklists` | `id`, `user_id`, `appointment_id` (unique), `completed` | One checklist per appointment |
| `checklist_items` | `id` (auto number), `checklist_id`, `item_key`, `label`, `completed`, `position` | `(checklist_id, item_key)` is unique |

> **Why items became rows:** each item is ticked individually, so completion is
> real per-item state — exactly what a row represents.

### Remaining tables

| Table | Key fields | Notes |
| --- | --- | --- |
| `symptom_intakes` | `id`, `user_id`, `conversation_id` (unique), `record` (JSON) | The eight fields, as one object |
| `notifications` | `id`, `user_id`, `appointment_id`, `channel`, `status`, `recipient`, `detail`, `message` | `channel` must be `email`, `whatsapp` or `sms` |
| `medical_information` | `id`, `user_id`, `title`, `category`, `encrypted_details`, `nonce`, `encryption` | Details are **only** stored encrypted |
| `complaints` | `id`, `user_id`, `appointment_id`, `subject`, `details`, `status` | — |
| `payments` | `id`, `user_id`, `purpose`, `amount`, `currency`, `status`, `mode` | `amount >= 0` enforced |
| `redirect_handoffs` | `id`, `user_id`, `specialist_id`, `reference_id`, `url`, `summary` | Audit trail of referral links |

### Indexes and why each exists

| Index | Table | Columns | Why |
| --- | --- | --- | --- |
| `ix_users_email_lower` | users | `email` | Sign-in looks up by email on every login |
| `ix_appointments_user_date` | appointments | `user_id`, `appointment_date`, `appointment_time` | "My appointments, in date order" — the dashboard query |
| `ix_appointments_slot` | appointments | `appointment_date`, `appointment_time` | Slot-conflict checks |
| `ix_appointments_doctor` / `ix_appointments_specialist` | appointments | `doctor_id` / `specialist_id` | Narrow a slot check to one doctor |
| `ix_turns_conversation_position` | conversation_turns | `conversation_id`, `position` | Load a conversation in order |
| `ix_notifications_user_created` | notifications | `user_id`, `created_at` | Newest-first notification list |
| `ix_doctors_provider_doctor_id` | doctors | `provider_doctor_id` | Find a doctor by the provider's ID |
| `ix_doctors_normalized_name` | doctors | `normalized_name` | Detect same-name doctors |

> **Why `ix_appointments_slot` leads with date and time.** It originally led with
> `doctor_id`. Measurement showed a **full table scan**, because a booking may
> identify its doctor through either `doctor_id` *or* `specialist_id`, and a
> database cannot use an index leading with one column when the query says "this
> OR that". Date and time are always present, so leading with them works. After
> the change: `SEARCH appointments USING INDEX ix_appointments_slot`.

---

## Database Relationships

```
User
 ├── has many Appointments        (delete user → appointments deleted)
 │      ├── has one Checklist     (delete appointment → checklist deleted)
 │      │      └── has many ChecklistItems
 │      ├── has many Notifications(delete appointment → link cleared, record kept)
 │      └── has many Complaints   (delete appointment → link cleared, record kept)
 ├── has many Conversations       (delete user → conversations deleted)
 │      ├── has many ConversationTurns
 │      └── has one SymptomIntake
 ├── has many MedicalInformation
 ├── has many Payments
 └── has many RedirectHandoffs

Doctor
 └── referenced by many Appointments  (delete doctor → link cleared, appointment KEPT)
```

### Why the delete rules differ

| Relationship | Rule | Reason |
| --- | --- | --- |
| User → everything | `CASCADE` (delete) | If an account is removed, its data should go with it |
| Appointment → Checklist | `CASCADE` | A checklist has no meaning without its appointment |
| **Doctor → Appointment** | `SET NULL` (keep) | **A patient's appointment history must survive a doctor leaving the directory.** The snapshot fields keep it readable |
| Appointment → Notification | `SET NULL` | The delivery log is an audit trail and should outlive the appointment |

There is a test for the doctor rule: `test_removing_a_doctor_keeps_the_appointment_history`.

---

## Database Migrations

> A **migration** is a recorded change to the database structure. Instead of
> creating tables by hand, each change is a file, so every machine ends up with
> exactly the same structure.

Migrations live in [`python_backend/migrations/versions/`](python_backend/migrations/versions/).

| Revision | File | What it does |
| --- | --- | --- |
| `69ed89c0fecc` | `20260918_0334_initial_schema.py` | Creates all 13 tables |
| `71e2c3d62429` | `20260918_0350_index_slot_lookup_by_date_and_time.py` | Fixes the slot-lookup index |

### Commands

```powershell
$env:PYTHONPATH = (Get-Location).Path

.venv\Scripts\python.exe -m alembic upgrade head        # apply all pending
.venv\Scripts\python.exe -m alembic current             # which revision am I on?
.venv\Scripts\python.exe -m alembic history             # list revisions
.venv\Scripts\python.exe -m alembic downgrade -1        # undo the last one
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
```

**Are they safe to run repeatedly?** Yes. Alembic records which revisions have
run and applies only what is missing. `start-mediguide.bat` runs `upgrade head`
on every launch and refuses to start the server if it fails.

### Two design choices worth knowing

1. **The connection URL is not in `alembic.ini`.**
   [`migrations/env.py`](python_backend/migrations/env.py) imports the app's own
   `database_url()`. Migrations therefore always target the same database as the
   app, and **no credentials sit in a committed file**.

2. **`render_as_batch` is on for SQLite.** SQLite cannot alter most columns in
   place. Batch mode rebuilds the table instead. It is switched off for
   PostgreSQL, which does not need it.

---

## JSON-to-Database Migration

MediGuide previously stored everything in JSON files. Those files have been
imported into the database and removed.

### What was migrated

| Source file | Rows | Became |
| --- | --- | --- |
| `users.json` | 7 | `users` |
| `appointments.json` | 2 | `appointments` |
| `chat-sessions.json` | 76 | `conversations` (34) + `conversation_turns` (76) |
| `notifications-log.json` | 4 | `notifications` |
| `checklists.json` | 0 | `checklists` + `checklist_items` |
| `complaints.json`, `payments.json`, `medical-information.json`, `redirect-handoffs.json`, `doctors.json`, `symptom-intakes.json` | 0 each | their tables |

### How it works

Script: [`migrate_json.py`](python_backend/app/database/migrate_json.py)

```
Validate  →  Import  →  Verify
```

1. **Validate** — checks for missing IDs, duplicate IDs, duplicate emails,
   missing password hashes and references to users that do not exist. If anything
   fails, **nothing is written**.
2. **Import** — preserves original IDs and timestamps. Because rows are matched on
   their original ID, running it twice updates rather than duplicates.
3. **Verify** — compares source counts against database counts, spot-checks
   fields (emails, password hashes, names, dates, symptom text) and looks for
   orphaned rows.

### Transformations that were not one-to-one

- **Chat sessions → two tables.** The file repeated the conversation ID on every
  message. That is a one-to-many relationship stored flat.
- **Checklist items → rows.** They were an array inside each checklist.
- **Legacy status values were mapped.** Old records had only a lower-case
  `status`; `confirmed` became `bookingStatus = CONFIRMED`, and so on.
- **Unknown fields were preserved** into the `extra` JSON column rather than
  dropped.

### One discrepancy, and how it was resolved

Verification first reported **32 expected conversations but 34 created**.

The cause: **three messages had no conversation ID.** The importer gave each its
own conversation; the checker had grouped all three into one.

The importer was right. Three messages with no conversation ID are not
demonstrably the same conversation, and merging them would invent a relationship
that is not in the data. The **checker** was corrected to match the documented
rule, and the result became:

```
conversations (derived): source 34, database 34  ✓  (31 keyed + 3 with no conversationId)
```

### Final verification

```
users 7/7 · appointments 2/2 · turns 76/76 · notifications 4/4 · conversations 34/34
all_counts_match: true · field_problems: [] · orphans: 0 · ok: true
```

### What happened afterwards

The 11 migrated files were **deleted only after** verification passed. The app
was then restarted with them gone: all end-to-end checks passed and **no JSON
file was recreated**, proving there is no hidden fallback.

**Files deliberately kept** (not application data):

| File | Why kept |
| --- | --- |
| `rag-ingestion-manifest.json` | Records which WHO/NHS pages were indexed, so ingestion is repeatable |
| `python-evaluation-report.json` | Generated evaluation output |
| `medical/kaggle-*.json` | Trained model and its report |
| `medical/Training.csv`, `Testing.csv` | Source datasets |

### Running it yourself

```powershell
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json --dry-run  # validate only
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json           # import + verify
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json --verify  # verify only
```

If no JSON files are present it reports zero rows and does nothing.

---

## Backend

### Entry point

[`python_backend/app/main.py`](python_backend/app/main.py) creates the FastAPI
application, and in this order:

1. Configures flow logging.
2. Adds CORS.
3. Registers error handlers ([`errors.py`](python_backend/app/errors.py)).
4. Registers the HTML pages ([`ui.py`](python_backend/app/ui.py)).
5. Adds the security middleware.
6. Defines the JSON API routes.

### How a request travels

```
HTTP request
   ↓
Security middleware   — size limit, rate limit, security headers
   ↓
Route match           — FastAPI picks the handler
   ↓
Pydantic validation   — types and field rules; failure → 422
   ↓
require_user()        — for protected routes; failure → 401
   ↓
Domain logic          — the actual rules
   ↓
db facade → repository → SQLAlchemy → database
   ↓
Response (JSON or HTML)
   ↓
Error handlers        — if anything raised
```

### The security middleware

Defined in [`main.py`](python_backend/app/main.py):

| Check | Value | Why |
| --- | --- | --- |
| Body size limit | 1,000,000 bytes | Stops oversized uploads exhausting memory |
| Rate limit | 120 writes per minute per IP | Slows brute-force and scripted abuse |
| `X-Content-Type-Options` | `nosniff` | Stops the browser guessing file types |
| `X-Frame-Options` | `DENY` | Prevents clickjacking |
| `Referrer-Policy` | `same-origin` | Stops leaking URLs to other sites |
| `Permissions-Policy` | camera/mic/location off | The app never needs them |
| `Content-Security-Policy` | same-origin plus inline styles/scripts | Limits what a page may load |
| `Strict-Transport-Security` | added only over HTTPS | Forces HTTPS once seen |

> **Honest note:** the rate limit is in-process. It resets when the server
> restarts and is not shared between worker processes.

---

## API Reference

All JSON endpoints. Interactive documentation is generated automatically at
`/docs` and `/redoc` while the server runs.

**Authentication:** send `Authorization: Bearer <token>` where marked "Yes".
Get a token from `POST /auth/login`.

**Error shape:** every error returns

```json
{ "error": { "code": "NOT_AUTHENTICATED", "message": "…", "details": [], "reference": "…" } }
```

### Health

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | No | Liveness plus a real database round trip |

```json
{ "status": "ok", "service": "MediGuide Python backend",
  "database": { "status": "ok", "dialect": "sqlite" } }
```

### Accounts

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | No | Create an account |
| `POST` | `/auth/login` | No | Sign in |
| `GET` | `/profile` | Yes | Read your profile |
| `PATCH` | `/profile` | Yes | Update name, phone, city, contact preference, picture |

`POST /auth/register` body: `fullName`, `email`, `password` (≥ 8), optional
`phone`, `city`, `preferredContact`.
Errors: `400` missing/invalid fields, `409` email already registered.

### Symptoms and medical information

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/ai/health-chat` | Yes | Structured intake, then retrieval when complete |
| `GET` | `/ai/health-chat/history` | Yes | Your past conversation turns |

Request: `symptoms` (3–4000 chars), optional `conversationId`, optional
`answers` (`{"severity": "7"}`).

Two possible replies:

```json
{ "status": "needs_more_information",
  "structuredRecord": { … }, "missingFields": ["severity", "age"],
  "followUpQuestions": [{ "field": "severity", "question": "…" }] }
```

```json
{ "status": "complete", "normalizedQuery": "Main symptom: headache\n…",
  "assessment": "…may be consistent with…", "possibleConditions": [ … ],
  "precautions": [ … ], "redFlags": [ … ], "urgent": false,
  "recommendedSpecialty": "Neurology", "sources": [ … ] }
```

Errors: `400 INCOMPLETE_SYMPTOMS`, `503 RAG_UNAVAILABLE`.

### Doctors

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/doctors/search` | No | Live search on oladoc.com |
| `GET` | `/doctors/{doctor_id}/canonical` | Yes | The shared canonical doctor record |
| `GET` | `/specialists` | No | The built-in specialist directory |
| `GET` | `/booking/providers` | No | Which booking providers are configured |
| `GET` | `/booking/oladoc/{specialist_id}` | No | Redirect to a verified directory page |

`POST /doctors/search` body: `symptoms` (3–4000), `city` (default `Lahore`),
`specialty`. Returns `status` of `found`, `clarify` or `not_found`.
`503` if the provider cannot be reached.

### Appointments

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/appointments` | Yes | Your appointments, date order |
| `POST` | `/appointments` | Yes | Book an in-app clinic slot |
| `POST` | `/appointments/confirm` | Yes | Same as above (compatibility) |
| `POST` | `/appointments/playwright` | Yes | Book the exact doctor via the provider |
| `POST` | `/appointments/calcom` | Yes | Book through Cal.com, if configured |
| `POST` | `/appointments/external-confirmed` | Yes | Record a booking made elsewhere |
| `POST` | `/appointments/{id}/cancel` | Yes | Cancel |
| `GET` | `/availability/{specialist_id}?date=YYYY-MM-DD` | No | In-app slots for a built-in specialist |

`POST /appointments/playwright` body: `doctorId`, `date`, `time`, `reason`.

| Status | Meaning |
| --- | --- |
| `404 DOCTOR_ID_MISSING` | No doctor could be resolved from the supplied identity |
| `409 DOCTOR_IDENTITY_MISMATCH` | The provider showed a different doctor — **stopped** |
| `503 DOCTOR_UNAVAILABLE` | Profile unreachable or no bookable slots |

> The backend resolves the doctor **server-side from the ID**. A name sent by the
> client is never used to find a doctor.

### Checklists, complaints, records, notifications, payments

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/checklists/{appointment_id}` | Yes | Checklist for an appointment |
| `PATCH` | `/checklists/{checklist_id}` | Yes | Tick items |
| `GET` / `POST` | `/complaints` | Yes | List / submit a complaint |
| `GET` / `POST` | `/medical-information` | Yes | List / add an encrypted record |
| `GET` | `/notifications` | Yes | Delivery log |
| `GET` | `/notifications/email/status` | No | Whether SMTP is configured |
| `POST` | `/webhooks/whatsapp` | No | Provider delivery status callback |
| `GET` | `/payments` | Yes | Payment history |
| `POST` | `/payments/payment-intent` | Yes | Create a Stripe **test** intent |
| `POST` | `/booking/redirect-link` | Yes | Generate a referral link |
| `GET` | `/booking/redirect-handoffs` | Yes | Referral link audit trail |
| `GET` | `/booking/calcom/status` | No | Whether Cal.com is configured |

---

## Frontend

**There is no separate frontend project.** Pages are HTML strings built in
[`ui.py`](python_backend/app/ui.py) and returned by FastAPI.

| Concern | How it is handled |
| --- | --- |
| Routing | FastAPI routes under `/ui` |
| State | The signed-in user comes from the session cookie on each request. Chat progress is stored in the database |
| Forms | Plain HTML `<form>` posts, parsed with `parse_qs` |
| Navigation | Real links and redirects, so Back and bookmarks work |
| Styling | One CSS design system inside `ui.py` |
| JavaScript | A few dozen lines total: theme toggle, doctor filtering, back button |

**Why no framework:** see [Decision 2](#decision-2--server-rendered-html-instead-of-a-javascript-framework).

**The honest trade-off:** HTML inside Python f-strings is harder to read than
template files. Moving to Jinja2 would improve this without losing anything, and
is the recommended next step if page code becomes painful.

---

## Pages and Screens

All require sign-in unless stated.

| Page | Path | Purpose | Key actions |
| --- | --- | --- | --- |
| Landing | `/ui` (signed out) | Explains the product | Register, sign in |
| Dashboard | `/ui` (signed in) | Overview and appointments | Open chat, find a doctor, cancel |
| Register | `/ui/register` *(public)* | Create an account | Submit |
| Sign in | `/ui/login` *(public)* | Sign in | Submit |
| Sign out | `/ui/logout` | Clears the cookie | — |
| AI health chat | `/ui/chat` | Symptom intake and results | Describe symptoms, answer questions |
| Find a doctor | `/ui/doctors` | Directory and live search | Search, filter, four actions |
| Live slots | `/ui/slots/{doctor_id}` | Real free times | Pick a clinic and date, choose a time |
| Booking form | `/ui/book/{doctor_id}` | Confirm details | Start booking |
| Booking progress | `/ui/booking/{session_id}` | Live status | **Enter the OTP** |
| Confirmation | `/ui/confirmation/{appointment_id}` | Appointment card and checklist | Tick items, download, resend email |
| Appointments | `/ui/appointments` | Built-in specialists and booking | Check schedule, book |
| Profile | `/ui/profile` | Account details | Save |
| Medical information | `/ui/medical-information` | Encrypted records | Add |
| Complaints | `/ui/complaints` | Raise an issue | Submit |
| Notifications | `/ui/notifications` | Delivery log | — |
| Payments | `/ui/payments` | Payment records | — |
| History | `/ui/history` | Saved conversations | — |

### The doctor card

Every doctor — built-in or live — is rendered by the **same** function
(`_doctor_card` in [`ui.py`](python_backend/app/ui.py)) and always shows four
actions:

| Action | What it does |
| --- | --- |
| **Book with Playwright** | Starts booking for that exact doctor |
| **Check Live Slots** | Opens their real availability |
| **View Live Profile** | Opens their profile page in a new tab |
| **Open Oladoc** | Opens their directory listing in a new tab |

> **Why one shared function:** previously live results showed only two actions
> while built-in ones showed four, because the markup was written twice. One
> function makes that impossible.

---

## MediGuide Branding and Icon System

Defined in [`branding.py`](python_backend/app/branding.py).

### The mark

A **vital-signs trace that rises and turns into a navigation arrowhead** — health
plus being guided somewhere. Drawn on a 32×32 grid with 2.6px strokes and no
fine detail, so it stays readable at 16px.

Deliberately avoided: a medical cross, a hospital block, a brain, a stethoscope,
and any emoji. Those are generic and, in the case of emoji, render differently on
every platform.

| Use | Function |
| --- | --- |
| Header | `logo_lockup()` — mark plus "Medi**Guide**" wordmark |
| Favicon / app icon | `favicon_data_uri()` — same glyph as an inline SVG data URI |
| Standalone | `logo_svg(size, tile=False)` — inherits the surrounding text colour |

> **Why an inline data URI rather than an `.ico` file:** it is one vector that
> scales to any size, needs no extra HTTP request, and cannot go missing.

### The icon set

19 icons on a 24×24 grid, 2px round-capped strokes, `currentColor`:

`chat · stethoscope · calendar · clock · bell · user · search · shield · check ·
alert · info · pin · arrow-right · arrow-left · external · document · logout ·
moon · sun`

Usage: `branding.icon("calendar", size=20)` — decorative by default
(`aria-hidden`), or pass `label=` when the icon alone carries meaning.

### The corrupted icons that were fixed

The UI previously contained **27 broken character sequences** such as `Â·`, `â†'`
and `âœ"`. These came from text being written as UTF-8 and read back as Windows
cp1252 — genuine data corruption, not a font problem. Alongside them, Unicode
dingbats (`◈`, `□`, `✦`, `➤`) were being used as interface icons.

Both were fixed at the source: the corrupted sequences were repaired to their
correct characters, and the dingbats were replaced with the SVG set. The page
output now contains zero mojibake, and there is a check for it.

---

## UI Design System

All tokens are CSS custom properties defined once in
[`ui.py`](python_backend/app/ui.py). **Do not write raw colours or pixel values in
page code** — use a token.

| Group | Tokens |
| --- | --- |
| Spacing | `--space-1` (4px) → `--space-8` (64px) |
| Radius | `--radius-sm` 8px, `--radius-md` 12px, `--radius-lg` 18px, `--radius-pill` |
| Shadow | `--shadow-1` (subtle) → `--shadow-3` (raised) |
| Brand | `--brand-600` `#0f7a63`, `--brand-500`, `--brand-400`, `--brand-50` |
| Text | `--ink-900` → `--ink-300` |
| Surface | `--surface`, `--surface-2`, `--canvas`, `--line` |
| Status | `--danger-600`, `--warn-600`, `--ok-600` and their tints |
| Type | `--font-sans` (DM Sans), `--font-display` (Space Grotesk) |

> **Why a green brand colour:** green reads as calm and health-related without
> the alarm of red or the coldness of clinical blue. `#0f7a63` is dark enough to
> pass contrast checks against white for normal text.

### Components

`.panel` · `.card` · `.doctor-card` · `.button` (`.primary`, `.secondary`) ·
`.chip` · `.field` · `.alert` (`--error`, `--warn`, `--ok`) · `.slot-chip` ·
`.summary-row` · `.status-pill` · `.empty-state` · `.spinner` · `.breadcrumb`

### Dark mode

Follows the system setting and can be overridden by a toggle stored in
`localStorage`. Implemented by redefining the same tokens under
`html[data-theme='dark']`, so components need no dark-specific rules.

---

## Accessibility

What has actually been done:

| Item | How |
| --- | --- |
| Semantic HTML | Real `<button>`, `<a>`, `<form>`, `<label>`, `<nav>`, `<main>`. No `<div>` used as a button |
| Labels | Every input has a `<label for=…>` |
| Icon labels | Decorative icons are `aria-hidden`; meaningful ones take a real label |
| Action labels | Each doctor action has an `aria-label` naming the doctor, so "Book with Playwright" is not ambiguous out of context |
| Focus | A visible focus ring (`:focus-visible`) with a 3px brand-coloured halo |
| Keyboard | Everything is a link, button or form control, so tab order works by default |
| Contrast | Text tokens chosen against their surfaces for normal-text contrast |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` disables animation |
| New tabs | External links use `rel="noopener noreferrer"` |

**Not claimed:** no formal WCAG audit, no screen-reader testing, and no
automated accessibility test suite has been run. Treat the above as
"implemented with care", not "certified".

---

## Responsive Design

One layout that adapts, with two breakpoints.

| Width | Behaviour |
| --- | --- |
| Above 900px | Full layout, sticky navigation, multi-column grids |
| 640–900px | Navigation wraps; grids narrow |
| Below 640px | Single column; doctor actions stack full width with 46px targets; detail rows stack |

Techniques used: CSS Grid with `auto-fill`/`minmax` (so grids reflow without
media queries), flex wrapping, relative units, and a minimum 44px touch target
on interactive controls.

---

## Deterministic Modules

> **Deterministic** means: the same input always produces the same output. No
> randomness, no hidden state, no clock, no network. Such code is easy to test
> and easy to reason about.

| Module | Input | Output | Side effects |
| --- | --- | --- | --- |
| [`symptom_intake.py`](python_backend/app/symptom_intake.py) | Free text or field answers | Structured record, missing fields, questions, the exact RAG block | **None** |
| [`doctor_identity.py`](python_backend/app/doctor_identity.py) | Doctor dictionaries | Canonical record, match verdict | **None** |
| [`appointments.py`](python_backend/app/appointments.py) | Doctor, date, time, clinic | Appointment payload, status labels, card rows | Only when explicitly asked to save |
| [`booking_session.py`](python_backend/app/booking_session.py) | State transitions | New state or a refusal | In-memory registry only |
| [`branding.py`](python_backend/app/branding.py) | Icon name, size | SVG string | **None** |
| [`availability.py`](python_backend/app/availability.py) | Specialist, date, time | Available or a reason | Reads the database for conflicts |

Side effects are deliberately pushed to the edges: `repositories.py` (database),
`oladoc_provider.py` / `booking_worker.py` (browser), `email_service.py` (SMTP),
`llm.py` (network).

That separation is what allows the whole booking state machine — including the
OTP pause — to be tested with a fake provider and no network.

---

## Important Business Logic

### Rule 1 — The eight symptom fields

Defined by `FIELD_ORDER` in [`symptom_intake.py`](python_backend/app/symptom_intake.py):

| # | Label | Key | Validation |
| --- | --- | --- | --- |
| 1 | `Main symptom` | `main_symptom` | Non-empty |
| 2 | `Location` | `location` | Non-empty |
| 3 | `When it started` | `when_started` | Non-empty |
| 4 | `Severity from 0–10` | `severity` | Whole number 0–10 |
| 5 | `Other symptoms` | `other_symptoms` | Non-empty; "none" → `none reported` |
| 6 | `Possible trigger` | `possible_trigger` | Non-empty; "none known" → `none identified` |
| 7 | `Age` | `age` | Whole number 0–120 |
| 8 | `Important negatives` | `important_negatives` | Only explicitly denied red flags |

> **Why the label uses an en dash (`–`) and not a hyphen (`-`):** the field labels
> are a fixed contract with the retrieval layer, so the exact characters matter.
> It is written in source as `"Severity from 0–10"` precisely so the
> character survives being saved and loaded on any system. There is a test
> asserting the en dash is present and the hyphen version is not.

**Nothing is invented.** The word "severe" is not converted into a number — the
patient is still asked for 0–10.

### Rule 2 — Only ask for what is missing

`pending_questions()` returns questions only for empty fields. Answers arrive
keyed by field name (`answer_severity`), so each answer is applied to the
question it answers rather than re-parsed from loose text.

### Rule 3 — Only record a denial the patient actually made

If the patient says *"no fever, but I do have vision loss"*, the answer is split
into clauses first, so:

- `fever` → recorded as denied
- `vision loss` → recorded as **present** and moved into Other symptoms

A flag never mentioned stays unestablished. **Silence is not a denial.**

> This was a real bug: the leading "no" originally applied to the whole sentence,
> so vision loss was recorded as denied. Clause splitting fixed it.

### Rule 4 — Denials must not drive retrieval

`retrieval_text()` in [`rag.py`](python_backend/app/rag.py) removes the field
labels **and the entire `Important negatives` line** before scoring.

> **Why this matters enormously.** Without it, "no chest pain" would pull
> retrieval towards the cardiac document and trip the emergency scan — a denial
> would be read as a symptom. The full block is still what the LLM sees; only
> scoring uses the trimmed text.

### Rule 5 — Doctor identity, strongest first

| Order | Signal | Strength |
| --- | --- | --- |
| 1 | `provider_doctor_id` | Definitive |
| 2 | `oladoc_profile_url` | Strong |
| 3 | Normalized name **and** specialty | Weak, last resort |
| — | Name alone | **Never accepted** |

- A definitive ID that disagrees is a mismatch **even if the names match**.
- Matching names with no distinguishing specialty is rejected.
- A doctor with no verifiable identity is **dropped from results**, not guessed.

### Rule 6 — Never claim a confirmation that did not happen

`bookingStatus` starts `SELECTED` and becomes `CONFIRMED` only when the provider
says so. The card shows "Selected" or "Confirmed" accordingly, and a reference
number appears only if the provider returned one.

### Rule 7 — Clinic hours for built-in specialists

Monday–Saturday, 09:00–17:00, 30-minute slots, within 60 days. This applies only
to the **built-in** directory; live Oladoc doctors use the provider's real hours.

---

## Data Flows

### Symptoms to medical information

```
Patient types text
   ↓ apply_free_text()          extract what is present
   ↓ pending_questions()        ask only for what is missing
   ↓ apply_answer()             attribute each answer to its field
   ↓ validate()                 severity 0–10, age 0–120
   ↓ format_rag_query()         the exact eight-line block
   ↓ retrieval_text()           strip labels and denials, for scoring only
   ↓ ChromaDB similarity search
   ↓ retrieve_evidence()        keep only WHO/NHS chunks, with source URLs
   ↓ assess_structured()        possible patterns, precautions, red flags, specialty
   ↓ llm.contextual_summary()   optional phrasing (skipped if unavailable)
   ↓ HTML page or JSON response
```

### Doctor to booked appointment

```
Live search → canonical doctor (with the provider's ID)
   ↓ saved to the `doctors` table
   ↓ doctor card carries data-doctor-id
   ↓ "Check Live Slots" → provider booking page → real times
   ↓ patient picks a time
   ↓ booking session created (doctor, date and slot are then IMMUTABLE)
   ↓ appointment row created with bookingStatus = IN_PROGRESS
   ↓ worker thread opens that doctor's own booking page
   ↓ GATE 1: verify the doctor → mismatch stops everything
   ↓ select the slot, fill patient details
   ↓ if the provider asks: OTP_REQUIRED → patient types the code → passed on, discarded
   ↓ GATE 2: verify the doctor again, immediately before confirming
   ↓ provider confirms → bookingStatus = CONFIRMED + reference
   ↓ confirmation email (its own status field)
```

---

## Authentication

| Step | What happens |
| --- | --- |
| Register | Password hashed with scrypt (`n=16384, r=8, p=1`, random 16-byte salt), stored as `salt:hash` |
| Sign in | Hash recomputed and compared with `hmac.compare_digest` (constant time) |
| Session | A JWT signed HS256 with the application secret, valid **7 days** |
| Storage | `httponly`, `samesite=lax` cookie; `secure` added automatically over HTTPS |
| API | `Authorization: Bearer <token>` |
| Sign out | Cookie deleted |

**Not implemented:** password reset, email verification, two-factor
authentication, "remember me" (the checkbox on the sign-in page is not wired to
anything), account deletion.

> **Why `samesite=lax` and not `strict`:** `strict` would drop the cookie when
> arriving from an external link — including a link in a confirmation email —
> logging the patient out for no reason.

---

## Authorization

There is one role, so authorization is **ownership checking**, and it happens on
the server.

| Protection | How |
| --- | --- |
| API routes | `require_user()` raises `401` without a valid token |
| Pages | `_user(request)` returns `None` → redirect to `/ui/login` |
| Row ownership | Queries require both the record ID **and** the user ID |
| Booking sessions | Bound to one account; another account gets `None` |
| Doctor identity | Resolved server-side from the ID; a client-supplied name is never trusted |

**Hiding a button is not security.** Every restriction above is enforced in the
backend. The UI hides links only to avoid showing dead ends.

---

## Validation

Three layers, each with a different job:

| Layer | Where | Example |
| --- | --- | --- |
| Browser | HTML attributes | `required`, `type="email"`, `minlength="8"` — convenience only |
| Request | Pydantic models in [`main.py`](python_backend/app/main.py) | `symptoms: str = Field(min_length=3, max_length=4000)` |
| Domain | Validation functions | Severity 0–10, age 0–120, date not in the past, confirmation required |
| Database | Constraints | `UNIQUE` email, `CHECK` on statuses, `amount >= 0`, foreign keys |

> **Why the database repeats checks the code already makes:** code can be
> bypassed by a bug or a script; a constraint cannot. It is the last line.

---

## Error Handling

All handled centrally in [`errors.py`](python_backend/app/errors.py).

| Situation | Status | Code | Shown to the user |
| --- | --- | --- | --- |
| Invalid request body | 422 | `VALIDATION_FAILED` | Which fields, and why |
| Not signed in | 401 | `NOT_AUTHENTICATED` | "Please sign in" |
| Not found | 404 | `NOT_FOUND` | A friendly page or JSON |
| Constraint violated | 409 | `CONFLICT` | "That conflicts with existing information" |
| Database unreachable | 503 | `DATABASE_UNAVAILABLE` | "Try again shortly" |
| Anything unexpected | 500 | `INTERNAL_ERROR` | "Something went wrong" + a reference code |

**The rule:** technical detail is *logged*; the user gets a safe message plus a
reference code that ties their report to the log entry. Stack traces, SQL and
file paths are never returned.

Domain-specific codes: `DOCTOR_IDENTITY_MISMATCH`, `DOCTOR_ID_MISSING`,
`DOCTOR_NOT_FOUND`, `DOCTOR_UNAVAILABLE`, `NO_SLOTS_PUBLISHED`,
`PROVIDER_UNAVAILABLE`, `SUBMIT_DISABLED`, `OTP_TIMEOUT`, `OTP_REJECTED`,
`SLOT_NO_LONGER_AVAILABLE`, `RAG_UNAVAILABLE`, `EMAIL_FAILED`.

---

## Loading, Empty and Error States

| Situation | What the patient sees |
| --- | --- |
| Booking in progress | Spinner, "Connecting to the booking provider…", page refreshes itself |
| Waiting for OTP | An input box and a note that MediGuide never reads your messages |
| No doctors found | Suggestion to try another specialty or location |
| No slots that day | `NO_SLOTS_PUBLISHED` plus the other dates that clinic does offer |
| Provider unreachable | `PROVIDER_UNAVAILABLE` and an explicit note that **no example times were substituted** |
| Knowledge base down | `RAG_UNAVAILABLE` — and **no medical text is invented** |
| Email failed | The appointment stays confirmed; a retry button is offered |
| No records yet | A dashed empty-state panel explaining what will appear |

---

## RAG and the Medical Knowledge Base

> **RAG** stands for *retrieval-augmented generation*: look up real documents
> first, then answer using them, instead of answering from memory.

```
WHO / NHS pages  →  fetch  →  extract text  →  clean  →  split into chunks
      →  embed (MiniLM)  →  ChromaDB
Patient's structured block  →  embed  →  similarity search  →  closest chunks
      →  shown with sources (and given to the LLM, if available)
```

### What is indexed

35 allowlisted pages — WHO fact sheets and NHS Health A–Z pages — producing
**347 chunks** (WHO 172 / NHS 175 at last run), each about 1,100 characters with
150 characters of overlap so sentences are not cut in half.

The ChromaDB collection holds **359 chunks** in total: those 347 plus 12 short
in-repo topic notes that are indexed separately and act as the fallback when no
WHO/NHS document matches. Only WHO and NHS chunks are ever cited as sources.

**This is an allowlist, not a crawler.** Only the hosts `who.int` and `nhs.uk`
are ever fetched, and links are never followed.

### Idempotent ingestion

Chunk IDs come from the source URL plus the chunk position, and a manifest stores
a hash of each page. Re-running skips unchanged pages:

```
Run 1: ingested 35 documents, 347 chunks written
Run 2: ingested 0, skipped 35 unchanged, collection unchanged
```

### Verified retrieval

```
"burning feeling when passing urine"     → NHS: Urinary tract infections  (0.399)
"throbbing headache…sensitivity to light"→ NHS: Migraine (0.358), WHO: Headache disorders (0.460)
```

(Lower distance = closer match.)

### Commands

```powershell
.venv\Scripts\python.exe -m python_backend.app.ingestion            # fetch and index
.venv\Scripts\python.exe -m python_backend.app.ingestion --verify   # counts + live test queries
.venv\Scripts\python.exe -m python_backend.app.ingestion --force    # re-index everything
```

### The LLM layer

[`llm.py`](python_backend/app/llm.py) sends the structured block **plus the
retrieved chunks** to an OpenAI-compatible endpoint. The system prompt requires
it to use only the supplied context and to say when evidence is insufficient.

**Current state here:** the configured key returns `429 — no credits remaining`,
so `llmStatus` reports `unavailable` and `contextualResponse` is `null`. The
retrieved evidence and curated precautions are shown regardless. **No text is
invented to fill the gap.**

---

## Live Doctor Search and Booking

### How oladoc.com is read

| Page | URL shape | What is read |
| --- | --- | --- |
| Listing | `/pakistan/{city}/{specialty}` | Doctor profile links |
| Profile | `/pakistan/{city}/dr/{specialty}/{slug}/{providerId}` | Clinics and booking links |
| Booking | `/appointment/{clinicId}/{providerId}` | Date chips and free times |

The profile URL carries the city, the specialty **and the provider's own doctor
ID** — which is why it is the basis of identity.

### Selectors, and why they are safe ones

| Element | Selector | Why it is stable |
| --- | --- | --- |
| Date chips | `.slot-date` | The ISO date is in the element's class, e.g. `slot-date 2026-09-19` |
| Free times | `.timing` | One element per bookable time |
| Doctor name | `h1` / heading role | Semantic |

No `nth-child`, no coordinates, no fixed pixel positions. Waits are on
conditions (`wait_for_selector`, `wait_for_function`), not fixed sleeps.

### Search normalization

`urologist` · `Urologist` · `  UROLOGY  ` · `urology` all resolve to `urologist`.
Everyday phrasing is mapped too: `heart specialist` → `cardiologist`,
`skin doctor` → `dermatologist`, `kidney specialist` → `nephrologist`,
`ear nose throat` → `ent-specialist`.

Text that is clearly a person's name (`Dr Ahmed`) is **not** treated as a
specialty; it becomes a name filter instead.

### The safety gate

`OLADOC_SUBMIT_MODE` defaults to `disabled`. The flow runs all the way to the
provider's confirm button and then stops with `SUBMIT_DISABLED`.

**Why this default exists:** the alternative is software that creates real
appointments with real doctors during testing. Set `OLADOC_SUBMIT_MODE=live`
only when you intend real bookings.

Verified against the live site with submission disabled:

```
CREATED → DOCTOR_VERIFIED → SLOT_SELECTED → PATIENT_DETAILS_FILLED
        → BOOKING_SUBMITTED → FAILED (SUBMIT_DISABLED)
```

### Browser lifecycle

All browsers are created by one context manager, `browser_page()` in
[`oladoc_provider.py`](python_backend/app/oladoc_provider.py), which always
closes the page, context and browser — including on error. Verified: **zero
leaked browser processes** after full test runs.

---

## Performance

Every item below was measured, not assumed.

### Query counts (60 appointments, 60 conversation turns)

| Operation | Queries | Note |
| --- | --- | --- |
| List 60 appointments | **1** | No N+1 |
| Find one appointment | **1** | Indexed |
| Slot conflict check | **1** | Indexed |
| List 60 conversation turns | **1** | Single join |
| Checklist + 8 items | **2** | `selectinload` — the correct two-query pattern |
| Find user by email | **1** | Indexed |

> **N+1** means loading a list, then running one extra query per item. Twenty
> doctors would become twenty-one queries. Avoided by joining or by
> `selectinload`.

### The index fix

```
before: SCAN appointments
after:  SEARCH appointments USING INDEX ix_appointments_slot (appointment_date=? AND appointment_time=?)
```

Cause and reasoning are in [Database Schema](#database-schema).

### Other optimisations

| Optimisation | Where | Why |
| --- | --- | --- |
| Connection pooling | `engine.py` | Reuses connections instead of reconnecting |
| `pool_pre_ping` | `engine.py` | Detects connections a server closed |
| WAL journal | `engine.py` | Readers do not block on a writer |
| Doctor search cache (180s) | `oladoc_provider.py` | A live search takes 10–20s |
| Scroll until stable | `oladoc_provider.py` | Stops scrolling when the list stops growing |
| Short fill timeouts (4s) | `booking_worker.py` | Fixed a real stall — see below |
| Lazy embedding model | `chroma_store.py` | The 80 MB model loads on first use only |
| Optional ChromaDB import | `chroma_store.py` | A missing package degrades instead of breaking start-up |

> **The stall that measurement found:** filling patient details originally used
> the default 30-second timeout per selector. Real provider pages contain many
> hidden inputs matching names like `input[name*='name']`, so each attempt waited
> the full 30s and booking never progressed. Restricting to `:visible` elements
> with a 4-second timeout fixed it.

---

## Caching

Only two things are cached, both deliberately:

| What | Where | Lifetime | Invalidation |
| --- | --- | --- | --- |
| Doctor search results | In memory, `oladoc_provider.py` | 180 seconds | Expires by time |
| The embedding model | In memory | Process lifetime | Restart |

**Live appointment slots are never cached.** They change minute to minute; a
cached slot would be a slot that might no longer exist — exactly the kind of
false "live" data the project refuses to show.

There is no Redis and no external cache. Nothing here justifies one.

---

## Security

### Implemented

| Area | Measure |
| --- | --- |
| Passwords | scrypt, per-user salt, constant-time comparison |
| Sessions | Signed JWT, `httponly` + `samesite=lax` cookie, `secure` over HTTPS |
| Application secret | No hardcoded fallback; placeholders and short values rejected |
| Medical records | AES-256-GCM (authenticated encryption) |
| SQL injection | All queries are SQLAlchemy expressions, never string concatenation |
| XSS | 183 `escape()` call sites; no unescaped interpolation into HTML |
| Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` |
| MIME sniffing | `X-Content-Type-Options: nosniff` |
| Content loading | A Content-Security-Policy |
| Abuse | 120 writes/minute/IP, 1 MB body limit |
| Data isolation | Every query filtered by the owning user |
| Doctor identity | Resolved server-side; client-supplied names never trusted |
| Error leakage | Internal details logged, never returned |
| Secrets | Read from the environment; `.env` is git-ignored |

### The vulnerability that was found and fixed

`MEDIGUIDE_SECRET` was **not set**, so the application fell back to a hardcoded
string written in the source code. That value signed every session token *and*
derived the encryption key for medical records — so anyone who knew the default
(it was in the repository) could forge a valid session.

Fixed in [`secrets_manager.py`](python_backend/app/secrets_manager.py): there is
no hardcoded fallback, known placeholders are rejected, values under 32
characters are rejected, and local development uses a generated git-ignored key.

### Not implemented

CSRF tokens (mitigated but not eliminated by `samesite=lax`), account lockout,
password-strength rules beyond an 8-character minimum, audit logging of reads,
dependency vulnerability scanning, and multi-process rate limiting.

**No compliance claim is made.** This project has not been assessed against
HIPAA, GDPR or any other framework.

---

## Privacy and Sensitive Data

| Data | Where | Protection |
| --- | --- | --- |
| Password | `users.password_hash` | scrypt; the original is never stored |
| Email, phone, city | `users` | Plain text; needed to contact the patient |
| Medical records | `medical_information` | **AES-256-GCM encrypted**; decrypted only for the owner |
| Symptom conversations | `conversation_turns` | Plain text — needed to continue a conversation |
| Appointments | `appointments` | Plain text |
| Verification codes (OTP) | **Nowhere** | Passed to the provider and discarded |

### What is sent outside this machine

| Destination | What is sent | When |
| --- | --- | --- |
| oladoc.com | Name, email, phone; the chosen date and time | Only during a booking you start |
| SMTP server | The confirmation email | Only after a confirmed booking |
| LLM endpoint | The structured symptom block plus retrieved chunks | Only if a key is configured |
| WHO / NHS | Nothing about you — plain page fetches | Only during ingestion |

### What is never logged

Passwords, tokens, API keys, OTP codes, email bodies, and the free text of
medical records. [`flow_log.py`](python_backend/app/flow_log.py) redacts any
field whose name suggests a credential, an email or a phone number, and logs only
identifiers, counts and outcomes.

Example of a real log line — note it records *which fields were present*, never
their content:

```
SYMPTOM_NORMALIZED {"fields_present": ["main_symptom", "location", …], "severity": 7}
```

---

## External Services

| Service | Purpose | Variables | If it fails |
| --- | --- | --- | --- |
| **oladoc.com** | Live doctors, clinics, slots, booking | none required | `PROVIDER_UNAVAILABLE`; no substitute data shown |
| **SMTP** | Confirmation email | `SMTP_*` | Appointment stays confirmed; `emailStatus=FAILED`; retry offered |
| **WHO / NHS** | Knowledge base source | none | Ingestion reports failures; existing index keeps working |
| **OpenAI-compatible LLM** | Optional phrasing | `LLM_*` / `OPENAI_API_KEY` | `llmStatus=unavailable`; evidence still shown |
| **Stripe** | Payments, test mode only | `STRIPE_SECRET_KEY` | Refuses any key not starting `sk_test_` |
| **Cal.com** | Optional scheduling | `CAL_*` | `503` with a clear message |
| **WhatsApp Cloud API** | Optional notifications | `WHATSAPP_*` | Logged as `setup-required` |

---

## Dependencies

Only the important ones, with the reason each is present.

| Package | Why it is here | Could it be removed? |
| --- | --- | --- |
| `fastapi` | The web framework | No |
| `uvicorn` | Runs the ASGI app | No |
| `SQLAlchemy` | Database access and portability | No |
| `alembic` | Migrations | Only by giving up version-controlled schema changes |
| `PyJWT` | Session tokens | Could be hand-rolled, but signing your own JWTs is a classic source of bugs |
| `cryptography` | AES-256-GCM | No — encryption must not be hand-rolled |
| `playwright` | Reads a JavaScript-rendered site | No — there is no API |
| `chromadb` | Vector search plus a local embedding model | Yes, with degraded (word-only) search |
| `stripe` | Test-mode payments | Yes, if payments are dropped |

**Removed during cleanup:** `python-multipart` — FastAPI needs it only for
`Form(...)`/file uploads, and this app parses forms manually with `parse_qs`.
Verified unused before removal.

---

## Commands

| Command | Purpose |
| --- | --- |
| `py -m venv .venv` | Create the virtual environment |
| `.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt` | Install packages |
| `.venv\Scripts\python.exe -m playwright install chromium` | Install the browser |
| `.venv\Scripts\python.exe -m alembic upgrade head` | Apply migrations |
| `.venv\Scripts\python.exe -m alembic current` | Show the current revision |
| `.venv\Scripts\python.exe -m alembic downgrade -1` | Undo the last migration |
| `.venv\Scripts\python.exe -m alembic revision --autogenerate -m "msg"` | Create a migration |
| `.venv\Scripts\python.exe -m python_backend.app.database.migrate_json` | Import legacy JSON |
| `.venv\Scripts\python.exe -m python_backend.app.ingestion` | Index WHO/NHS pages |
| `.venv\Scripts\python.exe -m python_backend.app.ingestion --verify` | Check the index |
| `.venv\Scripts\python.exe -m pytest python_backend\tests -q` | Run all tests |
| `.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011` | Start the server |
| `start-mediguide.bat` | Migrate, start, open the browser |
| `.venv\Scripts\python.exe -m python_backend.app.evaluation` | Write the evaluation report |
| `.venv\Scripts\python.exe -m python_backend.app.agents.kaggle_training` | Train the disease model |

> **There is no `npm`, no build step and no bundler.** Python runs the source
> directly. "Building" this project means installing packages and running
> migrations.

---

## Development Workflow

```
1. Activate the environment       .venv\Scripts\activate
2. Set PYTHONPATH                 $env:PYTHONPATH = (Get-Location).Path
3. Apply migrations               python -m alembic upgrade head
4. Start with auto-reload         python -m uvicorn python_backend.app.main:app --reload --port 8011
5. Make your change
6. Run the tests                  python -m pytest python_backend\tests -q
7. If you changed models          python -m alembic revision --autogenerate -m "…"
                                  …read the generated file before applying it…
                                  python -m alembic upgrade head
8. Check the page in a browser
9. Update this README if behaviour changed
```

---

## Testing

**117 tests**, all passing.

| File | Tests | Covers |
| --- | --- | --- |
| [`test_doctor_identity_flow.py`](python_backend/tests/test_doctor_identity_flow.py) | 29 | Symptom intake, exact RAG format, doctor identity, mismatch refusal, duplicate names |
| [`test_database.py`](python_backend/tests/test_database.py) | 27 | Schema, CRUD, constraints, cascades, transactions, concurrency, indexes |
| [`test_live_provider_and_booking.py`](python_backend/tests/test_live_provider_and_booking.py) | 21 | Query normalization, provider URL parsing, sessions, email |
| [`test_booking_flow.py`](python_backend/tests/test_booking_flow.py) | 13 | Full booking including the OTP pause, with a stub provider |
| [`test_ui_booking_cards.py`](python_backend/tests/test_ui_booking_cards.py) | 5 | Doctor cards expose all four actions |
| [`test_chroma_rag.py`](python_backend/tests/test_chroma_rag.py) | 3 | Vector store and similarity search |
| [`test_playwright_booking.py`](python_backend/tests/test_playwright_booking.py) | 2 | Selector helpers; OTP fields are never filled |

```powershell
.venv\Scripts\python.exe -m pytest python_backend\tests -q                    # everything
.venv\Scripts\python.exe -m pytest python_backend\tests\test_database.py -q   # one file
.venv\Scripts\python.exe -m pytest python_backend\tests -q -k "identity"      # by name
.venv\Scripts\python.exe -m pytest python_backend\tests -v                    # verbose
```

### Database testing

[`python_backend/tests/conftest.py`](python_backend/tests/conftest.py) gives
**every test its own SQLite file** in a temporary directory, created from the
models and thrown away afterwards.

> **Why a fresh database per test rather than a shared one with cleanup:** total
> isolation. Tests cannot see each other's rows, order never matters, and a
> failing test cannot poison the next one. It is also fast at this size.

### No network required

Tests never contact oladoc.com. Provider behaviour is exercised through a stub
driver, and the page-shape logic is tested against the structures observed on the
real site. The suite runs offline in about 35 seconds.

### What is not covered

No browser-based UI tests (no Selenium/Playwright test runner against our own
pages), no load testing, and no test of the real OTP challenge — see
[Known Limitations](#known-limitations).

---

## Logging

Two systems:

**1. Standard Python logging** — used by modules for warnings and errors.

**2. Flow events** ([`flow_log.py`](python_backend/app/flow_log.py)) — one line
per important transition, as JSON:

```
SYMPTOM_NORMALIZED · RAG_QUERY_CREATED · RAG_EVIDENCE_RETRIEVED ·
DOCTOR_SEARCH_COMPLETED · DOCTOR_SELECTED · APPOINTMENT_DOCTOR_SET ·
LIVE_SLOTS_EXTRACTED · BOOKING_SESSION_CREATED · BOOKING_SESSION_STATE ·
OLADOC_DOCTOR_RESOLVED · OLADOC_DOCTOR_VERIFIED · OTP_REQUIRED · OTP_ACCEPTED ·
BOOKING_STARTED · BOOKING_COMPLETED · DOCTOR_IDENTITY_MISMATCH ·
BOOKING_FAILED · RAG_FAILED · CONFIRMATION_EMAIL_SENT · CONFIRMATION_EMAIL_FAILED
```

Set the detail level with `MEDIGUIDE_FLOW_LOG_LEVEL` (default `INFO`; use
`WARNING` for failures only).

> **Why a separate logger:** Uvicorn leaves the root logger at `WARNING`, so
> `INFO` events would silently vanish. `flow_log.configure()` attaches its own
> handler so these events are always visible.

**Health check:** `GET /` performs a real database query, so it fails if the
database is unreachable rather than reporting a false "ok".

---

## Build, Deployment, Docker and CI

**There is no build step.** Python runs the source directly.

**Deployment configuration is not currently defined in the repository.** There is
no deployment script, no platform configuration and no production server setup.

**Docker is not used in this repository.** There is no `Dockerfile` and no
`docker-compose.yml`.

**CI/CD is not configured.** There is no `.github/workflows` directory or
equivalent.

If you deploy this, you would at minimum need to: set `MEDIGUIDE_SECRET`, point
`DATABASE_URL` at a real database, run `alembic upgrade head`, serve behind HTTPS
(so the `secure` cookie flag and HSTS activate), set `ALLOWED_ORIGIN` to your
domain instead of `*`, and decide deliberately about `OLADOC_SUBMIT_MODE`.

---

## Troubleshooting

### The application will not start

**Problem:** `ModuleNotFoundError: No module named 'chromadb'`
**Cause:** packages not installed, or an old pin that has no wheel for your Python.
**Solution:** `pip install -r python_backend\requirements.txt`. The requirements
pin `chromadb==1.5.9` specifically because `0.5.23` has no wheel for Python 3.13+.
The import is optional now, so this should degrade rather than crash.

**Problem:** `ModuleNotFoundError: No module named 'python_backend'`
**Cause:** `PYTHONPATH` is not set.
**Solution:** `$env:PYTHONPATH = (Get-Location).Path` from the project root.

**Problem:** `MEDIGUIDE_SECRET is set to a known placeholder value`
**Cause:** the value is a placeholder from `.env.example`.
**Solution:** generate a real one:
`python -c "import secrets; print(secrets.token_urlsafe(48))"`

### Database problems

**Problem:** `no such table: users`
**Cause:** migrations were never applied.
**Solution:** `python -m alembic upgrade head`

**Problem:** `database is locked`
**Cause:** another process holds the SQLite file.
**Solution:** stop other instances. WAL and a 30-second busy timeout normally
prevent this.

**Problem:** Saved medical records become unreadable.
**Cause:** `MEDIGUIDE_SECRET` changed — it derives the encryption key.
**Solution:** restore the previous secret. There is no recovery without it.

### Doctor search and booking

**Problem:** Search returns nothing, or `PROVIDER_TIMEOUT`.
**Cause:** no browser installed, no internet, or the provider's page changed.
**Solution:** `python -m playwright install chromium`; check connectivity. If the
site changed, the selectors in
[`oladoc_provider.py`](python_backend/app/oladoc_provider.py) need updating.

**Problem:** `NO_SLOTS_PUBLISHED` for every date.
**Cause:** usually correct — the clinic genuinely has no availability.
**Solution:** try the other dates listed, or another clinic. Compare against the
`sourceUrl` shown on the page.

**Problem:** Booking always ends `SUBMIT_DISABLED`.
**Cause:** the safety gate is on by default.
**Solution:** this is intended. Set `OLADOC_SUBMIT_MODE=live` only if you intend
to create real appointments.

**Problem:** `DOCTOR_IDENTITY_MISMATCH`.
**Cause:** the provider showed a different doctor — often a stale profile URL.
**Solution:** search again to refresh the record. If two doctors share a name,
this is the system working correctly. Check `provider_doctor_id` in the reason.

### Other

**Problem:** Port 8011 already in use.
**Solution:** `netstat -ano | findstr :8011` then `taskkill /PID <pid> /F`, or use
another port.

**Problem:** Email never arrives.
**Cause:** SMTP not configured, or the provider requires an app password.
**Solution:** check `GET /notifications/email/status`. Gmail requires an app
password, not your normal one.

**Problem:** `llmStatus: unavailable`.
**Cause:** no key, no credit, or no network.
**Solution:** optional — retrieval and precautions still work without it.

**Problem:** Tests fail with import errors.
**Solution:** run from the project root, where [`conftest.py`](conftest.py) sets
the import path.

---

## FAQ

**What is MediGuide?**
A healthcare navigation app: describe symptoms, get cautious information from WHO
and NHS sources, and book a real appointment with a specific real doctor.

**Does it diagnose illness?**
No. It shows *possible patterns* from retrieved documents and always says a
clinician confirms diagnoses.

**How do I run it?**
Double-click `start-mediguide.bat`, or see [Quick Start](#quick-start).

**Where is the database?**
`python_backend/data/mediguide.db` by default. Set `DATABASE_URL` for PostgreSQL.

**Why SQLite and not PostgreSQL?**
See [Decision 3](#decision-3--sqlalchemy--alembic-sqlite-by-default-postgresql-supported).
Short version: no database server is installed on the target machine, and
PostgreSQL is supported through one setting.

**How do I create a migration?**
Edit [`models.py`](python_backend/app/database/models.py), then
`alembic revision --autogenerate -m "…"`, read the generated file, then
`alembic upgrade head`.

**Where are the API routes?**
JSON API in [`main.py`](python_backend/app/main.py); pages in
[`ui.py`](python_backend/app/ui.py). Live list at `/docs`.

**Where is authentication handled?**
[`auth.py`](python_backend/app/auth.py); enforcement in `require_user()`
(`main.py`) and `_user()` (`ui.py`).

**Where are the pages?**
All in [`ui.py`](python_backend/app/ui.py).

**How do I run the tests?**
`.venv\Scripts\python.exe -m pytest python_backend\tests -q`

**How do I build it?**
You do not. There is no build step.

**Are the doctors real?**
Yes — read live from oladoc.com. Times shown are the provider's real published
times.

**Will it actually book an appointment?**
Not by default. `OLADOC_SUBMIT_MODE=disabled` stops before the provider's confirm
button.

**Is my medical information safe?**
Records in `medical_information` are AES-256-GCM encrypted. Symptom conversations
are **not** encrypted. See [Privacy](#privacy-and-sensitive-data).

**Why does it keep asking me questions?**
It asks only for missing fields. If a question repeats, the answer was probably
not understood — answer it more directly (for severity, just a number).

---

## Important Files

| File | Purpose |
| --- | --- |
| [`README.md`](README.md) | This handbook |
| [`start-mediguide.bat`](start-mediguide.bat) | Launcher: migrations + server + browser |
| [`python_backend/requirements.txt`](python_backend/requirements.txt) | Packages and reasons |
| [`.env.example`](.env.example) | Every setting, documented |
| [`alembic.ini`](alembic.ini) | Migration configuration |
| [`conftest.py`](conftest.py) | Import path for tests |
| [`python_backend/app/main.py`](python_backend/app/main.py) | API routes, middleware, start-up |
| [`python_backend/app/ui.py`](python_backend/app/ui.py) | All pages and the design system |
| [`python_backend/app/database/models.py`](python_backend/app/database/models.py) | The schema |
| [`python_backend/app/database/repositories.py`](python_backend/app/database/repositories.py) | All SQL |
| [`python_backend/app/database/engine.py`](python_backend/app/database/engine.py) | Connection and pooling |
| [`python_backend/app/symptom_intake.py`](python_backend/app/symptom_intake.py) | The eight symptom fields |
| [`python_backend/app/doctor_identity.py`](python_backend/app/doctor_identity.py) | Doctor identity rules |
| [`python_backend/app/oladoc_provider.py`](python_backend/app/oladoc_provider.py) | Live search and slots |
| [`python_backend/app/booking_worker.py`](python_backend/app/booking_worker.py) | Booking and OTP |
| [`python_backend/app/secrets_manager.py`](python_backend/app/secrets_manager.py) | Secret resolution |
| [`python_backend/app/errors.py`](python_backend/app/errors.py) | Central error handling |
| [`python_backend/app/branding.py`](python_backend/app/branding.py) | Logo, favicon, icons |
| [`DOCTOR_IDENTITY_AND_INTAKE.md`](DOCTOR_IDENTITY_AND_INTAKE.md) | Deep dive on intake and identity |

---

## Where to Make Changes

| I need to… | Go to |
| --- | --- |
| Change how a page looks | [`ui.py`](python_backend/app/ui.py) — find the route, edit the HTML |
| Change colours, spacing, buttons | The design-system CSS block in [`ui.py`](python_backend/app/ui.py) |
| Change the logo or an icon | [`branding.py`](python_backend/app/branding.py) |
| Add or change a JSON API route | [`main.py`](python_backend/app/main.py) |
| Add a database table or column | [`models.py`](python_backend/app/database/models.py), then create a migration |
| Write or change a query | [`repositories.py`](python_backend/app/database/repositories.py) — **the only place for SQL** |
| Change the symptom questions | [`symptom_intake.py`](python_backend/app/symptom_intake.py) |
| Change red flags | `RED_FLAG_SETS` in [`symptom_intake.py`](python_backend/app/symptom_intake.py) |
| Change how doctors are matched | [`doctor_identity.py`](python_backend/app/doctor_identity.py) |
| Fix a broken provider selector | [`oladoc_provider.py`](python_backend/app/oladoc_provider.py) |
| Change the booking steps or OTP | [`booking_worker.py`](python_backend/app/booking_worker.py), [`booking_session.py`](python_backend/app/booking_session.py) |
| Change the confirmation email | [`email_service.py`](python_backend/app/email_service.py) |
| Add a WHO/NHS document | `SOURCES` in [`ingestion.py`](python_backend/app/ingestion.py), then re-run ingestion |
| Change authentication | [`auth.py`](python_backend/app/auth.py) |
| Change an error message or code | [`errors.py`](python_backend/app/errors.py) |
| Add a setting | [`config.py`](python_backend/app/config.py) **and** [`.env.example`](.env.example) |
| Add a test | [`python_backend/tests/`](python_backend/tests/) |

---

## Adding a New Feature

```
1. Decide what it does and who uses it
2. If it stores data:
      a. add or edit the model in database/models.py
      b. alembic revision --autogenerate -m "describe it"
      c. READ the generated migration before applying it
      d. alembic upgrade head
3. Add the queries to database/repositories.py   (SQL goes nowhere else)
4. Expose them on the db facade if existing code will call them
5. Put the rules in a domain module — keep it pure where you can
6. Add the API route in main.py, with a Pydantic model for the body
7. Add the page in ui.py, using existing design-system classes
8. Handle the states: loading, empty, error
9. Write tests: the rules, the queries, and the refusals
10. Run the full suite, then check the page in a browser
11. Update this README
```

---

## Changing the Database Safely

```
1. Edit database/models.py
2. alembic revision --autogenerate -m "what changed"
3. OPEN THE GENERATED FILE AND READ IT
      Autogenerate is a helpful draft, not an authority.
      It can miss renames (it sees a drop plus an add, which loses data).
4. alembic upgrade head
5. Check existing rows still look right
6. Update repositories.py and any mapping functions
7. Run the tests
```

**Rules**

- Never edit a migration that has already run somewhere else — add a new one.
- Never hand-edit the database to match the code; the schema is the migrations.
- Adding a `NOT NULL` column to a table with rows needs a default or a
  three-step migration (add nullable → backfill → enforce).
- Never delete data before verifying its replacement. The procedure that was
  actually used is in [JSON-to-Database Migration](#json-to-database-migration):
  **read → transform → insert → verify → test → only then remove**.

---

## Code Quality Rules

**Architecture**

- SQL only in `repositories.py`.
- Business rules never in `ui.py` or `main.py`.
- Keep pure logic separate from side effects.
- Dependencies point downwards. No cycles.

**Correctness**

- Never invent data. Absent means absent.
- Validate at the edge, and again in the database for anything that matters.
- Handle errors explicitly; never swallow an exception silently.
- Prefer a clear refusal over a silent fallback — especially around doctor identity.

**Performance**

- Do not query inside a loop; join or use `selectinload`.
- Add an index when a column is filtered or sorted often — and check the query
  plan afterwards, because an index the query cannot use is dead weight.
- Never cache live availability.

**Security**

- Never commit secrets. `.env` stays git-ignored.
- Never build SQL by string concatenation.
- Never trust the client for identity or authorization.
- Never log passwords, tokens, OTPs or medical free text.
- Escape everything interpolated into HTML.

---

## Known Limitations

1. **The LLM phrasing layer does not work here.** The configured key returns
   `429 — no credits remaining`. Retrieval, evidence and precautions work; only
   the generated wording is missing, and its absence is reported rather than
   hidden.

2. **PostgreSQL is supported but untested.** No server was available on this
   machine. The schema and queries are dialect-neutral, but that path has never
   been executed.

3. **The OTP step has never met a real challenge.** The clinic used for testing
   did not present one, so the pause/resume logic is proven only against a stub.
   The selectors written for the real OTP field are reasonable but unverified.

4. **No booking has ever been completed end to end.** `OLADOC_SUBMIT_MODE`
   defaults to `disabled`, so the provider's confirmation detection and reference
   extraction are untested against real output.

5. **Provider scraping is inherently fragile.** oladoc.com can change its markup
   at any time. Selectors were chosen to be as stable as possible, but there is
   no contract.

6. **Rate limiting is per process.** It resets on restart and is not shared
   between workers.

7. **`/ui/confirmation/<unknown-id>` returns 200** with a friendly "not found"
   page rather than a 404. Pre-existing behaviour, left unchanged.

8. **Three older documents are partly out of date.**
   [`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md),
   [`CHANGES.md`](CHANGES.md) and
   [`RAG_AND_WEBSITE_EVALUATION.txt`](RAG_AND_WEBSITE_EVALUATION.txt) describe an
   earlier React + Node design and reference `.mjs` files that no longer exist.
   They are kept for history. **This README is the current source of truth.**

9. **The disease model's 100% score is a dataset artifact.** The Kaggle teaching
   dataset's test split is trivially separable. It is not clinical accuracy, and
   the label is never shown to patients.

10. **`.env` in this checkout contains live API keys.** It is git-ignored, but
    rotate them if that file has ever been shared.

11. **No formal accessibility audit** and **no browser-based UI tests.**

---

## Glossary

| Term | Simple meaning |
| --- | --- |
| API | A way for programs to talk to the application, using JSON instead of web pages |
| Migration | A recorded, repeatable change to the database structure |
| ORM | A tool that maps database rows to Python objects so you write less SQL |
| Repository | The code responsible for talking to the database |
| Facade | A simple front door that hides a more complicated system behind it |
| Deterministic | Same input, same output, every time |
| Pure function | A function that only uses its inputs and changes nothing outside itself |
| Side effect | Anything a function does beyond returning a value — writing to a database, sending email |
| Embedding | A list of numbers representing a piece of text's meaning, so texts can be compared |
| Vector database | A database that finds text by meaning rather than by exact words |
| RAG | Retrieval-augmented generation: find real documents first, then answer using them |
| Chunk | A small piece of a longer document, sized so it can be searched usefully |
| JWT | A signed token proving you are signed in, which the server can check without a lookup |
| Hash | A one-way scramble of a password; you can check a guess but not reverse it |
| Salt | Random text added before hashing so identical passwords do not produce identical hashes |
| AES-256-GCM | Strong encryption that also detects tampering |
| OTP | One-time password — a short code the provider sends to verify it is really you |
| Index | A lookup structure that makes searching a column fast |
| N+1 query | Loading a list, then running one more query per item — slow, and avoidable |
| Cascade delete | Deleting a parent row automatically deletes its children |
| Foreign key | A column pointing at another table's row, which the database enforces |
| Constraint | A rule the database itself refuses to break |
| WAL | Write-Ahead Logging — a SQLite mode letting reads continue during a write |
| Transaction | A group of changes that all succeed or all fail together |
| Idempotent | Safe to run more than once with the same result |
| Middleware | Code that runs on every request, before and after the handler |
| CSP | Content-Security-Policy — tells the browser what a page is allowed to load |
| XSS | Cross-site scripting — injecting malicious script into a page |
| Mojibake | Text corrupted by being read with the wrong character encoding |
| Canonical | The single authoritative version of something |

---

## Complete Command Reference

```powershell
# ---- setup -------------------------------------------------------------
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
$env:PYTHONPATH = (Get-Location).Path
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"   # secret

# ---- database ----------------------------------------------------------
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -m alembic history
.venv\Scripts\python.exe -m alembic downgrade -1
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"

# ---- data --------------------------------------------------------------
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json --dry-run
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json
.venv\Scripts\python.exe -m python_backend.app.database.migrate_json --verify

# ---- knowledge base ----------------------------------------------------
.venv\Scripts\python.exe -m python_backend.app.ingestion
.venv\Scripts\python.exe -m python_backend.app.ingestion --verify
.venv\Scripts\python.exe -m python_backend.app.ingestion --force

# ---- run ---------------------------------------------------------------
.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011
.venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --reload --port 8011
start-mediguide.bat

# ---- tests -------------------------------------------------------------
.venv\Scripts\python.exe -m pytest python_backend\tests -q
.venv\Scripts\python.exe -m pytest python_backend\tests -v
.venv\Scripts\python.exe -m pytest python_backend\tests -q -k "identity"
.venv\Scripts\python.exe -m pytest python_backend\tests\test_database.py -q

# ---- models and reports ------------------------------------------------
.venv\Scripts\python.exe -m python_backend.app.agents.kaggle_training
.venv\Scripts\python.exe -m python_backend.app.evaluation
```

> **No lint or type-check command is configured** in this repository. There is no
> ruff, flake8, black or mypy configuration. `python -m compileall` is the
> closest available check.

---

## Documentation Map

```
Everything about the project      → README.md  (this file)
Symptom fields and doctor identity→ DOCTOR_IDENTITY_AND_INTAKE.md

Pages and design system           → python_backend/app/ui.py
JSON API                          → python_backend/app/main.py
Live API documentation            → http://localhost:8011/docs

Database schema                   → python_backend/app/database/models.py
Database queries                  → python_backend/app/database/repositories.py
Database connection               → python_backend/app/database/engine.py
Migrations                        → python_backend/migrations/versions/
Legacy JSON import                → python_backend/app/database/migrate_json.py

Symptom logic                     → python_backend/app/symptom_intake.py
Retrieval and red flags           → python_backend/app/rag.py
Knowledge base ingestion          → python_backend/app/ingestion.py
Doctor identity                   → python_backend/app/doctor_identity.py
Live search and slots             → python_backend/app/oladoc_provider.py
Booking and OTP                   → python_backend/app/booking_worker.py

Authentication                    → python_backend/app/auth.py
Secrets                           → python_backend/app/secrets_manager.py
Errors                            → python_backend/app/errors.py
Logging                           → python_backend/app/flow_log.py
Branding and icons                → python_backend/app/branding.py

Tests                             → python_backend/tests/
Settings                          → .env.example
Launcher                          → start-mediguide.bat

Historical notes (partly stale)   → AGENT_ARCHITECTURE.md, CHANGES.md,
                                    RAG_AND_WEBSITE_EVALUATION.txt
```

---

**MediGuide provides general health information and appointment support. It does
not diagnose conditions and does not replace professional medical advice or
emergency care. If you think you are having a medical emergency, contact your
local emergency services.**
