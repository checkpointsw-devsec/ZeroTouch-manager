"""
Main FastAPI application for Check Point Gateway Deployer.
"""
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from pathlib import Path
import uvicorn
import logging
import sys
import time
from loguru import logger

from .config import settings
from .api import zero_touch, smart1_cloud, deployment_orchestrator


class _InterceptHandler(logging.Handler):
    """Bridge stdlib logging into loguru so all log output goes to the same sinks."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level name to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the call stack to find the original caller outside this handler
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# Configure loguru with the LOG_LEVEL and LOG_FILE from settings
logger.remove()  # Remove default handler (no console output)

_log_level = settings.log_level.upper()

# File logging only - resolve log path relative to this file so it's always correct
# regardless of which directory the server is launched from
_app_dir = Path(__file__).parent.parent  # backend/
log_file_path = (_app_dir / settings.log_file).resolve()
log_file_path.parent.mkdir(parents=True, exist_ok=True)
logger.add(
    str(log_file_path),
    level=_log_level,  # Respects LOG_LEVEL from .env
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    rotation="10 MB",  # Rotate when file reaches 10MB
    retention="7 days",  # Keep logs for 7 days
    compression="zip",  # Compress rotated logs
    encoding="utf-8",
    mode="a",
    diagnose=False,  # Disable variable annotations in tracebacks (removes <function at 0x...> noise)
    backtrace=True   # Keep standard traceback formatting
)

# Intercept all stdlib logging (services use logging.getLogger) and route into loguru
logging.basicConfig(handlers=[_InterceptHandler()], level=logging.DEBUG, force=True)
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "fastapi"):
    logging.getLogger(_name).handlers = [_InterceptHandler()]
    logging.getLogger(_name).propagate = False

# Suppress redundant framework loggers — the ASGI middleware already logs
# request/response and the services use log_http_request / log_http_response.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Web application for deploying Check Point Quantum Force and Quantum Spark gateways"
)

# Paths that are not interesting for operational logging (static assets, favicon)
_SKIP_LOG_PREFIXES = ("/static/", "/favicon")

# Request/response URL logging — pure ASGI middleware (does not buffer streaming responses)
class DetailedLoggingMiddleware:
    """Logs incoming request method/URL and outgoing response status + duration.

    Implemented as a raw ASGI middleware instead of ``BaseHTTPMiddleware`` so that
    ``StreamingResponse`` / SSE connections are **not** buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip logging for static assets — they produce a lot of noise
        if path.startswith(_SKIP_LOG_PREFIXES):
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        method = scope.get("method", "")
        query = scope.get("query_string", b"").decode()
        url = f"{path}?{query}" if query else path
        logger.info(f"→ REQUEST: {method} {url}")

        status_code: int | None = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.time() - start_time
        logger.info(f"← RESPONSE: {status_code} (took {duration:.2f}s)")

app.add_middleware(DetailedLoggingMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(zero_touch.router)
app.include_router(smart1_cloud.router)
app.include_router(deployment_orchestrator.router)

# Get the frontend directory path
frontend_dir = Path(__file__).parent.parent.parent / "frontend"

# Mount static files
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

# Root endpoint - serve the frontend
@app.get("/")
async def read_root():
    """Serve the main frontend page."""
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Check Point Gateway Deployer API", "docs": "/docs"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon from the frontend directory."""
    ico = frontend_dir / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    return Response(status_code=204)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": settings.app_version}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="warning",  # Silence uvicorn's own console output
    )