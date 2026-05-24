"""Minimal Django settings for the django-mcp test suite."""

SECRET_KEY = "django-mcp-test-secret-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]
USE_TZ = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.messages",
    "django.contrib.sessions",
    "rest_framework",
    "django_mcp",
    "tests.testapp",
]

# DMCP-02 defaults: turn off the require-auth gate for the tests that exercise
# the discovery surface broadly. Tests that exercise INV-DMCP02-4 set this to
# True via the settings fixture.
DJANGO_MCP_REQUIRE_AUTH = False

MIDDLEWARE = []

ROOT_URLCONF = "tests.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

import tempfile as _tempfile  # noqa: E402

MEDIA_ROOT = _tempfile.mkdtemp(prefix="django_mcp_tests_media_")
MEDIA_URL = "/media/"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
