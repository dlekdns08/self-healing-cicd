"""
애플리케이션 진입점
"""
from dotenv import load_dotenv
load_dotenv()

import uvicorn
from storage.db import init_db
from webhook.server import app

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
