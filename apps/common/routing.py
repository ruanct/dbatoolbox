from django.urls import path

from .consumers import SSHConsumer

websocket_urlpatterns = [
    path("ws/terminal/<int:host_id>/", SSHConsumer.as_asgi()),
]
