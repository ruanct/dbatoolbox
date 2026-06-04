import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbatoolbox.settings.dev")

app = Celery("dbatoolbox")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
