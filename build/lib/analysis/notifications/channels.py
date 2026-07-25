"""Notification delivery adapters independent from condition evaluation."""
import json
import urllib.request


class ConsoleChannel:
    id = "console"

    def __init__(self, output=None):
        self.output = output or print

    def deliver(self, event):
        self.output(
            f"NOTIFICATION [{event['severity'].upper()}] "
            f"{event['title']}: {event['summary']}")


class WebhookChannel:
    id = "webhook"

    def __init__(self, url, *, timeout=5, headers=None, opener=None):
        self.url = str(url or "")
        self.timeout = max(0.1, float(timeout))
        self.headers = dict(headers or {})
        self.opener = opener or urllib.request.urlopen

    def deliver(self, event):
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("webhook URL must use HTTP or HTTPS")
        payload = {
            "schema_version": 1,
            "event": event,
        }
        headers = {"Content-Type": "application/json", **self.headers}
        request = urllib.request.Request(
            self.url, data=json.dumps(
                payload, sort_keys=True).encode(), headers=headers,
            method="POST")
        response = self.opener(request, timeout=self.timeout)
        status = int(getattr(response, "status", 200))
        if status >= 400:
            raise RuntimeError(f"webhook returned HTTP {status}")


class NotificationChannelRegistry:
    def __init__(self, *, output=None, webhook_opener=None):
        self.output = output
        self.webhook_opener = webhook_opener

    def enabled(self, config):
        channels = config.get("channels") or {}
        result = []
        console = channels.get("console", {})
        if console is True or (
                isinstance(console, dict) and console.get("enabled") is True):
            result.append(ConsoleChannel(self.output))
        webhook = channels.get("webhook", {})
        if isinstance(webhook, dict) and webhook.get("enabled") is True:
            result.append(WebhookChannel(
                webhook.get("url"), timeout=webhook.get("timeout_seconds", 5),
                headers=webhook.get("headers") or {},
                opener=self.webhook_opener))
        return tuple(result)

