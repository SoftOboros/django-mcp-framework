"""Test models exercising the breadth of DMCP-01 §7 schema derivation:
CharField + max_length, FK, M2M, IntegerField with validators, ChoiceField (flat
and optgroup), DateTimeField with default, an editable Boolean, and an unlisted
field for INV-DMCP01-3 visible-field testing.
"""

from django.conf import settings
from django.db import models

FLAT_CHOICES = [
    ("draft", "Draft"),
    ("review", "Review"),
    ("published", "Published"),
]

OPTGROUP_CHOICES = [
    ("Public", [("public_a", "Public A"), ("public_b", "Public B")]),
    ("Private", [("private_a", "Private A")]),
]


class Tag(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    description = models.TextField(blank=True)

    class Meta:
        app_label = "testapp"

    def __str__(self) -> str:
        return self.name


class Post(models.Model):
    title = models.CharField(max_length=200, verbose_name="Headline")
    body = models.TextField(blank=True, help_text="Long-form body of the post.")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    status = models.CharField(max_length=16, choices=FLAT_CHOICES, default="draft")
    visibility = models.CharField(max_length=32, choices=OPTGROUP_CHOICES, default="public_a")
    published = models.BooleanField(default=False)
    secret_note = models.CharField(max_length=500, blank=True)
    attachment = models.FileField(upload_to="post_attachments/", blank=True)

    class Meta:
        app_label = "testapp"

    def __str__(self) -> str:
        return self.title
