from django.db.models import TextChoices


class PartyKind(TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    STAFF = "STAFF", "Staff"
    SUPPLIER = "SUPPLIER", "Supplier"


class Unit(TextChoices):
    PACKET = "PACKET", "Packet"
    KG = "KG", "Kg"
    LITRE = "LITRE", "Litre"
    PIECE = "PIECE", "Piece"
