import json
import logging
import uuid
from base64 import b64encode

import httpx

from app.core.config import settings
from app.models.customer import Customer
from app.models.email_account import EmailAccount
from app.models.payment import Payment

logger = logging.getLogger(__name__)


def _auth_header() -> str:
    token = b64encode(
        f"{settings.klarna_api_user}:{settings.klarna_api_password}".encode()
    ).decode()
    return f"Basic {token}"


def _log_response(label: str, resp: httpx.Response) -> None:
    logger.info(
        "%s HTTP %s\nHeaders: %s\nBody: %s",
        label,
        resp.status_code,
        dict(resp.headers),
        resp.text,
    )


async def create_order(
    payment: Payment,
    customer: Customer,
    email_account: EmailAccount,
) -> dict:
    """Skapa en Klarna/Kustom Checkout-order. Returnerar order_id, html_snippet och status."""
    base_url = settings.base_url.rstrip("/")
    api_base = settings.klarna_api_url.rstrip("/")

    billing_address: dict = {"email": customer.contact_email}
    if customer.swish_phone:
        billing_address["phone"] = customer.swish_phone

    payload = {
        "purchase_country": "SE",
        "purchase_currency": "SEK",
        "locale": "sv-SE",
        "order_amount": payment.amount_ore,
        "order_tax_amount": 0,
        "order_lines": [
            {
                "type": "digital",
                "name": f"E-postadress {email_account.address}",
                "quantity": 1,
                "unit_price": payment.amount_ore,
                "tax_rate": 0,
                "total_amount": payment.amount_ore,
                "total_tax_amount": 0,
            }
        ],
        "merchant_urls": {
            "terms": f"{base_url}/villkor",
            "checkout": f"{base_url}/checkout?order_id={payment.id}",
            "confirmation": f"{base_url}/checkout/confirmation?order_id={payment.id}",
            "push": f"{base_url}/api/klarna/push?order_id={payment.id}",
        },
        "billing_address": billing_address,
    }

    logger.info(
        "Kustom create_order → %s/checkout/v3/orders\nPayload:\n%s",
        api_base,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base}/checkout/v3/orders",
            json=payload,
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
                "Klarna-Idempotency-Key": str(uuid.uuid4()),
            },
        )
        _log_response("Kustom create_order response:", resp)
        resp.raise_for_status()
        data = resp.json()

    return {
        "order_id": data["order_id"],
        "html_snippet": data["html_snippet"],
        "status": data["status"],
    }


async def get_order(klarna_order_id: str) -> dict:
    """Hämta en befintlig Kustom-order."""
    api_base = settings.klarna_api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{api_base}/checkout/v3/orders/{klarna_order_id}",
            headers={"Authorization": _auth_header()},
        )
        _log_response(f"Kustom get_order({klarna_order_id}) response:", resp)
        resp.raise_for_status()
        return resp.json()


async def acknowledge_order(klarna_order_id: str) -> bool:
    """Bekräfta en betald order mot Kustom Order Management API."""
    api_base = settings.klarna_api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base}/ordermanagement/v1/orders/{klarna_order_id}/acknowledge",
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
        )
        _log_response(f"Kustom acknowledge({klarna_order_id}) response:", resp)
        if resp.status_code == 204:
            return True
        logger.warning(
            "Kustom acknowledge returned %s for order %s",
            resp.status_code,
            klarna_order_id,
        )
        return False
