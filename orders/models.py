import uuid

from django.core.validators import MinValueValidator
from django.db import models


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
        PAID = "PAID", "Paid"
        CANCELED = "CANCELED", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_PAYMENT)

    currency = models.CharField(max_length=3, default="IRR")
    total_irr = models.PositiveIntegerField(default=0)

    # Payment fields (NEW)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_provider = models.CharField(max_length=50, blank=True)
    payment_session_id = models.CharField(max_length=255, blank=True)  # Zarinpal authority
    payment_ref_id = models.BigIntegerField(null=True, blank=True)     # Zarinpal ref_id after verify

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Order {self.id} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price_irr = models.PositiveIntegerField()

    def line_total_irr(self) -> int:
        return self.unit_price_irr * self.quantity

    def __str__(self) -> str:
        return f"{self.product_id} x{self.quantity}"
