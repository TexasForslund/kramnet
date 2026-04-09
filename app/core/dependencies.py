from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.customer import Customer
from app.services.auth_service import AuthService

_auth_service = AuthService()


async def get_current_customer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Customer:
    session_token = request.cookies.get("session")
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inte inloggad",
        )

    customer = await _auth_service.get_current_customer(session_token, db)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessionen är ogiltig eller har gått ut",
        )

    return customer


async def require_admin(request: Request) -> None:
    from fastapi.responses import RedirectResponse

    admin_secret = request.cookies.get("admin_session")
    if not admin_secret or admin_secret != settings.admin_secret:
        # Kasta exception för API-anrop, redirect för browser-navigation
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Åtkomst nekad — logga in på /admin/login",
        )
