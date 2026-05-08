from rest_framework import serializers

# Allowed MIME types and max sizes (bytes) for each upload context
PHOTO_ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
PHOTO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

DOCUMENT_ALLOWED_TYPES = {
    'image/jpeg', 'image/png', 'image/webp',
    'application/pdf',
}
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def validate_uploaded_file(file, allowed_types: set, max_bytes: int, field_name: str = 'file'):
    """
    Validate an uploaded InMemoryUploadedFile / TemporaryUploadedFile.
    Raises serializers.ValidationError on failure.
    """
    if file.content_type not in allowed_types:
        raise serializers.ValidationError({
            field_name: (
                f"Unsupported file type '{file.content_type}'. "
                f"Allowed: {', '.join(sorted(allowed_types))}."
            )
        })
    if file.size > max_bytes:
        raise serializers.ValidationError({
            field_name: (
                f"File too large ({file.size / (1024 * 1024):.1f} MB). "
                f"Maximum allowed: {max_bytes // (1024 * 1024)} MB."
            )
        })
    return file


def validate_photo(file):
    return validate_uploaded_file(
        file, PHOTO_ALLOWED_TYPES, PHOTO_MAX_BYTES, field_name='profile_photo'
    )


def validate_document(file):
    return validate_uploaded_file(
        file, DOCUMENT_ALLOWED_TYPES, DOCUMENT_MAX_BYTES, field_name='document'
    )
