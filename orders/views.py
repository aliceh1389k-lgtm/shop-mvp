from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Product
from .models import Order, OrderItem


@require_POST
def create_order(request, slug: str):
    product = get_object_or_404(Product, slug=slug, is_active=True)

    with transaction.atomic():
        order = Order.objects.create(
            status=Order.Status.PENDING_PAYMENT,
            currency="IRR",
            total_irr=product.price_irr,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=1,
            unit_price_irr=product.price_irr,
        )

    return redirect("orders:detail", order_id=order.id)


def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    items = order.items.select_related("product").all()
    computed_total = sum(i.line_total_irr() for i in items)

    return render(
        request,
        "orders/order_detail.html",
        {"order": order, "items": items, "computed_total": computed_total},
    )
