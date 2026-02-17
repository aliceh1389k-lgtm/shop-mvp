from django.db import models
from orders.models import Order


class PaymentEvent(models.Model):
    provider = models.CharField(max_length=50)  # e.g. "stripe"
    event_id = models.CharField(max_length=200, unique=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payment_events")

    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"
