from django.contrib import admin
from .models import PaymentAttempt


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "stage", "code", "authority", "ref_id", "created_at")
    list_filter = ("stage", "code", "created_at")
    search_fields = ("order__id", "authority", "ref_id")
    readonly_fields = ("order", "stage", "code", "authority", "ref_id", "raw", "created_at")
