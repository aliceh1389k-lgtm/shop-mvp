from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma

from .models import Product


def _toman(amount_irr: int) -> int:
    try:
        return (int(amount_irr) + 9) // 10
    except Exception:
        return 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "slug", "price_toman", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "slug")
    ordering = ("id",)
    list_per_page = 50
    save_on_top = True

    # این باعث میشه autocomplete_fields در OrderItemInline هم کار کنه
    def get_search_results(self, request, queryset, search_term):
        return super().get_search_results(request, queryset, search_term)

    @admin.display(description="قیمت (تومان)", ordering="price_irr")
    def price_toman(self, obj: Product) -> str:
        return f"{intcomma(_toman(getattr(obj, 'price_irr', 0)))}"
