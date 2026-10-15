import os
from django.core.exceptions import ImproperlyConfigured

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,.localhost").split(",")
    if host.strip()
]

SHARED_APPS = [
    "django_tenants",
    "django.contrib.contenttypes",
    "customers",
]
TENANT_APPS = [
    "conversations",
]
INSTALLED_APPS = SHARED_APPS + [app for app in TENANT_APPS if app not in SHARED_APPS]
MIDDLEWARE = ["django_tenants.middleware.main.TenantMainMiddleware"]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
TENANT_MODEL = "customers.Customer"
TENANT_DOMAIN_MODEL = "customers.Domain"
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ.get("POSTGRES_DB", "chatbot"),
        "USER": os.environ.get("POSTGRES_USER", "chatbot"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_CACHE_URL", "redis://localhost:6379/2"),
    }
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_DEFAULT_QUEUE = "messages"
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
