from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_customers():
    # TODO: lista alla kunder
    pass


@router.post("/")
async def create_customer():
    # TODO: skapa ny kund
    pass


@router.get("/{customer_id}")
async def get_customer(customer_id: int):
    # TODO: hämta en kund
    pass


@router.put("/{customer_id}")
async def update_customer(customer_id: int):
    # TODO: uppdatera en kund
    pass


@router.delete("/{customer_id}")
async def delete_customer(customer_id: int):
    # TODO: ta bort en kund
    pass
