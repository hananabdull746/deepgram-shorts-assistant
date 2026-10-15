from __future__ import annotations

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, workers=1,
                log_level="info", access_log=False)
