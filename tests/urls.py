from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("app/", include("tests.testapp.urls")),
    path("mcp/", include("django_mcp.urls")),
]
