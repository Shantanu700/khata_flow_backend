def getattr_recursive(instance, field: str):
    "getattr for dotted paths ('party.name'); None as soon as a link is missing."
    for part in field.split("."):
        if instance is None:
            return None
        instance = getattr(instance, part, None)
    return instance
