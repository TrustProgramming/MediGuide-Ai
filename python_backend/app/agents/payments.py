from __future__ import annotations

import os
from typing import Any, Dict


PURPOSE_AMOUNTS = {"booking_facilitation_fee": 500}


def create_payment_intent(purpose: str, currency: str = "pkr") -> Dict[str, Any]:
    secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("Stripe test mode is not configured. Set STRIPE_SECRET_KEY; card data is never accepted by MediGuide.")
    if not secret.startswith("sk_test_"):
        raise RuntimeError("Only Stripe test-mode keys are accepted by this local payment agent.")
    try:
        import stripe
    except ImportError as exc:
        raise RuntimeError("Install the stripe package to enable the optional payment agent.") from exc
    amount = PURPOSE_AMOUNTS.get(purpose)
    if amount is None:
        raise ValueError("Unknown payment purpose.")
    stripe.api_key = secret
    intent = stripe.PaymentIntent.create(amount=amount, currency=currency, metadata={"purpose": purpose})
    return {"id": intent.id, "clientSecret": intent.client_secret, "amount": amount, "currency": currency, "purpose": purpose, "mode": "test"}
