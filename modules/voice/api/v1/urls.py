from django.urls import path

from modules.voice.api.v1.views import (
    AudioCommandAV,
    CancelAV,
    ChatListAV,
    ChatMessagesAV,
    ClarifyAV,
    CommandDetailAV,
    ReplyAudioAV,
    StreamTicketAV,
    TextCommandAV,
)

urlpatterns = [
    path("commands/", AudioCommandAV.as_view()),
    path("commands/<int:pk>/", CommandDetailAV.as_view()),
    path("commands/<int:pk>/clarify/", ClarifyAV.as_view()),
    path("commands/<int:pk>/cancel/", CancelAV.as_view()),
    path("commands/<int:pk>/audio/", ReplyAudioAV.as_view()),
    path("text-commands/", TextCommandAV.as_view()),
    path("chats/", ChatListAV.as_view()),
    path("chats/<int:pk>/messages/", ChatMessagesAV.as_view()),
    path("stream-ticket/", StreamTicketAV.as_view()),
]
