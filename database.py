"""database.py - all Supabase calls for Project 8."""
import logging
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
logger = logging.getLogger("project8")

# Stop immediately if secrets are missing
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise ValueError("SUPABASE_URL or SUPABASE_SECRET_KEY is missing from .env")

# One shared client for the whole app
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


def init_db():
    """Check the database is reachable at startup. Warn (don't crash) if it is not."""
    try:
        supabase.table("orders").select("id").limit(1).execute()
        supabase.table("agent_logs").select("id").limit(1).execute()
    except Exception as error:  # Broad on purpose: network, auth, paused project, missing table
        logger.warning("Database check failed at startup: %s", error)


def find_order(order_number):
    """Return the order as a dictionary, or None if it does not exist."""
    try:
        result = (
            supabase.table("orders")
            .select("*")
            .eq("order_number", order_number.strip().upper())
            .limit(1)
            .execute()
        )
    except Exception as error:
        raise RuntimeError(f"Database lookup failed: {error}") from error
    return result.data[0] if result.data else None


def create_ticket(order_number, customer_email, reason, verified, session_id):
    """Save a ticket for a human to review. Returns the ticket id."""
    row = {
        "session_id": session_id,
        "order_number": order_number,
        "customer_email": customer_email,
        "reason": reason,
        "verified": verified,
    }
    try:
        result = supabase.table("review_tickets").insert(row).execute()
    except Exception as error:
        raise RuntimeError(f"Could not create ticket: {error}") from error
    return result.data[0]["id"] if result.data else None


def save_agent_log(session_id, event, tool_name=None, arguments=None, result=None, message=None):
    """Save one agent event. A logging failure must never break the agent."""
    row = {
        "session_id": session_id,
        "event": event,
        "tool_name": tool_name,
        "arguments": arguments,
        "result": result,
        "message": message,
    }
    try:
        supabase.table("agent_logs").insert(row).execute()
    except Exception as error:
        logger.warning("Could not save agent log: %s", error)


def get_recent_agent_logs(limit=50, session_id=None):
    """Return the newest agent log rows as a list of dictionaries."""
    try:
        query = supabase.table("agent_logs").select("*")
        if session_id:
            query = query.eq("session_id", session_id)
        result = query.order("id", desc=True).limit(limit).execute()
    except Exception as error:
        raise RuntimeError(f"Could not read agent logs: {error}") from error
    return result.data


def get_tickets(limit=20, status=None):
    """Return the newest review tickets (optionally only one status)."""
    try:
        query = supabase.table("review_tickets").select("*")
        if status:
            query = query.eq("status", status)
        result = query.order("id", desc=True).limit(limit).execute()
    except Exception as error:
        raise RuntimeError(f"Could not read tickets: {error}") from error
    return result.data