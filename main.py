"""main.py - Project 9: the FastAPI backend that also serves the store website."""
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import agent
import database

load_dotenv()

# Logs go to the screen AND to app.log
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "app.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("project9")

# Password that protects the admin pages
ADMIN_KEY = os.getenv("ADMIN_KEY")
if not ADMIN_KEY:
    raise ValueError("ADMIN_KEY was not found in the .env file.")


def read_int(name, default):
    """Read a whole number from .env, or use the default if it is missing or wrong."""
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# Settings from .env
RATE_LIMIT_MESSAGES = read_int("RATE_LIMIT_MESSAGES", 10)   # per visitor, per minute
RATE_LIMIT_SECONDS = 60
DAILY_MESSAGE_LIMIT = read_int("DAILY_MESSAGE_LIMIT", 50)   # for the whole site, per day
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").strip().lower() == "true"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

VALID_TICKET_STATUSES = {"open", "closed"}

# Counters kept in memory (they reset when the server restarts)
request_times = defaultdict(deque)
rate_lock = threading.Lock()
daily = {"date": None, "count": 0}
daily_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    """Runs once at startup: check the database."""
    database.init_db()
    logger.info("Website and agent ready.")
    yield


app = FastAPI(title="Project 9 - AI Store Website", lifespan=lifespan)

# CORS: only needed when ANOTHER website calls this backend. Empty list = no other sites allowed.
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Extra browser protection. The strict rules apply only to the store page."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
    return response


# ------------------------------------------------------------
# DATA SHAPES
# ------------------------------------------------------------

class ChatIn(BaseModel):
    """What the chat box sends us."""
    message: str = Field(..., max_length=1000)
    session_id: str = Field(..., min_length=8, max_length=100)

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty.")
        return value


class ChatOut(BaseModel):
    """What we send back."""
    reply: str
    handed_off: bool


class ResetIn(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=100)


# ------------------------------------------------------------
# PROTECTION: rate limit, daily cap, admin password
# ------------------------------------------------------------

def get_client_id(request):
    """Who is this visitor? Uses the proxy's header only if TRUST_PROXY is true."""
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # The LAST address is the one our own host's proxy added (the first can be faked)
            return forwarded.split(",")[-1].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def check_rate_limit(client_id):
    """Allow only a few messages per minute per visitor."""
    now = time.time()
    with rate_lock:
        # Tidy up: forget visitors we have not seen for a while
        if len(request_times) > 5000:
            old = [key for key, times in request_times.items()
                   if not times or now - times[-1] > RATE_LIMIT_SECONDS]
            for key in old:
                del request_times[key]

        times = request_times[client_id]
        while times and now - times[0] > RATE_LIMIT_SECONDS:
            times.popleft()
        if len(times) >= RATE_LIMIT_MESSAGES:
            raise HTTPException(status_code=429, detail="Too many messages. Please wait a minute.")
        times.append(now)


def check_daily_limit():
    """Stop all chats once the site-wide daily cap is reached (protects your AI quota)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with daily_lock:
        if daily["date"] != today:
            daily["date"] = today
            daily["count"] = 0
        if daily["count"] >= DAILY_MESSAGE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="The chat is very busy today. Please try again tomorrow or contact the store directly.",
            )
        daily["count"] += 1


def require_admin(x_admin_key: str | None = Header(default=None)):
    """Check the X-Admin-Key header (compared safely)."""
    if not x_admin_key or not secrets.compare_digest(
        x_admin_key.encode("utf-8"), ADMIN_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")


# ------------------------------------------------------------
# ROUTES (plain "def": FastAPI runs them in worker threads)
# ------------------------------------------------------------

@app.get("/", include_in_schema=False)
def home():
    """The store website."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
def health():
    """Quick check that the server is alive."""
    return {"status": "ok"}


@app.post("/agent-chat", response_model=ChatOut)
def agent_chat(body: ChatIn, request: Request):
    """The chat box sends each customer message here."""
    check_rate_limit(get_client_id(request))
    check_daily_limit()

    reply, handed_off = agent.run_agent(body.session_id, body.message)
    return ChatOut(reply=reply, handed_off=handed_off)


@app.post("/reset")
def reset(body: ResetIn):
    """Start a fresh chat for this session."""
    agent.reset_session(body.session_id)
    return {"status": "ok"}


@app.get("/logs", dependencies=[Depends(require_admin)])
def get_logs(limit: int = Query(50, ge=1, le=200), session_id: str | None = Query(None)):
    """Admin: recent agent events."""
    try:
        return database.get_recent_agent_logs(limit=limit, session_id=session_id)
    except RuntimeError as error:
        logger.error("Reading logs failed: %s", error)
        raise HTTPException(status_code=500, detail="Could not read logs.")


@app.get("/tickets", dependencies=[Depends(require_admin)])
def get_tickets(limit: int = Query(20, ge=1, le=100), status: str | None = Query(None)):
    """Admin: tickets waiting for a human. Optional: ?status=open"""
    if status is not None and status not in VALID_TICKET_STATUSES:
        raise HTTPException(status_code=400, detail="status must be 'open' or 'closed'.")
    try:
        return database.get_tickets(limit=limit, status=status)
    except RuntimeError as error:
        logger.error("Reading tickets failed: %s", error)
        raise HTTPException(status_code=500, detail="Could not read tickets.")


# Serve style.css and chat.js from the static folder (mounted last, so it never hides a route)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")