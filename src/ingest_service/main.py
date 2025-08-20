from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from typing import List
import os

from .config import settings
from .models import Telemetry
from .pubsub import publish_message

app = FastAPI(title="Ingest Webhook", version="0.1.0")

def auth_dependency(request: Request):
    if settings.allow_anon:
        return
    token = request.headers.get("x-api-key")
    if not token or token != os.environ.get("API_KEY"):
        raise HTTPException(status_code=401, detail="unauthorized")

@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"

@app.post("/ingest", dependencies=[Depends(auth_dependency)])
def ingest(event: Telemetry):
    payload = event.model_dump(mode="json")
    ordering_key = (payload.get(settings.ordering_key_field)
                    if settings.ordering_key_field else None)
    publish_message(payload, ordering_key=str(ordering_key) if ordering_key else None)
    return JSONResponse(status_code=202, content={"status": "queued"})

@app.post("/ingest/batch", dependencies=[Depends(auth_dependency)])
def ingest_batch(events: List[Telemetry]):
    for e in events:
        payload = e.model_dump(mode="json")
        ordering_key = (payload.get(settings.ordering_key_field)
                        if settings.ordering_key_field else None)
        publish_message(payload, ordering_key=str(ordering_key) if ordering_key else None)
    return {"status": "queued", "count": len(events)}
