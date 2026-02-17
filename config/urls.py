from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="catalog:product_list", permanent=False)),
    path("admin/", admin.site.urls),
    path("", include("catalog.urls")),
    path("orders/", include("orders.urls")),
    path("payments/", include("payments.urls")),
]
