#!/usr/bin/env python
import os
import sys

def main():
    settings_module = (
        'config.settings.test'
        if 'test' in sys.argv
        else 'config.settings.dev'
    )
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_module)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "that you have activated a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()