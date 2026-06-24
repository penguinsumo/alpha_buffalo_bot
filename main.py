
# Health Check Endpoint for Railway & UptimeRobot
@app.get("/")
async def root():
    return {"status": "OK", "bot": "Alpha Buffalo v11.2"}
