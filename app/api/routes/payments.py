from fastapi import APIRouter

router = APIRouter()


@router.post("/swish/initiate")
async def initiate_swish_payment():
    # TODO: initiera Swish-betalning
    pass


@router.post("/swish/callback")
async def swish_callback():
    # TODO: ta emot Swish-callback
    pass


@router.get("/{payment_id}")
async def get_payment(payment_id: int):
    # TODO: hämta betalningsstatus
    pass
