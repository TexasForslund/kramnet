from fastapi import APIRouter

router = APIRouter()


@router.get("/dashboard")
async def dashboard():
    # TODO: admin-översikt
    pass


@router.get("/customers")
async def admin_list_customers():
    # TODO: lista alla kunder (admin-vy)
    pass


@router.post("/customers/{customer_id}/suspend")
async def suspend_customer(customer_id: int):
    # TODO: stäng av kund
    pass
