from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils.html import format_html

from .models import Order

# اگر OrderItem اسمش فرق داشت، ImportError نخوریم
try:
    from .models import OrderItem
except Exception:
    OrderItem = None


def _toman(amount_irr: int) -> int:
    try:
        return (int(amount_irr) + 9) // 10
    except Exception:
        return 0


def _has_field(model, name: str) -> bool:
    return any(getattr(f, "name", None) == name for f in model._meta.get_fields())


def _short(s: str, n: int = 10) -> str:
    s = str(s or "")
    return s if len(s) <= n else (s[:n] + "…")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    autocomplete_fields = ("product",)

    # فیلدهای واقعی را داینامیک می‌سازیم که اگر اسمی فرق داشت ادمین نترکه
    def get_readonly_fields(self, request, obj=None):
        ro = []
        for f in ("product", "qty", "unit_price_irr"):
            if _has_field(self.model, f):
                ro.append(f)
        ro.append("line_total_toman")
        return ro

    def get_fields(self, request, obj=None):
        fields = []
        for f in ("product", "qty", "unit_price_irr"):
            if _has_field(self.model, f):
                fields.append(f)
        fields.append("line_total_toman")
        return fields

    @admin.display(description="جمع (تومان)")
    def line_total_toman(self, obj):
        qty = getattr(obj, "qty", 0) or 0
        unit = getattr(obj, "unit_price_irr", 0) or 0
        return intcomma(_toman(int(qty) * int(unit)))


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_per_page = 50
    save_on_top = True
    date_hierarchy = "created_at" if _has_field(Order, "created_at") else None

    search_fields = ("id",)
    list_filter = tuple(f for f in ("status", "created_at") if _has_field(Order, f))

    # ---------- list_display حرفه‌ای ----------
    def get_list_display(self, request):
        cols = ["id_short", "status_badge"]
        if _has_field(Order, "total_irr"):
            cols.append("total_toman")
        if _has_field(Order, "created_at"):
            cols.append("created_at")
        if _has_field(Order, "payment_provider"):
            cols.append("payment_provider")
        if _has_field(Order, "payment_ref_id"):
            cols.append("payment_ref_short")
        return tuple(cols)

    ordering = ("-created_at",) if _has_field(Order, "created_at") else ("-id",)

    # ---------- readonly_fields حرفه‌ای ----------
    def get_readonly_fields(self, request, obj=None):
        ro = ["id"]
        for f in ("created_at", "currency", "total_irr", "payment_provider", "payment_session_id", "payment_ref_id"):
            if _has_field(Order, f):
                ro.append(f)
        ro += ["total_toman"]
        return tuple(ro)

    # ---------- fieldsets مرتب ----------
    def get_fieldsets(self, request, obj=None):
        main_fields = []
        for f in ("status",):
            if _has_field(Order, f):
                main_fields.append(f)

        money_fields = []
        if _has_field(Order, "currency"):
            money_fields.append("currency")
        if _has_field(Order, "total_irr"):
            money_fields.append("total_irr")
        money_fields.append("total_toman")

        pay_fields = []
        for f in ("payment_provider", "payment_session_id", "payment_ref_id"):
            if _has_field(Order, f):
                pay_fields.append(f)

        meta_fields = []
        for f in ("id", "created_at"):
            if _has_field(Order, f) or f == "id":
                meta_fields.append(f)

        sets = []
        if main_fields:
            sets.append(("وضعیت", {"fields": tuple(main_fields)}))
        sets.append(("مبلغ", {"fields": tuple(money_fields)}))
        if pay_fields:
            sets.append(("پرداخت", {"fields": tuple(pay_fields)}))
        sets.append(("متادیتا", {"fields": tuple(meta_fields)}))

        return tuple(sets)

    # ---------- Inline آیتم‌ها ----------
    def get_inlines(self, request, obj):
        if OrderItem is None:
            return []
        return [OrderItemInline]

    # ---------- نمایش‌ها ----------
    @admin.display(description="سفارش", ordering="id")
    def id_short(self, obj: Order) -> str:
        return _short(getattr(obj, "id", ""), 12)

    @admin.display(description="وضعیت", ordering="status")
    def status_badge(self, obj: Order) -> str:
        s = str(getattr(obj, "status", "") or "")
        if s == "PAID":
            return format_html('<span style="padding:2px 8px;border-radius:10px;background:#e6f4ea;color:#137333;">پرداخت‌شده</span>')
        if s == "PENDING_PAYMENT":
            return format_html('<span style="padding:2px 8px;border-radius:10px;background:#fff4e5;color:#b06000;">در انتظار پرداخت</span>')
        return format_html('<span style="padding:2px 8px;border-radius:10px;background:#eee;color:#333;">{}</span>', s)

    @admin.display(description="مبلغ (تومان)", ordering="total_irr")
    def total_toman(self, obj: Order) -> str:
        return intcomma(_toman(getattr(obj, "total_irr", 0)))

    @admin.display(description="کد رهگیری", ordering="payment_ref_id")
    def payment_ref_short(self, obj: Order) -> str:
        return _short(getattr(obj, "payment_ref_id", ""), 12)

    # ---------- Actions حرفه‌ای ----------
    @admin.action(description="علامت‌گذاری به عنوان PAID")
    def mark_paid(self, request, queryset):
        queryset.update(status="PAID")

    @admin.action(description="علامت‌گذاری به عنوان PENDING_PAYMENT")
    def mark_pending(self, request, queryset):
        queryset.update(status="PENDING_PAYMENT")

    actions = ("mark_paid", "mark_pending")
