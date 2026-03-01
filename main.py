from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from services import calculate_dashboard_metrics, create_full_pdf
import uvicorn

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")

@app.get("/")
def read_root():
    index_path = BASE_DIR / "index.html"
    return Response(content=index_path.read_text(encoding="utf-8"), media_type="text/html")

@app.get("/api/dashboard")
async def get_dashboard_data(refresh: bool = False):
    return calculate_dashboard_metrics(force_refresh=refresh)

@app.get("/api/pdf")
async def get_pdf():
    return Response(content=create_full_pdf(), media_type="application/pdf")

@app.get("/favicon.ico")
async def favicon(): return Response(status_code=204)

import os

if __name__ == "__main__":
    # Railway'in atadığı PORT'u al, yoksa varsayılan 80'i kullan
    port = int(os.environ.get("PORT", 80))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
