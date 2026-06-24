import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlphaBuffaloGateway")

@app.on_event("startup")
async def startup_event():
    logger.info("Gateway Started. Listening for Railway Health Checks...")

@app.api_route("/", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "OK", "message": "Alpha Buffalo Gateway is Alive"}

@app.get("/signal/latest")
async def get_latest_signal(key: str = ""):
    if key != "DEMO123":
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        return {"status": "success", "signal": "TEST"}
    except Exception as e:
        logger.error(f"Error in /signal/latest: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Error"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    logger.info(f"Starting Uvicorn on Port: {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
