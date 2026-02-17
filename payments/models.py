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
from django.db import models
from orders.models import Order


class PaymentAttempt(models.Model):
    class Stage(models.TextChoices):
        REQUEST = "REQUEST", "Request"
        VERIFY = "VERIFY", "Verify"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payment_attempts")
    provider = models.CharField(max_length=50, default="zarinpal")

    stage = models.CharField(max_length=10, choices=Stage.choices)
    authority = models.CharField(max_length=255, blank=True)

    code = models.IntegerField(null=True, blank=True)
    ref_id = models.BigIntegerField(null=True, blank=True)

    raw = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.provider} {self.stage} {self.code}"
