from fastapi import FastAPI, Request

app = FastAPI()

# ใช้ @app.api_route เพื่อให้รองรับทั้ง GET, HEAD, POST ฯลฯ
@app.api_route("/", methods=["GET", "HEAD", "POST"])
async def root(request: Request):
    return {"status": "OK", "bot": "Alpha Buffalo v11.2"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
