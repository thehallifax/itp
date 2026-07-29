"""Small read-only asynchronous Mist REST client."""
import asyncio
import email.utils
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import (
    MistAuthenticationError,
    MistAuthorizationError,
    MistError,
    MistPaginationError,
)

LOG = logging.getLogger("collector.mist")


class MistClient:
    def __init__(self, base_url, organization_id, api_token, timeout=20, *,
                 max_pages=100, page_limit=1000, max_retries=3, backoff_limit=8,
                 client=None, sleep=asyncio.sleep):
        if not organization_id or not api_token:
            raise ValueError("Mist organization ID and API token are required")
        self.base_url = self.normalize_url(base_url)
        self.organization_id = organization_id
        self.max_pages = max_pages
        self.page_limit = page_limit
        self.max_retries = max_retries
        self.backoff_limit = backoff_limit
        self.sleep = sleep
        self._headers = {"Authorization": f"Token {api_token}", "Accept": "application/json",
                         "User-Agent": "itp-mist-collector/1.0"}
        self.api_requests = 0
        self.retry_count = 0
        self.rate_limit_remaining = None
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=timeout),
            headers=self._headers,
        )

    @staticmethod
    def normalize_url(value):
        parsed = urlsplit(str(value or "").strip())
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.path.rstrip("/") or parsed.query or parsed.fragment):
            raise ValueError(
                "Mist base URL must be a complete HTTPS origin without /api/v1")
        return urlunsplit(("https", parsed.netloc, "", "", ""))

    @staticmethod
    def _retry_after(response):
        value = response.headers.get("Retry-After")
        if not value: return None
        try: return max(0.0, float(value))
        except ValueError:
            try:
                target = email.utils.parsedate_to_datetime(value)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError): return None

    async def _request(self, path, params=None):
        transient = {429, 500, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            self.api_requests += 1
            try:
                response = await self.client.get(path, params=params, headers=self._headers)
            except httpx.TransportError as exc:
                if attempt == self.max_retries: raise MistError("Mist API transport failure") from exc
                response = None
            if response is not None:
                remaining = response.headers.get("X-RateLimit-Remaining")
                if remaining is not None:
                    try: self.rate_limit_remaining = int(remaining)
                    except ValueError: pass
                if response.status_code == 401: raise MistAuthenticationError("Mist API authentication failed (HTTP 401)")
                if response.status_code == 403: raise MistAuthorizationError("Mist API token lacks required read permission (HTTP 403)")
                if response.status_code < 400:
                    try: data = response.json()
                    except ValueError as exc: raise MistError("Mist API returned invalid JSON") from exc
                    if not isinstance(data, (list, dict)): raise MistError("Mist API returned an unexpected JSON type")
                    return response, data
                if response.status_code not in transient:
                    raise MistError(f"Mist API request failed with HTTP {response.status_code}")
                if attempt == self.max_retries:
                    raise MistError(f"Mist API request failed after retries (HTTP {response.status_code})")
            self.retry_count += 1
            delay = self._retry_after(response) if response is not None else None
            await self.sleep(min(delay if delay is not None else 2 ** attempt, self.backoff_limit))

    async def paginated_get(self, path, params=None):
        results = []
        base = dict(params or {})
        for page in range(1, self.max_pages + 1):
            response, data = await self._request(path, {**base, "limit": self.page_limit, "page": page})
            items = data.get("results") if isinstance(data, dict) else data
            if not isinstance(items, list): raise MistError("Mist paginated response does not contain a list")
            results.extend(items)
            total_header = response.headers.get("X-Page-Total")
            limit = int(response.headers.get("X-Page-Limit", self.page_limit))
            total = int(total_header) if total_header is not None else None
            if total is not None and len(results) >= total: return results
            if len(items) < limit: return results
        raise MistPaginationError(f"Mist pagination exceeded safety limit of {self.max_pages} pages")

    async def sites(self): return await self.paginated_get(f"/api/v1/orgs/{self.organization_id}/sites")
    async def inventory(self): return await self.paginated_get(f"/api/v1/orgs/{self.organization_id}/inventory")
    async def device_stats(self): return await self.paginated_get(f"/api/v1/orgs/{self.organization_id}/stats/devices", {"type": "all", "status": "all", "fields": "*"})

    async def close(self):
        if self._owns_client: await self.client.aclose()
