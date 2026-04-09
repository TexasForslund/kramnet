# TODO: Implementera mot Hostek API när dokumentation är mottagen

import logging

logger = logging.getLogger(__name__)


class HostekService:
    async def create_account(self, address: str, password: str) -> bool:
        logger.info("[STUB] Hostek: create_account(%s)", address)
        return True

    async def deactivate_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek: deactivate_account(%s)", address)
        return True

    async def activate_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek: activate_account(%s)", address)
        return True

    async def delete_account(self, address: str) -> bool:
        logger.info("[STUB] Hostek: delete_account(%s)", address)
        return True

    async def change_password(self, address: str, new_password: str) -> bool:
        logger.info("[STUB] Hostek: change_password(%s)", address)
        return True

    async def change_address(self, old_address: str, new_address: str) -> bool:
        logger.info("[STUB] Hostek: change_address(%s -> %s)", old_address, new_address)
        return True
