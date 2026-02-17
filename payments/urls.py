from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    path("start/<uuid:order_id>/", views.start_payment, name="start"),
]
