"""Lead Console backend. Run:  python server.py   then open http://127.0.0.1:8000"""
import json
from pathlib import Path
from typing import List, Literal

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import pipeline

app = FastAPI(title="Lead Console")
STATIC = Path(__file__).parent / "static"


class RunReq(BaseModel):
    query: str = Field(min_length=1)
    location: str = ""
    max_results: int = Field(50, ge=1, le=500)
    sources: List[Literal["google_maps", "justdial", "indiamart"]] = Field(min_length=1)
    show_browser: bool = True


class CampaignReq(BaseModel):
    min_grade: Literal["A", "B", "C"] = "B"
    daily_limit: int = Field(20, ge=1, le=500)
    dry_run: bool = True
    confirm_live: bool = False      # must be true to actually send
    name: str = ""
    company: str = ""
    service: str = ""
    website: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/run")
def run(req: RunReq):
    try:
        pipeline.start("scrape", pipeline.run_scrape, req.model_dump())
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/campaign")
def campaign(req: CampaignReq):
    if not req.dry_run and not req.confirm_live:
        raise HTTPException(400, "Live sending needs explicit confirmation.")
    try:
        pipeline.start("campaign", pipeline.run_campaign, req.model_dump())
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/status")
def status(since: int = 0):
    lines = pipeline.job.lines[since:]
    return {"status": pipeline.job.status, "kind": pipeline.job.kind,
            "lines": lines, "next": since + len(lines)}


@app.get("/api/leads")
def leads():
    df = pipeline.latest_scored()
    return JSONResponse([] if df is None else json.loads(df.to_json(orient="records")))


if __name__ == "__main__":
    import uvicorn
    print("\n  Lead Console → http://127.0.0.1:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)   # local only: there is no login