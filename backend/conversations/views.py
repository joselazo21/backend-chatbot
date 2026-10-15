import json

from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from customers.auth import require_service_token

from .models import Conversation


def serialize_conversation(conversation):
    return {"id": str(conversation.pk), "created_at": conversation.created_at.isoformat()}


@csrf_exempt
@require_service_token
@require_http_methods(["POST"])
def create_conversation(request):
    if request.body:
        if request.content_type != "application/json":
            return JsonResponse({"error": "json_required"}, status=415)
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"error": "invalid_json"}, status=400)
        if payload != {}:
            return JsonResponse({"error": "request_body_not_supported"}, status=400)
    conversation = Conversation.objects.create()
    response = JsonResponse(serialize_conversation(conversation), status=201)
    response["Location"] = reverse("conversation_detail", args=[conversation.pk])
    return response


@require_service_token
@require_http_methods(["GET"])
def conversation_detail(request, conversation_id):
    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(serialize_conversation(conversation))
