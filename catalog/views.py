from django.shortcuts import get_object_or_404, render
from .models import Product


def product_list(request):
    products = Product.objects.filter(is_active=True).order_by("-created_at")
    return render(request, "catalog/product_list.html", {"products": products})


def product_detail(request, slug: str):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    return render(request, "catalog/product_detail.html", {"product": product})
