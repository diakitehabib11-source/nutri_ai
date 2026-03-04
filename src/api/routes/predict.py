
from fastapi import APIRouter
import joblib

router = APIRouter(prefix="/predict", tags=["Prediction"])
model = joblib.load("models/model.pkl")

@router.post("/")
def predict(features:list):
    return {"prediction": int(model.predict([features])[0])}
