@app.on_event("startup")
async def startup_event():
    global session_clock, session_gate

    from session_clock import session_clock as sc
    from session_clock import session_gate as sg

    session_clock = sc
    session_gate = sg

    print("SYSTEM READY")
