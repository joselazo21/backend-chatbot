from django.urls import path

from conversations.views import conversation_detail, create_conversation
from customers.widget_views import widget_bootstrap

from .views import health

urlpatterns = [
    path("health/", health, name="health"),
    path("widget/v1/bootstrap", widget_bootstrap, name="widget_bootstrap"),
    path("api/v1/conversations/", create_conversation, name="conversation_create"),
    path(
        "api/v1/conversations/<uuid:conversation_id>/",
        conversation_detail,
        name="conversation_detail",
    ),
]
