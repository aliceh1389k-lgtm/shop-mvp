from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("create/<slug:slug>/", views.create_order, name="create"),
    path("<uuid:order_id>/", views.order_detail, name="detail"),
]
