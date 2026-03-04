
from fastapi import APIRouter, HTTPException
from jose import jwt

router = APIRouter(prefix="/auth", tags=["Auth"])
SECRET="SECRET123"

@router.post("/login")
def login(username:str,password:str):
    if username=="admin" and password=="admin":
        token = jwt.encode({"user":username},SECRET,algorithm="HS256")
        return {"access_token":token}
    raise HTTPException(status_code=401, detail="Invalid credentials")
