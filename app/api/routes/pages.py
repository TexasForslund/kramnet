from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/villkor", response_class=HTMLResponse)
async def villkor(request: Request):
    return templates.TemplateResponse("pages/villkor.html", {"request": request})
