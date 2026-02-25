"""
Personal Finance AI — FastAPI Main Entry Point
================================================
- Webhook endpoint for Telegram
- Health check endpoint
- Database initialization on startup
- Background task scheduler
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, get_async_engine, get_session_factory


# ============================================
# LIFESPAN (startup + shutdown)
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # --- Startup ---
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} starting...")

    # Write Google service account JSON from env var if file doesn't exist
    if settings.GOOGLE_SERVICE_ACCOUNT_JSON and not os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE):
        try:
            sa_path = "/tmp/google-sa.json"
            with open(sa_path, "w") as f:
                f.write(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            settings.GOOGLE_SERVICE_ACCOUNT_FILE = sa_path
            print("✅ Google credentials written from env var")
        except Exception as e:
            print(f"⚠️ Failed to write Google credentials: {e}")

    # Init database
    try:
        await init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"⚠️ Database init failed (will retry on first request): {e}")

    # Init scheduler for background tasks
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        # Placeholder for scheduled tasks (reports, backups)
        # scheduler.add_job(daily_report_job, 'cron', hour=21, minute=0)
        # scheduler.add_job(backup_job, 'cron', hour=3, minute=0)
        scheduler.start()
        app.state.scheduler = scheduler
        print("✅ Background scheduler started")
    except Exception as e:
        print(f"⚠️ Scheduler init failed: {e}")

    print(f"✅ {settings.APP_NAME} ready!")

    yield

    # --- Shutdown ---
    if hasattr(app.state, 'scheduler'):
        app.state.scheduler.shutdown(wait=False)
    print("👋 Shutting down...")


# ============================================
# APP
# ============================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health")
async def health():
    """Health check endpoint for Railway monitoring."""
    from app.modules.ai_processing.groq_rotator import groq_rotator

    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "connected",
        "groq_keys": groq_rotator.get_status()['total_keys'],
    }


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """
    Telegram webhook endpoint.
    Receives updates and routes to bot handler.
    """
    try:
        update = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # Process in background-ish (but within request lifecycle for DB session)
    from app.telegram.bot import handle_update
    from app.database import get_async_engine, get_session_factory

    engine = get_async_engine(settings.DATABASE_URL)
    factory = get_session_factory(engine)

    async with factory() as db:
        try:
            await handle_update(update, db)
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"[ERR] Webhook handler: {e}")

    return {"ok": True}


@app.get("/webhook/setup")
async def setup_webhook():
    """
    Set Telegram webhook URL.
    Open this URL in browser after deploying to Railway.
    """
    import httpx

    if not settings.TELEGRAM_TOKEN:
        return {"error": "TELEGRAM_TOKEN not configured"}

    # Auto-detect Railway URL (try multiple env vars)
    railway_url = (
        os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        or os.environ.get("RAILWAY_STATIC_URL", "")
    )
    if not railway_url:
        # Fallback: check request host
        return {
            "error": "Cannot detect Railway domain. Set RAILWAY_PUBLIC_DOMAIN env var.",
            "hint": "Go to Railway → web → Settings → Public Networking → Generate Domain",
        }

    # Clean up URL
    if railway_url.startswith("https://"):
        railway_url = railway_url.replace("https://", "")
    webhook_url = f"https://{railway_url}/webhook/telegram"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/setWebhook",
            json={"url": webhook_url},
            timeout=10,
        )
        result = resp.json()

    return {"webhook_url": webhook_url, "telegram_response": result}


# ============================================
# __init__ files for module packages
# ============================================
# These are created as empty files by the project scanner
