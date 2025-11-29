import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as alerts_router

app = FastAPI(
    title="Whale Watcher",
    description="Tracks large on-chain transfers and exposes recent whale alerts.",
)

# Include your API routes
app.include_router(alerts_router)

# Serve the static folder (for future CSS/JS if needed)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def root():
    # Serve the main HTML page
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
