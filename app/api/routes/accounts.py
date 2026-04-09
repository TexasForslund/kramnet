from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_accounts():
    # TODO: lista alla e-postkonton
    pass


@router.post("/")
async def create_account():
    # TODO: skapa nytt e-postkonto via Hostek
    pass


@router.get("/{account_id}")
async def get_account(account_id: int):
    # TODO: hämta ett e-postkonto
    pass


@router.delete("/{account_id}")
async def delete_account(account_id: int):
    # TODO: ta bort e-postkonto via Hostek
    pass
