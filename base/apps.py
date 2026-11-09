from django.apps import AppConfig
from django.conf import settings
from django.utils.autoreload import autoreload_started


def watch_env_file(sender, **kwargs) -> None:
    sender.watch_dir(settings.BASE_DIR, ".env")


class BaseConfig(AppConfig):
    name = "base"

    def ready(self) -> None:
        autoreload_started.connect(watch_env_file)
