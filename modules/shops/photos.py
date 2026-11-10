import uuid

from django.conf import settings
from django.db import models
from django.http import FileResponse, Http404
from rest_framework import serializers

# By the file's first bytes, never its name or the browser's word: anything else (SVG included) is refused.
SIGNATURES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}


def sniff(head: bytes) -> tuple[str, str] | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    return next((kind for sig, kind in SIGNATURES.items() if head.startswith(sig)), None)


class PhotoSerializer(serializers.Serializer):
    photo = serializers.FileField(help_text="JPEG, PNG or WebP. The app sends a 256 px square.")

    def validate_photo(self, photo):
        if photo.size > settings.MAX_PHOTO_BYTES:
            raise serializers.ValidationError("Photo is too large.")
        kind = sniff(photo.read(12))
        photo.seek(0)
        if not kind:
            raise serializers.ValidationError("Send a JPEG, PNG or WebP image.")
        photo.name = f"{uuid.uuid4().hex}.{kind[1]}"
        return photo


def put(row: models.Model, data) -> None:
    body = PhotoSerializer(data=data)
    body.is_valid(raise_exception=True)
    old = row.photo.name
    row.photo = body.validated_data["photo"]
    row.save(update_fields=["photo", "updated_at"])
    if old:
        row.photo.storage.delete(old)


def remove(row: models.Model) -> None:
    if row.photo:
        row.photo.delete(save=False)
        row.save(update_fields=["photo", "updated_at"])


def serve(row: models.Model) -> FileResponse:
    if not row.photo:
        raise Http404
    file = row.photo.open("rb")
    kind = sniff(file.read(12))
    file.seek(0)
    response = FileResponse(file, content_type=kind[0] if kind else "application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    # The URL changes with each new photo (?v=photo_key), so the old one can be cached for good.
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


def key(row: models.Model) -> str | None:
    "Changes with each new photo; null when there is none."
    return row.photo.name.rsplit("/", 1)[-1].split(".")[0] if row.photo else None
