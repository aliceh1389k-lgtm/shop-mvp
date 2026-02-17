from django.contrib import admin
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "price_irr", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
