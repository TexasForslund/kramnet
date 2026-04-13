"""
MigrationService — importerar befintliga Hostek-mailboxar till kramnet-databasen.

Flöde:
  preview_migration()  → jämförelse utan att skriva något
  run_migration()      → dry_run=True simulerar, dry_run=False sparar
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount, PackageType
from app.services.hostek_service import HostekService

logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000


class MigrationService:

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _fetch_all_mailboxes(self, hostek: HostekService) -> list[dict]:
        """Paginera Hostek tills sidan är tom, returnera alla mailboxar."""
        all_boxes: list[dict] = []
        offset = 0
        while True:
            page = await hostek.list_mailboxes(offset=offset)
            if not page:
                break
            all_boxes.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return all_boxes

    async def _existing_addresses(self, db: AsyncSession) -> set[str]:
        result = await db.execute(select(EmailAccount.address))
        return {row[0] for row in result.all()}

    def _address_for(self, mailbox: dict) -> str:
        """Bygg e-postadress från recipient-fältet (format: prefix utan @-del)."""
        recipient = mailbox.get("recipient", "")
        # recipient kan vara bara prefixet eller hela adressen
        if "@" in recipient:
            return recipient.lower()
        return f"{recipient.lower()}@kramnet.se"

    # ── public API ────────────────────────────────────────────────────────────

    async def preview_migration(
        self,
        db: AsyncSession,
        hostek: HostekService,
    ) -> dict:
        """
        Jämför Hostek-mailboxar mot befintliga email_accounts.
        Returnerar statistik och ett urval av vad som skulle importeras.
        """
        all_boxes = await self._fetch_all_mailboxes(hostek)
        existing = await self._existing_addresses(db)

        to_import = [
            mb for mb in all_boxes
            if self._address_for(mb) not in existing
        ]

        return {
            "total_in_hostek": len(all_boxes),
            "already_imported": len(all_boxes) - len(to_import),
            "to_import": len(to_import),
            "sample": [
                {**mb, "address": self._address_for(mb)}
                for mb in to_import[:10]
            ],
        }

    async def run_migration(
        self,
        db: AsyncSession,
        hostek: HostekService,
        dry_run: bool = True,
    ) -> dict:
        """
        Importera Hostek-mailboxar som saknas i databasen.

        dry_run=True  → beräkna vad som *skulle* ske, spara inget
        dry_run=False → spara Customer + EmailAccount + AuditLog
        """
        all_boxes = await self._fetch_all_mailboxes(hostek)
        existing = await self._existing_addresses(db)
        now = datetime.now(timezone.utc)

        imported = 0
        skipped = 0
        errors: list[str] = []

        for mb in all_boxes:
            address = self._address_for(mb)

            if address in existing:
                skipped += 1
                continue

            first = mb.get("first_name", "").strip()
            last = mb.get("last_name", "").strip()
            name = " ".join(part for part in [first, last] if part) or address

            try:
                if not dry_run:
                    customer = Customer(
                        name=name,
                        contact_email=address,
                        swish_phone="",
                        language="sv",
                    )
                    db.add(customer)
                    await db.flush()  # ger customer.id

                    account = EmailAccount(
                        customer_id=customer.id,
                        address=address,
                        package_type=PackageType.single,
                        status=AccountStatus.active,
                        activated_at=now,
                        expires_at=now + timedelta(days=365),
                    )
                    db.add(account)
                    await db.flush()

                    db.add(AuditLog(
                        customer_id=customer.id,
                        email_account_id=account.id,
                        event_type="account_migrated",
                        metadata_={
                            "hostek_id": mb.get("id"),
                            "username": mb.get("username"),
                            "address": address,
                        },
                        performed_by="admin/migration",
                    ))

                    # Lägg till i lokalt set så dubletter inom samma körning
                    # inte skapar en extra rad
                    existing.add(address)

                imported += 1

            except Exception as exc:
                logger.error("Migration error for %s: %s", address, exc)
                errors.append(f"{address}: {exc}")

        if not dry_run and imported > 0:
            await db.commit()
            logger.info("Migration done: %d imported, %d skipped", imported, skipped)
        elif dry_run:
            logger.info(
                "Migration dry-run: would import %d, skip %d", imported, skipped
            )

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "dry_run": dry_run,
        }
