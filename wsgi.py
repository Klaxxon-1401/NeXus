"""WSGI entry point for Gunicorn/uWSGI-style servers."""

from app import create_app

app = create_app()
