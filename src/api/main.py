
from fastapi import FastAPI
from src.api.routes import predict, auth
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="AI Web Platform")

app.include_router(auth.router)
app.include_router(predict.router)

app.mount("/static", StaticFiles(directory="src/web/static"), name="static")
templates = Jinja2Templates(directory="src/web/templates")

@app.get("/")
def home():
    return {"status": "AI Platform Ready"}
