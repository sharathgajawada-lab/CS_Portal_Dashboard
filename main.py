from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CMS_BASE = "https://cms.audibene.net/api/metrics"
API_KEY = os.environ.get("CMS_API_KEY", "API-186b82c0a37b42cc8a444a055de10549bacb351d")

@app.get("/api/metrics/{project}/query/time-series")
async def proxy_metrics(
    project: str,
    event: str = Query(...),
    since: Optional[str] = None,
    bucket: str = "day",
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    params = {"event": event, "bucket": bucket}
    if since:
        params["since"] = since
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    url = f"{CMS_BASE}/{project}/query/time-series"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers={"API-Key": API_KEY})
        return r.json()

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()
