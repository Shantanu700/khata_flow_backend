import uuid6
from django.db import models

from base.enums import STATUS_CHOICES, Status
from base.manager import DeleteStatusManager


class ModelBase(models.Model):
    "Soft-deleted rows (status DELETED) are hidden by `objects`; delete with soft_delete(), never .delete()."

    BASE_MODEL_FIELDS = ("id", "status", "created_at", "updated_at")

    uuid = models.UUIDField(default=uuid6.uuid6, unique=True, editable=False)
    status = models.IntegerField(choices=STATUS_CHOICES, default=Status.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DeleteStatusManager()

    # No base_manager_name: related access (entry.party) must still reach soft-deleted rows,
    # or old ledger entries of a deleted customer could no longer say whose they were.
    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        self.status = Status.DELETED
        self.save(update_fields=["status", "updated_at"])
