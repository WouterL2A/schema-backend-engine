from fastapi import FastAPI
from engine.routes import router

app = FastAPI()
app.include_router(router)
