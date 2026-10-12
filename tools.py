"""tools.py - the 3 tools the AI may use. Python checks everything the AI sends."""
import logging
import re
from datetime import date

import database

logger = logging.getLogger("project8")

# Rules and limits live here, where Python (not the AI) controls them
REFUND_WINDOW_DAYS = 30
MAX_TOOL_CALLS = 8          # per customer message
MAX_REASON_LENGTH = 300
ORDER_PATTERN = re.compile(r"ORD-\d{4}")
EMAIL_PATTERN = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]+")

# One message for "not found" AND "wrong email", so nobody can probe which orders exist
NO_MATCH = {"found": False, "message": "No order matches that order number and email."}

# The menu we show the AI: name, description, and the inputs each tool needs
TOOL_DECLARATIONS = [
    {
        "name": "get_order_status",
        "description": ("Look up an order's item, status and delivery dates. "
                        "Needs BOTH the order number and the email used for the order."),
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "Format ORD-1234"},
                "customer_email": {"type": "string", "description": "Email used for the order"},
            },
            "required": ["order_number", "customer_email"],
        },
    },
    {
        "name": "check_refund_eligibility",
        "description": ("Check whether an order is eligible for a refund under store rules. "
                        "This only CHECKS. It never issues a refund. "
                        "Needs BOTH the order number and the email used for the order."),
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "Format ORD-1234"},
                "customer_email": {"type": "string", "description": "Email used for the order"},
            },
            "required": ["order_number", "customer_email"],
        },
    },
    {
        "name": "request_human_review",
        "description": ("Create a ticket so a human team member reviews this case. Use it for "
                        "refund requests that look eligible, complaints, and anything you cannot "
                        "handle. Order number and email are optional here."),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short summary for the team"},
                "order_number": {"type": "string", "description": "Format ORD-1234, if known"},
                "customer_email": {"type": "string", "description": "Customer email, if known"},
            },
            "required": ["reason"],
        },
    },
]


def _clean_order_number(value):
    """Return a valid order number like ORD-1001, or None."""
    text = str(value or "").strip().upper()
    return text if ORDER_PATTERN.fullmatch(text) else None


def _clean_email(value):
    """Return a lowercase valid-looking email, or None."""
    text = str(value or "").strip().lower()
    return text if len(text) <= 254 and EMAIL_PATTERN.fullmatch(text) else None


def _verify_order(args):
    """Return (order, None) if order number AND email match, else (None, error_dict)."""
    order_number = _clean_order_number(args.get("order_number"))
    email = _clean_email(args.get("customer_email"))

    if not order_number:
        return None, {"found": False,
                      "message": "The order number is missing or not like ORD-1234. Ask the customer for it."}
    if not email:
        return None, {"found": False,
                      "message": "A valid email address is needed. Ask the customer for it."}

    order = database.find_order(order_number)
    stored_email = str((order or {}).get("email") or "").strip().lower()
    if not order or stored_email != email:
        return None, dict(NO_MATCH)
    return order, None


def get_order_status(args, session_id):
    """Tool 1: read-only lookup. Only safe fields are returned (no name, no price)."""
    order, error = _verify_order(args)
    if error:
        return error
    return {
        "found": True,
        "order_number": order["order_number"],
        "item": order["item"],
        "status": order["status"],
        "estimated_delivery": order.get("estimated_delivery") or "not available",
        "delivered_on": order.get("delivered_on") or "not delivered yet",
    }


def check_refund_eligibility(args, session_id):
    """Tool 2: Python applies the refund rules. The AI cannot change them."""
    order, error = _verify_order(args)
    if error:
        return error

    base = {"found": True, "order_number": order["order_number"], "item": order["item"],
            "human_approval_required": True}

    if order["status"] != "delivered":
        return {**base, "eligible": False,
                "reason": f"Order status is '{order['status']}'. Only delivered orders can be refunded."}

    try:
        days_ago = (date.today() - date.fromisoformat(str(order["delivered_on"]))).days
    except (TypeError, ValueError):
        return {**base, "eligible": False, "reason": "Delivery date is missing, so a human must check."}

    if days_ago > REFUND_WINDOW_DAYS:
        return {**base, "eligible": False,
                "reason": f"Delivered {days_ago} days ago. The refund window is {REFUND_WINDOW_DAYS} days."}

    # Expensive orders need a senior team member to approve
    approval_note = "A human must still approve the refund."
    if float(order.get("total") or 0) >= 75:
        approval_note = "This is a high-value order, so a senior team member must approve the refund."

    return {**base, "eligible": True,
            "reason": (f"Delivered {days_ago} days ago, inside the {REFUND_WINDOW_DAYS}-day window. "
                       f"{approval_note}")}


def request_human_review(args, session_id):
    """Tool 3: the only tool that writes data. It creates a ticket, never a refund."""
    reason = str(args.get("reason") or "").strip()[:MAX_REASON_LENGTH]
    if not reason:
        return {"ticket_created": False, "message": "A reason is required."}

    order_number = _clean_order_number(args.get("order_number"))
    email = _clean_email(args.get("customer_email"))

    # "verified" tells the team whether order number and email really matched
    verified = False
    if order_number and email:
        order = database.find_order(order_number)
        verified = bool(order) and str(order.get("email") or "").strip().lower() == email

    ticket_id = database.create_ticket(order_number, email, reason, verified, session_id)
    return {"ticket_created": True, "ticket_id": ticket_id,
            "message": "A team member will review this and contact the customer."}


# The registry: tool name -> Python function
TOOL_FUNCTIONS = {
    "get_order_status": get_order_status,
    "check_refund_eligibility": check_refund_eligibility,
    "request_human_review": request_human_review,
}


def run_tool(name, args, session_id, state):
    """Safely run one tool. Never raises. Always returns a dictionary for the AI."""
    state["calls"] += 1
    if state["calls"] > MAX_TOOL_CALLS:
        return {"error": "Too many tool calls. Stop and hand over to a human."}

    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": "Unknown tool."}
    if not isinstance(args, dict):
        return {"error": "Tool arguments must be an object."}
    # Only one ticket per customer message (stops duplicate tickets)
    if name == "request_human_review" and state["tickets"] >= 1:
        return {"ticket_created": False, "message": "A review request was already created."}

    try:
        result = function(args, session_id)
    except Exception as error:  # Broad on purpose: a tool failure must not crash the agent
        logger.error("Tool %s failed: %s", name, error)
        return {"error": "The system could not complete this action right now."}

    if name == "request_human_review" and result.get("ticket_created"):
        state["tickets"] += 1
    return result