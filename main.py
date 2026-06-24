from fastapi import FastAPI
app = FastAPI()

@app.get('/')
async def root():
    return {'status': 'OK', 'bot': 'Alpha Buffalo v11.2'}
