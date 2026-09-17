from fastapi import FastAPI 
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title ="Higload-Backend",
    version="1.0.0",
)

Instrumentator().instrument(app).expose(app)



@app.get("/health")
async def check_health():
    return {
"status": "ok"
    }

@app.get("/checklist")
async def checklist():
    return {
        "id": 1,
        "student": "Alikhan Aliaskar"
    }
