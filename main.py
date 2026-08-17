import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as alerts_router
from app.db import init_db
from app import whale_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        whale_service.fetch_whales(limit=50, min_amount=0.0)
        logger.info("Warmed whale transfer cache: %s", whale_service.cache_status())
    except Exception:
        logger.exception("Cache warm-up skipped")
    yield
    whale_service.close()


app = FastAPI(
    title="Whale Watcher",
    description="Tracks large on-chain transfers and exposes recent whale alerts.",
    lifespan=lifespan,
)

app.include_router(alerts_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
def health():
    return {"status": "ok", **whale_service.cache_status()}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
