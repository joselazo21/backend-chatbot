from django.urls import path

from conversations.views import conversation_detail, create_conversation
from conversations.stream import widget_message_events
from conversations.widget_views import (
    create_widget_session,
    current_widget_session,
    widget_message_generation,
    widget_messages,
)
from customers.widget_views import widget_bootstrap
from knowledge.views import document_detail, documents

from .views import health

urlpatterns = [
    path("health/", health, name="health"),
    path("widget/v1/bootstrap", widget_bootstrap, name="widget_bootstrap"),
    path("widget/v1/sessions/", create_widget_session, name="widget_session_create"),
    path("widget/v1/sessions/current", current_widget_session, name="widget_session_current"),
    path("widget/v1/messages/", widget_messages, name="widget_messages"),
    path(
        "widget/v1/messages/<int:message_id>/generation/",
        widget_message_generation,
        name="widget_message_generation",
    ),
    path(
        "widget/v1/messages/<int:message_id>/events/",
        widget_message_events,
        name="widget_message_events",
    ),
    path("api/v1/conversations/", create_conversation, name="conversation_create"),
    path("api/v1/documents/", documents, name="document_list"),
    path("api/v1/documents/<uuid:document_id>/", document_detail, name="document_detail"),
    path(
        "api/v1/conversations/<uuid:conversation_id>/",
        conversation_detail,
        name="conversation_detail",
    ),
]
