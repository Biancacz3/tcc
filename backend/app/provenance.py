"""Stable virtual device used only by authenticated browser photo uploads."""
from uuid import NAMESPACE_URL, uuid5

MANUAL_DEVICE_ID = uuid5(NAMESPACE_URL, "urn:tcc:manual-upload:v1")
MANUAL_DEVICE_NAME = "Envio pelo painel"
