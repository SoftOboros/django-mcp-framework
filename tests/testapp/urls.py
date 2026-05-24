"""DMCP-02 fixture URLs: FBV + CBV + DRF ViewSet + DRF APIView."""

from __future__ import annotations

from django.urls import include, path
from rest_framework import routers

from .views import (
    PingAPIView,
    PostDetailView,
    PostListView,
    PostMultiVerbView,
    PostViewSet,
    hello,
    hello_authed,
)

router = routers.SimpleRouter()
router.register("posts", PostViewSet, basename="post")

urlpatterns = [
    path("hello/<str:who>/", hello, name="hello"),
    path("hello-auth/<str:who>/", hello_authed, name="hello_authed"),
    path("posts/<int:pk>/", PostDetailView.as_view(), name="post-detail"),
    path("posts/", PostListView.as_view(), name="post-list"),
    path("multi/", PostMultiVerbView.as_view(), name="post-multi"),
    path("ping/", PingAPIView.as_view(), name="ping"),
    path("api/", include(router.urls)),
]
