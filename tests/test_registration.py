"""Tests for the customer registration flow."""
import pytest


# ── POST /register ────────────────────────────────────────────────────────────

async def test_register_new_customer(test_client, mocker):
    mocker.patch("app.api.routes.register._email_service.send_admin_notification")

    resp = await test_client.post("/register", data={
        "name": "Anna Svensson",
        "contact_email": "anna@example.com",
        "desired_prefix": "anna",
        "swish_phone": "0701234567",
        "package_type": "single",
        "language": "sv",
    })

    assert resp.status_code == 200
    assert b"anna@kramnet.se" in resp.content


async def test_register_duplicate_address(test_client, mocker):
    mocker.patch("app.api.routes.register._email_service.send_admin_notification")

    common = {
        "desired_prefix": "duptest",
        "swish_phone": "0701234567",
        "package_type": "single",
        "language": "sv",
    }
    # First registration succeeds
    await test_client.post("/register", data={
        **common, "name": "First User", "contact_email": "first@example.com",
    })
    # Second registration with the same prefix should fail
    resp = await test_client.post("/register", data={
        **common, "name": "Second User", "contact_email": "second@example.com",
    })

    assert resp.status_code == 409


# ── GET /api/check-address ────────────────────────────────────────────────────

async def test_check_address_available(test_client):
    resp = await test_client.get("/api/check-address?prefix=freeprefix99")

    assert resp.status_code == 200
    assert resp.json() == {"available": True}


async def test_check_address_taken(test_client, mocker):
    mocker.patch("app.api.routes.register._email_service.send_admin_notification")

    # Register the address first
    await test_client.post("/register", data={
        "name": "Taken Owner",
        "contact_email": "takenowner@example.com",
        "desired_prefix": "takenaddr",
        "swish_phone": "0701234567",
        "package_type": "single",
        "language": "sv",
    })

    resp = await test_client.get("/api/check-address?prefix=takenaddr")

    assert resp.status_code == 200
    assert resp.json() == {"available": False}
