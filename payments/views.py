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
    resp = requests.post(
        url,
        json=payload,
        headers={"accept": "application/json", "content-type": "application/json"},
        timeout=20,
    )
    try:
        return resp.json()
    except Exception:
        return {
            "data": None,
            "errors": [{"code": -999, "message": f"Non-JSON response (HTTP {resp.status_code})"}],
        }


def _extract_code_and_message(raw: Dict[str, Any]) -> Tuple[Optional[int], str]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else None
    errors = raw.get("errors")

    if data and isinstance(data.get("code"), int):
        return data["code"], str(data.get("message", ""))

    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        msg = errors[0].get("message", "")
        return (code if isinstance(code, int) else None), str(msg)

    return None, ""


def _attempt_create(
    *,
    order: Order,
    stage: str,
    code: Optional[int],
    authority: str = "",
    ref_id: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> None:
    # Attempt logging must never break the flow
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


def _startpay_url(authority: str) -> str:
    _, _, startpay = _zarinpal_endpoints()
    return f"{startpay}{authority}"


@require_POST
def start_payment(request: HttpRequest, order_id: str) -> HttpResponse:
    """
    POST /payments/start/<uuid>/
    Idempotent: if order already has authority in payment_session_id, reuse it.
    """
    o = get_object_or_404(Order, id=order_id)

    # اگر قبلا پرداخت شده: فقط برگرد
    if getattr(o, "status", "") == "PAID":
        return HttpResponseRedirect(f"/orders/{o.id}/")

    # اگر وضعیت قابل پرداخت نیست: برگرد
    if getattr(o, "status", "") != "PENDING_PAYMENT":
        return HttpResponseRedirect(f"/orders/{o.id}/")

    # idempotency: اگر authority از قبل داریم، دوباره request جدید نزن
    existing_authority = (getattr(o, "payment_session_id", "") or "").strip()
    if existing_authority:
        return HttpResponseRedirect(_startpay_url(existing_authority))

    request_url, _, _ = _zarinpal_endpoints()

    callback_url = f"{settings.PUBLIC_BASE_URL}{reverse('payments:zarinpal_callback')}?order_id={o.id}"
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": int(o.total_irr),
        "currency": getattr(settings, "ZARINPAL_CURRENCY", "IRR"),
        "callback_url": callback_url,
        "description": f"پرداخت سفارش {o.id}",
        "metadata": {"auto_verify": False},
    }

    raw = _post_json(request_url, payload)
    code, msg = _extract_code_and_message(raw)
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    authority = str(data.get("authority") or "")

    _attempt_create(order=o, stage="REQUEST", code=code, authority=authority, raw=raw)

    if code == 100 and authority:
        # payment_session_id را خالی/None نکن (ممکنه NOT NULL باشد)
        o.payment_provider = getattr(settings, "PAYMENTS_PROVIDER", "zarinpal")
        o.payment_session_id = authority
        o.save(update_fields=["payment_provider", "payment_session_id"])
        return HttpResponseRedirect(_startpay_url(authority))

    # خطای -12: throttling از سمت زرین‌پال
    if code == -12:
        return HttpResponse(f"Too many attempts, please try again later. (code={code})", status=429)

    return HttpResponse(f"Payment request failed: {msg or 'unknown'} (code={code})", status=400)


@require_GET
def zarinpal_callback(request: HttpRequest) -> HttpResponse:
    """
    GET /payments/zarinpal/callback/?Authority=...&Status=OK|NOK
    (order_id ممکن است باشد یا نباشد؛ تست‌ها معمولاً نمی‌فرستند.)
    """
    authority = (request.GET.get("Authority") or "").strip()
    status = (request.GET.get("Status") or "").strip().upper()
    order_id = (request.GET.get("order_id") or "").strip()

    if not authority:
        return HttpResponseBadRequest("Missing Authority")

    # 1) اگر order_id داده شده، همان را بگیر
    order = None
    if order_id:
        try:
            order = Order.objects.filter(id=order_id).first()
        except Exception:
            order = None

    # 2) اگر نبود، از PaymentAttempt پیدا کن (راه درست برای تست‌ها)
    if order is None:
        attempt = (
            PaymentAttempt.objects.filter(stage="REQUEST", authority=authority)
            .select_related("order")
            .order_by("-id")
            .first()
        )
        order = attempt.order if attempt else None

    # 3) اگر باز نبود، از خود Order با payment_session_id پیدا کن
    if order is None:
        order = Order.objects.filter(payment_session_id=authority).order_by("-created_at").first()

    if order is None:
        # برای UX بهتر: به جای 400، یک 302 به لیست محصولات
        return HttpResponseRedirect("/products/")

    # اگر قبلاً paid شده، همان صفحه سفارش
    if getattr(order, "status", "") == "PAID":
        return HttpResponseRedirect(f"/orders/{order.id}/")

    # اگر کاربر Cancel کرده یا Status != OK
    if status != "OK":
        with transaction.atomic():
            o = Order.objects.select_for_update().get(id=order.id)
            o.payment_session_id = ""  # دقیقاً چیزی که تست می‌خواهد
            o.save(update_fields=["payment_session_id"])
            _attempt_create(
                order=o,
                stage="CANCEL",
                code=0,
                authority=authority,
                raw={"Status": status, "Authority": authority},
            )
        return HttpResponseRedirect(f"/orders/{order.id}/")

    # ---- Verify وقتی OK است ----
    _, verify_url, _ = _zarinpal_endpoints()

    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": int(order.total_irr),
        "authority": authority,
    }

    with transaction.atomic():
        o = Order.objects.select_for_update().get(id=order.id)

        raw = _post_json(verify_url, payload)
        code, msg = _extract_code_and_message(raw)

        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        ref_id = data.get("ref_id") or data.get("refId")

        _attempt_create(
            order=o,
            stage="VERIFY",
            code=code,
            authority=authority,
            ref_id=str(ref_id) if ref_id else None,
            raw=raw,
        )

        if code in (100, 101):
            o.status = "PAID"
            o.payment_session_id = authority

            # ذخیره ref_id برای پاس شدن تست و گزارش پرداخت
            if hasattr(o, "payment_ref_id"):
                o.payment_ref_id = str(ref_id) if ref_id is not None else ""

            fields = ["status", "payment_session_id"]
            if hasattr(o, "payment_ref_id"):
                fields.append("payment_ref_id")

            o.save(update_fields=fields)
            return HttpResponseRedirect(f"/orders/{o.id}/")
            
