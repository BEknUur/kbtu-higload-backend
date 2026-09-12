from fastapi import FastAPI 


app = FastAPI()


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
