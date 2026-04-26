import re
from datetime import datetime, timedelta
from typing import Optional


def parse_coordinate(raw: str) -> float:
    """
    Accepts both decimal (30.8086) and DMS (30°48′31″N) coordinate formats.
    Returns decimal float.
    """
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass

    # DMS: extract all numbers
    parts = re.findall(r"[\d.]+", raw)
    if not parts:
        raise ValueError(f"Cannot parse coordinate: {raw}")

    degrees = float(parts[0])
    minutes = float(parts[1]) if len(parts) > 1 else 0.0
    seconds = float(parts[2]) if len(parts) > 2 else 0.0
    decimal = degrees + minutes / 60 + seconds / 3600

    if any(d in raw.upper() for d in ['S', 'W']):
        decimal = -decimal

    return round(decimal, 6)


# ==========================================
# DOUBLE-CLICK PROTECTION
# Stores message_id → timestamp to prevent
# two admins approving the same expense.
# Auto-cleans entries older than 1 hour.
# ==========================================
_processed_messages: dict[int, datetime] = {}


def is_already_processed(message_id: int) -> bool:
    """Returns True if this message was already processed. Registers it if not."""
    # Clean up stale entries to prevent memory leak
    cutoff = datetime.utcnow() - timedelta(hours=1)
    stale = [k for k, v in _processed_messages.items() if v < cutoff]
    for k in stale:
        del _processed_messages[k]

    if message_id in _processed_messages:
        return True

    _processed_messages[message_id] = datetime.utcnow()
    return False


def unregister_message(message_id: int):
    """Removes a message from processed set (called on failure to allow retry)."""
    _processed_messages.pop(message_id, None)


def format_ist(utc_dt: datetime) -> str:
    """Converts UTC datetime to IST string."""
    return (utc_dt + timedelta(hours=5, minutes=30)).strftime("%d %b, %I:%M %p")