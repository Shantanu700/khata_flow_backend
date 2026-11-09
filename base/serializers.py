from rest_framework import serializers

from base.constants import FIELDS_MAPPING
from base.model import ModelBase
from base.types import DynamicKeysType


class BaseSerializer(serializers.ModelSerializer):
    """ModelSerializer with per-call `fields=[...]` / `exclude=[...]` subsetting (ModelBase.BASE_MODEL_FIELDS
    are always kept) and `dynamic_keys`: related attributes flattened into read-only top-level fields,
    either "party.name" (becomes party_name) or {"name", "source", "type"}."""

    dynamic_keys: list[str | DynamicKeysType] = []

    def __init__(self, *args, fields: list[str] | None = None, exclude: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        always = set(ModelBase.BASE_MODEL_FIELDS)
        if fields is not None:
            drop = set(self.fields) - set(fields) - always
        else:
            drop = set(exclude or []) - always
        for name in drop:
            self.fields.pop(name, None)

        for key in self.dynamic_keys:
            if isinstance(key, dict):
                name, source, kind = key["name"], key["source"], key.get("type", "str")
            else:
                name, source, kind = key.replace(".", "_"), key, "str"
            field_class = getattr(serializers, FIELDS_MAPPING.get(kind, "CharField"))
            # default=None: a missing link (entry without a party) renders null instead of dropping the key.
            self.fields[name] = field_class(source=source, read_only=True, default=None)
