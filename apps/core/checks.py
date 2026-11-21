from django.core.checks import Error, register


@register()
def field_encryption_key_check(app_configs, **kwargs):
    from apps.core.crypto import configured_keys
    if configured_keys():
        try:
            from apps.core.crypto import _fernet
            _fernet()
        except Exception as exc:
            return [Error(f'FIELD_ENCRYPTION_KEYS is invalid: {exc.__class__.__name__}',
                          id='core.E001')]
        return []
    return [Error(
        'FIELD_ENCRYPTION_KEYS is not set; encrypted fields cannot be read or written.',
        hint='Set FIELD_ENCRYPTION_KEYS (Fernet key, comma-separated for rotation) in the environment.',
        id='core.E002',
    )]
