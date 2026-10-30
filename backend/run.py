"""Run the standalone Copy Studio service."""
import os

import uvicorn

from copy_studio.api import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("COPY_STUDIO_HOST", "127.0.0.1"),
        port=int(os.environ.get("COPY_STUDIO_PORT", "8765")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
