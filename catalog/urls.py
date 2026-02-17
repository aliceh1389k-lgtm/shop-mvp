from django.urls import path
from . import views

app_name = "catalog"

urlpatterns = [
    path("products/", views.product_list, name="product_list"),
    path("products/<slug:slug>/", views.product_detail, name="product_detail"),
]
