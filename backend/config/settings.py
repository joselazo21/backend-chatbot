import os
from django.core.exceptions import ImproperlyConfigured

from customers.validation import normalize_origin

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
CELERY_TASK_PUBLISH_RETRY = False
CELERY_BROKER_CONNECTION_TIMEOUT = 2
CELERY_BEAT_SCHEDULE = {
    "dispatch-pending-assistant-runs": {
        "task": "conversations.tasks.dispatch_pending_assistant_runs",
        "schedule": 30.0,
    }
}

LLM_CHAT_URL = os.environ.get("LLM_CHAT_URL", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_API_TOKEN = os.environ.get("LLM_API_TOKEN", "")
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
if LLM_CHAT_URL and not LLM_MODEL:
    raise ImproperlyConfigured("LLM_MODEL is required when LLM_CHAT_URL is set")
if LLM_TIMEOUT_SECONDS < 1:
    raise ImproperlyConfigured("LLM_TIMEOUT_SECONDS must be positive")

WIDGET_SESSION_TTL_SECONDS = int(os.environ.get("WIDGET_SESSION_TTL_SECONDS", "1800"))
WIDGET_SESSION_SITE_LIMIT_PER_MINUTE = int(
    os.environ.get("WIDGET_SESSION_SITE_LIMIT_PER_MINUTE", "60")
)
WIDGET_SESSION_IP_LIMIT_PER_MINUTE = int(
    os.environ.get("WIDGET_SESSION_IP_LIMIT_PER_MINUTE", "10")
)
WIDGET_MESSAGE_LIMIT_PER_MINUTE = int(
    os.environ.get("WIDGET_MESSAGE_LIMIT_PER_MINUTE", "20")
)
try:
    WIDGET_FRONTEND_ORIGIN = (
        normalize_origin(os.environ["WIDGET_FRONTEND_ORIGIN"])
        if os.environ.get("WIDGET_FRONTEND_ORIGIN")
        else ""
    )
except ValueError as error:
    raise ImproperlyConfigured("WIDGET_FRONTEND_ORIGIN must be a valid origin") from error
if min(
    WIDGET_SESSION_TTL_SECONDS,
    WIDGET_SESSION_SITE_LIMIT_PER_MINUTE,
    WIDGET_SESSION_IP_LIMIT_PER_MINUTE,
    WIDGET_MESSAGE_LIMIT_PER_MINUTE,
) < 1:
    raise ImproperlyConfigured("Widget session TTL and rate limits must be positive")

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
