import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.auth_token import AuthToken
from app.models.email_account import AccountStatus, EmailAccount
from app.models.customer import Customer
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

_email_service = EmailService()


async def daily_renewal_check() -> None:
    """08:00 dagligen — skicka påminnelser 30 och 7 dagar innan utgång."""
    logger.info("daily_renewal_check: starting")
    now = datetime.now(timezone.utc)
    thresholds = [30, 7]

    async with AsyncSessionLocal() as db:
        for days_left in thresholds:
            target_date = (now + timedelta(days=days_left)).date()
            result = await db.execute(
                select(EmailAccount)
                .options(selectinload(EmailAccount.customer))
                .where(
                    EmailAccount.status == AccountStatus.active,
                    # Jämför enbart datum-delen för att inte missa p.g.a. klocktid
                    EmailAccount.expires_at >= datetime(
                        target_date.year, target_date.month, target_date.day,
                        0, 0, 0, tzinfo=timezone.utc
                    ),
                    EmailAccount.expires_at < datetime(
                        target_date.year, target_date.month, target_date.day,
                        0, 0, 0, tzinfo=timezone.utc
                    ) + timedelta(days=1),
                )
            )
            accounts = result.scalars().all()
            logger.info(
                "daily_renewal_check: %d accounts expiring in %d days",
                len(accounts),
                days_left,
            )
            for account in accounts:
                try:
                    await _email_service.send_renewal_reminder(
                        customer=account.customer,
                        email_account=account,
                        days_left=days_left,
                        db=db,
                    )
                except Exception:
                    logger.exception(
                        "daily_renewal_check: failed for account %s", account.id
                    )
        await db.commit()

    logger.info("daily_renewal_check: done")


async def daily_deactivation_check() -> None:
    """09:00 dagligen — inaktivera förfallna konton."""
    logger.info("daily_deactivation_check: starting")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EmailAccount)
            .options(selectinload(EmailAccount.customer))
            .where(
                EmailAccount.status == AccountStatus.active,
                EmailAccount.expires_at < now,
            )
        )
        accounts = result.scalars().all()
        logger.info(
            "daily_deactivation_check: %d expired accounts to deactivate",
            len(accounts),
        )

        for account in accounts:
            try:
                account.status = AccountStatus.inactive
                account.deactivated_at = now

                db.add(
                    AuditLog(
                        email_account_id=account.id,
                        customer_id=account.customer_id,
                        event_type="account_deactivated",
                        metadata_={"reason": "subscription_expired"},
                        performed_by="system",
                    )
                )

                await _email_service.send_deactivation_notice(
                    customer=account.customer,
                    email_account=account,
                    db=db,
                )
                await _email_service.send_admin_notification(
                    event_type="account_deactivated",
                    details={
                        "address": account.address,
                        "customer": account.customer.name,
                        "customer_email": account.customer.contact_email,
                        "expired_at": str(account.expires_at),
                    },
                    db=db,
                )
            except Exception:
                logger.exception(
                    "daily_deactivation_check: failed for account %s", account.id
                )

        await db.commit()

    logger.info("daily_deactivation_check: done")


async def monthly_inactive_report() -> None:
    """1:a varje månad 07:00 — rapport över inaktiva konton."""
    logger.info("monthly_inactive_report: starting")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EmailAccount)
            .options(selectinload(EmailAccount.customer))
            .where(EmailAccount.status == AccountStatus.inactive)
            .order_by(EmailAccount.deactivated_at.asc().nulls_last())
        )
        accounts = result.scalars().all()

        rows = []
        for account in accounts:
            days_inactive = (
                (now - account.deactivated_at).days
                if account.deactivated_at
                else "?"
            )
            rows.append(
                f"  {account.address:<35} | "
                f"{account.customer.name:<25} | "
                f"inaktiv sedan {account.deactivated_at.strftime('%Y-%m-%d') if account.deactivated_at else '?':>10} "
                f"({days_inactive} dagar)"
            )

        report_lines = [
            f"Månadsrapport inaktiva konton — {now.strftime('%Y-%m')}",
            f"Totalt inaktiva: {len(accounts)}",
            "",
            f"{'Adress':<35} | {'Kund':<25} | Inaktiv sedan",
            "-" * 90,
        ] + rows

        await _email_service.send_admin_notification(
            event_type="monthly_inactive_report",
            details={"rapport": "\n" + "\n".join(report_lines)},
            db=db,
        )
        await db.commit()

    logger.info("monthly_inactive_report: done, %d inactive accounts", len(accounts))


async def cleanup_audit_logs() -> None:
    """Varje söndag 03:00 — rensa audit_logs äldre än 90 dagar."""
    logger.info("cleanup_audit_logs: starting")
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff)
        )
        await db.commit()

    logger.info("cleanup_audit_logs: deleted %d rows", result.rowcount)


async def cleanup_expired_tokens() -> None:
    """Varje natt 02:00 — rensa utgångna auth_tokens."""
    logger.info("cleanup_expired_tokens: starting")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(AuthToken).where(AuthToken.expires_at < now)
        )
        await db.commit()

    logger.info("cleanup_expired_tokens: deleted %d rows", result.rowcount)


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="Europe/Stockholm")
        self._register_jobs()

    def _register_jobs(self) -> None:
        self._scheduler.add_job(
            daily_renewal_check,
            CronTrigger(hour=8, minute=0),
            id="daily_renewal_check",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            daily_deactivation_check,
            CronTrigger(hour=9, minute=0),
            id="daily_deactivation_check",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            monthly_inactive_report,
            CronTrigger(day=1, hour=7, minute=0),
            id="monthly_inactive_report",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            cleanup_audit_logs,
            CronTrigger(day_of_week="sun", hour=3, minute=0),
            id="cleanup_audit_logs",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            cleanup_expired_tokens,
            CronTrigger(hour=2, minute=0),
            id="cleanup_expired_tokens",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
