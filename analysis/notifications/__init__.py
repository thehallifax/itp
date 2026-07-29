"""Registry-driven notification foundation."""

from .channels import (
    ConsoleChannel, NotificationChannelRegistry, WebhookChannel)
from .engine import NotificationEngine
from .models import (
    NotificationChannel, NotificationDelivery, NotificationDeliveryStatus,
    NotificationEvent, NotificationFingerprint, NotificationRule,
    NotificationSeverity)
from .store import NotificationStore

__all__ = [
    "ConsoleChannel", "NotificationChannel", "NotificationChannelRegistry",
    "NotificationDelivery", "NotificationDeliveryStatus", "NotificationEngine",
    "NotificationEvent", "NotificationFingerprint", "NotificationRule",
    "NotificationSeverity", "NotificationStore", "WebhookChannel",
]
