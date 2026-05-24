"""Views exercising DMCP-02 §5 / §8 / §10 cases.

- ``hello`` — bare FBV, NO auth gate (used by INV-DMCP02-4 tests).
- ``hello_authed`` — FBV with ``@login_required`` (auth gate detectable).
- ``PostDetailView`` — CBV ``DetailView``, ``LoginRequiredMixin`` + only-get
  (verb-narrows to ``view.retrieve:``).
- ``PostListView`` — CBV ``ListView``, ``LoginRequiredMixin`` + only-get
  (verb-narrows to ``view.list:``).
- ``PostMultiVerbView`` — CBV with both ``get`` and ``post`` defined
  (verb-narrows to ``view.invoke:`` — INV-DMCP02-5 fallback case).
- ``PostSerializer`` / ``PostViewSet`` — DRF ``ModelViewSet`` (full CRUD).
- ``ping`` — DRF ``APIView`` (no ViewSet machinery).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, JsonResponse
from django.views.generic import DetailView, ListView, View
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Post


def hello(request: HttpRequest, who: str) -> JsonResponse:
    return JsonResponse({"hi": who})


@login_required
def hello_authed(request: HttpRequest, who: str) -> JsonResponse:
    return JsonResponse({"hi": who, "by": request.user.username})


class PostDetailView(LoginRequiredMixin, DetailView):
    model = Post
    template_name = None

    def render_to_response(self, context, **kwargs):  # type: ignore[override]
        return JsonResponse({"id": context["object"].pk, "title": context["object"].title})


class PostListView(LoginRequiredMixin, ListView):
    model = Post
    template_name = None

    def render_to_response(self, context, **kwargs):  # type: ignore[override]
        rows = [{"id": p.pk, "title": p.title} for p in context["object_list"]]
        return JsonResponse({"results": rows})


class PostMultiVerbView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse({"verb": "get"})

    def post(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse({"verb": "post"})


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ("id", "title", "body", "author", "status", "published")


class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes: list = []  # opt-out for the test; permissions tested separately


class PingAPIView(APIView):
    permission_classes: list = []

    def get(self, request, *args, **kwargs):  # type: ignore[override]
        return Response({"pong": True})
