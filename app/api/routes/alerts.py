from fastapi import APIRouter

router = APIRouter(prefix="/alerts", tags=["alerts"])
# Alert persistence is modeled; endpoints will be added when alert generation rules are defined.

