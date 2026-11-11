from itertools import groupby

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from modules.voice.models import Chat, VoiceCommand


def _shop_day(command: VoiceCommand):
    return command.shop_id, timezone.localdate(command.created_at)


class Command(BaseCommand):
    help = "File commands made before chats existed into one chat per shop per day. Safe to rerun."

    def handle(self, *args, **options):
        loose = VoiceCommand.objects.filter(chat=None).order_by("shop_id", "id")
        made = 0
        with transaction.atomic():
            for (shop_id, _), rows in groupby(loose, key=_shop_day):
                rows = list(rows)
                title = next((" ".join(c.transcript.split()) for c in rows if c.transcript.strip()), "")
                chat = Chat.objects.create(shop_id=shop_id, title=title[:120], last_message_at=rows[-1].created_at)
                VoiceCommand.objects.filter(pk__in=[c.pk for c in rows]).update(chat=chat)
                made += 1
        self.stdout.write(f"Filed old commands into {made} chat(s).")
