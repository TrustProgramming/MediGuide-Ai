from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from datetime import date, timedelta
from html import escape
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .auth import auth_response, hash_password, user_from_token, verify_password
from .agents.chat_history import load_chat_context, save_chat_turn
from .agents.medical_information import decrypt_details, encrypt_details
from .agents.notifications import notify_appointment, notify_cancellation
from .availability import BOOKING_WINDOW_DAYS, is_slot_available
from .db import db
from .models import SPECIALISTS
from .playwright_booking import (
    DoctorIdentityMismatch,
    DoctorUnavailable,
    book_with_canonical_doctor,
    get_live_schedule,
    search_live_oladoc_doctors,
)
from .rag import KNOWLEDGE, assess
from .agents.qa import assess_structured
from . import doctor_identity as identity
from . import flow_log
from . import branding
from . import symptom_intake
from . import oladoc_provider
from . import appointments
from . import booking_session
from . import booking_worker
from . import email_service

logger = logging.getLogger(__name__)


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root{--ink:#18312d;--muted:#657672;--green:#16796b;--mint:#dcefe8;--gold:#e9b949;--paper:#f7fbf8}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#e9f5ee 0,transparent 32%),var(--paper);color:var(--ink);font-family:'DM Sans',sans-serif}h1,h2,h3{font-family:'Space Grotesk',sans-serif}a{color:var(--green);font-weight:700;text-decoration:none}.shell{max-width:1120px;margin:auto;padding:26px 22px 60px}.nav{display:flex;align-items:center;justify-content:space-between;padding:10px 0 28px}.logo{font:700 1.35rem 'Space Grotesk';display:flex;align-items:center;gap:10px}.logo b{display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:var(--gold);font-size:22px}.navlinks{display:flex;gap:18px;align-items:center}.hero{border-radius:24px;padding:42px;background:linear-gradient(120deg,#18312d,#24685b 72%,#3e9680);color:#fff}.hero h1{font-size:clamp(2rem,5vw,3.5rem);max-width:700px;margin:12px 0}.hero p{max-width:650px;color:#d9eee5;font-size:1.08rem}.eyebrow{color:var(--green);font-size:.74rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.hero .eyebrow{color:#a9e0c9}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}.section{margin-top:30px}.card,.panel{background:#fff;border:1px solid #dce9e2;border-radius:18px;padding:20px;box-shadow:0 8px 25px rgba(24,49,45,.05)}.doctor{min-height:210px}.avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:14px;background:var(--mint);color:var(--green);font-weight:700}.tag{display:inline-block;background:#edf7f1;color:var(--green);border-radius:99px;padding:5px 9px;font-size:.75rem;font-weight:700;margin-top:14px}.muted{color:var(--muted);font-size:.9rem}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field{display:flex;flex-direction:column;gap:6px;margin:10px 0}.field.full{grid-column:1/-1}label{font-weight:600;font-size:.9rem}input,select,textarea{font:inherit;border:1px solid #cbdcd3;border-radius:10px;padding:11px 12px;background:#fff;color:var(--ink)}textarea{min-height:90px;resize:vertical}button{border:0;border-radius:10px;padding:12px 17px;background:var(--green);color:#fff;font:700 .92rem 'DM Sans';cursor:pointer}button.secondary{background:#edf7f1;color:var(--green)}.auth{max-width:520px;margin:55px auto}.notice{border-left:4px solid var(--gold);background:#fff8e7;padding:12px 14px;border-radius:8px;color:#68521d}.appointment{border-left:4px solid var(--green);margin:12px 0}.footer{color:var(--muted);font-size:.85rem;margin-top:35px}@media(max-width:640px){.hero{padding:28px 22px}.formgrid{grid-template-columns:1fr}.field.full{grid-column:auto}.navlinks{gap:8px;font-size:.85rem}}
</style>
<style>
.cta-band .cta-button{min-height:52px!important;min-width:220px!important;display:inline-flex;align-items:center;justify-content:space-between!important;gap:18px;padding:13px 14px 13px 18px!important;border:1px solid rgba(115,215,232,.32)!important;border-radius:14px!important;background:linear-gradient(135deg,#f2c66d,#e7a84e)!important;color:#123d37!important;box-shadow:0 12px 24px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.5)!important;transition:transform .2s ease,box-shadow .2s ease,background .2s ease}
.cta-band .cta-button:hover{background:linear-gradient(135deg,#ffd985,#edb45c)!important;color:#123d37!important;transform:translateY(-2px);box-shadow:0 16px 30px rgba(0,0,0,.24),inset 0 1px 0 rgba(255,255,255,.6)!important}
.cta-button span{display:block;line-height:1.2}
.cta-button b{display:grid;place-items:center;width:29px;height:29px;border-radius:9px;background:rgba(18,61,55,.12);font-size:1.15rem;line-height:1;transition:transform .2s ease,background .2s ease}
.cta-button:hover b{background:rgba(18,61,55,.2);transform:translateX(2px)}
.doctor form{display:grid;gap:10px;margin:18px 0;padding:14px;border:1px solid rgba(115,215,232,.14);border-radius:14px;background:rgba(5,20,18,.42)}.doctor form label{font-size:.76rem;color:var(--muted);letter-spacing:.08em;text-transform:uppercase}.doctor form input{width:100%;min-height:44px;color:var(--ink);background:rgba(7,25,22,.78);border-color:rgba(115,215,232,.26);border-radius:10px}.doctor form button{width:100%;min-height:44px;padding:10px 14px;border:1px solid rgba(72,215,176,.48);border-radius:10px;background:linear-gradient(135deg,#38cfa9,#25a98f);color:#06221c;font-weight:800;box-shadow:0 8px 18px rgba(16,134,108,.2);transition:transform .2s ease,box-shadow .2s ease}.doctor form button:hover{transform:translateY(-2px);box-shadow:0 12px 24px rgba(16,134,108,.3)}.doctor>a{display:inline-flex;align-items:center;gap:7px;margin-top:8px}.live-schedule .slot-list{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}.live-slot,.live-date{margin-top:0;padding:9px 12px;border:1px solid rgba(72,215,176,.25);background:rgba(35,142,116,.16);border-radius:10px}.live-date:hover{background:rgba(72,215,176,.28);color:var(--ink)}
</style>
"""


CSS += """
<style>
input, select, textarea {
    background: #f4fffb !important;
    color: #123d39 !important;
    border: 1px solid #a9daca !important;
    border-radius: 12px !important;
    box-shadow: inset 0 1px 2px rgba(18, 61, 57, .04), 0 5px 14px rgba(26, 167, 124, .06) !important;
    transition: border-color .18s ease, box-shadow .18s ease, background .18s ease, transform .18s ease;
}
input:hover, select:hover, textarea:hover {
    background: #ffffff !important;
    border-color: #65bd98 !important;
}
input:focus, select:focus, textarea:focus {
    outline: 3px solid rgba(26, 167, 124, .18) !important;
    border-color: #15966f !important;
    background: #ffffff !important;
    box-shadow: 0 0 0 1px #15966f, 0 8px 20px rgba(26, 167, 124, .12) !important;
}
input::placeholder, textarea::placeholder {
    color: #6b8f83 !important;
    opacity: 1;
}
select {
    appearance: auto;
    cursor: pointer;
}
textarea {
    min-height: 110px;
    line-height: 1.5;
}
.field label {
    color: #24584d;
}
.logo-mark {
    position: relative;
    display: grid !important;
    place-items: center;
    overflow: hidden;
}
.logo-mark::before {
    content: "+";
    color: #123d39;
    font: 700 1.35rem/1 'Space Grotesk', sans-serif;
    transform: rotate(-8deg);
}
.auth-mark {
    position: relative;
    display: grid;
    place-items: center;
    width: 58px;
    height: 58px;
    border-radius: 18px;
    background: linear-gradient(145deg, #f5d77a, #e9b84e);
    color: #123d39;
    box-shadow: 0 12px 24px rgba(233, 184, 78, .25);
}
.auth-mark::before {
    content: "+";
    font: 700 1.8rem/1 'Space Grotesk', sans-serif;
}
.auth-mark::after {
    content: "";
    position: absolute;
    inset: 9px;
    border: 2px solid rgba(18, 61, 57, .2);
    border-radius: 13px;
}
.card, .panel {
    border-radius: 16px;
    box-shadow: 0 14px 32px rgba(18, 61, 57, .08);
}
.card:hover {
    transform: translateY(-3px);
    box-shadow: 0 20px 38px rgba(18, 61, 57, .13);
}
.auth {
    border-top: 4px solid #1aa77c;
}
.directory-panel {
    padding: clamp(22px, 4vw, 38px);
    border: 1px solid #bfe7d5;
    background: linear-gradient(145deg, #ffffff 0%, #f1fcf7 100%);
}
.directory-search {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: end;
    padding: 16px;
    margin: 24px 0 18px;
    border: 1px solid #cdeade;
    border-radius: 16px;
    background: #e9f9f1;
}
.directory-search .field { margin: 0; }
.directory-search button { min-height: 46px; white-space: nowrap; }
.directory-filters {
    padding: 16px;
    border: 1px solid #d4ede1;
    border-radius: 16px;
    background: rgba(255, 255, 255, .78);
}
.directory-results { margin-top: 22px; scroll-margin-top: 24px; }
.doctor-result {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 320px;
    padding: 22px;
    position: relative;
    overflow: hidden;
    border: 1px solid #d9ece3;
    border-top: 5px solid #1aa77c;
    background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(239,250,244,.96));
    box-shadow: 0 18px 36px rgba(18, 61, 57, .12);
    transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
.doctor-result::before {
    content: "";
    position: absolute;
    top: -60px;
    right: -54px;
    width: 160px;
    height: 160px;
    background: radial-gradient(circle, rgba(26, 167, 124, .12) 0%, rgba(26, 167, 124, 0) 68%);
    border-radius: 50%;
}
.doctor-result:hover { transform: translateY(-6px); box-shadow: 0 26px 44px rgba(18, 61, 57, .18); border-color: #9edac1; }
.doctor-result h3 { margin: 12px 0 4px; font-size: 1.27rem; line-height: 1.25; }
.doctor-result .button { margin-top: auto; display: inline-flex; justify-content: center; align-items: center; min-height: 42px; }
.doctor-result .actions {
    margin-top: auto;
    padding-top: 12px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    position: relative;
    z-index: 1;
}
.doctor-result .actions a {
    width: 100%;
    text-align: center;
    padding: 10px 12px;
    border-radius: 10px;
    font-size: .82rem;
}
.doctor-result .button.primary {
    background: linear-gradient(135deg, #1aa77c, #0e7f65);
    color: #ffffff;
    border: 1px solid rgba(14, 127, 101, .2);
    box-shadow: 0 10px 22px rgba(26, 167, 124, .24);
}
.doctor-result .button.secondary {
    background: #fff;
    border: 1px solid #cfe5dc;
    color: var(--ink);
}
.doctor-result .meta-stack {
    display: grid;
    gap: 4px;
    position: relative;
    z-index: 1;
}
.question-panel { border-left: 5px solid #e9b84e; background: linear-gradient(145deg, #fffdf5, #ffffff); }
.chat-session { border-left: 5px solid #1aa77c; }
.chat-session-label { display: inline-block; margin-bottom: 8px; color: #16796b; font-size: .74rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
.chat-session ol { padding-left: 22px; }
.chat-session li { margin: 10px 0; }
.question-list { display: grid; gap: 14px; padding-left: 24px; }
.question-list label { display: block; margin-bottom: 6px; line-height: 1.4; }
.question-list textarea { width: 100%; min-height: 64px; }
.answer-panel { border-left: 5px solid #5c9bd1; }
.answer-row { display: grid; gap: 5px; padding: 12px 0; border-bottom: 1px solid #dfeee8; }
.answer-row:last-child { border-bottom: 0; }
.answer-row strong { font-size: .9rem; line-height: 1.4; }
.answer-row span { color: #5f7d76; }
.review-panel { position: relative; overflow: hidden; border: 1px solid #e7c77e; border-left: 6px solid #d39a36; border-radius: 18px; padding: 26px 28px; background: linear-gradient(135deg, #fff8e8 0%, #ffffff 72%); box-shadow: 0 16px 34px rgba(139, 91, 19, .12); }
.review-panel::after { content: ''; position: absolute; width: 150px; height: 150px; right: -54px; top: -68px; border: 18px solid rgba(211, 154, 54, .12); border-radius: 50%; }
.review-panel h2 { margin: 6px 0 10px; color: #775016; font-size: 1.45rem; }
.review-panel p { max-width: 700px; line-height: 1.65; }
.review-panel .button { position: relative; z-index: 1; margin-top: 8px; }
.landing-hero .icon-tile, .landing-hero .cta-mark, .feature-card .icon-tile, .cta-band .cta-mark { display: none !important; }
.logo-mark {
    display: grid !important;
    position: relative;
    place-items: center;
    width: 40px;
    height: 40px;
    flex: 0 0 40px;
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, .7);
    border-radius: 13px;
    background: linear-gradient(145deg, #f7d778 0%, #e5a943 100%);
    box-shadow: 0 8px 18px rgba(214, 150, 47, .28), inset 0 1px 0 rgba(255, 255, 255, .7);
    transform: rotate(-6deg);
}
.logo-mark::before {
    content: "+";
    position: relative;
    z-index: 1;
    color: #123d39;
    font: 700 1.65rem/1 'Space Grotesk', sans-serif;
    transform: rotate(6deg);
}
.logo-mark::after {
    content: "";
    position: absolute;
    inset: 6px;
    border: 1px solid rgba(18, 61, 57, .22);
    border-radius: 9px;
}
.logo { gap: 10px; }
.auth-mark, .icon-tile, .cta-mark { display: none !important; }
.hero:after, .status-dot, .chat-input span { display: none !important; }
.process-step:after, .cta-button b, .chat-input span { display: none !important; }
.process-step:not(:last-child):after { content: none !important; }
.feature-card .icon-tile { display: grid !important; width: 48px; height: 48px; border-radius: 14px; font-size: 0; background-color: #e4f5ef; background-repeat: no-repeat; background-position: center; background-size: 24px; }
.feature-card .icon-tile::before { content: none; }
.feature-card:nth-child(1) .icon-tile { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23138a78' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 12.5z'/%3E%3Cpath d='M8 8h8M8 11h5'/%3E%3C/svg%3E"); }
.feature-card:nth-child(2) .icon-tile { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23138a78' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 20s-7-3.6-7-9.1A4 4 0 0 1 12 8a4 4 0 0 1 7 2.9C19 16.4 12 20 12 20z'/%3E%3Cpath d='M12 5V2M10.5 3.5h3'/%3E%3C/svg%3E"); }
.feature-card:nth-child(3) .icon-tile { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23138a78' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='4' y='5' width='16' height='16' rx='2'/%3E%3Cpath d='M8 3v4M16 3v4M4 10h16M8 14h3M8 17h6'/%3E%3C/svg%3E"); }
.feature-card a { font-size: 0; }
.feature-card:nth-child(1) a::after { content: 'Explore care guidance'; font-size: .9rem; }
.feature-card:nth-child(2) a::after { content: 'Meet your care team'; font-size: .9rem; }
.feature-card:nth-child(3) a::after { content: 'Plan an appointment'; font-size: .9rem; }
.landing-hero { min-height: 430px; padding: clamp(28px, 6vw, 64px); border-radius: 24px; }
.landing-stats { margin-top: 24px; }
.process-grid { gap: clamp(20px, 4vw, 42px); }
.trust-points span, .cta-proof span { font-size: 0 !important; }
.trust-points span::after, .cta-proof span::after { content: attr(data-label); font-size: .9rem; }
.trust-points span:nth-child(1)::after { content: 'Internal disease labels stay private'; }
.trust-points span:nth-child(2)::after { content: 'Medical information is encrypted'; }
.trust-points span:nth-child(3)::after { content: 'OTP and CAPTCHA stay with the patient'; }
.cta-proof span:nth-child(1)::after { content: 'Free to get started'; }
.cta-proof span:nth-child(2)::after { content: 'Private by design'; }
.conversation-card h3 { margin-bottom: 4px; color: #123d39; }
.conversation-date { margin: 0 0 14px; color: #6a8980; font-size: .82rem; font-weight: 700; }
@media (max-width: 680px) {
    .directory-search { grid-template-columns: 1fr; }
    .directory-search button { width: 100%; }
    .doctor-result .actions { display: grid; grid-template-columns: 1fr; }
}
</style>
"""


CSS += """
<style>
/* ------------------------------------------------------------------
   MediGuide design system
   Tokens first, then components. Everything below composes from these
   values so spacing, radius, depth and colour stay consistent.
------------------------------------------------------------------ */
:root{
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px;
  --space-5:24px; --space-6:32px; --space-7:48px; --space-8:64px;
  --radius-sm:8px; --radius-md:12px; --radius-lg:18px; --radius-pill:999px;
  --shadow-1:0 1px 2px rgba(16,42,38,.06), 0 1px 3px rgba(16,42,38,.04);
  --shadow-2:0 4px 12px rgba(16,42,38,.07), 0 2px 4px rgba(16,42,38,.04);
  --shadow-3:0 12px 28px rgba(16,42,38,.10), 0 4px 10px rgba(16,42,38,.05);
  --brand-600:#0f7a63; --brand-500:#13977b; --brand-400:#2bb394; --brand-50:#eaf7f3;
  --ink-900:#0d2320; --ink-700:#28453f; --ink-500:#5b7671; --ink-300:#8fa7a1;
  --line:#dde9e5; --surface:#ffffff; --surface-2:#f6faf8; --canvas:#f2f7f5;
  --danger-600:#b3261e; --danger-50:#fdecea;
  --warn-600:#8a5a00; --warn-50:#fff6e5;
  --ok-600:#1b6b4a; --ok-50:#e8f6ef;
  --focus:0 0 0 3px rgba(19,151,123,.35);
  --font-sans:'DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-display:'Space Grotesk',var(--font-sans);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme='light']){
    --ink-900:#eaf4f1; --ink-700:#c6dad5; --ink-500:#9ab5af; --ink-300:#6d8a84;
    --line:#284742; --surface:#122320; --surface-2:#162b27; --canvas:#0c1a18;
    --brand-50:#12302a; --shadow-1:none;
  }
}
html[data-theme='dark']{
  --ink-900:#eaf4f1; --ink-700:#c6dad5; --ink-500:#9ab5af; --ink-300:#6d8a84;
  --line:#284742; --surface:#122320; --surface-2:#162b27; --canvas:#0c1a18;
  --brand-50:#12302a;
  --shadow-1:0 1px 2px rgba(0,0,0,.3); --shadow-2:0 4px 12px rgba(0,0,0,.35);
  --shadow-3:0 12px 28px rgba(0,0,0,.42);
  --danger-50:#3a1714; --warn-50:#33270d; --ok-50:#10301f;
}

/* --- base ------------------------------------------------------- */
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink-900);font-family:var(--font-sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
body::before{content:none!important}
h1,h2,h3,h4{font-family:var(--font-display);line-height:1.2;letter-spacing:-.015em;margin:0}
p{margin:0}
a{color:var(--brand-600);text-decoration:none;font-weight:600}
a:hover{color:var(--brand-500);text-decoration:underline}
:where(a,button,input,select,textarea,[tabindex]):focus-visible{
  outline:2px solid transparent;box-shadow:var(--focus);border-radius:var(--radius-sm)}
.shell{max-width:1140px;margin:0 auto;padding:var(--space-5) var(--space-4) var(--space-8)}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

/* --- navigation -------------------------------------------------- */
.nav{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);
  padding:var(--space-3) var(--space-4);margin-bottom:var(--space-6);
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);
  box-shadow:var(--shadow-1);position:sticky;top:var(--space-2);z-index:20}
.logo{display:inline-flex;align-items:center;gap:var(--space-3);font:700 1.15rem var(--font-display);
  color:var(--ink-900)}
.logo:hover{text-decoration:none;color:var(--ink-900)}
.nav-actions{display:flex;align-items:center;gap:var(--space-2);flex-wrap:wrap;justify-content:flex-end}
.navlinks{display:flex;align-items:center;gap:var(--space-1);flex-wrap:wrap}
.navlinks a{padding:var(--space-2) var(--space-3);border-radius:var(--radius-sm);
  font-size:.9rem;font-weight:600;color:var(--ink-700)}
.navlinks a:hover{background:var(--brand-50);color:var(--brand-600);text-decoration:none}
.theme-toggle{display:inline-flex;align-items:center;min-height:38px;padding:0 var(--space-3);
  border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--surface);
  color:var(--ink-700);font:600 .85rem var(--font-sans);cursor:pointer}
.theme-toggle:hover{border-color:var(--brand-400);color:var(--brand-600)}

/* --- surfaces ---------------------------------------------------- */
.panel,.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);
  padding:var(--space-5);box-shadow:var(--shadow-1)}
.panel+.panel,.section+.section{margin-top:var(--space-5)}
.panel::before,.panel::after,.card::before,.card::after{content:none!important}
.panel__title{font-size:1.1rem;margin-bottom:var(--space-3)}
.panel__heading{font-size:clamp(1.6rem,3.4vw,2.2rem);margin:var(--space-2) 0}
.panel--hero{background:linear-gradient(140deg,var(--brand-600),var(--brand-400));
  border-color:transparent;color:#fff;box-shadow:var(--shadow-3)}
.panel--hero .panel__heading,.panel--hero h1{color:#fff}
.panel--hero .card-meta{color:rgba(255,255,255,.88)}
.panel--hero .chip--accent{background:rgba(255,255,255,.2);color:#fff}
.card-meta{color:var(--ink-500);font-size:.92rem}
.card-note{color:var(--ink-500);font-size:.82rem;margin-top:var(--space-2)}
.card-note--warn{color:var(--warn-600)}
.breadcrumb{display:flex;gap:var(--space-2);align-items:center;font-size:.85rem;
  color:var(--ink-500);margin-bottom:var(--space-4)}

/* --- chips ------------------------------------------------------- */
.chip{display:inline-flex;align-items:center;gap:var(--space-1);padding:var(--space-1) var(--space-3);
  border-radius:var(--radius-pill);background:var(--surface-2);border:1px solid var(--line);
  color:var(--ink-700);font-size:.78rem;font-weight:600;margin-right:var(--space-2)}
.chip--accent{background:var(--brand-50);border-color:transparent;color:var(--brand-600)}
.chip--muted{color:var(--ink-500)}
.chip--link{cursor:pointer}
.chip--link:hover{border-color:var(--brand-400);text-decoration:none}
.chip--active{background:var(--brand-600);border-color:var(--brand-600);color:#fff}
.chip-row{display:flex;flex-wrap:wrap;gap:var(--space-2);margin-top:var(--space-3)}

/* --- buttons ----------------------------------------------------- */
.button,button{display:inline-flex;align-items:center;justify-content:center;gap:var(--space-2);
  min-height:44px;padding:0 var(--space-4);border-radius:var(--radius-md);border:1px solid transparent;
  font:600 .92rem var(--font-sans);cursor:pointer;text-decoration:none;
  transition:background .15s ease,border-color .15s ease,color .15s ease,box-shadow .15s ease}
.button.primary,button:not(.secondary):not(.theme-toggle):not(.back-button){
  background:var(--brand-600);color:#fff;box-shadow:var(--shadow-1)}
.button.primary:hover,button:not(.secondary):not(.theme-toggle):not(.back-button):hover{
  background:var(--brand-500);color:#fff;text-decoration:none;box-shadow:var(--shadow-2)}
.button.secondary{background:var(--surface);border-color:var(--line);color:var(--ink-700)}
.button.secondary:hover{border-color:var(--brand-400);color:var(--brand-600);
  background:var(--brand-50);text-decoration:none}
.button[disabled],button[disabled]{opacity:.55;cursor:not-allowed}
.actions{display:flex;flex-wrap:wrap;gap:var(--space-2);margin-top:var(--space-4)}

/* --- forms ------------------------------------------------------- */
.field{display:flex;flex-direction:column;gap:var(--space-2);margin-bottom:var(--space-4)}
label{font-size:.88rem;font-weight:600;color:var(--ink-700)}
input,select,textarea{width:100%;min-height:44px;padding:var(--space-3);
  border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface);
  color:var(--ink-900);font:inherit;box-shadow:none!important;transition:border-color .15s ease}
input:hover,select:hover,textarea:hover{border-color:var(--brand-400)}
input:focus,select:focus,textarea:focus{border-color:var(--brand-500);outline:none;box-shadow:var(--focus)!important}
input[readonly]{background:var(--surface-2);color:var(--ink-700)}
textarea{min-height:110px;resize:vertical}
.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:var(--space-4)}
.formgrid .full{grid-column:1/-1}
.inline-form{display:flex;flex-wrap:wrap;gap:var(--space-3);align-items:flex-end;margin-top:var(--space-4)}
.inline-form .field{margin-bottom:0;min-width:200px;flex:1}

/* --- alerts ------------------------------------------------------ */
.alert,.notice{padding:var(--space-4);border-radius:var(--radius-md);border:1px solid var(--line);
  background:var(--surface-2);color:var(--ink-700);font-size:.92rem;margin-bottom:var(--space-3)}
.alert--error,.notice.danger{background:var(--danger-50);border-color:transparent;color:var(--danger-600)}
.alert--warn{background:var(--warn-50);border-color:transparent;color:var(--warn-600)}
.alert--ok{background:var(--ok-50);border-color:transparent;color:var(--ok-600)}
.alert strong{font-weight:700}

/* --- grids and doctor cards -------------------------------------- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:var(--space-4)}
.doctor-grid{align-items:stretch}
.doctor-card{display:flex;flex-direction:column;gap:var(--space-3);
  transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
.doctor-card:hover{border-color:var(--brand-400);box-shadow:var(--shadow-3);transform:translateY(-2px)}
.doctor-card__head{display:flex;gap:var(--space-3);align-items:flex-start}
.doctor-card__name{font-size:1.12rem;margin:var(--space-2) 0 var(--space-1)}
.doctor-card__meta{display:flex;flex-direction:column;gap:var(--space-1)}
.avatar{display:grid;place-items:center;width:48px;height:48px;flex:0 0 48px;
  border-radius:var(--radius-md);background:var(--brand-50);color:var(--brand-600);
  font:700 1rem var(--font-display)}
.doctor-actions{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-2);margin-top:auto;
  padding-top:var(--space-3);border-top:1px solid var(--line)}
.doctor-actions .button{width:100%;min-height:42px;font-size:.84rem;padding:0 var(--space-2)}

/* --- live slots -------------------------------------------------- */
.slot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));
  gap:var(--space-2);margin-top:var(--space-4)}
.slot-chip{display:flex;flex-direction:column;align-items:center;gap:2px;padding:var(--space-3);
  border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface);
  text-decoration:none;transition:border-color .15s ease,background .15s ease}
.slot-chip:hover{border-color:var(--brand-500);background:var(--brand-50);text-decoration:none}
.slot-chip__time{font-weight:700;color:var(--ink-900)}
.slot-chip__hint{font-size:.74rem;color:var(--ink-500)}

/* --- states ------------------------------------------------------ */
.empty-state{padding:var(--space-7) var(--space-4);text-align:center;border:1px dashed var(--line);
  border-radius:var(--radius-lg);background:var(--surface-2)}
.empty-state h3{margin-bottom:var(--space-2)}
.is-loading{display:flex;align-items:center;gap:var(--space-3);color:var(--ink-500);font-size:.92rem}
.spinner{width:18px;height:18px;border:2px solid var(--line);border-top-color:var(--brand-500);
  border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion: reduce){
  *{animation-duration:.01ms!important;transition-duration:.01ms!important}
}

/* --- checklist / confirmation ------------------------------------ */
.summary-list{display:grid;gap:var(--space-2);margin-top:var(--space-3)}
.summary-row{display:flex;justify-content:space-between;gap:var(--space-4);padding:var(--space-3) 0;
  border-bottom:1px solid var(--line)}
.summary-row:last-child{border-bottom:0}
.summary-row dt{color:var(--ink-500);font-size:.86rem}
.summary-row dd{margin:0;font-weight:600;text-align:right}
.status-pill{display:inline-flex;align-items:center;gap:var(--space-2);padding:var(--space-2) var(--space-4);
  border-radius:var(--radius-pill);font-weight:700;font-size:.85rem}
.status-pill--confirmed{background:var(--ok-50);color:var(--ok-600)}
.status-pill--selected{background:var(--warn-50);color:var(--warn-600)}
.status-pill--failed{background:var(--danger-50);color:var(--danger-600)}

/* --- responsive -------------------------------------------------- */
@media (max-width:900px){
  .nav{position:static;flex-wrap:wrap}
  .nav-actions{width:100%;justify-content:space-between}
}
@media (max-width:640px){
  .shell{padding:var(--space-4) var(--space-3) var(--space-7)}
  .panel,.card{padding:var(--space-4)}
  .grid{grid-template-columns:1fr}
  .doctor-actions{grid-template-columns:1fr}
  .doctor-actions .button{min-height:46px;font-size:.9rem}
  .slot-grid{grid-template-columns:repeat(auto-fill,minmax(104px,1fr))}
  .summary-row{flex-direction:column;gap:var(--space-1)}
  .summary-row dd{text-align:left}
  .navlinks a{font-size:.84rem;padding:var(--space-2)}
}
</style>
"""


VISUAL_LAYER = """
<style>
/* ==========================================================================
   MediGuide visual layer
   Loaded last, so it is the authority. Earlier blocks remain only so that any
   page not yet restyled keeps working.
   Goals: real depth, a clear hierarchy, one accent colour, and motion that
   responds to the user rather than decorating the page.
   ========================================================================== */

:root{
  --brand-700:#0a5f4c; --brand-600:#0f7a63; --brand-500:#13977b;
  --brand-400:#25b493; --brand-300:#6fd6bb; --brand-50:#e8f7f2;
  --accent-600:#b4560f; --accent-500:#e0761f; --accent-50:#fdf1e5;
  --ink-900:#0a1f1b; --ink-800:#16302a; --ink-700:#2b4740;
  --ink-500:#5e7a72; --ink-400:#829a93; --ink-300:#a8bdb6;
  --surface:#ffffff; --surface-2:#f7fbf9; --canvas:#eef5f2; --line:#dde9e4;
  --ring:0 0 0 3px rgba(19,151,123,.28);
  --e1:0 1px 2px rgba(10,31,27,.05), 0 1px 3px rgba(10,31,27,.04);
  --e2:0 2px 4px rgba(10,31,27,.04), 0 6px 16px rgba(10,31,27,.07);
  --e3:0 8px 20px rgba(10,31,27,.09), 0 20px 44px rgba(10,31,27,.10);
  --e-brand:0 10px 28px rgba(15,122,99,.26);
}
html[data-theme='dark']{
  --ink-900:#eefaf6; --ink-800:#dcefe9; --ink-700:#c2dbd4;
  --ink-500:#93b0a8; --ink-400:#7b978f; --ink-300:#658079;
  --surface:#122421; --surface-2:#162d28; --canvas:#0b1816; --line:#27443d;
  --brand-50:#12322a; --accent-50:#33220f;
  --e1:0 1px 2px rgba(0,0,0,.4); --e2:0 6px 16px rgba(0,0,0,.42);
  --e3:0 20px 44px rgba(0,0,0,.5); --e-brand:0 10px 28px rgba(0,0,0,.45);
}

/* --- page ground ---------------------------------------------------------
   A soft radial wash rather than a flat fill, so large empty areas have some
   life without competing with content. */
body{
  background:
    radial-gradient(1100px 620px at 12% -8%, rgba(37,180,147,.13), transparent 60%),
    radial-gradient(900px 520px at 100% 0%, rgba(15,122,99,.10), transparent 55%),
    var(--canvas);
  background-attachment:fixed;
  color:var(--ink-800);
  -webkit-font-smoothing:antialiased;
}
.shell{max-width:1180px}

/* --- typography ---------------------------------------------------------- */
h1,h2,h3,h4{color:var(--ink-900);letter-spacing:-.022em}
h1{font-size:clamp(2rem,4.2vw,3.1rem);line-height:1.06}
h2{font-size:clamp(1.35rem,2.4vw,1.85rem);line-height:1.15}
h3{font-size:1.06rem}
.eyebrow{
  display:inline-flex;align-items:center;gap:6px;
  font-size:.7rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
  color:var(--brand-600);
}

/* --- navigation: frosted, floating -------------------------------------- */
.nav{
  background:color-mix(in srgb, var(--surface) 82%, transparent);
  backdrop-filter:saturate(1.6) blur(14px);
  -webkit-backdrop-filter:saturate(1.6) blur(14px);
  border:1px solid color-mix(in srgb, var(--line) 80%, transparent);
  border-radius:16px;box-shadow:var(--e2);
}
.logo__word{font:800 1.16rem/1 var(--font-display);letter-spacing:-.03em;color:var(--ink-900)}
.logo__word-accent{color:var(--brand-600)}
.brand-mark{filter:drop-shadow(0 4px 10px rgba(15,122,99,.32));transition:transform .25s cubic-bezier(.34,1.56,.64,1)}
.logo:hover .brand-mark{transform:rotate(-8deg) scale(1.06)}
.navlinks a{position:relative;font-weight:600;color:var(--ink-700);border:0;background:none;box-shadow:none}
.navlinks a::after{
  content:'';position:absolute;left:10px;right:10px;bottom:3px;height:2px;
  border-radius:2px;background:var(--brand-500);
  transform:scaleX(0);transform-origin:left;transition:transform .2s ease;
}
.navlinks a:hover{background:transparent;color:var(--brand-600)}
.navlinks a:hover::after{transform:scaleX(1)}
.theme-toggle{border-radius:11px;gap:7px;font-weight:700;color:var(--ink-700)}

/* --- surfaces ------------------------------------------------------------ */
.panel,.card{
  background:var(--surface);border:1px solid var(--line);
  border-radius:18px;box-shadow:var(--e1);
}
.panel{padding:clamp(20px,2.4vw,30px)}

/* Hero: layered gradient with a faint grid, so it reads as a surface with
   depth rather than a coloured rectangle. */
.panel--hero,.hero{
  position:relative;overflow:hidden;border:0;color:#fff;
  background:
    radial-gradient(760px 300px at 88% -30%, rgba(111,214,187,.42), transparent 62%),
    linear-gradient(135deg,var(--brand-700) 0%,var(--brand-600) 46%,var(--brand-500) 100%);
  box-shadow:var(--e-brand);
}
.panel--hero::after,.hero::after{
  content:'';position:absolute;inset:0;pointer-events:none;opacity:.5;
  background-image:
    linear-gradient(rgba(255,255,255,.07) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.07) 1px,transparent 1px);
  background-size:46px 46px;
  mask-image:radial-gradient(760px 340px at 78% 0%,#000,transparent 72%);
  -webkit-mask-image:radial-gradient(760px 340px at 78% 0%,#000,transparent 72%);
}
.panel--hero>*,.hero>*{position:relative;z-index:1}
.panel--hero h1,.hero h1,.panel--hero .panel__heading{color:#fff}
.panel--hero .card-meta,.hero p,.panel--hero p{color:rgba(255,255,255,.9)}
.panel--hero .eyebrow,.hero .eyebrow{color:var(--brand-300)}
.panel--hero .chip--accent{background:rgba(255,255,255,.18);color:#fff;border-color:transparent}

/* --- buttons ------------------------------------------------------------- */
.button,button{
  border-radius:12px;font-weight:700;letter-spacing:-.01em;
  transition:transform .16s ease, box-shadow .16s ease, background .16s ease, border-color .16s ease;
}
.button.primary,button:not(.secondary):not(.theme-toggle):not(.back-button){
  background:linear-gradient(180deg,var(--brand-500),var(--brand-600));
  border:1px solid var(--brand-700);color:#fff;box-shadow:var(--e-brand);
}
.button.primary:hover,button:not(.secondary):not(.theme-toggle):not(.back-button):hover{
  transform:translateY(-1px);box-shadow:0 14px 32px rgba(15,122,99,.34);
}
.button.primary:active,button:active{transform:translateY(0)}
.button.secondary{
  background:var(--surface);border:1px solid var(--line);color:var(--ink-700);box-shadow:var(--e1);
}
.button.secondary:hover{border-color:var(--brand-400);color:var(--brand-600);background:var(--brand-50)}
.hero .button.secondary,.panel--hero .button.secondary{
  background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.34);color:#fff;
}
.hero .button.secondary:hover,.panel--hero .button.secondary:hover{background:rgba(255,255,255,.24);color:#fff}

/* --- doctor card: the centrepiece ---------------------------------------- */
.doctor-grid{grid-template-columns:repeat(auto-fill,minmax(322px,1fr));gap:18px}
.doctor-card{
  display:flex;flex-direction:column;gap:14px;padding:20px;position:relative;overflow:hidden;
  transition:transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
/* A brand rail that appears on hover: motion with a purpose, indicating focus. */
.doctor-card::before{
  content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,var(--brand-400),var(--brand-600));
  transform:scaleY(0);transform-origin:top;transition:transform .22s ease;
}
.doctor-card:hover{transform:translateY(-3px);box-shadow:var(--e3);border-color:var(--brand-300)}
.doctor-card:hover::before{transform:scaleY(1)}
.doctor-card__top{display:grid;grid-template-columns:auto 1fr auto;align-items:start;gap:12px}
.doctor-card__id{min-width:0}
.doctor-card__name{
  margin:0 0 2px;font-size:1.07rem;line-height:1.25;color:var(--ink-900);
  overflow-wrap:anywhere;
}
.doctor-card__specialty{margin:0;color:var(--brand-600);font-weight:650;font-size:.87rem}
.doctor-card__meta{margin:0;color:var(--ink-500);font-size:.85rem;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.doctor-card__body{display:flex;flex-direction:column;gap:10px}
.doctor-card__tags{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.doctor-card__warn{
  margin:0;display:flex;gap:7px;align-items:flex-start;
  font-size:.79rem;color:var(--accent-600);background:var(--accent-50);
  padding:8px 10px;border-radius:9px;
}
.dot{width:3px;height:3px;border-radius:50%;background:var(--ink-300);display:inline-block}
.avatar{
  width:46px;height:46px;flex:0 0 46px;border-radius:14px;display:grid;place-items:center;
  font:800 .92rem/1 var(--font-display);color:#fff;letter-spacing:.02em;
  background:linear-gradient(140deg,var(--brand-500),var(--brand-700));
  box-shadow:0 6px 14px rgba(15,122,99,.3);
}
.badge{
  display:inline-flex;align-items:center;gap:5px;white-space:nowrap;
  padding:5px 9px;border-radius:999px;font-size:.69rem;font-weight:800;
  letter-spacing:.02em;background:var(--brand-50);color:var(--brand-700);
  border:1px solid color-mix(in srgb,var(--brand-400) 30%,transparent);
}
.badge--live{
  background:linear-gradient(135deg,var(--brand-500),var(--brand-600));color:#fff;border-color:transparent;
}
.tag--soft{
  display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;
  background:var(--surface-2);border:1px solid var(--line);
  color:var(--ink-500);font-size:.73rem;font-weight:650;
}
.rating{display:inline-flex;align-items:center;gap:3px}
.star{color:var(--ink-300);font-size:.82rem;line-height:1}
.star--on{color:#e8a33d}
.rating__value{margin-left:4px;font-size:.78rem;font-weight:800;color:var(--ink-700)}

/* Four actions as a 2x2 grid: present but subordinate to the doctor. */
.doctor-actions{
  display:grid !important;grid-template-columns:1fr 1fr;gap:8px;
  margin-top:auto;padding-top:14px;border-top:1px solid var(--line);
}
.act{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-height:40px;padding:0 10px;border-radius:10px;text-decoration:none;
  font-size:.81rem;font-weight:700;white-space:nowrap;
  background:var(--surface-2);border:1px solid var(--line);color:var(--ink-700);
  transition:transform .14s ease,background .14s ease,border-color .14s ease,color .14s ease;
}
.act:hover{background:var(--brand-50);border-color:var(--brand-300);color:var(--brand-700);text-decoration:none;transform:translateY(-1px)}
.act svg{flex:0 0 auto;opacity:.75}
.act--primary{
  background:linear-gradient(180deg,var(--brand-500),var(--brand-600));
  border-color:var(--brand-700);color:#fff;box-shadow:0 5px 14px rgba(15,122,99,.28);
}
.act--primary:hover{color:#fff;background:linear-gradient(180deg,var(--brand-400),var(--brand-500));border-color:var(--brand-700)}
.act--primary svg{opacity:1}

/* --- live slots ---------------------------------------------------------- */
.slot-grid{grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:10px}
.slot-chip{
  border-radius:12px;border:1px solid var(--line);background:var(--surface);
  padding:12px 10px;cursor:pointer;
  transition:transform .14s ease,border-color .14s ease,box-shadow .14s ease,background .14s ease;
}
.slot-chip:hover{
  transform:translateY(-2px);border-color:var(--brand-400);
  background:var(--brand-50);box-shadow:var(--e2);
}
.slot-chip__time{font-weight:800;color:var(--ink-900);font-size:.95rem}
.slot-chip__hint{font-size:.7rem;color:var(--brand-600);font-weight:700}
.slot-form{margin:0}

/* --- forms --------------------------------------------------------------- */
input,select,textarea{
  border-radius:11px;border:1px solid var(--line);background:var(--surface);
  color:var(--ink-900);transition:border-color .15s ease,box-shadow .15s ease;
}
input:hover,select:hover,textarea:hover{border-color:var(--brand-300)}
input:focus,select:focus,textarea:focus{border-color:var(--brand-500);box-shadow:var(--ring) !important;outline:none}
label{color:var(--ink-700);font-weight:650}
.directory-search{
  background:linear-gradient(135deg,var(--brand-50),var(--surface-2));
  border:1px solid var(--line);border-radius:16px;
}

/* --- alerts -------------------------------------------------------------- */
.alert,.notice{border-radius:13px;border:1px solid var(--line);font-size:.9rem;line-height:1.55}
.alert--error,.notice.danger{background:#fdeceb;border-color:#f3c9c5;color:#95241c}
.alert--warn{background:var(--accent-50);border-color:#f0d3b0;color:var(--accent-600)}
.alert--ok{background:var(--brand-50);border-color:var(--brand-300);color:var(--brand-700)}
html[data-theme='dark'] .alert--error,html[data-theme='dark'] .notice.danger{background:#3a1613;border-color:#6b2a23;color:#ffb4ac}

/* --- stats / landing ----------------------------------------------------- */
.landing-stats{
  border:1px solid var(--line);border-radius:18px;overflow:hidden;
  background:var(--line);box-shadow:var(--e1);
}
.landing-stats div{background:var(--surface);padding:24px 18px}
.landing-stats strong{
  display:block;font:800 2.1rem/1 var(--font-display);
  background:linear-gradient(135deg,var(--brand-500),var(--brand-700));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
}
.landing-stats span{color:var(--ink-500);font-size:.82rem;margin-top:6px;display:block}
.feature-card{
  border-radius:18px;border:1px solid var(--line);background:var(--surface);
  box-shadow:var(--e1);transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease;
}
.feature-card:hover{transform:translateY(-4px);box-shadow:var(--e3);border-color:var(--brand-300)}
.feature-card .icon-tile{
  width:46px;height:46px;border-radius:14px;
  background:linear-gradient(140deg,var(--brand-50),#d6f0e7);
  border:1px solid color-mix(in srgb,var(--brand-400) 26%,transparent);
}
.process-section{background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--e1)}
.process-step span{
  display:inline-grid;place-items:center;width:30px;height:30px;border-radius:9px;
  background:var(--brand-50);color:var(--brand-700);font:800 .8rem/1 var(--font-display);margin-bottom:8px;
}
.trust-band{background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--e1)}
.trust-points span{
  background:var(--surface-2);border:1px solid var(--line);border-radius:11px;color:var(--ink-700);
}
/* The old call-to-action used a gold button that matched nothing else. */
.cta-band{
  border-radius:20px;border:0;color:#fff;
  background:
    radial-gradient(620px 260px at 82% -30%, rgba(111,214,187,.4), transparent 60%),
    linear-gradient(135deg,var(--brand-700),var(--brand-500));
  box-shadow:var(--e-brand);
}
.cta-band .cta-button,.cta-band .cta-button:hover{
  background:#fff !important;color:var(--brand-700) !important;border:0 !important;
  box-shadow:0 10px 26px rgba(0,0,0,.2) !important;font-weight:800;
}
.landing-nav{
  display:inline-flex;gap:4px;padding:6px;margin:0 auto 22px;border-radius:999px;
  background:var(--surface);border:1px solid var(--line);box-shadow:var(--e1);
}
.landing-nav a{padding:7px 14px;border-radius:999px;font-size:.85rem;color:var(--ink-700)}
.landing-nav a:hover{background:var(--brand-50);color:var(--brand-700);text-decoration:none}
.care-preview{border-radius:18px;border:1px solid rgba(255,255,255,.3);box-shadow:var(--e3)}

/* --- states -------------------------------------------------------------- */
.empty-state{
  border:1px dashed var(--line);border-radius:18px;background:var(--surface-2);
  padding:52px 24px;text-align:center;
}
.is-loading{color:var(--ink-500)}
.spinner{border-color:var(--line);border-top-color:var(--brand-500)}
.status-pill{padding:7px 14px;font-weight:800;border-radius:999px}
.status-pill--confirmed{background:var(--brand-50);color:var(--brand-700)}
.status-pill--selected{background:var(--accent-50);color:var(--accent-600)}
.summary-row{border-bottom:1px solid var(--line);padding:13px 0}
.summary-row dt{color:var(--ink-500);font-size:.84rem}
.summary-row dd{font-weight:700;color:var(--ink-900)}

/* --- entrance motion -----------------------------------------------------
   A short, once-only fade so pages settle rather than snap. Staggered per card
   to guide the eye down the grid. */
@keyframes mg-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.panel,.card{animation:mg-rise .42s cubic-bezier(.22,.8,.3,1) both}
.doctor-grid>.doctor-card:nth-child(1){animation-delay:.02s}
.doctor-grid>.doctor-card:nth-child(2){animation-delay:.06s}
.doctor-grid>.doctor-card:nth-child(3){animation-delay:.10s}
.doctor-grid>.doctor-card:nth-child(4){animation-delay:.14s}
.doctor-grid>.doctor-card:nth-child(n+5){animation-delay:.18s}

@media (prefers-reduced-motion: reduce){
  .panel,.card{animation:none}
  *{transition-duration:.01ms !important}
  .doctor-card:hover,.act:hover,.slot-chip:hover,.feature-card:hover,.button:hover{transform:none}
}

/* --- responsive ---------------------------------------------------------- */
@media (max-width:900px){
  .doctor-grid{grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
}
@media (max-width:640px){
  .shell{padding-left:14px;padding-right:14px}
  .doctor-grid{grid-template-columns:1fr}
  .doctor-actions{grid-template-columns:1fr 1fr}
  .act{min-height:44px;font-size:.82rem}
  .doctor-card__top{grid-template-columns:auto 1fr;row-gap:8px}
  .badge{grid-column:1/-1;justify-self:start}
  .landing-nav{display:flex;overflow-x:auto;width:100%;border-radius:14px}
  .slot-grid{grid-template-columns:repeat(auto-fill,minmax(98px,1fr))}
}

/* --- corrections found during visual review ------------------------------ */
/* The older stylesheet centre-aligned text inside cards, which left the
   location line centred while everything around it was flush left. */
.doctor-card,.doctor-card *{text-align:left}
.doctor-card__meta{justify-content:flex-start}
.doctor-card__body{gap:9px;margin-top:-2px}
.doctor-card__top{margin-bottom:2px}

/* The landing hero kept a pale background from an earlier block. */
.landing-hero{
  border:0 !important;color:#fff !important;
  background:
    radial-gradient(760px 320px at 88% -30%, rgba(111,214,187,.42), transparent 62%),
    linear-gradient(135deg,var(--brand-700) 0%,var(--brand-600) 46%,var(--brand-500) 100%) !important;
  box-shadow:var(--e-brand) !important;
}
.landing-hero h1,.landing-hero .hero-copy h1{color:#fff !important}
.landing-hero p,.landing-hero .safety-note{color:rgba(255,255,255,.88) !important}
.landing-hero .eyebrow{color:var(--brand-300) !important}
.landing-hero .button:not(.secondary){background:#fff !important;color:var(--brand-700) !important;border:0 !important}
.landing-hero .button.secondary{
  background:rgba(255,255,255,.14) !important;border-color:rgba(255,255,255,.35) !important;color:#fff !important;
}
.care-preview{background:rgba(255,255,255,.97) !important;transform:rotate(1.2deg)}
.care-preview:hover{transform:rotate(0deg);transition:transform .35s ease}

/* "Back to dashboard" was floating over the results as a stray pill. */
.directory-results>.actions{margin:0 0 18px}
.directory-results>.actions .button{box-shadow:var(--e1)}

/* Trust and process bands picked up a lilac tint from the old palette. */
.trust-band{background:var(--surface) !important;border:1px solid var(--line) !important}
.process-section{background:var(--surface) !important}
.feature-card{background:var(--surface) !important}

/* --- contrast fixes found in visual review ------------------------------- */
/* Inside the hero, the chat preview is a light card: its text must not
   inherit the hero's white, or it becomes white-on-white. */
.care-preview,.care-preview *{color:var(--ink-800) !important}
.care-preview .muted,.care-preview .chat-input{color:var(--ink-500) !important}
.care-preview strong{color:var(--ink-900) !important}
.care-preview .chat-bubble.ai{background:var(--brand-50) !important;color:var(--ink-800) !important}
.care-preview .chat-bubble.patient{background:#e8eefb !important;color:#324a6b !important}

/* The call-to-action band is dark, so its eyebrow needs a light tint. */
/* Measured at 4.09:1 against the band gradient, below the 4.5 threshold for
   normal-size text. Lightened to clear it. */
.cta-band .eyebrow,.cta-copy .eyebrow{color:#c7f2e4 !important}
.cta-band .cta-proof span,.cta-band p{color:rgba(255,255,255,.88) !important}

/* The amber availability notice was pale text on a pale ground. */
.notice{background:var(--surface-2);border:1px solid var(--line);color:var(--ink-700)}
.notice strong{color:var(--ink-900)}
.live-notice,.section .notice{
  background:var(--accent-50);border:1px solid #f0d3b0;color:#7a4409;
}
.live-notice strong,.section .notice strong{color:var(--accent-600)}
html[data-theme='dark'] .notice{background:var(--surface-2);color:var(--ink-700)}
html[data-theme='dark'] .live-notice,html[data-theme='dark'] .section .notice{
  background:#33240f;border-color:#6b4a1c;color:#f1c98a;
}

/* Mobile: the navigation stacked into a tall column and pushed content down. */
@media (max-width:640px){
  .nav{padding:10px 12px;gap:8px}
  .nav-actions .navlinks{gap:2px;overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}
  .nav-actions .navlinks::-webkit-scrollbar{display:none}
  .nav-actions .navlinks a{padding:6px 9px;font-size:.78rem;white-space:nowrap}
  .theme-toggle{padding:0 9px;min-height:34px;font-size:.74rem}
}

/* --- accessibility + dark-mode corrections ------------------------------- */
/* The focus ring was written with :where(), which contributes zero
   specificity, so component shadows overrode it and keyboard focus became
   invisible. Stated plainly here instead. */
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
textarea:focus-visible,[tabindex]:focus-visible,.act:focus-visible,
.slot-chip:focus-visible,.navlinks a:focus-visible,.theme-toggle:focus-visible{
  outline:3px solid var(--brand-500) !important;
  outline-offset:2px !important;
  box-shadow:0 0 0 5px rgba(19,151,123,.22) !important;
  border-radius:10px;
}

/* Dark mode: earlier rules hardcoded surface colours with higher specificity
   than the tokens, so the palette below is stated at matching specificity. */
html[data-theme='dark'] body{
  background:
    radial-gradient(1100px 620px at 12% -8%, rgba(37,180,147,.10), transparent 60%),
    var(--canvas) !important;
}
html[data-theme='dark'] .card,html[data-theme='dark'] .panel,
html[data-theme='dark'] .doctor-card,html[data-theme='dark'] .nav,
html[data-theme='dark'] .feature-card,html[data-theme='dark'] .trust-band,
html[data-theme='dark'] .process-section,html[data-theme='dark'] .landing-stats div{
  background:var(--surface) !important;border-color:var(--line) !important;color:var(--ink-800) !important;
}
html[data-theme='dark'] .act{background:var(--surface-2) !important;border-color:var(--line) !important;color:var(--ink-700) !important}
html[data-theme='dark'] .act:hover{background:var(--brand-50) !important;color:var(--brand-300) !important}
html[data-theme='dark'] .act--primary,html[data-theme='dark'] .act--primary:hover{
  background:linear-gradient(180deg,var(--brand-500),var(--brand-600)) !important;color:#fff !important;
}
html[data-theme='dark'] .doctor-card__name,html[data-theme='dark'] h1,
html[data-theme='dark'] h2,html[data-theme='dark'] h3{color:var(--ink-900) !important}
html[data-theme='dark'] .doctor-card__specialty{color:var(--brand-300) !important}
html[data-theme='dark'] .badge{background:var(--brand-50) !important;color:var(--brand-300) !important}
html[data-theme='dark'] .tag--soft{background:var(--surface-2) !important;color:var(--ink-500) !important}
html[data-theme='dark'] input,html[data-theme='dark'] select,html[data-theme='dark'] textarea{
  background:var(--surface-2) !important;border-color:var(--line) !important;color:var(--ink-900) !important;
}

/* An earlier rule hardcoded .field label to a dark green, which survived into
   dark mode and rendered dark-green on dark-green (measured 1.55:1). */
html[data-theme='dark'] label,html[data-theme='dark'] .field label,
html[data-theme='dark'] .eyebrow{color:var(--ink-700) !important}
html[data-theme='dark'] .eyebrow{color:var(--brand-300) !important}
html[data-theme='dark'] .card-meta,html[data-theme='dark'] .muted,
html[data-theme='dark'] .doctor-card__meta{color:var(--ink-500) !important}

/* --- booking checklist + autofill report --------------------------------- */
.autofill{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}
.autofill__row{
  display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;
  padding:11px 13px;border-radius:11px;border:1px solid var(--line);background:var(--surface-2);
  font-size:.9rem;
}
.autofill__row em{font-style:normal;font-size:.76rem;font-weight:700;white-space:nowrap}
.autofill__row--ok{background:var(--brand-50);border-color:color-mix(in srgb,var(--brand-400) 30%,transparent)}
.autofill__row--ok em{color:var(--brand-700)}
.autofill__row--ok svg{color:var(--brand-600)}
.autofill__row--manual{background:var(--accent-50);border-color:#f0d3b0}
.autofill__row--manual em{color:var(--accent-600)}
.autofill__row--manual svg{color:var(--accent-600)}

.field--locked{
  background:var(--surface-2);border:1px solid var(--line);
  border-radius:12px;padding:12px 13px;margin-bottom:0;
}
.locked-value{
  display:flex;align-items:flex-start;gap:7px;margin:0 0 8px;
  color:var(--ink-900);font-size:.96rem;line-height:1.35;
}
.locked-value svg{color:var(--brand-600);flex:0 0 auto;margin-top:2px}
.field--locked .check-label{
  display:inline-flex;align-items:center;gap:7px;margin:0;
  font-size:.82rem;font-weight:650;color:var(--ink-500);cursor:pointer;
}
.field--locked .check-label input{width:auto;min-height:0;accent-color:var(--brand-600);cursor:pointer}
.field-hint{display:block;margin-top:5px;font-size:.76rem;color:var(--accent-600);font-weight:650}

html[data-theme='dark'] .autofill__row{background:var(--surface-2);border-color:var(--line)}
html[data-theme='dark'] .autofill__row--ok{background:#123028}
html[data-theme='dark'] .autofill__row--manual{background:#33240f;border-color:#6b4a1c}
html[data-theme='dark'] .field--locked{background:var(--surface-2)}
html[data-theme='dark'] .locked-value{color:var(--ink-900)}

@media (max-width:640px){
  .autofill__row{grid-template-columns:auto 1fr;row-gap:4px}
  .autofill__row em{grid-column:2;justify-self:start}
}

/* --- "did it get booked?" question --------------------------------------- */
.panel--ask{
  border:1px solid color-mix(in srgb,var(--brand-400) 34%,transparent);
  background:linear-gradient(140deg,var(--brand-50),var(--surface));
  box-shadow:var(--e2);
}
.outcome{margin-top:14px}
.outcome .field{max-width:420px}
.outcome__choices{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
.outcome__choices .button{min-height:46px}
html[data-theme='dark'] .panel--ask{
  background:linear-gradient(140deg,#12302a,var(--surface));border-color:#2f5a4e;
}
@media (max-width:640px){
  .outcome__choices{flex-direction:column}
  .outcome__choices .button{width:100%}
}

.autofill__row--info{background:var(--surface-2);border-color:var(--line);grid-template-columns:1fr auto}
.autofill__row--info em{color:var(--ink-900);font-weight:700}
.step--done dt{color:var(--brand-600);font-weight:800}
.step--skipped dt{color:var(--ink-400)}
.step--skipped dd{color:var(--ink-400)}
.step--pending dt{color:var(--accent-600);font-weight:700}
.alert__code{display:block;margin-top:6px;font-size:.76rem;opacity:.75;letter-spacing:.02em}

/* Before-your-visit list: the checkbox is the marker, so drop the bullet. */
ul.checklist{list-style:none;margin:14px 0 18px;padding:0;display:grid;gap:2px}
ul.checklist li{margin:0;padding:0}
ul.checklist li::marker{content:none}
ul.checklist .check-label{
  display:flex;align-items:flex-start;gap:10px;width:100%;
  padding:10px 12px;border-radius:10px;font-weight:500;
  color:var(--ink-900);line-height:1.45;cursor:pointer;
}
ul.checklist .check-label:hover{background:var(--surface-2)}
ul.checklist .check-label input{
  width:auto;min-height:0;margin-top:3px;flex:0 0 auto;
  accent-color:var(--brand-600);cursor:pointer;
}
ul.checklist .check-label input:checked+span{color:var(--ink-500);text-decoration:line-through}

/* The amber notice reads as a caution, which is right for "unavailable" but
   wrong for a directory that loaded correctly. */
.section .notice.notice--ok{
  background:var(--ok-50);border-color:transparent;color:#14543a;
}
.section .notice.notice--ok strong{color:#0f3f2b}
html[data-theme='dark'] .section .notice.notice--ok{color:#8fe3bd}
html[data-theme='dark'] .section .notice.notice--ok strong{color:#b6f0d6}
@media (prefers-color-scheme: dark){
  html:not([data-theme='light']) .section .notice.notice--ok{color:#8fe3bd}
  html:not([data-theme='light']) .section .notice.notice--ok strong{color:#b6f0d6}
}
</style>
"""


def _initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper()


def _conversation_date(record: Dict[str, Any]) -> str:
    created = str(record.get("createdAt", ""))
    return created[:10] if len(created) >= 10 else "Date unavailable"


def _secure_cookies(request: Request) -> bool:
    """Mark cookies Secure when the request arrived over HTTPS.

    Local development runs on plain HTTP, where a Secure cookie would simply be
    dropped, so the flag follows the actual scheme instead of being hardcoded.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return (forwarded or request.url.scheme) == "https"


def _user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("mediguide_token", "")
    return user_from_token(token) if token else None


async def _form_data(request: Request) -> Dict[str, str]:
    body = await request.body()
    values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: items[-1] for key, items in values.items()}


def _page(content: str, user: Optional[Dict[str, Any]] = None, title: str = "MediGuide") -> HTMLResponse:
    account = f"<a href='/ui'>Dashboard</a><a href='/ui/chat'>AI health chat</a><a href='/ui/doctors'>Find a doctor</a><a href='/ui/appointments'>Appointments</a><a href='/ui/notifications'>Notifications</a><a href='/ui/profile'>Profile</a><a href='/ui/logout'>Sign out</a>" if user else "<a href='/ui/login'>Sign in</a><a class='button secondary' href='/ui/register'>Create account</a>"
    theme_toggle = (
        "<button class='theme-toggle' type='button' data-theme-toggle "
        "aria-label='Switch between light and dark theme'>"
        f"{branding.icon('moon', size=16)}<span data-theme-label>Dark</span></button>"
    )
    polish = "<style>:root{--ink:#123d39;--muted:#5f7d76;--green:#1aa77c;--green-dark:#0e7f65;--mint:#eafaf4;--gold:#f3c96a;--paper:#f5fffb;--line:#dfeee8;--panel:#ffffff;--panel-soft:#f8fffc;--cyan:#b7f0dd;--danger:#ff8f84}html{scroll-behavior:smooth}body{line-height:1.55;background:linear-gradient(135deg,#f4fffb 0%,#eafaf4 40%,#f8fefc 100%);color:var(--ink);background-attachment:fixed}body:before{content:'';position:fixed;inset:0;pointer-events:none;opacity:.12;background-image:linear-gradient(rgba(26,167,124,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(26,167,124,.05) 1px,transparent 1px);background-size:40px 40px}h1,h2,h3{line-height:1.12;letter-spacing:-.01em}h1{font-size:clamp(2.2rem,5vw,4rem)}h2{font-size:1.8rem}a{color:var(--green)}a:hover{color:var(--green-dark);text-decoration:none}.shell{position:relative}.nav{padding:14px 16px;margin-bottom:28px;border:1px solid rgba(26,167,124,.12);border-radius:16px;background:rgba(255,255,255,.82);backdrop-filter:blur(18px);box-shadow:0 12px 30px rgba(18,61,57,.08)}.logo:hover{text-decoration:none;color:var(--ink)}.logo b{background:linear-gradient(135deg,#f5d77a,#e9b84e);box-shadow:0 10px 22px rgba(233,184,78,.28)}.navlinks a{padding:8px 10px;border-radius:9px;font-size:.9rem;color:var(--ink)}.navlinks a:hover{background:rgba(26,167,124,.08)}.hero{position:relative;overflow:hidden;border:1px solid rgba(26,167,124,.14);background:linear-gradient(135deg,#0f7d69 0%,#1ea97d 58%,#8fe8c8 100%);box-shadow:0 20px 45px rgba(26,167,124,.17)}.hero:after{content:'+';position:absolute;right:8%;top:12%;font:700 11rem 'Space Grotesk';line-height:1;color:rgba(255,255,255,.08);transform:rotate(12deg)}.hero h1{position:relative;z-index:1;color:#fff}.hero p{position:relative;z-index:1;color:#ecfff8}.hero .button{position:relative;z-index:2}.card,.panel,.stat{position:relative;overflow:hidden;background:linear-gradient(145deg,#ffffff,#f8fffc);border:1px solid rgba(26,167,124,.14);border-radius:18px;box-shadow:0 10px 25px rgba(18,61,57,.06)}.stat .eyebrow{color:var(--green)}.stat-value{color:var(--green)}.tag{background:#edfdf7;border:1px solid #d6f3e9}.button.secondary{background:#fff;color:var(--green);border-color:#cfeae2}.notice{background:#edfdf7;border:1px solid #d3f1e4;border-left:4px solid #1aa77c;color:#0d5e4f}.auth{background:#fff;box-shadow:0 12px 28px rgba(18,61,57,.06)}.auth .avatar{background:var(--mint);color:var(--green)};.2);color:var(--ink)}.card:before,.panel:before,.stat:before{content:'';position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,transparent,var(--green),var(--cyan),transparent);opacity:.58}.card:after,.panel:after{content:'';position:absolute;width:120px;height:120px;right:-70px;bottom:-78px;border:1px solid rgba(115,215,232,.12);border-radius:50%;box-shadow:0 0 0 12px rgba(115,215,232,.025),0 0 0 24px rgba(115,215,232,.018);pointer-events:none}.card:hover{transform:translateY(-4px);border-color:rgba(72,215,176,.55);box-shadow:0 20px 44px rgba(0,0,0,.3)}.panel{box-shadow:0 20px 48px rgba(0,0,0,.26)}.stat{padding:18px 20px}.stat:hover{border-color:rgba(115,215,232,.4)}.avatar{background:linear-gradient(135deg,#19443f,#163235);color:var(--green);border:1px solid rgba(72,215,176,.25);box-shadow:0 0 22px rgba(72,215,176,.08)}.tag{background:rgba(72,215,176,.12);color:var(--green);border:1px solid rgba(72,215,176,.2)}.muted,.appointment-meta,.stat-label{color:var(--muted)}.stat-value{font:700 1.9rem 'Space Grotesk';color:var(--green);margin:5px 0}.quick-card{min-height:150px}.appointment{border-left:3px solid var(--green);background:linear-gradient(145deg,rgba(19,43,39,.98),rgba(11,27,24,.98))}.appointment-title{font:600 1.05rem 'Space Grotesk';color:var(--ink)}.appointment-meta{margin-top:4px}.notice{background:rgba(242,198,109,.1);border:1px solid rgba(242,198,109,.24);border-left:4px solid var(--gold);color:#f3d99b;box-shadow:0 12px 28px rgba(0,0,0,.16)}.notice.danger{background:rgba(218,95,82,.13);border-color:rgba(255,143,132,.3);border-left-color:var(--danger);color:#ffb3a8}.field{margin:14px 0}input,select,textarea{width:100%;background:#0b1d1a;color:var(--ink);border:1px solid #31534d;border-radius:12px;box-shadow:inset 0 1px 2px rgba(0,0,0,.18),0 4px 14px rgba(0,0,0,.08)}input::placeholder,textarea::placeholder{color:#66807b}input:focus,select:focus,textarea:focus{outline:3px solid rgba(72,215,176,.16);border-color:var(--green)}button,.button{display:inline-block;text-decoration:none;border:1px solid rgba(242,198,109,.55);box-shadow:0 8px 20px rgba(0,0,0,.18);transition:transform .18s ease,filter .18s ease,box-shadow .18s ease}button:hover,.button:hover{filter:brightness(1.08);text-decoration:none;transform:translateY(-2px);box-shadow:0 12px 25px rgba(0,0,0,.25)}.button.secondary{background:rgba(72,215,176,.1);border-color:rgba(72,215,176,.32);color:var(--green)}.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.grid{align-items:stretch}.grid>.card,.grid>.panel,.grid>.stat{height:100%}.footer{color:#718d87;border-color:#203b36}@media(max-width:680px){.nav{padding:12px;margin-bottom:18px}.navlinks{gap:3px}.navlinks a{font-size:.8rem;padding:7px 6px}.hero{padding:28px 22px}.hero:after{font-size:7rem;right:2%}.formgrid{grid-template-columns:1fr}.stat{padding:14px}.quick-card{min-height:0}.card,.panel{border-radius:15px}}</style>"
    landing = "<style>.landing-nav{display:flex;justify-content:center;gap:28px;margin:-10px 0 24px;font-size:.9rem}.landing-nav a{color:var(--muted);font-weight:700}.landing-hero{display:grid;grid-template-columns:1.1fr .9fr;gap:36px;align-items:center;background:linear-gradient(135deg,#eaf8f3,#d8f0e9 58%,#dfeafa);border:1px solid #c8e6dd;color:#173c37;box-shadow:0 22px 55px rgba(39,107,91,.13)}.landing-hero h1{max-width:680px;color:#123d37}.landing-hero p{color:#55736d}.hero-copy{position:relative;z-index:2}.ai-badge{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;border-radius:99px;background:rgba(255,255,255,.76);border:1px solid #c8e6dd;color:#138a78;font-weight:700;font-size:.78rem;margin-bottom:20px}.safety-note{font-size:.78rem!important;margin:20px 0 0;max-width:470px}.care-preview{background:rgba(255,255,255,.86);border:1px solid rgba(111,178,166,.4);border-radius:20px;padding:17px;box-shadow:0 20px 35px rgba(38,99,88,.13);transform:rotate(1.5deg)}.chat-top{display:flex;align-items:center;gap:8px;padding-bottom:14px;border-bottom:1px solid var(--line);font-size:.86rem}.chat-top .muted{margin-left:auto;font-size:.75rem}.status-dot{width:9px;height:9px;border-radius:50%;background:#36b98f;box-shadow:0 0 0 4px rgba(54,185,143,.14)}.chat-bubble{max-width:88%;padding:10px 12px;border-radius:13px;margin:12px 0;font-size:.84rem;line-height:1.4}.chat-bubble.ai{background:#eef8f5;color:#24564e;border-bottom-left-radius:4px}.chat-bubble.patient{margin-left:auto;background:#e8eefb;color:#3d5270;border-bottom-right-radius:4px}.chat-input{border:1px solid var(--line);border-radius:10px;color:#8ba19d;padding:10px 12px;font-size:.78rem}.chat-input span{float:right;color:#138a78;font-weight:700}.landing-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;margin:24px 0 4px;border:1px solid var(--line);border-radius:16px;background:var(--line);overflow:hidden}.landing-stats div{background:#fff;padding:20px;text-align:center}.landing-stats strong{display:block;color:#138a78;font:700 1.8rem 'Space Grotesk'}.landing-stats span{display:block;color:var(--muted);font-size:.8rem;margin-top:4px}.section-intro{display:flex;justify-content:space-between;gap:30px;align-items:end;margin-bottom:18px}.section-intro>p{max-width:340px}.feature-card{background:#fff;border-color:var(--line);box-shadow:0 12px 28px rgba(31,91,78,.07)}.feature-card h3{color:#173c37}.icon-tile{display:grid;place-items:center;width:44px;height:44px;border-radius:13px;background:#e4f5ef;color:#138a78;font-size:1.4rem;font-weight:700;border:1px solid #cbe8df}.process-section{padding:30px;border-radius:18px;background:#edf7f4;border:1px solid var(--line)}.process-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;margin-top:24px}.process-step{position:relative}.process-step:not(:last-child):after{content:'→';position:absolute;right:-20px;top:4px;color:#9dc6bc;font-size:1.4rem}.process-step span{color:#138a78;font:700 1rem 'Space Grotesk'}.trust-band{display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center;padding:30px;border:1px solid #d9def1;border-radius:18px;background:linear-gradient(135deg,#f7f8ff,#eef8f5)}.trust-points{display:grid;gap:12px}.trust-points span{padding:13px 15px;border-radius:11px;background:rgba(255,255,255,.86);border:1px solid var(--line);color:#3e655e;font-size:.88rem}.cta-band{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:20px;padding:26px 30px;border-radius:20px;background:linear-gradient(120deg,#123f39,#1b7669);color:white;box-shadow:0 20px 42px rgba(19,86,74,.22)}.cta-mark{display:grid;place-items:center;width:58px;height:58px;border-radius:18px;background:#f4c66f;color:#173c37;font-size:1.7rem}.cta-copy h2{margin:5px 0;color:white}.cta-proof{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;color:#d5f2ea;font-size:.75rem}.landing-footer{display:grid;grid-template-columns:1.4fr .7fr .7fr 1.2fr;gap:28px;margin-top:42px;padding:30px 0 10px;border-top:1px solid var(--line)}.landing-footer a{display:block;font-size:.84rem;color:var(--muted);margin:7px 0}@media(prefers-color-scheme:dark){.landing-hero{background:linear-gradient(135deg,#123b35,#1d6559 58%,#20354c);border-color:#315950}.landing-hero h1{color:#edf7f4}.landing-hero p{color:#c1d8d2}.landing-nav a{color:#9bbab3}.care-preview,.landing-stats div,.feature-card,.trust-points{background:#10231f;color:#edf7f4;border-color:#31534d}.chat-bubble.ai{background:#173b35;color:#cce9df}.chat-bubble.patient{background:#233550;color:#d6e0f4}.process-section{background:#102b26;border-color:#31534d}.trust-band{background:linear-gradient(135deg,#142535,#102b26);border-color:#31534d}}@media(max-width:680px){.landing-nav{gap:14px;justify-content:flex-start;overflow:auto;white-space:nowrap}.landing-hero{grid-template-columns:1fr;gap:12px}.care-preview{transform:none}.landing-stats{grid-template-columns:1fr}.section-intro,.trust-band,.cta-band{display:block}.process-grid{grid-template-columns:1fr;gap:18px}.process-step:not(:last-child):after{content:'↓';right:auto;top:auto;bottom:-18px;left:4px}.trust-points{margin-top:22px}.cta-button{margin-top:18px}.landing-footer{grid-template-columns:1fr 1fr;gap:22px}}</style>"
    landing_fix = "<style>.logo b{display:grid;place-items:center;width:38px;height:38px;border-radius:13px;background:linear-gradient(145deg,#f5c96f,#e79b3e);color:#123d37;font-size:1.35rem;font-weight:700;line-height:1;box-shadow:0 7px 18px rgba(214,150,47,.25),inset 0 1px 0 rgba(255,255,255,.55);transform:rotate(-8deg)}.logo span{letter-spacing:-.02em}.landing-hero .button,.hero .actions .button{min-height:46px;display:inline-flex;align-items:center;justify-content:center;padding:12px 20px;border-radius:11px;background:#138a78;color:#fff;border:1px solid #138a78;line-height:1;white-space:nowrap;font-weight:700}.landing-hero .button:hover,.hero .actions .button:hover{background:#0a665b;color:#fff}.landing-hero .button.secondary,.hero .actions .button.secondary{background:rgba(255,255,255,.78);color:#0a665b;border-color:#9ccfc3}.landing-hero .button.secondary:hover,.hero .actions .button.secondary:hover{background:#fff;color:#07574d}.hero>p:has(button){margin-top:24px}.hero>p:has(button) a{display:inline-block}.hero>p:has(button) button{min-height:46px;padding:12px 20px;border-radius:11px;background:#f2c66d;color:#173c37;border:1px solid #f2c66d;font-weight:700;box-shadow:0 10px 22px rgba(0,0,0,.2)}.hero>p:has(button) button:hover{background:#e7b957;transform:translateY(-2px)}.doctor h3{font-size:1.38rem;line-height:1.12;margin:12px 0 6px;color:var(--ink);letter-spacing:-.015em}.doctor .tag{font-size:.78rem;padding:6px 10px}.doctor .avatar{width:52px;height:52px;font-size:1.05rem}.doctor a{font-size:.88rem}.cta-band{min-height:150px}.cta-button{min-height:48px;justify-content:center;font-size:.95rem;font-weight:700;letter-spacing:0}.cta-button span{display:block}.cta-copy h2{max-width:580px}.section-intro h2,.process-section h2,.trust-band h2{max-width:680px}.landing-footer a{line-height:1.4}.auth{max-width:560px;padding:32px;margin:48px auto}.auth h1{font-size:clamp(2.5rem,5.5vw,4rem);line-height:1.02;margin:4px 0;color:var(--ink);letter-spacing:-.02em}.auth-brand{display:flex;align-items:center;gap:14px}.auth-brand .avatar{flex:0 0 46px}.auth-lead{font-size:1rem;max-width:430px;margin:12px 0 24px}.auth-options{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:2px 0 18px;font-size:.82rem}.check-label{display:flex;flex-direction:row;align-items:center;gap:7px;font-weight:500;color:var(--muted)}.check-label input{width:auto;accent-color:var(--green)}.auth-submit{width:100%;min-height:48px}.auth-note{font-size:.78rem;margin:14px 0}.auth-switch{text-align:center;margin-top:18px}.auth .formgrid{gap:10px}@media(max-width:680px){.landing-hero .actions,.hero .actions{width:100%}.landing-hero .button,.hero .actions .button{flex:1;min-width:0;padding-inline:12px}.hero>p:has(button) button{width:100%}.doctor h3{font-size:1.22rem}.cta-band{padding:24px 22px}.cta-button{width:100%;white-space:normal;text-align:center}.cta-button span{max-width:220px}.cta-copy h2{font-size:1.55rem}.auth{margin:28px auto;padding:24px 18px}.auth h1{font-size:2.7rem}.auth-options{align-items:flex-start;flex-direction:column}}</style>"
    theme = """<style>
.theme-toggle { display: inline-flex !important; align-items: center; justify-content: center; min-height: 36px; padding: 8px 11px; border: 1px solid #b9dacc; border-radius: 10px; background: #ffffff; color: #123d39; font: 700 .8rem 'DM Sans', sans-serif; cursor: pointer; position: static; white-space: nowrap; flex: 0 0 auto; box-shadow: 0 8px 18px rgba(18, 61, 57, .12); }
.theme-toggle:hover { border-color: #1aa77c; transform: translateY(-1px); }
.nav-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; min-width: 0; flex: 1; }
.nav-actions .navlinks { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; justify-content: flex-end; min-width: 0; }
.nav-actions .navlinks a { display: inline-flex; align-items: center; min-height: 34px; padding: 7px 10px; border: 1px solid #d1e7dc; border-radius: 10px; background: rgba(255, 255, 255, .72); color: #123d39; font-size: .82rem; white-space: nowrap; box-shadow: 0 5px 12px rgba(18, 61, 57, .05); transition: background .18s ease, border-color .18s ease, transform .18s ease; }
.nav-actions .navlinks a:hover { border-color: #7dc9a9; background: #ffffff; transform: translateY(-1px); }
.nav-actions .navlinks a.button { border-color: #9bd8bd; background: #e9f9f1; }
.page-tools { display: flex; justify-content: flex-start; margin: -12px 0 20px; }
.back-button { display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 9px 14px; border: 1px solid #b9dacc; border-radius: 10px; background: #ffffff; color: #123d39; font: 700 .86rem 'DM Sans', sans-serif; cursor: pointer; box-shadow: 0 6px 16px rgba(18, 61, 57, .07); }
.back-button::before { content: "←"; font-size: 1.1rem; line-height: 1; }
.back-button:hover { border-color: #1aa77c; color: #0e7f65; transform: translateX(-2px); }
html[data-theme='dark'] body { background: linear-gradient(135deg, #0e1817 0%, #132522 48%, #101b1a 100%); color: #e8f3ee; }
html[data-theme='dark'] body:before { opacity: .04; }
html[data-theme='dark'] .nav, html[data-theme='dark'] .card, html[data-theme='dark'] .panel, html[data-theme='dark'] .stat, html[data-theme='dark'] .appointment, html[data-theme='dark'] .care-preview { background: #172825; border-color: #2c4941; color: #e8f3ee; box-shadow: 0 14px 32px rgba(0, 0, 0, .24); }
html[data-theme='dark'] .nav { background: rgba(23, 40, 37, .92); }
html[data-theme='dark'] .navlinks a, html[data-theme='dark'] .logo { color: #e8f3ee; }
html[data-theme='dark'] .nav-actions .navlinks a { background: #203c35; border-color: #3c6256; color: #e8f3ee; }
html[data-theme='dark'] .nav-actions .navlinks a:hover { background: #294d42; border-color: #6b9b87; }
html[data-theme='dark'] .nav-actions .navlinks a.button { background: #245344; border-color: #5d9b7f; }
html[data-theme='dark'] .directory-panel, html[data-theme='dark'] .directory-filters, html[data-theme='dark'] .directory-search { background: #172825; border-color: #2c4941; }
html[data-theme='dark'] .doctor-result { background: linear-gradient(145deg, #1b302c, #14231f); border-color: #376357; }
html[data-theme='dark'] .landing-hero { background: linear-gradient(135deg, #152b27, #1b3a32 58%, #203f39); border-color: #31554a; color: #e8f3ee; }
html[data-theme='dark'] .landing-hero h1, html[data-theme='dark'] .landing-hero p { color: #e8f3ee; }
html[data-theme='dark'] .landing-stats, html[data-theme='dark'] .landing-stats div, html[data-theme='dark'] .landing-footer { background: #172825; border-color: #2c4941; color: #e8f3ee; }
html[data-theme='dark'] .muted, html[data-theme='dark'] .appointment-meta { color: #a9c0b7; }
html[data-theme='dark'] input, html[data-theme='dark'] select, html[data-theme='dark'] textarea { background: #10201d !important; color: #e8f3ee !important; border-color: #3a6256 !important; }
html[data-theme='dark'] input::placeholder, html[data-theme='dark'] textarea::placeholder { color: #8da99e !important; }
html[data-theme='dark'] .notice { background: #1b332e; color: #d8eee5; border-color: #3b8069; }
html[data-theme='dark'] .tag { background: #21483d; color: #b9f0d7; }
html[data-theme='dark'] .chat-bubble.ai { background: #204138; color: #d9f1e7; }
html[data-theme='dark'] .chat-bubble.patient { background: #273b4b; color: #e1edf6; }
html[data-theme='dark'] .chat-input, html[data-theme='dark'] .question-panel { background: #14231f; color: #d9eee5; border-color: #587c6d; }
html[data-theme='dark'] .chat-session-label { color: #8fe8c8; }
html[data-theme='dark'] .theme-toggle { background: #203c35; color: #e8f3ee; border-color: #4c7667; }
html[data-theme='dark'] .back-button { background: #203c35; color: #e8f3ee; border-color: #4c7667; }
html[data-theme='dark'] .conversation-card h3 { color: #e8f3ee; }
html[data-theme='dark'] .conversation-date { color: #a9c0b7; }
html[data-theme='dark'] .answer-panel { border-color: #5c9bd1; }
html[data-theme='dark'] .answer-row { border-color: #2c4941; }
html[data-theme='dark'] .answer-row span { color: #a9c0b7; }
html[data-theme='dark'] .review-panel { background: linear-gradient(135deg, #302719, #211d16); border-color: #d39a36; box-shadow: 0 16px 34px rgba(0, 0, 0, .25); }
html[data-theme='dark'] .review-panel h2 { color: #f0c978; }
@media (max-width: 1050px) { .nav { align-items: flex-start; gap: 12px; flex-wrap: wrap; } .nav-actions { width: 100%; justify-content: space-between; } .nav-actions .navlinks { justify-content: flex-start; flex-wrap: wrap; } }
@media (max-width: 560px) { .theme-toggle { top: 14px; right: 14px; min-height: 36px; padding: 7px 10px; font-size: .76rem; } }
</style>"""
    theme_script = """<script>
const themeToggle = document.querySelector('[data-theme-toggle]');
const storedTheme = localStorage.getItem('mediguide-theme') || 'light';
document.documentElement.dataset.theme = storedTheme;
function updateThemeLabel() {
    const label = themeToggle?.querySelector('[data-theme-label]');
    if (label) label.textContent = document.documentElement.dataset.theme === 'dark' ? 'Light' : 'Dark';
}
updateThemeLabel();
themeToggle?.addEventListener('click', () => {
    const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = nextTheme;
    localStorage.setItem('mediguide-theme', nextTheme);
    updateThemeLabel();
});
document.querySelector('[data-back-button]')?.addEventListener('click', () => {
    const referrer = document.referrer ? new URL(document.referrer) : null;
    if (referrer && referrer.origin === window.location.origin && window.history.length > 1) window.history.back();
    else window.location.href = '/ui';
});
</script>"""
    page_tools = "" if title == "MediGuide" else "<div class='page-tools'><button class='back-button' type='button' data-back-button aria-label='Return to the previous page'>Back</button></div>"
    response = HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{branding.head_links()}<meta http-equiv='cache-control' content='no-store'><title>{escape(title)}</title>{CSS}{polish}{landing}{landing_fix}{theme}{VISUAL_LAYER}</head><body><main class='shell'><nav class='nav' aria-label='Main navigation'>{branding.logo_lockup()}<div class='nav-actions'><div class='navlinks'>{account}</div>{theme_toggle}</div></nav>{page_tools}{content}<div class='footer'>General health information and appointment support. Not a replacement for professional medical advice.</div></main>{theme_script}</body></html>")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _directory_stat(city: str = "Lahore") -> str:
    """Doctor count for the dashboard, from the warmed cache only.

    Never triggers a live fetch: the dashboard must not wait on Oladoc, and
    showing "0" while the directory is still warming would be a lie.
    """
    from . import oladoc_provider

    count = oladoc_provider.cached_directory_count(city)
    return str(count) if count is not None else "--"


def _oladoc_directory(city: str = "Lahore") -> list[Dict[str, Any]]:
    """The doctors shown when no search has been entered.

    Read live from Oladoc. There is deliberately no local doctor list behind
    this: a doctor who has no Oladoc profile cannot be opened or booked on
    Oladoc, so listing one would offer a booking that cannot happen.
    """
    from . import oladoc_provider

    return oladoc_provider.directory_doctors(city)


def _doctor_diseases(specialist_id: str) -> str:
    matches = [entry["label"] for entry in KNOWLEDGE if specialist_id in entry.get("specialist_ids", [])]
    return ", ".join(matches) or "general care"


def _specialty_key(value: Any) -> str:
    normalized = " ".join(str(value or "").lower().replace("&", "and").split())
    aliases = {
        "neurologist": "neurology",
        "neuro physician": "neurology",
        "cardiologist": "cardiology and emergency",
        "cardiology": "cardiology and emergency",
        "gastroenterologist": "gastroenterology",
        "dermatologist": "dermatology",
        "general physician": "primary care",
        "primary care physician": "primary care",
    }
    return aliases.get(normalized, normalized)


def _canonical_doctor(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the canonical record for a card item, building it once if needed.

    Live search results already carry their canonical identity. Directory
    specialists are canonicalized from their own id. Anything that cannot be
    given a stable identity returns None and is never offered for booking.
    """
    if not item:
        return None
    if item.get("doctor_id"):
        return item
    try:
        if item.get("id"):
            return identity.from_specialist(item)
        return identity.from_live_search_result(item)
    except identity.DoctorIdentityError as exc:
        logger.warning("Doctor cannot be booked without a stable identity: %s", exc)
        return None


def _register_doctor(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Persist the canonical doctor so later stages resolve it by id, server-side."""
    doctor = _canonical_doctor(item)
    if not doctor:
        return None
    try:
        db.upsert_doctor(doctor)
    except ValueError as exc:
        logger.warning("Could not register canonical doctor: %s", exc)
        return None
    return doctor


def _resolve_booking_target(specialist: Optional[Dict[str, Any]]) -> str:
    """Booking link for a doctor, keyed on canonical id only.

    A doctor is never matched to a different doctor by name or specialty: an
    entry without a stable identity simply gets no booking link.
    """
    doctor = _canonical_doctor(specialist)
    if not doctor:
        return ""
    return f"/ui/book/{quote(str(doctor['doctor_id']), safe='')}"


def _doctor_actions(doctor: Dict[str, Any]) -> str:
    """The four actions every valid doctor result exposes.

    One implementation, used by directory doctors and live search results alike,
    all driven from the same canonical doctor object. Laid out as a 2x2 grid so
    the actions never outweigh the doctor's own details.
    """
    doctor_id = quote(str(doctor.get("doctor_id", "")), safe="")
    profile_url = doctor.get("oladoc_profile_url") or doctor.get("oladoc_directory_url") or ""
    directory_url = doctor.get("oladoc_directory_url") or profile_url or ""
    name = escape(doctor.get("doctor_name", "this doctor"))

    return (
        "<div class='doctor-actions'>"
        f"<a class='act act--primary' href='/ui/book/{doctor_id}' "
        f"aria-label='Book an appointment with {name} using Playwright' title='Book with Playwright'>"
        f"{branding.icon('calendar', size=16)}<span>Book</span></a>"
        f"<a class='act' href='/ui/slots/{doctor_id}' "
        f"aria-label='Check live appointment slots for {name}' title='Check Live Slots'>"
        f"{branding.icon('clock', size=16)}<span>Live slots</span></a>"
        f"<a class='act' href='{escape(profile_url)}' target='_blank' rel='noopener noreferrer' "
        f"aria-label='Open the live provider profile for {name} in a new tab' title='View Live Profile'>"
        f"{branding.icon('user', size=16)}<span>Profile</span></a>"
        f"<a class='act' href='{escape(directory_url)}' target='_blank' rel='noopener noreferrer' "
        f"aria-label='Open {name} on Oladoc in a new tab' title='Open Oladoc'>"
        f"{branding.icon('external', size=16)}<span>Oladoc</span></a>"
        "</div>"
    )


def _rating_stars(rating_label: str) -> str:
    """Render a rating as stars. Returns nothing when the provider gave no rating.

    Showing "Rating not shown" on every card is noise; absence of a rating is
    better communicated by absence of the element.
    """
    match = re.search(r"(\d(?:\.\d)?)", str(rating_label or ""))
    if not match:
        return ""
    try:
        value = float(match.group(1))
    except ValueError:
        return ""
    if not 0 < value <= 5:
        return ""
    filled = int(round(value))
    stars = "".join(
        f"<span class='star{' star--on' if i < filled else ''}' aria-hidden='true'>&#9733;</span>"
        for i in range(5)
    )
    return (
        f"<span class='rating' title='Rated {value} out of 5'>"
        f"{stars}<span class='rating__value'>{value:g}</span></span>"
    )


def _doctor_card(doctor: Dict[str, Any], *, shares_name: bool = False) -> str:
    """One card markup for every doctor, whatever the source."""
    name = str(doctor.get("doctor_name", "Doctor"))
    specialty = str(doctor.get("specialty", ""))
    location = str(doctor.get("location", "") or doctor.get("city", ""))
    clinic = str(doctor.get("clinic", ""))
    experience = str(doctor.get("experience", ""))
    is_live = str(doctor.get("source", "")).startswith("live") or doctor.get("source") == "Oladoc live"

    # A verified badge, not internal vocabulary. The exact identity strength is
    # engineering detail and belongs in the tooltip, not the card face.
    strength = str(doctor.get("identity_strength", ""))
    verified_title = {
        "provider_doctor_id": "Identity confirmed by this doctor's Oladoc provider id",
        "profile_url": "Identity confirmed by this doctor's exact Oladoc profile",
        "name_specialty": "Listed in the verified directory; matched on name and specialty",
    }.get(strength, "Listed in the verified directory")
    badge = (
        f"<span class='badge {'badge--live' if is_live else ''}' title='{escape(verified_title)}'>"
        f"{branding.icon('shield', size=13)}{'Live on Oladoc' if is_live else 'Verified'}</span>"
    )

    meta_bits = [bit for bit in (location, clinic, experience) if bit]
    meta = (
        "<p class='doctor-card__meta'>"
        + " <span class='dot'></span> ".join(escape(b) for b in meta_bits)
        + "</p>"
    ) if meta_bits else ""

    rating = _rating_stars(str(
        doctor.get("rating_label") or doctor.get("ratingLabel") or doctor.get("rating") or ""
    ))
    duplicate_note = (
        "<p class='doctor-card__warn'>"
        f"{branding.icon('alert', size=13)}Another listed doctor shares this name. "
        "This card is tied to one specific Oladoc profile.</p>" if shares_name else ""
    )

    return (
        f"<article class='card doctor-card' data-doctor-id='{escape(str(doctor.get('doctor_id', '')))}' "
        f"data-name='{escape(name.lower())}' data-specialty='{escape(specialty.lower())}' "
        f"data-city='{escape(location.lower())}' "
        f"data-disease='{escape(str(doctor.get('normalized_specialty', specialty)).lower())}' "
        f"data-rating='{escape(str(doctor.get('rating', '') or 0))}'>"
        f"<div class='doctor-card__top'>"
        f"<span class='avatar' aria-hidden='true'>{_initials(name)}</span>"
        f"<div class='doctor-card__id'>"
        f"<h3 class='doctor-card__name'>{escape(name)}</h3>"
        f"<p class='doctor-card__specialty'>{escape(specialty)}</p></div>"
        f"{badge}</div>"
        f"<div class='doctor-card__body'>{meta}"
        f"<div class='doctor-card__tags'>{rating}"
        f"<span class='tag tag--soft'>Pay at clinic</span></div>"
        f"{duplicate_note}</div>"
        f"{_doctor_actions(doctor)}"
        "</article>"
    )


def _doctor_cards(specialists: Optional[list[Dict[str, Any]]] = None, query: str = "") -> str:
    """Render a result grid. Doctors are registered first so their ids resolve later."""
    specialists = specialists if specialists is not None else []
    canonical = [item for item in (_register_doctor(entry) for entry in specialists) if item]
    if not canonical:
        return (
            "<div class='empty-state'><h3>No doctors to show</h3>"
            "<p class='card-meta'>No result had a verifiable provider identity, so none can be booked safely.</p></div>"
        )
    duplicates = identity.find_duplicate_names(canonical)
    cards = "".join(
        _doctor_card(doctor, shares_name=(doctor.get("normalized_name") or "") in duplicates)
        for doctor in canonical
    )
    return f"<div class='grid doctor-grid'>{cards}</div>"


def register_ui_routes(app: Any) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def ui_home(request: Request):
        user = _user(request)
        if not user:
            content = "<div class='landing-nav'><a href='#home'>Home</a><a href='#features'>Features</a><a href='#how-it-works'>How it works</a><a href='#trust'>Trust & safety</a></div><section class='hero landing-hero' id='home'><div class='hero-copy'><div class='eyebrow'>MediGuide care platform</div><h1>Clarity for your next healthcare step.</h1><p>Understand what may need attention, find the right type of care, and keep your appointments organized in one calm, private workspace.</p><div class='actions'><a class='button' href='/ui/register'>Get started</a><a class='button secondary' href='/ui/login'>Sign in</a></div><p class='safety-note'>For general health information only. Emergencies require local emergency services.</p></div><div class='care-preview'><div class='chat-top'><span class='status-dot'></span><strong>MediGuide care team</strong><span class='muted'>Clinical support</span></div><div class='chat-bubble ai'>Tell me what you are experiencing. I can help you plan a safer next step.</div><div class='chat-bubble patient'>Headache and nausea since yesterday.</div><div class='chat-bubble ai'>I can share precautions and help route you to the most appropriate care.</div><div class='chat-input'>Describe your symptoms <span></span></div></div></section><section class='landing-stats'><div><strong>11</strong><span>care guidance topics</span></div><div><strong>41</strong><span>trained disease classes</span></div><div><strong>100%</strong><span>patient-facing safety focus</span></div></section><section class='section' id='features'><div class='section-intro'><div><div class='eyebrow'>A clearer care journey</div><h2>Everything you need to move forward</h2></div><p class='muted'>Thoughtful tools for understanding, planning, and staying organized between visits.</p></div><div class='grid feature-grid'><article class='card feature-card'><div class='icon-tile'></div><h3>AI health chat</h3><p class='muted'>Describe symptoms naturally and receive precautions, follow-up questions, urgency guidance, and care routing.</p><a href='/ui/register'>Explore care guidance →</a></article><article class='card feature-card'><div class='icon-tile'></div><h3>Specialist matching</h3><p class='muted'>Review credentials, experience, ratings, location, and the reason a specialist is recommended.</p><a href='/ui/register'>Meet your care team →</a></article><article class='card feature-card'><div class='icon-tile'></div><h3>Appointment planning</h3><p class='muted'>Find bookable slots, confirm the details, and keep your visit history in one place.</p><a href='/ui/register'>Plan an appointment →</a></article></div></section><section class='section process-section' id='how-it-works'><div class='eyebrow'>How it works</div><h2>From uncertainty to an organized next step</h2><div class='process-grid'><div class='process-step'><span>01</span><h3>Share what you notice</h3><p class='muted'>Tell the care guide about your symptoms in your own words.</p></div><div class='process-step'><span>02</span><h3>Review your guidance</h3><p class='muted'>See precautions, urgency signals, and the most relevant care direction.</p></div><div class='process-step'><span>03</span><h3>Plan with confidence</h3><p class='muted'>Choose a specialist, schedule a visit, and keep the details accessible.</p></div></div></section><section class='section trust-band' id='trust'><div><div class='eyebrow'>Trust & safety</div><h2>Helpful by design. Honest about limits.</h2><p class='muted'>MediGuide provides general health information and care routing. It does not diagnose conditions or replace a qualified medical professional.</p></div><div class='trust-points'><span>✓ Internal disease labels stay private</span><span>✓ Medical information is encrypted</span><span>✓ OTP and CAPTCHA stay with the patient</span></div></section><section class='section cta-band'><div class='cta-mark'></div><div class='cta-copy'><div class='eyebrow'>Your private care workspace</div><h2>Make your next step feel more manageable.</h2><p>Save your guidance, organize visits, and keep your care details close.</p><div class='cta-proof'><span>✓ Free to get started</span><span>✓ Private by design</span></div></div><a class='button cta-button' href='/ui/register'><span>Create your free account</span><b>→</b></a></section><footer class='landing-footer'><div><strong>MediGuide<span> AI</span></strong><p class='muted'>A calmer way to organize your care journey.</p></div><div><strong>Explore</strong><a href='#features'>Features</a><a href='#how-it-works'>How it works</a><a href='#trust'>Trust & safety</a></div><div><strong>Account</strong><a href='/ui/login'>Sign in</a><a href='/ui/register'>Get started</a></div><div><strong>Important</strong><p class='muted'>General information only. Not a replacement for medical advice or emergency care.</p></div></footer>"
            return _page(content)
        appointments = db.get_appointments_for_user(user["id"])
        confirmed_appointments = [item for item in appointments if item.get("status") == "confirmed"]
        recent_items = []
        for item in reversed(appointments):
            cancel_button = ""
            if item.get("status") not in {"cancelled", "completed"}:
                cancel_button = f"<form method='post' action='/ui/appointments/{escape(item['id'])}/cancel'><button class='button secondary' type='submit'>Cancel appointment</button></form>"
            recent_items.append(
                f"<div class='card appointment'><div class='appointment-title'>{escape(item['specialistName'])}</div><div class='appointment-meta'>{escape(item['specialistSpecialty'])} · {escape(item['date'])} at {escape(item['time'])}</div><div class='appointment-meta'>Reference: {escape(item.get('bookingReference', 'Pending'))}</div><div class='appointment-meta'>Reason: {escape(item.get('reason', 'Not provided'))}</div><div class='actions'><span class='tag'>{escape(item.get('status', 'confirmed'))}</span>{cancel_button}</div></div>"
            )
        recent = "".join(recent_items) or "<div class='notice'>You have no appointments yet. Choose a specialist to get started.</div>"
        content = f"<section class='hero'><div class='eyebrow'>MediGuide care desk</div><h1>Good to see you, {escape(user['fullName'].split()[0])}.</h1><p>Find a specialist, plan a physical appointment, and keep every confirmation in one place.</p><div class='actions'><a class='button' href='/ui/chat'>Open AI health chat</a><a class='button secondary' href='/ui/doctors'>Find an Oladoc doctor</a><a class='button secondary' href='/ui/profile'>Manage profile</a></div></section><section class='section'><div class='grid'><div class='stat'><div class='eyebrow'>Oladoc doctors</div><div class='stat-value'>{_directory_stat()}</div><div class='stat-label'>Pay-at-clinic profiles</div></div><div class='stat'><div class='eyebrow'>Confirmed appointments</div><div class='stat-value'>{len(confirmed_appointments)}</div><div class='stat-label'>Active confirmed visits</div></div><div class='stat'><div class='eyebrow'>Care location</div><div class='stat-value'>{escape(user.get('city', 'Lahore'))}</div><div class='stat-label'>Your preferred city</div></div></div></section><section class='section'><div class='eyebrow'>Start with what you need</div><h2>Plan your next step</h2><div class='grid'><a class='card quick-card' href='/ui/chat'><div class='avatar'>01</div><h3>Get care guidance</h3><p class='muted'>Describe symptoms and review precautions with specialist routing.</p></a><a class='card quick-card' href='/ui/doctors'><div class='avatar'>02</div><h3>Find a specialist</h3><p class='muted'>Search Oladoc doctors by symptoms, specialty, rating, and city.</p></a></div></section><section class='section'><div class='eyebrow'>Your care history</div><h2>Appointments</h2>{recent}</section>"
        return _page(content, user)

    @app.get("/ui/doctors", response_class=HTMLResponse)
    async def ui_doctors(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        query = str(request.query_params.get("q", "")).strip()
        requested_specialty = str(request.query_params.get("specialty", "")).strip()
        live_error = ""
        city = str(request.query_params.get("city", "Lahore")) or "Lahore"
        try:
            if query or requested_specialty:
                doctors = await asyncio.to_thread(
                    search_live_oladoc_doctors, query, city, requested_specialty
                )
            else:
                doctors = await asyncio.to_thread(_oladoc_directory, city)
        except RuntimeError as exc:
            # Oladoc is the only source, so there is nothing to fall back to.
            # Say so rather than showing doctors that cannot be booked.
            live_error = str(exc)
            doctors = []
        specialties = sorted({str(item["specialty"]) for item in doctors})
        cities = sorted({str(item.get("city", "")) for item in doctors if item.get("city")})
        diseases = sorted({_doctor_diseases(str(item.get("id", ""))).split(", ")[0] if item.get("id") else "general care" for item in doctors})
        options = lambda values: "".join(f"<option value='{escape(value.lower())}'>{escape(value)}</option>" for value in values)
        cal_url = os.getenv("CAL_EMBED_URL", "").strip()
        cal_panel = f"<section class='section'><div class='panel'><div class='eyebrow'>Optional Cal.com scheduling</div><h2>Choose a real-time calendar</h2><p class='muted'>Cal.com is shown only when an approved embed URL is configured. Oladoc remains the source of truth for the doctors listed here.</p><iframe title='Cal.com appointment calendar' src='{escape(cal_url)}' loading='lazy' style='width:100%;min-height:620px;border:0;border-radius:12px'></iframe></div></section>" if cal_url else "<section class='section'><div class='notice'><strong>Live availability:</strong> choose a doctor and date to check the verified Oladoc schedule. MediGuide does not invent open slots.</div></section>"
        script = """<script>
const search = document.querySelector('#doctor-search');
const controls = [...document.querySelectorAll('[data-doctor-filter]')];
const cards = [...document.querySelectorAll('.doctor-card')];
const count = document.querySelector('#doctor-count');
function filterDoctors() {
const term = search.value.trim().toLowerCase();
const values = Object.fromEntries(controls.map(control => [control.name, control.value]));
let visible = 0;
cards.forEach(card => {
    const text = [card.dataset.name, card.dataset.specialty, card.dataset.city, card.dataset.disease].join(' ');
    const matches = (!term || text.includes(term)) && (!values.specialty || card.dataset.specialty === values.specialty) && (!values.disease || card.dataset.disease.includes(values.disease)) && (!values.city || card.dataset.city === values.city) && (!values.rating || Number(card.dataset.rating) >= Number(values.rating)) && (!values.availability || values.availability === 'live');
    card.hidden = !matches;
    if (matches) visible += 1;
});
count.textContent = cards.length === 0 ? '' :
    (visible === cards.length ? `Showing all ${visible} doctors`
     : `Showing ${visible} of ${cards.length} doctors`);
const params = new URLSearchParams();
if (term) params.set('q', term);
controls.forEach(control => { if (control.value) params.set(control.name, control.value); });
history.replaceState(null, '', params.toString() ? '?' + params.toString() : location.pathname);
}
[search, ...controls].forEach(control => control.addEventListener('input', filterDoctors));
const initialParams = new URLSearchParams(location.search);
search.value = initialParams.get('q') || '';
controls.forEach(control => { control.value = initialParams.get(control.name) || ''; });
filterDoctors();
if (location.search && document.querySelector('.directory-results')) {
    document.querySelector('.directory-results').scrollIntoView({block: 'start'});
}
</script>"""
        searched = bool(query or requested_specialty)
        if live_error:
            live_notice = (
                "<div class='notice danger'><strong>Oladoc could not be reached.</strong> "
                f"{escape(live_error)} No substitute doctors are shown: Oladoc is the only "
                "source MediGuide books through.</div>"
            )
        elif doctors:
            live_notice = (
                "<div class='notice notice--ok'><strong>Live from Oladoc.</strong> Every doctor below was "
                "read from the public Oladoc directory and has a real Oladoc profile, so the "
                "booking opens on their own page. Payment is pay at clinic.</div>"
            )
        elif searched:
            live_notice = "<div class='notice'><strong>No live Oladoc doctors matched that search.</strong> Try a specialty such as cardiologist, dermatologist, or neurologist.</div>"
        else:
            live_notice = "<div class='notice'><strong>No doctors are published on Oladoc right now.</strong></div>"
        content = f"<section class='panel directory-panel'><div class='eyebrow'>Oladoc live directory</div><h1>Find the right doctor</h1><p class='muted'>Every doctor here is read live from Oladoc, the only provider MediGuide books through. Search by doctor name, specialty, city, or care area.</p><form class='directory-search' method='get' action='/ui/doctors'><div class='field'><label for='doctor-search'>Search doctors on Oladoc</label><input id='doctor-search' name='q' value='{escape(query)}' type='search' placeholder='Search Dr. name, Neurology, Lahore...' autocomplete='off'></div><button type='submit'>Search live doctors</button></form><div class='directory-filters'><div class='eyebrow'>Refine your results</div><div class='formgrid'><div class='field'><label for='doctor-disease'>Care area</label><select id='doctor-disease' name='disease' data-doctor-filter><option value=''>Any care area</option>{options(diseases)}</select></div><div class='field'><label for='doctor-specialty'>Specialization</label><select id='doctor-specialty' name='specialty' data-doctor-filter><option value=''>Any specialty</option>{options(specialties)}</select></div><div class='field'><label for='doctor-rating'>Minimum rating</label><select id='doctor-rating' name='rating' data-doctor-filter><option value=''>Any rating</option><option value='4.5'>4.5+</option><option value='4.8'>4.8+</option><option value='5'>5.0</option></select></div><div class='field'><label for='doctor-city'>Location</label><select id='doctor-city' name='city' data-doctor-filter><option value=''>Any location</option>{options(cities)}</select></div><div class='field'><label for='doctor-availability'>Availability</label><select id='doctor-availability' name='availability' data-doctor-filter><option value=''>Any verified profile</option><option value='live'>Check live on Oladoc</option></select></div></div></div><p id='doctor-count' class='muted' aria-live='polite'></p></section><section class='section directory-results'>{live_notice}<div class='actions'><a href='/ui' class='button secondary'>Back to dashboard</a></div>{_doctor_cards(doctors, str(request.query_params))}</section>{cal_panel}{script}"
        return _page(content, user, "Find an Oladoc doctor | MediGuide")

    @app.get("/ui/chat", response_class=HTMLResponse)
    async def ui_chat_form(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        conversation_id = f"chat-{secrets.token_hex(12)}"
        content = f"<section class='panel'><div class='eyebrow'>Private care routing</div><h1>AI health chat</h1><p class='muted'>Describe what you are experiencing. MediGuide retrieves relevant health guidance, keeps the internal disease label private, and recommends the most appropriate specialist.</p><form method='post' action='/ui/chat'><input type='hidden' name='conversationId' value='{escape(conversation_id)}'><div class='field'><label>What symptoms are you experiencing?</label><textarea name='symptoms' required placeholder='For example: stomach pain and nausea for two days'></textarea></div><button type='submit'>Review symptoms</button></form><p class='muted'>This is general information, not a diagnosis. Sudden or severe symptoms require urgent medical care.</p></section>"
        response = _page(content, user, "AI health chat | MediGuide")
        response.set_cookie("mediguide_chat_session", conversation_id, httponly=True, samesite="lax", secure=_secure_cookies(request), max_age=3600)
        return response

    @app.post("/ui/appointments/{appointment_id}/cancel")
    async def ui_cancel_appointment(request: Request, appointment_id: str):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        appointment = db.find_appointment_for_user(appointment_id, user["id"])
        if appointment and appointment.get("status") not in {"cancelled", "completed"}:
            updated = db.update_appointment(appointment_id, user["id"], {"status": "cancelled", "cancelledAt": date.today().isoformat()})
            asyncio.create_task(asyncio.to_thread(notify_cancellation, user, updated))
        return RedirectResponse("/ui", status_code=303)

    def _intake_summary_panel(record: Dict[str, Any]) -> str:
        """Show the patient what we have recorded so far, in the canonical field order."""
        rows = "".join(
            f"<div class='answer-row'><strong>{escape(label)}</strong>"
            f"<span>{escape(symptom_intake.display_value(record, key))}</span></div>"
            for key, label in symptom_intake.FIELD_ORDER
        )
        return f"<div class='card answer-panel'><h2>What I have so far</h2>{rows}</div>"

    def _question_form(conversation_id: str, questions: list, errors: list) -> str:
        error_html = "".join(f"<li>{escape(item)}</li>" for item in errors)
        error_panel = f"<div class='notice danger'><strong>Please check these answers:</strong><ul>{error_html}</ul></div>" if errors else ""
        fields = "".join(
            f"<li><label for='answer-{escape(item['field'])}'>{escape(item['question'])}</label>"
            f"<textarea id='answer-{escape(item['field'])}' name='answer_{escape(item['field'])}' "
            f"placeholder='Your answer'></textarea></li>"
            for item in questions
        )
        return (
            "<div class='card question-panel chat-session'>"
            "<div class='chat-session-label'>MediGuide is building your symptom summary</div>"
            f"{error_panel}"
            "<h2>Only the missing details are asked</h2>"
            "<p class='muted'>Everything you have already told me is kept. Answer what you can in one submission.</p>"
            "<form method='post' action='/ui/chat' aria-label='Answer the remaining intake questions'>"
            f"<input type='hidden' name='conversationId' value='{escape(conversation_id)}'>"
            f"<ol class='question-list'>{fields}</ol>"
            "<button type='submit'>Send answers</button></form></div>"
        )

    @app.post("/ui/chat")
    async def ui_chat(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)
        conversation_id = (
            str(form.get("conversationId", "")).strip()
            or request.cookies.get("mediguide_chat_session")
            or f"chat-{secrets.token_hex(12)}"
        )

        stored = db.get_intake(user["id"], conversation_id)
        record = symptom_intake.normalize_record((stored or {}).get("record"))

        # Direct answers are attributed to the field whose question was asked.
        errors: list = []
        answered_any = False
        for key, value in form.items():
            if not key.startswith("answer_"):
                continue
            field = key[len("answer_"):]
            if not str(value).strip():
                continue
            answered_any = True
            error = symptom_intake.apply_answer(record, field, str(value))
            if error:
                errors.append(error)

        free_text = str(form.get("symptoms", "")).strip()
        if free_text:
            record = symptom_intake.apply_free_text(record, free_text)
        elif not answered_any and not symptom_intake.is_complete(record):
            return RedirectResponse("/ui/chat", status_code=303)

        errors.extend(symptom_intake.validate(record))
        db.save_intake(user["id"], conversation_id, record)

        patient_message = (
            f"<div class='card patient-message'><div class='eyebrow'>Your latest message</div>"
            f"<p>{escape(free_text)}</p></div>" if free_text else ""
        )

        # --- still collecting -------------------------------------------------
        questions = symptom_intake.pending_questions(record)
        if questions or errors:
            content = (
                "<section class='hero'><div class='eyebrow'>AI health chat</div>"
                "<h1>I am building your symptom summary.</h1>"
                "<p>I only ask for what is still missing. Nothing you have already answered is asked again.</p></section>"
                f"<section class='section'>{patient_message}{_intake_summary_panel(record)}"
                f"{_question_form(conversation_id, questions, errors)}</section>"
                "<section class='section'><a href='/ui'>Return to dashboard</a></section>"
            )
            save_chat_turn(user["id"], conversation_id, free_text or "(answers submitted)",
                           {"recommendation": {"specialty": None, "specialistIds": []},
                            "followUpQuestions": [item["question"] for item in questions],
                            "emergency": False, "confident": False})
            response = _page(content, user, "AI health chat | MediGuide")
            response.set_cookie("mediguide_chat_session", conversation_id, httponly=True, samesite="lax", secure=_secure_cookies(request))
            return response

        # --- complete: run the existing RAG pipeline --------------------------
        try:
            result = await asyncio.to_thread(assess_structured, record)
        except ValueError as exc:
            return _page(
                f"<section class='panel'><h1>More information needed</h1><p class='notice'>{escape(str(exc))}</p>"
                "<a class='button secondary' href='/ui/chat'>Start again</a></section>",
                user, "AI health chat | MediGuide",
            )
        except Exception as exc:
            flow_log.event(flow_log.RAG_FAILED, error=type(exc).__name__)
            return _page(
                "<section class='panel'><h1>The medical knowledge search is unavailable</h1>"
                "<p class='notice danger'><strong>RAG_UNAVAILABLE</strong> &middot; "
                f"{escape(type(exc).__name__)} while retrieving medical information.</p>"
                "<p class='muted'>Your answers are saved. Try again shortly.</p>"
                "<a class='button secondary' href='/ui/chat'>Try again</a></section>",
                user, "AI health chat | MediGuide",
            )

        # --- red flags come before any booking suggestion ---------------------
        urgent_panel = ""
        if result["urgent"]:
            urgent_panel = (
                "<div class='notice danger'><strong>Seek urgent medical care now.</strong>"
                "<p>Some of what you described can be associated with conditions that need urgent assessment. "
                "Contact your local emergency services or go to an emergency department rather than waiting for an appointment.</p></div>"
            )
        red_flag_items = "".join(f"<li>{escape(item)}</li>" for item in result["redFlags"])
        red_flag_panel = (
            f"<div class='card review-panel'><h2>When to seek urgent care</h2><ul>{red_flag_items}</ul></div>"
            if red_flag_items else ""
        )

        conditions = "".join(
            f"<li>{escape(item['pattern'])}<span class='muted'> &middot; usually seen by {escape(item['specialty'])}</span></li>"
            for item in result["possibleConditions"]
        )
        supporting = ", ".join(escape(item) for item in result["supportingSymptoms"])
        precaution_items = "".join(f"<li>{escape(item)}</li>" for item in result["precautions"])
        sources = ", ".join(escape(item) for item in result["sources"])

        assessment_panel = (
            "<div class='card'><h2>What this information suggests</h2>"
            f"<p>{escape(result['assessment'])}</p>"
            f"{'<h3>Possible patterns</h3><ul>' + conditions + '</ul>' if conditions else ''}"
            f"{'<h3>Supporting symptoms you reported</h3><p class=\"muted\">' + supporting + '</p>' if supporting else ''}"
            f"{'<h3>Precautions and self-care</h3><ul>' + precaution_items + '</ul>' if precaution_items else ''}"
            f"<p class='muted'>Sources: {sources}. This is general health information for care routing, not a diagnosis.</p></div>"
        )
        contextual = (
            f"<div class='card'><h3>In context</h3><p>{escape(str(result['contextualResponse']))}</p></div>"
            if result.get("contextualResponse") else ""
        )

        # --- doctors: live search, canonicalized, registered ------------------
        specialty = result["recommendedSpecialty"] or ""
        live_doctors: list = []
        live_error = ""
        try:
            live_doctors = await asyncio.to_thread(
                search_live_oladoc_doctors, result["structuredRecord"].get("main_symptom", ""),
                user.get("city", "Lahore"), specialty,
            )
        except RuntimeError as exc:
            live_error = str(exc)

        matched = _specialty_key(specialty)
        live_doctors = [d for d in live_doctors if _specialty_key(d.get("specialty")) == matched][:3]

        if live_error:
            doctor_panel = (
                "<div class='notice danger'><strong>LIVE_SEARCH_UNAVAILABLE</strong>"
                f"<p>{escape(live_error)}</p><p>No substitute doctors were shown. Please try again shortly.</p></div>"
            )
        elif not live_doctors:
            doctor_panel = (
                "<div class='notice'><strong>No live doctor matched this care area.</strong>"
                f"<p>Try the <a href='/ui/doctors?specialty={escape(quote(specialty))}'>doctor directory</a> or a different location.</p></div>"
            )
        else:
            doctor_panel = _doctor_cards(live_doctors)

        save_chat_turn(user["id"], conversation_id, free_text or "(answers submitted)", {
            "recommendation": {"specialty": specialty, "specialistIds": result["specialistIds"]},
            "retrievedDocuments": result["retrievedDocuments"],
            "emergency": result["urgent"],
            "followUpQuestions": [],
            "confident": True,
            "internalLabel": (result["possibleConditions"][0]["pattern"] if result["possibleConditions"] else ""),
        })

        content = (
            "<section class='hero'><div class='eyebrow'>AI health chat</div>"
            "<h1>Here is what the medical knowledge base returned.</h1>"
            "<p>This is general health information and care routing. It is not a diagnosis.</p></section>"
            f"<section class='section'>{patient_message}{urgent_panel}{_intake_summary_panel(record)}"
            f"{assessment_panel}{contextual}{red_flag_panel}</section>"
            f"<section class='section'><div class='eyebrow'>Recommended care</div>"
            f"<h2>{escape(specialty) if specialty else 'Suggested specialists'}</h2>"
            f"{doctor_panel}</section>"
            "<section class='section'><a href='/ui'>Return to dashboard</a></section>"
        )
        response = _page(content, user, "Care guidance | MediGuide")
        response.set_cookie("mediguide_chat_session", conversation_id, httponly=True, samesite="lax", secure=_secure_cookies(request))
        return response

    @app.get("/ui/profile", response_class=HTMLResponse)
    async def ui_profile(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        content = f"<section class='panel'><div class='eyebrow'>Patient profile</div><h1>Your information</h1><p class='muted'>This information is saved locally and used to prefill supported booking steps. CAPTCHA and OTP are always completed by you.</p><form method='post' action='/ui/profile'><div class='formgrid'><div class='field'><label>Full name</label><input name='fullName' value='{escape(user.get('fullName',''))}' required></div><div class='field'><label>Phone</label><input name='phone' value='{escape(user.get('phone',''))}'></div><div class='field'><label>City</label><input name='city' value='{escape(user.get('city','Lahore'))}'></div><div class='field'><label>Preferred contact</label><select name='preferredContact'><option value='both'>Email and phone</option><option value='email'>Email</option><option value='phone'>Phone</option></select></div><div class='field full'><label>Profile picture URL</label><input name='profilePicture' type='url' value='{escape(user.get('profilePicture',''))}' placeholder='https://...'></div></div><button type='submit'>Save profile</button></form><p class='muted'>Email: {escape(user.get('email',''))}</p></section><section class='section'><div class='grid'><a class='card' href='/ui/medical-information'><h3>Medical information</h3><p class='muted'>Keep important health details together.</p></a><a class='card' href='/ui/complaints'><h3>Complaints and feedback</h3><p class='muted'>Report an issue with a visit or service.</p></a><a class='card' href='/ui/history'><h3>Conversation history</h3><p class='muted'>Review saved care context.</p></a><a class='card' href='/ui/payments'><h3>Payment history</h3><p class='muted'>Review payment records.</p></a></div></section>"
        return _page(content, user, "Profile | MediGuide")

    @app.post("/ui/profile")
    async def ui_profile_update(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)
        db.update_user(user["id"], {key: str(form.get(key, "")) for key in ["fullName", "phone", "city", "preferredContact", "profilePicture"]})
        return RedirectResponse("/ui/profile", status_code=303)

    @app.get("/ui/history", response_class=HTMLResponse)
    async def ui_history(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        records = db.get_records("chat_sessions", user["id"])
        items = "".join(f"<article class='card conversation-card'><h3>{escape(item.get('diseaseName') or item.get('internalAssessment') or 'Assessment in progress')}</h3><p class='conversation-date'>Saved on {escape(_conversation_date(item))}</p><p>{escape(item.get('symptoms', ''))}</p><p class='muted'>Sources: {escape(', '.join(item.get('retrievedDocuments', [])))}</p></article>" for item in records) or "<div class='notice'>No conversations saved yet.</div>"
        return _page(f"<section class='panel'><div class='eyebrow'>Saved care context</div><h1>Conversation history</h1><p class='muted'>Your saved symptom reports and care-routing context.</p></section><section class='section'><div class='grid'>{items}</div></section>", user, "Conversation history | MediGuide")

    @app.get("/ui/notifications", response_class=HTMLResponse)
    async def ui_notifications(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        records = db.get_notifications_for_user(user["id"])
        items = "".join(f"<div class='card'><h3>{escape(item.get('channel', 'notification').title())}</h3><p class='muted'>Status: {escape(item.get('status', 'unknown'))}</p><p>{escape(item.get('detail', ''))}</p></div>" for item in records) or "<div class='notice'>No notifications yet.</div>"
        return _page(f"<section class='panel'><div class='eyebrow'>Delivery activity</div><h1>Notifications</h1><p class='muted'>Appointment confirmations and delivery states.</p></section><section class='section'><div class='grid'>{items}</div></section>", user, "Notifications | MediGuide")

    @app.get("/ui/payments", response_class=HTMLResponse)
    async def ui_payments(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        records = db.get_payments_for_user(user["id"])
        items = "".join(f"<div class='card'><h3>{escape(item.get('purpose', 'Payment').replace('_', ' ').title())}</h3><p class='muted'>{escape(str(item.get('amount', '')))} {escape(item.get('currency', 'PKR').upper())} · {escape(item.get('status', 'unknown'))}</p><p class='muted'>Payment ID: {escape(item.get('id', ''))}</p></div>" for item in records) or "<div class='notice'>No payments recorded.</div>"
        return _page(f"<section class='panel'><div class='eyebrow'>Payment history</div><h1>Payments</h1><p class='muted'>Payment records are created only after a server-side Stripe test intent.</p></section><section class='section'><div class='grid'>{items}</div></section>", user, "Payments | MediGuide")

    @app.get("/ui/medical-information", response_class=HTMLResponse)
    async def ui_medical_information(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        records = db.get_records("medical_information", user["id"])
        items = "".join(f"<div class='card'><h3>{escape(item['title'])}</h3><p>{escape(decrypt_details(item) if item.get('encryptedDetails') else item.get('details', ''))}</p><p class='muted'>{escape(item.get('category','general'))}</p></div>" for item in records) or "<div class='notice'>No medical information saved yet.</div>"
        content = f"<section class='panel'><div class='eyebrow'>Private record</div><h1>Medical information</h1><form method='post' action='/ui/medical-information'><div class='field'><label>Title</label><input name='title' required placeholder='Allergies, medicines, history'></div><div class='field'><label>Details</label><textarea name='details' required></textarea></div><button type='submit'>Save information</button></form></section><section class='section'><h2>Saved information</h2><div class='grid'>{items}</div></section>"
        return _page(content, user, "Medical information | MediGuide")

    @app.post("/ui/medical-information")
    async def ui_medical_information_save(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)
        db.create_record("medical_information", user["id"], {"title": str(form.get("title", "")), "category": "patient-entered", **encrypt_details(str(form.get("details", "")))})
        return RedirectResponse("/ui/medical-information", status_code=303)

    @app.get("/ui/complaints", response_class=HTMLResponse)
    async def ui_complaints(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        complaints = db.get_records("complaints", user["id"])
        items = "".join(f"<div class='card'><h3>{escape(item['subject'])}</h3><p>{escape(item['details'])}</p><p class='muted'>{escape(item.get('category','general'))} · Status: {escape(item.get('status','open'))}</p><p class='muted'>Desired resolution: {escape(item.get('desiredResolution','Not specified'))}</p></div>" for item in complaints) or "<div class='notice'>No complaints submitted.</div>"
        content = f"<section class='panel'><div class='eyebrow'>Patient support</div><h1>Complaints and feedback</h1><form method='post' action='/ui/complaints'><div class='formgrid'><div class='field'><label>Category</label><select name='category'><option>Appointment</option><option>Service</option><option>Technical</option><option>Billing</option><option>General</option></select></div><div class='field'><label>Subject</label><input name='subject' required></div><div class='field full'><label>Details</label><textarea name='details' required></textarea></div><div class='field full'><label>Desired resolution</label><input name='desiredResolution' placeholder='What would help resolve this?'></div></div><button type='submit'>Submit complaint</button></form></section><section class='section'><h2>Your submissions</h2><div class='grid'>{items}</div></section>"
        return _page(content, user, "Complaints | MediGuide")

    @app.post("/ui/complaints")
    async def ui_complaint_save(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)
        db.create_record("complaints", user["id"], {"subject": str(form.get("subject", "")), "details": str(form.get("details", "")), "category": str(form.get("category", "General")), "desiredResolution": str(form.get("desiredResolution", "")), "status": "open"})
        return RedirectResponse("/ui/complaints", status_code=303)

    @app.get("/ui/login", response_class=HTMLResponse)
    async def ui_login_form(request: Request):
        content = "<section class='panel auth'><div class='auth-brand'><div class='auth-mark' aria-hidden='true'></div><div><div class='eyebrow'>MediGuide account</div><h1>Welcome back.</h1></div></div><p class='muted auth-lead'>Sign in to continue your private care journey, review guidance, and manage appointments.</p><form method='post' action='/ui/login' aria-label='Sign in form'><div class='field'><label for='login-email'>Email address</label><input id='login-email' name='email' type='email' autocomplete='email' required placeholder='you@example.com'></div><div class='field'><label for='login-password'>Password</label><input id='login-password' name='password' type='password' autocomplete='current-password' required placeholder='Enter your password'></div><div class='auth-options'><label class='check-label'><input type='checkbox' name='remember'> Remember me</label><a href='/ui/register'>Need an account?</a></div><button class='auth-submit' type='submit'>Sign in securely</button></form><p class='muted auth-note'>Your health information stays behind authentication.</p></section>"
        return _page(content, title="Sign in | MediGuide")

    @app.post("/ui/login")
    async def ui_login(request: Request):
        form = await _form_data(request)
        user = db.find_user_by_email(str(form.get("email", "")).strip().lower())
        if not user or not verify_password(str(form.get("password", "")), user["passwordHash"]):
            return _page("<section class='panel auth'><h1>Sign in failed</h1><p class='notice'>Email or password is incorrect.</p><a href='/ui/login'>Try again</a></section>")
        response = RedirectResponse("/ui", status_code=303)
        response.set_cookie("mediguide_token", auth_response(user)["accessToken"], httponly=True, samesite="lax", secure=_secure_cookies(request))
        return response

    @app.get("/ui/register", response_class=HTMLResponse)
    async def ui_register_form(request: Request):
        content = "<section class='panel auth'><div class='auth-brand'><div class='auth-mark' aria-hidden='true'></div><div><div class='eyebrow'>Start your care workspace</div><h1>Create your account.</h1></div></div><p class='muted auth-lead'>One private place for care guidance, appointments, records, and support.</p><form method='post' action='/ui/register' aria-label='Create account form'><div class='field'><label for='register-name'>Full name</label><input id='register-name' name='fullName' autocomplete='name' required placeholder='Your full name'></div><div class='field'><label for='register-email'>Email address</label><input id='register-email' name='email' type='email' autocomplete='email' required placeholder='you@example.com'></div><div class='formgrid'><div class='field'><label for='register-city'>City</label><input id='register-city' name='city' autocomplete='address-level2' value='Lahore'></div><div class='field'><label for='register-phone'>Phone <span class='muted'>(optional)</span></label><input id='register-phone' name='phone' type='tel' autocomplete='tel' placeholder='+92...'></div></div><div class='field'><label for='register-password'>Password</label><input id='register-password' name='password' type='password' autocomplete='new-password' minlength='8' required placeholder='At least 8 characters'></div><p class='muted auth-note'>By creating an account, you agree to use MediGuide for general guidance, not emergency diagnosis.</p><button class='auth-submit' type='submit'>Create your account</button></form><p class='muted auth-switch'>Already registered? <a href='/ui/login'>Sign in</a></p></section>"
        return _page(content, title="Create account | MediGuide")

    @app.post("/ui/register")
    async def ui_register(request: Request):
        form = await _form_data(request)
        name = str(form.get("fullName", "")).strip()
        email = str(form.get("email", "")).strip().lower()
        password = str(form.get("password", ""))
        if not name or "@" not in email or len(password) < 8 or db.find_user_by_email(email):
            return _page("<section class='panel auth'><h1>Could not create account</h1><p class='notice'>Use a valid, available email and a password with at least 8 characters.</p><a href='/ui/register'>Try again</a></section>")
        user = db.create_user({"fullName": name, "email": email, "passwordHash": hash_password(password), "city": str(form.get("city", "Lahore")), "phone": str(form.get("phone", ""))})
        response = RedirectResponse("/ui", status_code=303)
        response.set_cookie("mediguide_token", auth_response(user)["accessToken"], httponly=True, samesite="lax", secure=_secure_cookies(request))
        return response

    @app.get("/ui/appointments", response_class=HTMLResponse)
    async def ui_appointments(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        # Oladoc is the only source: a doctor who is not on Oladoc has no
        # schedule to read and cannot be booked, so none are offered here.
        try:
            live_doctors = await asyncio.to_thread(_oladoc_directory, user.get("city", "Lahore"))
        except RuntimeError:
            live_doctors = []
        specialist_options = "".join(
            f"<option value='{escape(str(d['doctor_id']))}'>{escape(str(d['doctor_name']))} · {escape(str(d['specialty']))}</option>"
            for d in live_doctors
        )
        live_specialist_id = str(request.query_params.get("liveSpecialist", "")).strip()
        live_date = str(request.query_params.get("liveDate", "")).strip()
        live_time = str(request.query_params.get("liveTime", "")).strip()
        back_url = str(request.query_params.get("back", "/ui/doctors")).strip() or "/ui/doctors"
        live_schedule = None
        live_schedule_error = ""
        live_schedule_code = ""
        if live_specialist_id and live_date:
            live_specialist = _lookup_doctor(live_specialist_id)
            if live_specialist:
                try:
                    live_schedule = await asyncio.to_thread(get_live_schedule, live_specialist, live_date)
                except RuntimeError as exc:
                    live_schedule_error = str(exc)
                    live_schedule_code = str(getattr(exc, "code", "PROVIDER_UNAVAILABLE"))
        cards = "".join(
            "<article class='card doctor'>"
            f"<div class='avatar'>{_initials(str(s['doctor_name']))}</div>"
            f"<span class='tag'>{escape(str(s['specialty']))}</span>"
            f"<h3>{escape(str(s['doctor_name']))}</h3>"
            f"<p class='muted'>{escape(str(s.get('clinic') or 'Clinic shown on Oladoc'))}</p>"
            f"<p class='muted'>{escape(str(s.get('city', '')))}"
            f"{' · ' + escape(str(s['experience'])) if s.get('experience') else ''}"
            f"{' · Rating ' + escape(str(s['rating_label'])) if s.get('rating_label') else ''}</p>"
            "<form method='get' action='/ui/appointments'>"
            f"<input type='hidden' name='liveSpecialist' value='{escape(str(s['doctor_id']))}'>"
            "<label>Check a future date</label>"
            f"<input name='liveDate' type='date' min='{(date.today() + timedelta(days=1)).isoformat()}' "
            f"max='{(date.today() + timedelta(days=BOOKING_WINDOW_DAYS)).isoformat()}' required>"
            "<button type='submit'>View live schedule</button></form>"
            f"<a href='/ui/appointments?liveSpecialist={quote(str(s['doctor_id']), safe='')}#appointment-form'>"
            "Book in appointment section</a></article>"
            for s in live_doctors
        )
        selected_options = "".join(
            f"<option value='{escape(str(d['doctor_id']))}'"
            f"{' selected' if str(d['doctor_id']) == live_specialist_id else ''}>"
            f"{escape(str(d['doctor_name']))} · {escape(str(d['specialty']))}</option>"
            for d in live_doctors
        )
        _chosen = next((d for d in live_doctors if str(d["doctor_id"]) == live_specialist_id), None)
        reason_value = (
            f"Consultation with {_chosen['doctor_name']} for {_chosen['specialty']}."
            if _chosen else ""
        )
        content = f"<section class='panel' id='appointment-form'><div class='eyebrow'>Appointment booking</div><h1>Book a specialist appointment</h1><p class='muted'>Your selected live specialist, date, and time are filled in automatically below.</p><form method='post' action='/ui/booking/start'><div class='formgrid'><div class='field'><label>Specialist</label><select name='specialistId' required>{selected_options}</select></div><div class='field'><label>Date</label><input name='date' type='date' value='{escape(live_date)}' min='{date.today().isoformat()}' max='{(date.today() + timedelta(days=BOOKING_WINDOW_DAYS)).isoformat()}' required></div><div class='field'><label>Time</label><input name='time' type='time' value='{escape(live_time)}' step='1800' required></div><div class='field full'><label>Reason for visit</label><textarea name='reason' required placeholder='Describe the purpose of your appointment.'>{escape(reason_value)}</textarea></div></div><button type='submit'>Start real booking</button></form></section><section class='section'><div class='eyebrow'>Available specialists</div><h2>Real doctors and live schedules</h2><div class='grid'>{cards}</div></section>"
        instructions = "<section class='panel booking-guide'><div class='eyebrow'>How real booking works</div><h2>Book from the provider's live schedule</h2><ol><li>Select a specialist and load the live schedule.</li><li>Choose an available date and time shown on Oladoc.</li><li>Select that exact time in the booking form and submit it.</li><li>Playwright opens the provider page and fills your details.</li><li>Complete OTP or CAPTCHA yourself. Online payment is never selected; payment remains pay at clinic.</li></ol><p class='muted'>Only times read from the live provider page can be selected. If no slot appears, choose another date.</p></section>"
        live_panel = ""
        if live_schedule is not None:
            slot_items = "".join(f"<a class='tag live-slot' href='/ui/appointments?liveSpecialist={escape(live_specialist_id)}&liveDate={escape(live_date)}&liveTime={escape(slot)}#appointment-form'>{escape(slot)} · select</a>" for slot in live_schedule["slots"]) or "<p class='notice'>No visible slots were published for this date. Choose another date.</p>"
            live_panel = f"<section class='panel live-schedule'><div class='eyebrow'>Live provider schedule</div><h2>{escape(live_schedule['specialistName'])} · {escape(live_date)}</h2><p class='muted'>Times below were read from the public Oladoc page. Select one of these exact times in the booking form.</p><div class='slot-list'>{slot_items}</div></section>"
            if live_schedule["slots"]:
                time_options = "".join(f"<option value='{escape(slot)}'{' selected' if slot == live_time else ''}>{escape(slot)}</option>" for slot in live_schedule["slots"])
                content = content.replace("<input name='time' type='time' step='1800' required>", f"<select name='time' required><option value=''>Select live time</option>{time_options}</select>")
        elif live_schedule_error and live_schedule_code == "DOCTOR_NOT_ON_OLADOC":
            # Retrying cannot help: there is no Oladoc page for this doctor.
            live_panel = (
                "<section class='panel'><h2>This doctor is not on Oladoc</h2>"
                "<p class='notice'>MediGuide reads live schedules and books through Oladoc only, "
                "so there is no schedule to show for them. Nothing was booked.</p>"
                "<p class='muted'>This usually means the appointment was made before MediGuide "
                "moved to Oladoc-only listings. Pick a doctor from the live Oladoc directory to "
                "see real times.</p>"
                "<div class='actions'><a class='button primary' href='/ui/doctors'>"
                "Browse live Oladoc doctors</a>"
                f"<a class='button secondary' href='{escape(back_url)}'>Back to search</a></div></section>"
            )
        elif live_schedule_error:
            live_panel = f"<section class='panel'><h2>We could not read that live schedule</h2><p class='notice'>{escape(live_schedule_error)}</p><p class='muted'>This means the provider page did not respond, not that the doctor has no appointments. Try again later or open the doctor directly on Oladoc.</p><div class='actions'><a class='button secondary' href='{escape(back_url)}'>Back to search</a><a class='button secondary' href='/ui/doctors'>See other doctors</a></div></section>"
        elif live_specialist_id:
            live_panel = "<section class='panel live-schedule'><div class='eyebrow'>Live provider schedule</div><h2>Select a future date to check</h2><p class='muted'>Choose a future date on a specialist card. MediGuide will check only that real provider date and show the published times.</p></section>"
        back_link = f"<p><a href='{escape(back_url)}'>← Back to search</a></p>"
        content = back_link + live_panel + instructions + content
        return _page(content, user, "Appointment booking | MediGuide")

    @app.post("/ui/appointments")
    async def ui_appointments_book(request: Request):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)
        specialist_id = str(form.get("specialistId", "")).strip()
        appointment_date = str(form.get("date", "")).strip()
        appointment_time = str(form.get("time", ""))
        reason = str(form.get("reason", "")).strip()
        # Ids posted here are canonical doctor ids (oladoc:NNN). _lookup_doctor
        # resolves them server-side and still understands legacy directory ids.
        canonical = _lookup_doctor(specialist_id)
        if not canonical:
            return _page("<section class='panel'><h1>Specialist not found</h1><a href='/ui/appointments'>Back to booking</a></section>", user)
        if not appointment_date or not appointment_time or not reason:
            return _page("<section class='panel'><h1>Missing appointment details</h1><a href='/ui/appointments'>Choose again</a></section>", user)
        try:
            selected_date = date.fromisoformat(appointment_date)
        except ValueError:
            selected_date = None
        if selected_date is None or selected_date < date.today():
            return _page("<section class='panel'><h1>Choose a valid date</h1><p class='notice'>Appointments must be booked for today or a future date.</p><a href='/ui/appointments'>Try another date</a></section>", user)
        slot = is_slot_available(str(canonical["doctor_id"]), appointment_date, appointment_time, user["id"])
        if not slot["available"]:
            return _page(f"<section class='panel'><h1>Time is unavailable</h1><p class='notice'>{escape(slot['reason'])}</p><a href='/ui/appointments'>Choose another time</a></section>", user)
        # Stored through the shared record builder so status handling is uniform.
        # It starts SELECTED: nothing here has been confirmed by a provider.
        record = appointments.build_record(
            doctor=canonical, date=appointment_date, time_24=appointment_time,
            time_label=appointment_time, reason=reason,
            booking_status=appointments.SELECTED,
        )
        record["specialistId"] = str(canonical["doctor_id"])
        record["providerName"] = "MediGuide Python UI"
        appointment = db.create_appointment(user["id"], record)
        asyncio.create_task(asyncio.to_thread(notify_appointment, user, appointment))
        return RedirectResponse(f"/ui/confirmation/{appointment['id']}", status_code=303)

    @app.get("/ui/confirmation/{appointment_id}", response_class=HTMLResponse)
    async def ui_confirmation(request: Request, appointment_id: str):
        """Appointment detail card.

        The heading and status pill state plainly whether this is still only a
        selection or something the provider actually confirmed. A provider
        reference is shown only when the provider returned one.
        """
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        appointment = db.find_appointment_for_user(appointment_id, user["id"])
        if not appointment:
            return _page(
                "<section class='panel'><h1>Appointment not found</h1>"
                "<p class='alert alert--warn'>We could not find that appointment in your account.</p>"
                "<a class='button secondary' href='/ui'>Back to dashboard</a></section>",
                user, "Not found | MediGuide",
            )

        confirmed = appointments.is_confirmed(appointment)
        name = appointment.get("specialistName", "your doctor")

        checklist = db.find_checklist_by_appointment(appointment_id, user["id"])
        if not checklist:
            guidance = assess(appointment.get("reason", ""))
            checklist = db.create_checklist(user["id"], appointment_id, [
                {"id": "documents", "label": "Bring accepted identification, current medicines, allergies, and relevant reports.", "completed": False},
                *[{"id": f"precaution-{i}", "label": v, "completed": False}
                  for i, v in enumerate(guidance["precautions"], 1)],
                {"id": "arrival", "label": "Arrive 15 minutes before the appointment time.", "completed": False},
                {"id": "payment", "label": "Payment is pay at clinic; confirm the accepted method with the provider.", "completed": False},
            ])
        checklist_items = checklist.get("items", [])

        if request.query_params.get("download") == "1":
            lines = [
                "MediGuide appointment",
                f"Status: {appointments.status_label(appointment)}",
                "",
            ]
            lines += [f"{label}: {value}" for label, value in appointments.confirmation_fields(appointment, user)]
            lines += ["", "Checklist:"]
            lines += [f"- [{'x' if i.get('completed') else ' '}] {i.get('label', '')}" for i in checklist_items]
            if not confirmed:
                lines += ["", "This appointment is not confirmed by the provider yet."]
            return PlainTextResponse(
                "\n".join(lines) + "\n", media_type="text/plain",
                headers={"Content-Disposition": "attachment; filename=mediguide-appointment.txt"},
            )

        # --- headline ------------------------------------------------------
        if confirmed:
            source = str(appointment.get("confirmationSource", ""))
            heading = "Appointment confirmed"
            if source == "patient":
                lead = (
                    "You told us Oladoc confirmed this appointment. MediGuide has recorded it "
                    "as confirmed by you, not verified by the provider."
                )
                hero_note = (
                    "<div class='alert alert--warn'><strong>Confirmed by you</strong> &middot; "
                    "MediGuide did not see the provider confirm this. If anything looks wrong, "
                    "check directly with the clinic.</div>"
                )
            else:
                lead = (
                    f"{escape(appointment.get('provider', 'The provider'))} confirmed this "
                    f"appointment with {escape(name)}."
                )
                hero_note = ""
        else:
            heading = "Appointment selected"
            lead = (
                "You have chosen this doctor and time. It is <strong>not confirmed</strong> "
                "until the provider confirms it."
            )
            hero_note = (
                "<div class='alert alert--warn'><strong>Not confirmed yet</strong> &middot; "
                "Nothing here is a booking confirmation. Complete the provider flow to confirm.</div>"
            )

        rows = _detail_rows(appointment, user)
        if confirmed:
            rows += (
                "<div class='summary-row'><dt>Confirmation</dt>"
                f"<dd>{escape(appointments.confirmed_by(appointment))}</dd></div>"
            )
        reference = str(appointment.get("providerAppointmentId", "")).strip()
        reference_note = (
            "" if reference else
            "<p class='card-note'>No provider reference number was returned for this appointment.</p>"
        )

        items = "".join(
            "<li><label class='check-label'>"
            f"<input type='checkbox' name='item' value='{escape(item['id'])}'"
            f"{' checked' if item.get('completed') else ''}> <span>{escape(item['label'])}</span>"
            "</label></li>"
            for item in checklist_items
        )
        share_text = quote(
            f"MediGuide appointment ({appointments.status_label(appointment)}) with {name} "
            f"on {appointment.get('date')} at {appointment.get('timeLabel', appointment.get('time'))}."
        )

        content = (
            "<section class='panel panel--hero'>"
            f"{_status_pill(appointment)}"
            f"<h1 class='panel__heading'>{escape(heading)}</h1><p class='card-meta'>{lead}</p></section>"
            f"{hero_note}"
            "<section class='panel'><h2 class='panel__title'>Appointment details</h2>"
            f"<dl class='summary-list'>{rows}</dl>{reference_note}</section>"
            f"{_email_panel(appointment)}"
            "<section class='panel'><h2 class='panel__title'>Before your visit</h2>"
            f"<form method='post' action='/ui/confirmation/{escape(appointment_id)}/checklist'>"
            f"<ul class='checklist'>{items}</ul>"
            "<button class='button primary' type='submit'>Save checklist</button></form>"
            "<div class='actions'>"
            f"<a class='button secondary' href='/ui/confirmation/{quote(appointment_id)}?download=1'>Download details</a>"
            f"<a class='button secondary' href='mailto:?subject=MediGuide%20appointment&body={share_text}'>Share by email</a>"
            "</div></section>"
            "<section class='section'><a href='/ui'>Back to dashboard</a></section>"
        )
        return _page(content, user, f"{heading} | MediGuide")

    @app.post("/ui/confirmation/{appointment_id}/checklist")
    async def ui_confirmation_checklist(request: Request, appointment_id: str):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        appointment = db.find_appointment_for_user(appointment_id, user["id"])
        checklist = db.find_checklist_by_appointment(appointment_id, user["id"]) if appointment else None
        if not appointment or not checklist:
            return RedirectResponse("/ui", status_code=303)
        values = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        completed_ids = {str(value) for value in values.get("item", [])}
        items = [{**item, "completed": item.get("id") in completed_ids} for item in checklist.get("items", [])]
        db.update_checklist(checklist["id"], user["id"], items)
        return RedirectResponse(f"/ui/confirmation/{quote(appointment_id)}", status_code=303)

    def _lookup_doctor(doctor_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a doctor id to its canonical record, server-side.

        The registry is authoritative. A legacy directory id is canonicalized on
        the fly so old links keep working, but a name from the client is never
        used to find a doctor.
        """
        target = str(doctor_id or "").strip()
        if not target:
            return None
        stored = db.find_doctor(target)
        if stored:
            return stored
        for specialist in SPECIALISTS:
            if str(specialist.get("id", "")) == target:
                return _register_doctor(specialist)
            try:
                if identity.from_specialist(specialist)["doctor_id"] == target:
                    return _register_doctor(specialist)
            except identity.DoctorIdentityError:
                continue
        return None

    def _identity_error_page(user: Dict[str, Any], payload: Dict[str, Any], detail: str) -> HTMLResponse:
        """Explicit stop. No alternative doctor is ever substituted."""
        reasons = "".join(f"<li>{escape(str(item))}</li>" for item in payload.get("reasons", []))
        return _page(
            "<section class='panel identity-error'><div class='eyebrow'>Booking stopped</div>"
            "<h1>We stopped before booking</h1>"
            f"<p class='notice danger'><strong>{escape(str(payload.get('error', 'DOCTOR_IDENTITY_MISMATCH')))}</strong> &middot; {escape(detail)}</p>"
            f"{'<ul>' + reasons + '</ul>' if reasons else ''}"
            "<p class='muted'>The booking provider was not showing the doctor you selected, so nothing was submitted. "
            "We will not book a different doctor on your behalf.</p>"
            "<div class='actions'><a class='button secondary' href='/ui/doctors'>Choose a doctor again</a>"
            "<a class='button secondary' href='/ui'>Back to dashboard</a></div></section>",
            user,
            "Booking stopped | MediGuide",
        )

    def _status_pill(appointment: Dict[str, Any]) -> str:
        status = str(appointment.get("bookingStatus", appointments.SELECTED))
        modifier = {
            appointments.CONFIRMED: "confirmed",
            appointments.FAILED: "failed",
            appointments.CANCELLED: "failed",
        }.get(status, "selected")
        return (
            f"<span class='status-pill status-pill--{modifier}'>"
            f"{escape(appointments.status_label(appointment))}</span>"
        )

    def _detail_rows(appointment: Dict[str, Any], user: Dict[str, Any]) -> str:
        rows = appointments.confirmation_fields(appointment, user)
        return "".join(
            f"<div class='summary-row'><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
            for label, value in rows
        )

    def _email_panel(appointment: Dict[str, Any]) -> str:
        """Email outcome, reported independently of the booking outcome."""
        if not appointments.is_confirmed(appointment):
            return ""
        status = str(appointment.get("emailStatus", email_service.EMAIL_PENDING))
        detail = str(appointment.get("emailDetail", ""))
        retry = (
            f"<form method='post' action='/ui/appointments/{escape(appointment['id'])}/resend-email'>"
            "<button class='button secondary' type='submit'>Resend confirmation email</button></form>"
        )
        if status == email_service.EMAIL_SENT:
            return (
                "<div class='alert alert--ok'><strong>Confirmation email sent.</strong> "
                f"{escape(detail)}</div>"
            )
        if status == email_service.EMAIL_NOT_CONFIGURED:
            return (
                "<div class='alert alert--warn'><strong>EMAIL_NOT_CONFIGURED</strong> &middot; "
                f"{escape(detail)} Your appointment is confirmed regardless.</div>{retry}"
            )
        if status == email_service.EMAIL_FAILED:
            return (
                "<div class='alert alert--error'><strong>EMAIL_FAILED</strong> &middot; "
                f"{escape(detail)}</div>"
                "<p class='card-note'>The appointment remains confirmed. Only the email failed.</p>"
                f"{retry}"
            )
        return "<div class='alert'>Confirmation email is queued.</div>"

    @app.post("/ui/appointments/{appointment_id}/resend-email")
    async def ui_resend_confirmation_email(request: Request, appointment_id: str):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        appointment = db.find_appointment_for_user(appointment_id, user["id"])
        if appointment and appointments.is_confirmed(appointment):
            await asyncio.to_thread(appointments.send_confirmation_email, db, appointment, user)
        return RedirectResponse(f"/ui/confirmation/{quote(appointment_id)}", status_code=303)

    @app.post("/ui/booking/start")
    async def ui_booking_start(request: Request):
        """Create the booking session and hand it to the provider worker."""
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        form = await _form_data(request)

        doctor = _lookup_doctor(str(form.get("doctorId", "")).strip())
        if doctor is None and form.get("specialistId"):
            # The built-in specialist form posts specialistId; resolve it to the
            # same canonical record so both entry points share one flow.
            doctor = _lookup_doctor(str(form.get("specialistId", "")).strip())
        if not doctor:
            return _page(
                "<section class='panel'><h1>Doctor could not be identified</h1>"
                "<p class='alert alert--error'><strong>DOCTOR_ID_MISSING</strong> &middot; "
                "This booking did not carry a doctor we can identify.</p>"
                "<a class='button secondary' href='/ui/doctors'>Find a doctor</a></section>",
                user, "Booking stopped | MediGuide",
            )

        appointment_date = str(form.get("date", "")).strip()
        time_24 = str(form.get("time", "")).strip()
        time_label = str(form.get("timeLabel", "")).strip() or time_24
        clinic_id = str(form.get("clinicId", "")).strip()
        reason = str(form.get("reason", "")).strip()
        slot_id = str(form.get("slotId", "")).strip()

        if not appointment_date or not time_24:
            return _page(
                "<section class='panel'><h1>Choose a live slot first</h1>"
                "<p class='alert alert--warn'>Pick a time from this doctor's live availability.</p>"
                f"<a class='button primary' href='/ui/slots/{quote(str(doctor['doctor_id']), safe='')}'>"
                "Check live slots</a></section>",
                user, "Booking | MediGuide",
            )

        # The clinic is resolved by the worker, because reading it needs a live
        # page load and this request must return straight away.
        clinic: Dict[str, Any] = {"clinicId": clinic_id} if clinic_id else {}
        booking_url = ""

        session = booking_session.registry.create(
            user_id=user["id"], doctor=doctor, date=appointment_date,
            slot_id=slot_id, slot_label=time_label,
            clinic_id=clinic.get("clinicId", ""), clinic_name=clinic.get("name", ""),
        )

        # The appointment exists as SELECTED from here; it becomes CONFIRMED only
        # when the provider says so.
        appointment = db.create_appointment(user["id"], appointments.build_record(
            doctor=doctor, date=appointment_date, time_24=time_24, time_label=time_label,
            reason=reason, clinic=clinic, timezone_name=oladoc_provider.PROVIDER_TIMEZONE,
            booking_status=appointments.IN_PROGRESS,
            booking_session_id=session["bookingSessionId"], slot_id=slot_id,
        ))

        def _on_confirmed(confirmed_session: Dict[str, Any]) -> None:
            record = appointments.mark_confirmed(
                db, appointment["id"], user["id"],
                provider_appointment_id=str(confirmed_session.get("providerAppointmentId", "")),
            )
            if record:
                appointments.send_confirmation_email(db, record, user)

        # Pre-fill the checklist from what MediGuide already knows, so the
        # patient confirms rather than retypes.
        booking_session.registry.update(
            session["bookingSessionId"], user_id=user["id"],
            checklist=booking_session.seed_checklist(session, user, reason),
        )

        booking_worker.start_booking(
            session=session, doctor=doctor, patient=user,
            booking_url=booking_url, on_confirmed=_on_confirmed,
        )
        return RedirectResponse(f"/ui/booking/{session['bookingSessionId']}", status_code=303)

    def _outcome_panel(summary: Dict[str, Any]) -> str:
        """Ask the patient what actually happened in the Oladoc window.

        MediGuide cannot see inside the provider's page once the patient takes
        over for OTP or CAPTCHA, so the honest thing is to ask rather than to
        assume a booking that may not exist.
        """
        session_id = escape(summary["bookingSessionId"])
        return (
            "<section class='panel panel--ask'>"
            "<span class='chip chip--accent'>One quick question</span>"
            "<h2 class='panel__title'>Did Oladoc confirm your appointment?</h2>"
            "<p class='card-meta'>We opened "
            f"<strong>{escape(summary['doctorName'])}</strong>'s booking page for "
            f"{escape(summary['date'])} at {escape(summary['slotLabel'])}. "
            "Only you can see whether Oladoc finished the booking, so please tell us.</p>"
            f"<form method='post' action='/ui/booking/{session_id}/outcome' class='outcome'>"
            "<div class='field'>"
            "<label for='outcome-ref'>Booking reference from Oladoc "
            "<span class='muted'>(optional)</span></label>"
            "<input id='outcome-ref' name='reference' placeholder='Paste it here if Oladoc gave you one'>"
            "</div>"
            "<div class='outcome__choices'>"
            "<button class='button primary' type='submit' name='outcome' value='booked'>"
            f"{branding.icon('check', size=16)} Yes, it is booked</button>"
            "<button class='button secondary' type='submit' name='outcome' value='not_booked'>"
            f"{branding.icon('alert', size=16)} No, it did not go through</button>"
            "</div>"
            "<p class='card-note'>If you say yes, MediGuide records this as confirmed by you, "
            "not by the provider. We never mark something confirmed that we did not see.</p>"
            "</form></section>"
        )

    @app.post("/ui/booking/{session_id}/outcome")
    async def ui_booking_outcome(request: Request, session_id: str):
        """Record whether the patient got the booking through on Oladoc."""
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        session = booking_session.registry.get(session_id, user_id=user["id"])
        if not session:
            return RedirectResponse("/ui/doctors", status_code=303)

        form = await _form_data(request)
        outcome = str(form.get("outcome", "")).strip()
        if outcome not in {"booked", "not_booked"}:
            return RedirectResponse(f"/ui/booking/{quote(session_id)}", status_code=303)
        reference = re.sub(r"[^A-Za-z0-9\-_/]", "", str(form.get("reference", "")))[:40]

        booking_session.registry.update(
            session_id, user_id=user["id"],
            patientOutcome=outcome, patientReference=reference,
        )
        # They have finished with the provider's page, so close that window.
        booking_worker.close_window(session_id)
        flow_log.event(
            "PATIENT_REPORTED_OUTCOME",
            booking_session_id=session_id, outcome=outcome, has_reference=bool(reference),
        )

        appointment = next(
            (a for a in db.get_appointments_for_user(user["id"])
             if a.get("bookingSessionId") == session_id), None
        )
        if appointment:
            if outcome == "booked":
                # Confirmed, but recorded as reported by the patient. The
                # confirmation card states the difference plainly.
                appointments.update_record(
                    db, appointment["id"], user["id"],
                    bookingStatus=appointments.CONFIRMED,
                    confirmationSource="patient",
                    providerAppointmentId=reference,
                    confirmationReference=reference,
                )
            else:
                appointments.update_record(
                    db, appointment["id"], user["id"],
                    bookingStatus=appointments.FAILED,
                    confirmationSource="",
                )
        return RedirectResponse(f"/ui/booking/{quote(session_id)}", status_code=303)

    def _autofill_panel(summary: Dict[str, Any]) -> str:
        """What the provider filled in automatically, and what it did not.

        Shown so a partial or failed autofill is visible and actionable rather
        than silently leaving the patient stuck.
        """
        if not summary.get("autofillAttempted"):
            # The checklist immediately below already shows every one of these
            # values, so repeating them here would only make the page longer.
            return (
                "<div class='alert alert--warn'><strong>Nothing was filled in for you.</strong> "
                "We could not reach the provider's booking form, so you will have entered your "
                "details on Oladoc yourself. Check them against the list below.</div>"
            )
        autofill = summary.get("autofill") or {}
        labels = {"name": "Patient name", "email": "Email", "phone": "Contact number",
                  "date": "Appointment date", "time": "Appointment time"}
        filled = [labels.get(k, k) for k, v in autofill.items() if v]
        missed = [labels.get(k, k) for k, v in autofill.items() if not v]

        rows = "".join(
            f"<li class='autofill__row autofill__row--ok'>{branding.icon('check', size=15)}"
            f"<span>{escape(item)}</span><em>Filled on Oladoc</em></li>" for item in filled
        ) + "".join(
            f"<li class='autofill__row autofill__row--manual'>{branding.icon('alert', size=15)}"
            f"<span>{escape(item)}</span><em>Enter this on Oladoc yourself</em></li>" for item in missed
        )
        if not rows:
            return (
                "<div class='alert alert--warn'><strong>Nothing could be filled automatically.</strong> "
                "Your details are listed below — copy them into the Oladoc window.</div>"
            )
        headline = (
            "Everything was filled in for you on Oladoc."
            if not missed else
            f"{len(filled)} detail(s) filled automatically, {len(missed)} need you to enter them."
        )
        return (
            "<section class='panel'><h2 class='panel__title'>What we filled in for you</h2>"
            f"<p class='card-meta'>{escape(headline)}</p>"
            f"<ul class='autofill'>{rows}</ul></section>"
        )

    def _checklist_panel(session: Dict[str, Any], summary: Dict[str, Any], errors: list) -> str:
        """The on-site checklist: confirm every booking detail before finishing."""
        checklist = summary.get("checklist") or {}
        complete = summary.get("checklistComplete")
        autofill = summary.get("autofill") or {}

        error_html = "".join(f"<li>{escape(e)}</li>" for e in errors)
        error_block = (
            f"<div class='alert alert--error'><strong>Please complete these:</strong>"
            f"<ul>{error_html}</ul></div>" if errors else ""
        )

        if complete:
            rows = "".join(
                f"<div class='summary-row'><dt>{escape(label)}</dt>"
                f"<dd>{escape(str(checklist.get(key, '')) or 'Not provided')}</dd></div>"
                for key, label, _ in booking_session.CHECKLIST_FIELDS
                if str(checklist.get(key, "")).strip()
            )
            return (
                "<section class='panel'><h2 class='panel__title'>"
                f"{branding.icon('check', size=18)} Checklist confirmed</h2>"
                "<p class='card-meta'>You confirmed these details. They are saved with your appointment.</p>"
                f"<dl class='summary-list'>{rows}</dl>"
                f"<form method='post' action='/ui/booking/{escape(summary['bookingSessionId'])}/checklist'>"
                "<input type='hidden' name='reopen' value='1'>"
                "<button class='button secondary' type='submit'>Edit these details</button></form>"
                "</section>"
            )

        fields = []
        for key, label, editable in booking_session.CHECKLIST_FIELDS:
            value = escape(str(checklist.get(key, "")))
            hint = ""
            if key in {"name": 1, "patientName": 1} and autofill.get("name") is False:
                hint = "<span class='field-hint'>Oladoc could not fill this</span>"
            if key == "patientPhone" and autofill.get("phone") is False:
                hint = "<span class='field-hint'>Oladoc could not fill this</span>"
            if key == "patientEmail" and autofill.get("email") is False:
                hint = "<span class='field-hint'>Oladoc could not fill this</span>"

            if editable:
                multiline = key == "reason"
                control = (
                    f"<textarea id='cl-{key}' name='{key}' rows='2' "
                    f"placeholder='Optional'>{value}</textarea>"
                    if multiline else
                    f"<input id='cl-{key}' name='{key}' value='{value}' "
                    f"{'required' if key != 'reason' else ''}>"
                )
                fields.append(
                    f"<div class='field'><label for='cl-{key}'>{escape(label)}</label>{control}{hint}</div>"
                )
            else:
                # Doctor, date and time come from the live slot the patient chose.
                # They are confirmed, not retyped, so the booking cannot drift
                # onto a different doctor or time.
                fields.append(
                    f"<div class='field field--locked'><label>{escape(label)}</label>"
                    f"<p class='locked-value'>{branding.icon('shield', size=15)}"
                    f"<strong>{value or 'Not set'}</strong></p>"
                    "<label class='check-label'><input type='checkbox' name='confirm_"
                    f"{key}' value='1' required> Confirmed</label></div>"
                )

        change_link = (
            f"<a class='button secondary' href='/ui/slots/"
            f"{quote(str(summary['doctorId']), safe='')}'>Change date or time</a>"
        )
        return (
            "<section class='panel'><h2 class='panel__title'>Appointment checklist</h2>"
            "<p class='card-meta'>Check every detail below. The doctor, date and time come from the "
            "live schedule you chose and are locked so the booking cannot drift; everything else you "
            "can edit.</p>"
            f"{error_block}"
            f"<form method='post' action='/ui/booking/{escape(summary['bookingSessionId'])}/checklist'>"
            f"<div class='formgrid'>{''.join(fields)}</div>"
            "<div class='actions'><button class='button primary' type='submit'>"
            "Confirm these details</button>" + change_link + "</div></form></section>"
        )

    @app.post("/ui/booking/{session_id}/checklist")
    async def ui_booking_checklist(request: Request, session_id: str):
        """Save the patient's confirmation of the booking details."""
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        session = booking_session.registry.get(session_id, user_id=user["id"])
        if not session:
            return RedirectResponse("/ui/doctors", status_code=303)

        form = await _form_data(request)
        if form.get("reopen"):
            booking_session.registry.update(session_id, user_id=user["id"], checklistComplete=False)
            return RedirectResponse(f"/ui/booking/{quote(session_id)}", status_code=303)

        checklist = dict(session.get("checklist") or {})
        for key, _, editable in booking_session.CHECKLIST_FIELDS:
            if editable and key in form:
                checklist[key] = str(form.get(key, "")).strip()

        missing = booking_session.checklist_missing(checklist)
        unconfirmed = [
            label for key, label, editable in booking_session.CHECKLIST_FIELDS
            if not editable and not form.get(f"confirm_{key}")
        ]
        errors = [f"{label} is required." for label in missing]
        errors += [f"Tick the box to confirm the {label.lower()}." for label in unconfirmed]

        booking_session.registry.update(
            session_id, user_id=user["id"],
            checklist=checklist, checklistComplete=not errors,
            checklistErrors=errors,
        )

        if not errors:
            # Keep the stored appointment in step with what the patient confirmed.
            appointment = next(
                (a for a in db.get_appointments_for_user(user["id"])
                 if a.get("bookingSessionId") == session_id), None
            )
            if appointment:
                appointments.update_record(
                    db, appointment["id"], user["id"],
                    clinicName=checklist.get("clinicName", ""),
                    reason=checklist.get("reason", ""),
                    patientName=checklist.get("patientName", ""),
                    patientPhone=checklist.get("patientPhone", ""),
                    patientEmail=checklist.get("patientEmail", ""),
                )
                # The confirmation email goes out here rather than the moment
                # the booking is confirmed, because the checklist is where the
                # clinic and contact details become final. A booking the
                # provider confirmed itself was emailed already, so this only
                # sends when nothing has gone out yet.
                latest = db.find_appointment_for_user(appointment["id"], user["id"])
                if latest and latest.get("emailStatus") != email_service.EMAIL_SENT:
                    await asyncio.to_thread(
                        appointments.send_confirmation_email, db, latest, user
                    )
            flow_log.event("BOOKING_CHECKLIST_CONFIRMED", booking_session_id=session_id)
        return RedirectResponse(f"/ui/booking/{quote(session_id)}", status_code=303)

    @app.get("/ui/booking/{session_id}", response_class=HTMLResponse)
    async def ui_booking_progress(request: Request, session_id: str):
        """Live progress, including the OTP hand-off when the provider asks for one."""
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        session = booking_session.registry.get(session_id, user_id=user["id"])
        if not session:
            return _page(
                "<section class='panel'><h1>Booking session not found</h1>"
                "<p class='alert alert--warn'>This booking session has expired or belongs to another account.</p>"
                "<a class='button secondary' href='/ui/doctors'>Start again</a></section>",
                user, "Booking | MediGuide",
            )

        summary = booking_session.summarize(session)
        # Derived once, before any branch reads them.
        checklist_errors = list(session.get("checklistErrors") or [])
        ask_outcome = booking_session.needs_patient_outcome(session)
        confirmed_source = booking_session.confirmation_source(session)
        appointment = next(
            (a for a in db.get_appointments_for_user(user["id"])
             if a.get("bookingSessionId") == session_id), None
        )

        steps = [
            (booking_session.DOCTOR_VERIFIED, "Doctor verified on the provider"),
            (booking_session.SLOT_SELECTED, "Slot selected"),
            (booking_session.PATIENT_DETAILS_FILLED, "Your details filled in"),
            (booking_session.OTP_VERIFIED, "Verification code accepted"),
            (booking_session.CONFIRMED, "Provider confirmed"),
        ]
        reached = {entry["state"] for entry in session.get("history", [])}
        finished = summary["state"] in {booking_session.CONFIRMED, booking_session.FAILED}
        rows = []
        for state, label in steps:
            if state in reached:
                mark, cls = "Done", "step--done"
            elif finished:
                # The run has ended, so anything not reached never will be.
                mark, cls = "Not reached", "step--skipped"
            else:
                mark, cls = "Waiting", "step--pending"
            rows.append(
                f"<div class='summary-row {cls}'><dt>{mark}</dt><dd>{escape(label)}</dd></div>"
            )
        step_html = "".join(rows)

        if summary["awaitingOtp"]:
            body = (
                "<div class='alert alert--warn'><strong>OTP_REQUIRED</strong> &middot; "
                "The provider sent a verification code to your phone. Enter it below - "
                "only you ever see this code.</div>"
                f"<form method='post' action='/ui/booking/{escape(session_id)}/otp' class='inline-form'>"
                "<div class='field'><label for='otp-code'>Verification code</label>"
                "<input id='otp-code' name='code' inputmode='numeric' autocomplete='one-time-code' "
                "pattern='[0-9]{4,8}' required placeholder='Enter the code you received'></div>"
                "<button class='button primary' type='submit'>Submit code</button></form>"
                "<p class='card-note'>MediGuide never reads your messages and never stores this code.</p>"
            )
        elif confirmed_source and appointment:
            # Either the provider confirmed it, or the patient told us Oladoc
            # did. Both reach the confirmation card; the card states which.
            headline = (
                "Confirmed by the provider." if confirmed_source == "provider"
                else "You confirmed this booking went through on Oladoc."
            )
            body = (
                f"<div class='alert alert--ok'><strong>{escape(headline)}</strong></div>"
                + (f"<a class='button primary' href='/ui/confirmation/{escape(appointment['id'])}'>"
                   "View your confirmation card</a>"
                   if summary.get("checklistComplete") else
                   "<p class='card-meta'>Confirm the checklist below to finish.</p>")
            )
        elif summary["state"] == booking_session.FAILED and not confirmed_source:
            code = summary["error"] or "BOOKING_FAILED"
            explain = {
                "SUBMIT_DISABLED": "Final submission to the provider is switched off in this deployment, "
                                   "so nothing was submitted. Everything up to the final step completed.",
                "DOCTOR_IDENTITY_MISMATCH": "The provider displayed a different doctor, so we stopped. "
                                            "No other doctor was booked in their place.",
                "SLOT_NO_LONGER_AVAILABLE": "That time was taken while we were booking. Pick another live slot.",
                "OTP_TIMEOUT": "The verification code was not entered in time.",
                "OTP_REJECTED": "The provider did not accept that verification code.",
                "PROVIDER_DID_NOT_CONFIRM": "The provider did not confirm the appointment, so it is not booked.",
                "NO_CLINIC_PUBLISHED": "This doctor has no clinic taking online bookings right now, "
                                       "so we could not open a booking form for them.",
                "SESSION_EXPIRED": "This booking page sat idle too long and timed out. Nothing was booked.",
            }.get(code, "The provider flow could not be completed.")
            provider_url = str(session.get("providerProfileUrl") or "")
            manual = (
                f"<a class='button primary' href='{escape(provider_url)}' target='_blank' "
                "rel='noopener noreferrer'>Open Oladoc to finish manually</a>" if provider_url else ""
            )
            window_open = summary.get("providerWindowOpen")
            if window_open:
                next_step = (
                    "A browser window is open on this doctor's own Oladoc page. Finish the "
                    "booking there - pick your time, and enter any code or CAPTCHA Oladoc "
                    "asks for. Then come back and tell us what happened."
                )
            elif ask_outcome:
                next_step = (
                    "Your details are safe on this page. Answer the question below so we know "
                    "whether to record this appointment."
                )
            else:
                next_step = (
                    "Your details are safe on this page. Confirm the checklist below, "
                    "then use them to complete the booking on Oladoc."
                )
            headline = (
                f"<div class='alert alert--warn'><strong>Finish your booking in the Oladoc "
                f"window.</strong><p>{escape(explain)}</p>"
                f"<span class='alert__code'>Reference: {escape(code)}</span></div>"
                if window_open else
                f"<div class='alert alert--error'><strong>{escape(explain)}</strong>"
                f"<span class='alert__code'>Reference: {escape(code)}</span></div>"
            )
            body = (
                headline
                + f"<p class='card-meta'>{next_step}</p>"
                f"<div class='actions'>{manual}"
                f"<a class='button secondary' href='/ui/slots/"
                f"{quote(str(session['doctorId']), safe='')}'>Choose another time</a>"
                "<a class='button secondary' href='/ui/doctors'>Choose another doctor</a></div>"
            )
        else:
            body = (
                "<div class='is-loading'><span class='spinner' aria-hidden='true'></span>"
                "<span>Connecting to the booking provider…</span></div>"
                "<p class='card-note'>Keep this page open. It refreshes on its own.</p>"
            )


        # Auto-refresh only while the provider flow is still working and the
        # patient has nothing to type. Refreshing under an open question, an
        # open checklist or an OTP box would discard what they are entering.
        still_working = summary["state"] not in {booking_session.CONFIRMED, booking_session.FAILED}
        awaiting_input = (
            summary["awaitingOtp"] or ask_outcome or not summary.get("checklistComplete")
        )
        refresh = "<meta http-equiv='refresh' content='4'>" if still_working and not awaiting_input else ""
        # Order of the flow: ask what happened, then show what we filled in,
        # then the checklist. Nothing later is shown until the question is
        # answered, so the patient is never asked to confirm details for a
        # booking that did not happen.
        outcome_panel = _outcome_panel(summary) if ask_outcome else ""
        said_not_booked = summary.get("patientOutcome") == "not_booked"
        show_rest = not ask_outcome and not said_not_booked
        autofill_panel = _autofill_panel(summary) if show_rest else ""
        checklist_panel = _checklist_panel(session, summary, checklist_errors) if show_rest else ""

        if said_not_booked:
            doctor_key = quote(str(session["doctorId"]), safe="")
            checklist_panel = (
                "<section class='panel'><h2 class='panel__title'>Not booked</h2>"
                "<p class='card-meta'>Nothing was recorded as confirmed. Your details are still saved, "
                "so you can pick another time or try this one again.</p>"
                f"<div class='actions'><a class='button primary' href='/ui/slots/{doctor_key}'>"
                "Choose another time</a>"
                f"<a class='button secondary' href='/ui/book/{doctor_key}'>Try this slot again</a>"
                "<a class='button secondary' href='/ui/doctors'>Choose another doctor</a></div></section>"
            )

        content = (
            f"{refresh}"
            "<section class='panel panel--hero'><span class='chip chip--accent'>Booking in progress</span>"
            f"<h1 class='panel__heading'>{escape(summary['doctorName'])}</h1>"
            f"<p class='card-meta'>{escape(summary['specialty'])} &middot; "
            f"{escape(summary['date'])} at {escape(summary['slotLabel'])}"
            f"{' &middot; ' + escape(summary['clinicName']) if summary['clinicName'] else ''}</p></section>"
            f"<section class='panel'><h2 class='panel__title'>Status</h2>{body}</section>"
            f"{outcome_panel}"
            f"{autofill_panel}"
            f"{checklist_panel}"
            f"<section class='panel'><h2 class='panel__title'>Progress</h2>"
            f"<div class='summary-list'>{step_html}</div></section>"
        )
        return _page(content, user, "Booking | MediGuide")

    @app.post("/ui/booking/{session_id}/otp")
    async def ui_booking_otp(request: Request, session_id: str):
        """Hand the patient's own code to the waiting provider session."""
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        session = booking_session.registry.get(session_id, user_id=user["id"])
        if session and session["state"] == booking_session.OTP_REQUIRED:
            form = await _form_data(request)
            code = re.sub(r"\D", "", str(form.get("code", "")))[:8]
            if code:
                # Handed straight to the worker; never stored, never logged.
                booking_worker.provide_otp(session_id, code)
        return RedirectResponse(f"/ui/booking/{quote(session_id)}", status_code=303)

    @app.get("/ui/slots/{doctor_id:path}", response_class=HTMLResponse)
    async def ui_live_slots(request: Request, doctor_id: str):
        """Live availability for one exact doctor, read from the provider now.

        Every time shown here was extracted by Playwright from Oladoc's own
        booking page for this doctor. Nothing is generated locally.
        """
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        doctor = _lookup_doctor(doctor_id)
        if not doctor:
            return _page(
                "<section class='panel'><h1>Doctor not found</h1>"
                "<p class='alert alert--error'><strong>DOCTOR_NOT_FOUND</strong> &middot; "
                "That doctor is no longer in this session's results.</p>"
                "<a class='button secondary' href='/ui/doctors'>Find a doctor</a></section>",
                user, "Doctor not found | MediGuide",
            )

        requested_date = str(request.query_params.get("date", "")).strip() or date.today().isoformat()
        clinic_id = str(request.query_params.get("clinic", "")).strip()
        doctor_key = quote(str(doctor["doctor_id"]), safe="")

        clinics: list = []
        schedule = None
        error = ""
        error_code = ""
        try:
            clinics = await asyncio.to_thread(oladoc_provider.get_doctor_clinics, doctor)
            if clinics:
                chosen = next((c for c in clinics if c["clinicId"] == clinic_id), None) or clinics[0]
                clinic_id = chosen["clinicId"]
                schedule = await asyncio.to_thread(
                    oladoc_provider.get_live_slots, doctor, requested_date, clinic_id=clinic_id
                )
        except oladoc_provider.ProviderError as exc:
            error, error_code = str(exc), getattr(exc, "code", "PROVIDER_UNAVAILABLE")
        except ValueError as exc:
            error, error_code = str(exc), "INVALID_DATE"

        # --- clinic selector -------------------------------------------------
        clinic_chips = "".join(
            f"<a class='chip chip--link{' chip--active' if c['clinicId'] == clinic_id else ''}' "
            f"href='/ui/slots/{doctor_key}?date={escape(requested_date)}&clinic={escape(c['clinicId'])}'>"
            f"{escape(c['name'])}{' · ' + escape(c['fee']) if c['fee'] else ''}</a>"
            for c in clinics
        )
        clinic_panel = (
            f"<section class='panel'><h2 class='panel__title'>Clinic</h2>"
            f"<p class='card-meta'>These are the clinics this doctor publishes on Oladoc.</p>"
            f"<div class='chip-row'>{clinic_chips}</div></section>" if clinics else ""
        )

        # --- date picker -----------------------------------------------------
        date_form = (
            f"<form class='inline-form' method='get' action='/ui/slots/{doctor_key}'>"
            f"<input type='hidden' name='clinic' value='{escape(clinic_id)}'>"
            "<div class='field'><label for='slot-date'>Appointment date</label>"
            f"<input id='slot-date' name='date' type='date' value='{escape(requested_date)}' "
            f"min='{date.today().isoformat()}' required></div>"
            "<button class='button primary' type='submit'>Check live availability</button></form>"
        )

        # --- results ---------------------------------------------------------
        if error_code == "DOCTOR_NOT_ON_OLADOC":
            # Not a fault: this doctor simply is not on Oladoc, and Oladoc is
            # the only provider MediGuide reads schedules from. Retrying would
            # never help, so offer a way forward instead of a Retry button.
            specialty_query = quote(str(doctor.get("specialty", "")), safe="")
            body = (
                "<div class='alert alert--warn'><strong>This doctor is not on Oladoc.</strong>"
                "<p>MediGuide reads live schedules and books through Oladoc only, so there is "
                "no schedule to show for them here. Nothing was booked, and no example times "
                "are shown in place of live data.</p>"
                f"<span class='alert__code'>Reference: {escape(error_code)}</span></div>"
                "<div class='actions'>"
                f"<a class='button primary' href='/ui/doctors?specialty={specialty_query}'>"
                f"Find a {escape(str(doctor.get('specialty', 'doctor')))} on Oladoc</a>"
                "<a class='button secondary' href='/ui/doctors'>Browse all Oladoc doctors</a></div>"
            )
        elif error:
            body = (
                f"<div class='alert alert--error'><strong>{escape(error)}</strong>"
                "<p>No example times are shown in place of live data.</p>"
                f"<span class='alert__code'>Reference: {escape(error_code)}</span></div>"
                f"<a class='button secondary' href='/ui/slots/{doctor_key}?date={escape(requested_date)}'>Retry</a>"
            )
        elif schedule is None:
            body = (
                "<div class='alert alert--warn'><strong>This doctor does not publish a bookable "
                "clinic on Oladoc, so live times cannot be read.</strong>"
                "<span class='alert__code'>Reference: NO_CLINIC_PUBLISHED</span></div>"
            )
        elif schedule["slots"]:
            chips = "".join(
                f"<form class='slot-form' method='post' action='/ui/booking/start'>"
                f"<input type='hidden' name='doctorId' value='{escape(str(doctor['doctor_id']))}'>"
                f"<input type='hidden' name='date' value='{escape(schedule['date'])}'>"
                f"<input type='hidden' name='time' value='{escape(slot['time24'])}'>"
                f"<input type='hidden' name='timeLabel' value='{escape(slot['startLabel'])}'>"
                f"<input type='hidden' name='clinicId' value='{escape(clinic_id)}'>"
                f"<input type='hidden' name='slotId' value='{escape(slot['slotId'])}'>"
                f"<input type='hidden' name='reason' value='Appointment booked through MediGuide'>"
                f"<button class='slot-chip' type='submit' "
                f"aria-label='Book {escape(schedule['date'])} at {escape(slot['startLabel'])}'>"
                f"<span class='slot-chip__time'>{escape(slot['startLabel'])}</span>"
                "<span class='slot-chip__hint'>Book this time</span></button></form>"
                for slot in schedule["slots"]
            )
            body = (
                f"<p class='card-meta'>{len(schedule['slots'])} live slot(s) read from Oladoc for "
                f"<strong>{escape(schedule['date'])}</strong> ({escape(schedule['timezone'])}).</p>"
                f"<div class='slot-grid'>{chips}</div>"
                f"<p class='card-note'>Source: <a href='{escape(schedule['sourceUrl'])}' target='_blank' "
                "rel='noopener noreferrer'>this doctor's Oladoc booking page</a>. Times are the provider's "
                "local clinic times and are shown exactly as published.</p>"
            )
        else:
            upcoming = "".join(
                f"<a class='chip chip--link' href='/ui/slots/{doctor_key}?date={escape(d)}&clinic={escape(clinic_id)}'>{escape(d)}</a>"
                for d in (schedule.get("availableDates") or [])[:10] if d != schedule["date"]
            )
            body = (
                "<div class='alert alert--warn'><strong>NO_SLOTS_PUBLISHED</strong> &middot; "
                f"Oladoc published no bookable times for {escape(schedule['date'])} at this clinic. "
                "This reflects the provider's real schedule; no example times are shown.</div>"
                + (f"<p class='card-meta'>Other dates this clinic offers:</p><div class='chip-row'>{upcoming}</div>" if upcoming else "")
            )

        content = (
            "<nav class='breadcrumb'><a href='/ui/doctors'>Find a doctor</a><span>/</span>"
            f"<span>{escape(doctor['doctor_name'])}</span></nav>"
            "<section class='panel panel--hero'><span class='chip chip--accent'>Live availability</span>"
            f"<h1 class='panel__heading'>{escape(doctor['doctor_name'])}</h1>"
            f"<p class='card-meta'>{escape(doctor.get('specialty', ''))}"
            f"{' · ' + escape(doctor.get('location', '')) if doctor.get('location') else ''}</p>"
            f"{date_form}</section>"
            f"{clinic_panel}"
            f"<section class='panel'><h2 class='panel__title'>Available times</h2>{body}</section>"
        )
        return _page(content, user, f"Live slots | {doctor['doctor_name']} | MediGuide")

    @app.get("/ui/book/{doctor_id:path}", response_class=HTMLResponse)
    async def ui_book_form(request: Request, doctor_id: str):
        user = _user(request)
        if not user:
            return RedirectResponse("/ui/login", status_code=303)
        doctor = _lookup_doctor(doctor_id)
        if not doctor:
            return _page(
                "<section class='panel'><h1>Doctor not found</h1>"
                "<p class='notice danger'><strong>DOCTOR_NOT_FOUND</strong> &middot; That doctor is no longer in this session's results.</p>"
                "<p class='muted'>Search again so the doctor can be identified exactly before booking.</p>"
                "<a class='button secondary' href='/ui/doctors'>Find a doctor</a></section>",
                user,
                "Doctor not found | MediGuide",
            )

        flow_log.event(flow_log.DOCTOR_SELECTED, **identity.describe(doctor))
        flow_log.event(flow_log.APPOINTMENT_DOCTOR_SET, doctor_id=doctor.get("doctor_id"))

        preset_date = str(request.query_params.get("date", "")).strip()
        preset_time = str(request.query_params.get("time", "")).strip()
        preset_clinic = str(request.query_params.get("clinic", "")).strip()
        preset_time_label = str(request.query_params.get("label", "")).strip() or preset_time
        profile_url = identity.booking_url_for(doctor)
        profile_link = (
            f"<p class='muted'><a href='{escape(profile_url)}' target='_blank' rel='noopener'>Open this exact Oladoc profile</a></p>"
            if profile_url else
            "<p class='notice danger'><strong>DOCTOR_PROFILE_UNAVAILABLE</strong> &middot; No Oladoc profile URL is on file for this doctor, so automated booking cannot verify them.</p>"
        )
        identity_note = {
            "provider_doctor_id": "Verified by this doctor's Oladoc provider id.",
            "profile_url": "Verified by this doctor's exact Oladoc profile URL.",
            "name_specialty": "Only name and specialty are available for verification.",
        }.get(str(doctor.get("identity_strength")), "")

        content = (
            "<section class='panel'><div class='eyebrow'>Real provider booking</div>"
            f"<h1>Book with {escape(doctor['doctor_name'])}</h1>"
            f"<p class='muted'>{escape(doctor.get('specialty', ''))}"
            f"{' &middot; ' + escape(doctor.get('location', '')) if doctor.get('location') else ''}</p>"
            f"<p class='muted'>{escape(identity_note)}</p>"
            f"{profile_link}"
            "<form method='post' action='/ui/booking/start'>"
            f"<input type='hidden' name='doctorId' value='{escape(str(doctor['doctor_id']))}'>"
            f"<input type='hidden' name='clinicId' value='{escape(preset_clinic)}'>"
            f"<input type='hidden' name='timeLabel' value='{escape(preset_time_label)}'>"
            "<div class='formgrid'>"
            "<div class='field'><label for='booking-doctor'>Doctor</label>"
            f"<input id='booking-doctor' name='doctorName' value='{escape(doctor['doctor_name'])}' readonly>"
            "<span class='muted'>This appointment is locked to the doctor you selected.</span></div>"
            f"<div class='field'><label>Specialty</label><input value='{escape(doctor.get('specialty', ''))}' readonly></div>"
            f"<div class='field'><label for='booking-date'>Date from live schedule</label>"
            f"<input id='booking-date' name='date' type='date' value='{escape(preset_date)}' required></div>"
            f"<div class='field'><label for='booking-time'>Time from live schedule</label>"
            f"<input id='booking-time' name='time' type='time' value='{escape(preset_time)}' required></div>"
            "<div class='field full'><label>Reason for visit</label>"
            "<textarea name='reason' required placeholder='Tell the doctor what you would like help with.'></textarea></div>"
            "</div><button type='submit'>Start real booking</button></form>"
            "<p class='muted'>OTP and CAPTCHA are completed by you. Payment remains pay at clinic. "
            "We verify the doctor on Oladoc before anything is submitted.</p></section>"
        )
        return _page(content, user, "Book appointment | MediGuide")

    @app.post("/ui/appointments/real")
    async def ui_real_appointment_book(request: Request):
        """Retired entry point.

        This used to run its own 20-second booking attempt that could not show a
        checklist, a confirmation card or send an email. It now forwards into the
        session-based flow so there is exactly one booking path.
        """
        return await ui_booking_start(request)


    @app.post("/ui/book/{doctor_id:path}")
    async def ui_book(request: Request, doctor_id: str):
        return await ui_real_appointment_book(request)

    @app.get("/ui/logout")
    async def ui_logout():
        response = RedirectResponse("/ui", status_code=303)
        response.delete_cookie("mediguide_token")
        return response
