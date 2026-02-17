import requests

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from orders.models import Order
from payments.models import PaymentAttempt



def _zarinpal_urls():
    sandbox = getattr(settings, "ZARINPAL_SANDBOX", True)
    if sandbox:
        return {
            "request": "https://sandbox.zarinpal.com/pg/v4/payment/request.json",
            "verify": "https://sandbox.zarinpal.com/pg/v4/payment/verify.json",
            "startpay": "https://sandbox.zarinpal.com/pg/StartPay/",
        }
    return {
        "request": "https://payment.zarinpal.com/pg/v4/payment/request.json",
        "verify": "https://payment.zarinpal.com/pg/v4/payment/verify.json",
        "startpay": "https://payment.zarinpal.com/pg/StartPay/",
    }


def _zarinpal_enabled() -> bool:
    provider = getattr(settings, "PAYMENTS_PROVIDER", "mock").lower()
    merchant_id = getattr(settings, "ZARINPAL_MERCHANT_ID", "")
    return provider == "zarinpal" and bool(merchant_id)

@require_POST
def start_payment(request, order_id):
    # قفل DB: همزمانی و چند کلیک پشت سر هم را کنترل می‌کنیم
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if not order:
            return render(request, "payments/payment_error.html", {"error": "ORDER_NOT_FOUND"})

        if order.status == Order.Status.PAID:
            # اگر قبلاً پرداخت شده، دوباره درگاه نرو
            return redirect("orders:detail", order_id=order.id)

        if order.status != Order.Status.PENDING_PAYMENT:
            return render(request, "payments/payment_error.html", {"order": order})

        if not _zarinpal_enabled():
            return render(request, "payments/payment_mock.html", {"order": order})

        urls = _zarinpal_urls()

        public_base = getattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        callback_url = f"{public_base}{reverse('payments:zarinpal_callback')}"

        # اگر قبلاً authority گرفتیم، دوباره request نزن؛ همان را استفاده کن
        if order.payment_provider == "zarinpal" and order.payment_session_id:
            return redirect(f"{urls['startpay']}{order.payment_session_id}", permanent=False)

        payload = {
            "merchant_id": getattr(settings, "ZARINPAL_MERCHANT_ID", ""),
            "amount": int(order.total_irr),
            "callback_url": callback_url,
            "description": f"Order {order.id}",
            "currency": getattr(settings, "ZARINPAL_CURRENCY", "IRR"),
            "metadata": {"order_id": str(order.id)},
        }

        headers = {"accept": "application/json", "content-type": "application/json"}

        try:
            resp = requests.post(urls["request"], json=payload, headers=headers, timeout=20)
            data = resp.json()
        except Exception:
            PaymentAttempt.objects.create(
                order=order,
                provider="zarinpal",
                stage=PaymentAttempt.Stage.REQUEST,
                raw={"error": "NETWORK_ERROR"},
            )
            return render(request, "payments/payment_error.html", {"order": order, "error": "NETWORK_ERROR"})

        d = data.get("data") or {}
        code = d.get("code")
        authority = d.get("authority", "")

        PaymentAttempt.objects.create(
            order=order,
            provider="zarinpal",
            stage=PaymentAttempt.Stage.REQUEST,
            authority=authority or "",
            code=code if isinstance(code, int) else None,
            raw=data,
        )

        if code != 100 or not authority:
            return render(request, "payments/payment_error.html", {"order": order, "zarinpal": data})

        order.payment_provider = "zarinpal"
        order.payment_session_id = authority
        order.save(update_fields=["payment_provider", "payment_session_id"])

        return redirect(f"{urls['startpay']}{authority}", permanent=False)


@require_GET
def zarinpal_callback(request):
    authority = request.GET.get("Authority", "")
    status = request.GET.get("Status", "")

    if not authority:
        return HttpResponseBadRequest("Missing Authority")

    order = Order.objects.filter(payment_provider="zarinpal", payment_session_id=authority).first()
    if not order:
        return render(request, "payments/zarinpal_result.html", {"ok": False, "error": "ORDER_NOT_FOUND"})

    # Verify must be called ONLY when Status=OK
    if status != "OK":
        return render(request, "payments/zarinpal_result.html", {"ok": False, "order": order, "status": status})

    urls = _zarinpal_urls()
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": int(order.total_irr),
        "authority": authority,
    }
    headers = {"accept": "application/json", "content-type": "application/json"}

    try:
        resp = requests.post(urls["verify"], json=payload, headers=headers, timeout=20)
        data = resp.json()
    except Exception:
        return render(request, "payments/zarinpal_result.html", {"ok": False, "order": order, "error": "NETWORK_ERROR"})
    PaymentAttempt.objects.create(
    order=order,
    provider="zarinpal",
    stage=PaymentAttempt.Stage.VERIFY,
    authority=authority,
    code=d.get("code") if isinstance(d.get("code"), int) else None,
    ref_id=d.get("ref_id") if d.get("ref_id") else None,
    raw=data,
    )


    d = data.get("data") or {}
    code = d.get("code")

    # 100 = verified, 101 = already verified (treat both as paid)
    if code in (100, 101):
        ref_id = d.get("ref_id")
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(id=order.id)
            if locked.status != Order.Status.PAID:
                locked.status = Order.Status.PAID
                locked.paid_at = timezone.now()
            # Always store ref_id if present
            if ref_id:
                locked.payment_ref_id = int(ref_id)
            locked.save(update_fields=["status", "paid_at", "payment_ref_id"])

        return render(request, "payments/zarinpal_result.html", {"ok": True, "order": order, "verify": data})

    return render(request, "payments/zarinpal_result.html", {"ok": False, "order": order, "verify": data})
