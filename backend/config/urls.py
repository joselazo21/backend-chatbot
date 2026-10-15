from django.urls import path

from conversations.views import conversation_detail, create_conversation

from .views import health

urlpatterns = [
    path("health/", health, name="health"),
    path("api/v1/conversations/", create_conversation, name="conversation_create"),
    path(
        "api/v1/conversations/<uuid:conversation_id>/",
        conversation_detail,
        name="conversation_detail",
    ),
]
