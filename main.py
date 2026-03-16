"""
애플리케이션 진입점
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import uvicorn
from storage.db import init_db
from webhook.server import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
