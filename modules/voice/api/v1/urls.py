from django.urls import path

from modules.voice.api.v1.views import AudioCommandAV, ClarifyAV, CommandDetailAV, TextCommandAV

urlpatterns = [
    path("commands/", AudioCommandAV.as_view()),
    path("commands/<int:pk>/", CommandDetailAV.as_view()),
    path("commands/<int:pk>/clarify/", ClarifyAV.as_view()),
    path("text-commands/", TextCommandAV.as_view()),
]
