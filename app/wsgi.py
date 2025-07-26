# wsgi.py
from app.app import app  # Flask instance created in app.py

# Gunicorn looks for a callable named "application" by default,
# so either export it explicitly or tell gunicorn the name with -w
application = app
