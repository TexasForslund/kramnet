import logging
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


async def create_order(
    payment: Payment,
    customer: Customer,
    email_account: EmailAccount,
) -> dict:
    """Skapa en Klarna Checkout-order. Returnerar order_id, html_snippet och status."""
    base_url = settings.base_url.rstrip("/")
    api_base = settings.klarna_api_url.rstrip("/")

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
            "terms": "https://kramnet.se/villkor",
            "checkout": f"{base_url}/checkout?order_id={payment.id}",
            "confirmation": f"{base_url}/checkout/confirmation?order_id={payment.id}",
            "push": f"{base_url}/api/klarna/push?order_id={payment.id}",
        },
        "billing_address": {
            "email": customer.contact_email,
            "phone": customer.swish_phone or "",
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base}/checkout/v3/orders",
            json=payload,
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "order_id": data["order_id"],
        "html_snippet": data["html_snippet"],
        "status": data["status"],
    }


async def get_order(klarna_order_id: str) -> dict:
    """Hämta en befintlig Klarna-order."""
    api_base = settings.klarna_api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{api_base}/checkout/v3/orders/{klarna_order_id}",
            headers={"Authorization": _auth_header()},
        )
        resp.raise_for_status()
        return resp.json()


async def acknowledge_order(klarna_order_id: str) -> bool:
    """Bekräfta en betald order mot Klarna Order Management API."""
    api_base = settings.klarna_api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base}/ordermanagement/v1/orders/{klarna_order_id}/acknowledge",
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 204:
            return True
        logger.warning(
            "Klarna acknowledge returned %s for order %s",
            resp.status_code,
            klarna_order_id,
        )
        return False
