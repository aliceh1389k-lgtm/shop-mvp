from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import requests
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from orders.models import Order
from .models import PaymentAttempt

log = logging.getLogger(__name__)


# -----------------------------
# Currency helpers (IRR <-> IRT)
# -----------------------------
def _gateway_currency() -> str:
    """
    Zarinpal supports: IRR (Rial) and IRT (Toman)
    """
    return str(getattr(settings, "ZARINPAL_CURRENCY", "IRR") or "IRR").upper().strip()


def _amount_for_gateway(amount_irr: int) -> int:
    """
    Our DB stores IRR (rial) in fields like total_irr.
    If gateway currency is IRT (toman), convert IRR -> IRT by dividing by 10.

    NOTE:
    If amount_irr is not divisible by 10, we use ceil division to avoid undercharging.
    (Better long-term: make your prices multiples of 10 rial.)
    """
    amount_irr = int(amount_irr or 0)
    if _gateway_currency() == "IRT":
        return (amount_irr + 9) // 10  # ceil(amount_irr/10)
    return amount_irr


# -----------------------------
# Zarinpal helpers
# -----------------------------
def _zarinpal_endpoints() -> Tuple[str, str, str]:
    """
    Returns: (REQUEST_URL, VERIFY_URL, STARTPAY_BASE)
    """
    if getattr(settings, "ZARINPAL_SANDBOX", False):
        base = "https://sandbox.zarinpal.com"
        return (
            f"{base}/pg/v4/payment/request.json",
            f"{base}/pg/v4/payment/verify.json",
            f"{base}/pg/StartPay/",
        )

    return (
        "https://payment.zarinpal.com/pg/v4/payment/request.json",
        "https://payment.zarinpal.com/pg/v4/payment/verify.json",
        "https://www.zarinpal.com/pg/StartPay/",
    )


def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Always returns a dict shaped like Zarinpal response or a synthetic error dict.
    """
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"accept": "application/json", "content-type": "application/json"},
            timeout=20,
        )
    except Exception as e:
        return {"data": None, "errors": {"code": -998, "message": f"Request exception: {e!r}"}}

    try:
        return resp.json()
    except Exception:
        return {
            "data": None,
            "errors": {"code": -999, "message": f"Non-JSON response (HTTP {resp.status_code})"},
        }


def _extract_code_and_message(raw: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """
    MUST return exactly 2 values (code, message).

    Zarinpal success:
      {"data": {"code": 100, "message": "...", ...}, "errors": []}

    Zarinpal failure (common):
      {"data": null, "errors": {"code": -12, "message": "..."}}

    Sometimes:
      {"data": null, "errors": [{"code": -12, "message": "..."}]}
    """
    data = raw.get("data")
    errors = raw.get("errors")

    # success-style
    if isinstance(data, dict) and isinstance(data.get("code"), int):
        code = data.get("code")
        msg = data.get("message") if isinstance(data.get("message"), str) else ""
        return code, msg

    # error-style dict
    if isinstance(errors, dict):
        code = errors.get("code") if isinstance(errors.get("code"), int) else None
        msg = errors.get("message") if isinstance(errors.get("message"), str) else ""
        return code, msg

    # error-style list
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        e0 = errors[0]
        code = e0.get("code") if isinstance(e0.get("code"), int) else None
        msg = e0.get("message") if isinstance(e0.get("message"), str) else ""
        return code, msg

    return None, ""


def _extract_authority(raw: Dict[str, Any]) -> str:
    data = raw.get("data")
    if isinstance(data, dict) and isinstance(data.get("authority"), str):
        return data["authority"]
    return ""


def _extract_ref_id(raw: Dict[str, Any]) -> Optional[str]:
    data = raw.get("data")
    if isinstance(data, dict):
        rid = data.get("ref_id") if data.get("ref_id") is not None else data.get("refId")
        if rid is None:
            return None
        return str(rid)
    return None


def _startpay_url(authority: str) -> str:
    _, _, startpay = _zarinpal_endpoints()
    return f"{startpay}{authority}"


def _attempt_create(
    *,
    order: Order,
    stage: str,
    code: Optional[int],
    authority: str = "",
    ref_id: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Never break payment flow even if attempt logging fails.
    """
    try:
        PaymentAttempt.objects.create(
            order=order,
            stage=stage,
            code=code,
            authority=authority or "",
            ref_id=ref_id,
            raw=raw or {},
        )
    except Exception:
        log.exception("Failed to create PaymentAttempt (ignored).")


# -----------------------------
# Views
# -----------------------------

@require_POST
def start_payment(request: HttpRequest, order_id: str) -> HttpResponse:
    """
    POST /payments/start/<uuid>/
    Strong idempotency:
      - Locks order row to prevent concurrent double-click requests
      - Uses a sentinel '__LOCK__' while requesting authority
      - Reuses authority if already created
    """
    LOCK_SENTINEL = "__LOCK__"

    # 1) Lock row and decide what to do
    with transaction.atomic():
        o = get_object_or_404(Order.objects.select_for_update(), id=order_id)

        if getattr(o, "status", "") == "PAID":
            return HttpResponseRedirect(f"/orders/{o.id}/")

        if getattr(o, "status", "") != "PENDING_PAYMENT":
            return HttpResponseRedirect(f"/orders/{o.id}/")

        existing = (getattr(o, "payment_session_id", "") or "").strip()

        # already has authority -> go to startpay (idempotent)
        if existing and existing != LOCK_SENTINEL:
            return HttpResponseRedirect(_startpay_url(existing))

        # another request is currently in progress -> stop spamming gateway
        if existing == LOCK_SENTINEL:
            return HttpResponse(
                "در حال آماده‌سازی پرداخت... چند ثانیه بعد دوباره تلاش کن.",
                status=429,
            )

        # set lock sentinel so concurrent requests won't call gateway
        o.payment_session_id = LOCK_SENTINEL
        o.payment_provider = getattr(settings, "PAYMENTS_PROVIDER", "zarinpal")
        o.save(update_fields=["payment_session_id", "payment_provider"])

        merchant_id = str(getattr(settings, "ZARINPAL_MERCHANT_ID", "") or "").strip()
        if not merchant_id:
            o.payment_session_id = ""
            o.save(update_fields=["payment_session_id"])
            return HttpResponse("Misconfigured: ZARINPAL_MERCHANT_ID is empty", status=500)

    # 2) Make gateway request OUTSIDE the DB lock
    request_url, _, _ = _zarinpal_endpoints()
    callback_url = f"{settings.PUBLIC_BASE_URL}{reverse('payments:zarinpal_callback')}"
    currency = _gateway_currency()
    amount = _amount_for_gateway(getattr(o, "total_irr", 0))

    payload = {
        "merchant_id": merchant_id,
        "amount": int(amount),
        "currency": currency,
        "callback_url": callback_url,
        "description": f"پرداخت سفارش {o.id}",
        "metadata": {"order_id": str(o.id)},
    }

    raw = _post_json(request_url, payload)
    code, msg = _extract_code_and_message(raw)
    authority = _extract_authority(raw)

    # 3) Save outcome (authority or clear lock)
    with transaction.atomic():
        o2 = Order.objects.select_for_update().get(id=o.id)

        _attempt_create(order=o2, stage="REQUEST", code=code, authority=authority, raw=raw)

        if code == 100 and authority:
            o2.payment_session_id = authority
            o2.save(update_fields=["payment_session_id"])
            return HttpResponseRedirect(_startpay_url(authority))

        # request failed -> clear sentinel so user can retry
        o2.payment_session_id = ""
        o2.save(update_fields=["payment_session_id"])

    if code == -12:
        return HttpResponse(f"Too many attempts, please try again later. (code={code})", status=429)

    return HttpResponse(f"Payment request failed: {msg or 'unknown'} (code={code})", status=400)


@require_GET
def zarinpal_callback(request: HttpRequest) -> HttpResponse:
    """
    GET /payments/zarinpal/callback/?Authority=...&Status=OK|NOK
    """
    authority = (request.GET.get("Authority") or "").strip()
    status = (request.GET.get("Status") or "").strip().upper()

    if not authority:
        return HttpResponseBadRequest("Missing Authority")

    # Find order by REQUEST attempt authority, fallback by payment_session_id
    attempt = (
        PaymentAttempt.objects.filter(stage="REQUEST", authority=authority)
        .select_related("order")
        .order_by("-id")
        .first()
    )
    order = attempt.order if attempt else None
    if order is None:
        order = Order.objects.filter(payment_session_id=authority).order_by("-created_at").first()

    if order is None:
        return HttpResponseRedirect("/products/")

    if getattr(order, "status", "") == "PAID":
        return HttpResponseRedirect(f"/orders/{order.id}/")

    # Cancel / NOK
    if status != "OK":
        with transaction.atomic():
            o = Order.objects.select_for_update().get(id=order.id)
            o.payment_session_id = ""
            o.save(update_fields=["payment_session_id"])
            _attempt_create(
                order=o,
                stage="CANCEL",
                code=0,
                authority=authority,
                raw={"Status": status, "Authority": authority},
            )
        return HttpResponseRedirect(f"/orders/{order.id}/")

    # Verify
    merchant_id = str(getattr(settings, "ZARINPAL_MERCHANT_ID", "") or "").strip()
    if not merchant_id:
        return HttpResponse("Misconfigured: ZARINPAL_MERCHANT_ID is empty", status=500)

    _, verify_url, _ = _zarinpal_endpoints()

    amount = _amount_for_gateway(getattr(order, "total_irr", 0))

    payload = {
        "merchant_id": merchant_id,
        "amount": int(amount),
        "authority": authority,
    }

    with transaction.atomic():
        o = Order.objects.select_for_update().get(id=order.id)

        raw = _post_json(verify_url, payload)
        code, msg = _extract_code_and_message(raw)
        ref_id = _extract_ref_id(raw)

        _attempt_create(order=o, stage="VERIFY", code=code, authority=authority, ref_id=ref_id, raw=raw)

        if code in (100, 101):
            o.status = "PAID"
            o.payment_session_id = authority

            if hasattr(o, "payment_ref_id"):
                o.payment_ref_id = ref_id or ""

            fields = ["status", "payment_session_id"]
            if hasattr(o, "payment_ref_id"):
                fields.append("payment_ref_id")

            o.save(update_fields=fields)
            return HttpResponseRedirect(f"/orders/{o.id}/")

        # verify failed: allow retry
        o.payment_session_id = ""
        o.save(update_fields=["payment_session_id"])
        return HttpResponseRedirect(f"/orders/{o.id}/")
