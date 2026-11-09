from django.db.models import Manager

from base.enums import Status


class DeleteStatusManager(Manager):
    def get_queryset(self):
        return super().get_queryset().exclude(status=Status.DELETED)
