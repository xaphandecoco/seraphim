"""
CiviCRM API v3 client.

Implements REST API calls for member sync, event sync, and attendance push.
"""

import logging
from typing import List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.config import dynamic_settings

logger = logging.getLogger(__name__)


def _is_transient_error(exception: BaseException) -> bool:
    """Return True for HTTP errors that are worth retrying."""
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in (429, 500, 502, 503)
    if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return False


class CiviCRMClient:
    def __init__(self):
        self.base_url = dynamic_settings.get_civicrm_url()
        self.api_key = dynamic_settings.get_civicrm_api_key()
        self.site_key = dynamic_settings.get_civicrm_site_key()
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception(_is_transient_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call(self, entity: str, action: str, params: dict) -> dict:
        """Make a CiviCRM API v3 call."""
        if not self.base_url:
            raise RuntimeError("CiviCRM URL not configured")

        url = f"{self.base_url.rstrip('/')}/civicrm/ajax/rest"
        payload = {
            "entity": entity,
            "action": action,
            "api_key": self.api_key,
            "key": self.site_key,
            "json": 1,
            **params,
        }
        resp = await self._client.post(url, data=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("is_error"):
            error_msg = data.get("error_message", "Unknown CiviCRM error")
            raise RuntimeError(f"CiviCRM error: {error_msg}")
        return data

    async def sync_members(self, limit: int = 2000) -> List[dict]:
        """Fetch all active Individual contacts from CiviCRM."""
        logger.info("Syncing members from CiviCRM")
        data = await self._call(
            "Contact",
            "get",
            {
                "contact_type": "Individual",
                "return": "id,first_name,last_name,email",
                "options[limit]": limit,
            },
        )
        values = data.get("values", {})
        return list(values.values())

    async def sync_events(self, start_date: Optional[str] = None, limit: int = 500) -> List[dict]:
        """Fetch upcoming events from CiviCRM."""
        logger.info("Syncing events from CiviCRM")
        params = {
            "return": "id,title,start_date,end_date",
            "options[limit]": limit,
            "is_active": 1,
        }
        if start_date:
            params["start_date"] = {">=": start_date}
        data = await self._call("Event", "get", params)
        values = data.get("values", {})
        return list(values.values())

    async def push_attendance(self, contact_id: int, event_id: int) -> bool:
        """Push a single attendance record to CiviCRM as Event Participant."""
        logger.info("Pushing attendance contact_id=%s event_id=%s", contact_id, event_id)
        data = await self._call(
            "Participant",
            "create",
            {
                "contact_id": contact_id,
                "event_id": event_id,
                "status_id": "Attended",
                "role_id": "Attendee",
            },
        )
        return not data.get("is_error")

    async def get_rsvp_list(self, event_id: int) -> List[dict]:
        """Get RSVP'd participants for an event."""
        logger.info("Getting RSVP list for event_id=%s", event_id)
        data = await self._call(
            "Participant",
            "get",
            {
                "event_id": event_id,
                "return": "contact_id,contact_id.first_name,contact_id.last_name,contact_id.email",
                "options[limit]": 2000,
            },
        )
        values = data.get("values", {})
        return list(values.values())
