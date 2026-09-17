"""Shared Uvicorn startup policy for the local app and disposable fixtures."""
import uvicorn


SHUTDOWN_GRACE_SECONDS = 5


def run(app, *, host="127.0.0.1", port=8787, reload=False):
    # Graph tabs keep SSE responses open indefinitely. Uvicorn drains connections
    # before calling ASGI lifespan shutdown, so lifespan cleanup cannot close them.
    uvicorn.run(app, host=host, port=port, reload=reload,
                timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS)
