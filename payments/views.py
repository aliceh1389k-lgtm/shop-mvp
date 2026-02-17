from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from orders.models import Order


@require_POST
def start_payment(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    # Guard: only allow payment for pending orders
    if order.status != Order.Status.PENDING_PAYMENT:
        return render(request, "payments/payment_error.html", {"order": order})

    # For now (no real gateway): simulate redirect to "payment page"
    return render(request, "payments/payment_mock.html", {"order": order})
