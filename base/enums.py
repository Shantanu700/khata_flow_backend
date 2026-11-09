from enum import IntEnum


class Status(IntEnum):
    CREATED = 0
    UPDATED = 1
    DELETED = 2


STATUS_CHOICES = [(status.value, status.name.title()) for status in Status]
ACTIVE_STATUSES = (Status.CREATED, Status.UPDATED)
