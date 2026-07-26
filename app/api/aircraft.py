from fastapi import APIRouter

router = APIRouter()

@router.get("/")
def aircraft_root():
    return {"message": "Aircraft API ready"}
