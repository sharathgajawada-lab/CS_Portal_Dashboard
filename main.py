from fastapi import FastAPI, Query, HTTPException
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
API_KEY = os.environ.get("CMS_API_KEY", "")

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
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                url, 
                params=params, 
                headers={"API-Key": API_KEY, "Accept": "application/json"}
            )
            # Log status for debugging
            print(f"CMS response: {r.status_code} for {project}/{event}")
            
            if r.status_code != 200:
                print(f"CMS error body: {r.text[:500]}")
                raise HTTPException(status_code=r.status_code, detail=f"CMS error: {r.text[:200]}")
            
            text = r.text.strip()
            if not text:
                # Return empty series if CMS returns nothing
                return {"series": []}
            
            return r.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="CMS request timed out")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"CMS connection error: {str(e)}")

@app.get("/health")
async def health():
    return {"status": "ok", "api_key_set": bool(API_KEY)}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("index.html") as f:
        return f.read()
