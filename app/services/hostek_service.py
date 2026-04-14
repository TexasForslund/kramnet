import logging
from xml.etree import ElementTree as ET

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class HostekAPIError(Exception):
    pass


def _parse_mailbox(elem: ET.Element) -> dict:
    """Parse an <email_mailbox> element into a dict.

    Actual API structure (confirmed 2026-04):
      <email_mailbox>
        <id>, <recipient>, <username>, <password>,
        <suspended>, <vacation_active>, <pending>,
        <created_at>, <updated_at>
      </email_mailbox>
    """
    def text(tag: str) -> str:
        child = elem.find(tag)
        if child is None:
            return ""
        return child.text or ""

    return {
        "id": text("id"),
        "recipient": text("recipient"),
        "username": text("username"),
        "suspended": text("suspended"),
        "pending": text("pending"),
        "vacation_active": text("vacation_active"),
        "created_at": text("created_at"),
        "updated_at": text("updated_at"),
    }


class HostekService:
    def __init__(self):
        self._base = settings.hostek_api_url.rstrip("/")
        self._auth = (settings.hostek_api_user, settings.hostek_api_password)
        self._cid = settings.hostek_customer_id
        self._did = settings.hostek_domain_id

    def _mailbox_url(self, suffix: str = "") -> str:
        return (
            f"{self._base}/customers/{self._cid}"
            f"/domains/{self._did}/email_mailboxes{suffix}"
        )

    # ─── READ methods ─────────────────────────────────────────────────────────

    async def get_mailbox(self, mailbox_id: str) -> dict | None:
        url = self._mailbox_url(f"/{mailbox_id}")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, auth=self._auth)
        except httpx.RequestError as exc:
            logger.error("Hostek connection error (get_mailbox %s): %s", mailbox_id, exc)
            raise HostekAPIError(f"Connection error: {exc}") from exc

        if resp.status_code == 404:
            return None
        if 400 <= resp.status_code < 500:
            logger.warning(
                "Hostek 4xx (get_mailbox %s): %s %s",
                mailbox_id, resp.status_code, resp.text,
            )
            return None
        if resp.status_code >= 500:
            logger.error(
                "Hostek 5xx (get_mailbox %s): %s %s",
                mailbox_id, resp.status_code, resp.text,
            )
            raise HostekAPIError(f"Server error {resp.status_code}")

        try:
            root = ET.fromstring(resp.text)
            # Single-mailbox response: root is <email_mailbox>
            elem = root if root.tag == "email_mailbox" else root.find("email_mailbox")
            if elem is None:
                logger.warning("Hostek get_mailbox: no <email_mailbox> in response: %r", resp.text[:300])
                return None
            return _parse_mailbox(elem)
        except ET.ParseError as exc:
            logger.error("Hostek XML parse error (get_mailbox): %s", exc)
            raise HostekAPIError(f"XML parse error: {exc}") from exc

    async def find_mailbox_by_email(self, address: str) -> dict | None:
        url = self._mailbox_url()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params={"search": address}, auth=self._auth)
        except httpx.RequestError as exc:
            logger.error("Hostek connection error (find_mailbox_by_email %s): %s", address, exc)
            raise HostekAPIError(f"Connection error: {exc}") from exc

        if 400 <= resp.status_code < 500:
            logger.warning(
                "Hostek 4xx (find_mailbox_by_email %s): %s %s",
                address, resp.status_code, resp.text,
            )
            return None
        if resp.status_code >= 500:
            logger.error(
                "Hostek 5xx (find_mailbox_by_email %s): %s %s",
                address, resp.status_code, resp.text,
            )
            raise HostekAPIError(f"Server error {resp.status_code}")

        try:
            root = ET.fromstring(resp.text)
            # List response: <email_mailboxes><email_mailbox>…</email_mailbox></email_mailboxes>
            # Single response: <email_mailbox>…</email_mailbox>
            if root.tag == "email_mailbox":
                return _parse_mailbox(root)
            elem = root.find("email_mailbox")
            if elem is None:
                return None
            return _parse_mailbox(elem)
        except ET.ParseError as exc:
            logger.error("Hostek XML parse error (find_mailbox_by_email): %s", exc)
            raise HostekAPIError(f"XML parse error: {exc}") from exc

    async def list_mailboxes(self, offset: int = 0) -> list[dict]:
        url = self._mailbox_url()
        logger.info(
            "Hostek list_mailboxes: GET %s  (offset=%s, user=%r)",
            url, offset, self._auth[0],
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params={"offset": offset}, auth=self._auth)
        except httpx.RequestError as exc:
            logger.error("Hostek connection error (list_mailboxes): %s", exc)
            raise HostekAPIError(f"Connection error: {exc}") from exc

        logger.info(
            "Hostek list_mailboxes: HTTP %s  body_preview=%r",
            resp.status_code, resp.text[:500],
        )

        if 400 <= resp.status_code < 500:
            logger.warning(
                "Hostek 4xx (list_mailboxes): %s %s",
                resp.status_code, resp.text,
            )
            return []
        if resp.status_code >= 500:
            logger.error(
                "Hostek 5xx (list_mailboxes): %s %s",
                resp.status_code, resp.text,
            )
            raise HostekAPIError(f"Server error {resp.status_code}")

        try:
            root = ET.fromstring(resp.text)
            logger.debug("Hostek list_mailboxes: root tag=%r, children=%d", root.tag, len(list(root)))
            # Expected: <email_mailboxes type="array"><email_mailbox>…
            mailboxes = root.findall("email_mailbox")
            # Fallback: single item returned as root element
            if not mailboxes and root.tag == "email_mailbox":
                mailboxes = [root]
            logger.info("Hostek list_mailboxes: found %d mailbox(es)", len(mailboxes))
            return [_parse_mailbox(m) for m in mailboxes]
        except ET.ParseError as exc:
            logger.error("Hostek XML parse error (list_mailboxes): %s  raw=%r", exc, resp.text[:500])
            raise HostekAPIError(f"XML parse error: {exc}") from exc

    # ─── WRITE stubs ──────────────────────────────────────────────────────────

    async def create_mailbox(self, **kwargs) -> bool:
        # TODO: POST XML to create a new mailbox
        logger.info("[STUB] Hostek WRITE: create_mailbox(%s)", kwargs)
        return True

    async def update_mailbox(self, mailbox_id: str, **kwargs) -> bool:
        # TODO: PUT/PATCH XML to update mailbox fields
        logger.info("[STUB] Hostek WRITE: update_mailbox(%s, %s)", mailbox_id, kwargs)
        return True

    async def delete_mailbox(self, mailbox_id: str) -> bool:
        # TODO: DELETE /email_mailboxes/{mailbox_id}
        logger.info("[STUB] Hostek WRITE: delete_mailbox(%s)", mailbox_id)
        return True

    async def deactivate_mailbox(self, mailbox_id: str) -> bool:
        # TODO: POST/PUT to deactivate mailbox via API
        logger.info("[STUB] Hostek WRITE: deactivate_mailbox(%s)", mailbox_id)
        return True

    async def activate_mailbox(self, mailbox_id: str) -> bool:
        # TODO: POST/PUT to activate mailbox via API
        logger.info("[STUB] Hostek WRITE: activate_mailbox(%s)", mailbox_id)
        return True

    # ─── Legacy stubs (used by existing admin routes) ─────────────────────────

    async def create_account(self, address: str, password: str) -> bool:
        logger.info("[STUB] Hostek WRITE: create_account(%s)", address)
        return True

    async def deactivate_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek WRITE: deactivate_account(%s)", address)
        return True

    async def activate_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek WRITE: activate_account(%s)", address)
        return True

    async def delete_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek WRITE: delete_account(%s)", address)
        return True

    async def change_password(self, address: str, new_password: str) -> bool:
        logger.info("[STUB] Hostek WRITE: change_password(%s)", address)
        return True

    async def change_address(self, old_address: str, new_address: str) -> bool:
        logger.info("[STUB] Hostek WRITE: change_address(%s -> %s)", old_address, new_address)
        return True
