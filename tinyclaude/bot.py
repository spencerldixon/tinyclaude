import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USERS = {int(uid) for uid in os.environ.get("ALLOWED_USERS", "").split(",") if uid.strip()}
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", Path.home() / ".tinyclaude" / "sessions"))
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT", "1800"))
HEARTBEAT_TIMEZONE = os.environ.get("HEARTBEAT_TIMEZONE", "Europe/London")

HEARTBEAT_PATH = Path(__file__).parent / "HEARTBEAT.md"

from functools import wraps

_heartbeat_mtime: float = 0.0


def authorize_user(fn):
    @wraps(fn)
    async def wrapper(update, context):
        if update.effective_user.id not in ALLOWED_USERS:
            return
        return await fn(update, context)
    return wrapper


def session_file(chat_id):
    return SESSIONS_DIR / f"{chat_id}.json"


def load_session(chat_id):
    path = session_file(chat_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if time.time() - data.get("last_used", 0) > SESSION_TIMEOUT:
        path.unlink()
        return None
    return data.get("session_id")


def save_session(chat_id, session_id):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_file(chat_id).write_text(json.dumps({"session_id": session_id, "last_used": time.time()}))


# ---------------------------------------------------------------------------
# Heartbeat parser / writer
# ---------------------------------------------------------------------------

def parse_heartbeat_file() -> list[dict]:
    """Parse HEARTBEAT.md into a list of heartbeat entry dicts."""
    if not HEARTBEAT_PATH.exists():
        return []
    content = HEARTBEAT_PATH.read_text().strip()
    if not content:
        return []

    entries = []
    # Split on ## headers
    blocks = re.split(r'^## ', content, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split('\n')
        name = lines[0].strip()
        entry = {"name": name}
        for line in lines[1:]:
            m = re.match(r'^- \*\*(\w+):\*\*\s*(.+)$', line.strip())
            if m:
                key, value = m.group(1), m.group(2).strip()
                if key == "enabled":
                    entry[key] = value.lower() == "true"
                else:
                    entry[key] = value
        if "schedule" in entry:
            entries.append(entry)
    return entries


def write_heartbeat_file(heartbeats: list[dict]) -> None:
    """Serialize list of heartbeat dicts back to HEARTBEAT.md format."""
    blocks = []
    for entry in heartbeats:
        lines = [f"## {entry['name']}"]
        for key in ("schedule", "chat_id", "prompt", "timezone", "enabled"):
            if key in entry:
                value = entry[key]
                if key == "enabled":
                    value = "true" if value else "false"
                lines.append(f"- **{key}:** {value}")
        blocks.append('\n'.join(lines))
    HEARTBEAT_PATH.write_text('\n\n'.join(blocks) + ('\n' if blocks else ''))


def _inject_chat_id(chat_id: int) -> None:
    """Backfill chat_id into any heartbeat entries missing it."""
    if not HEARTBEAT_PATH.exists():
        return
    heartbeats = parse_heartbeat_file()
    if not heartbeats:
        return
    changed = False
    for entry in heartbeats:
        if "chat_id" not in entry:
            entry["chat_id"] = str(chat_id)
            changed = True
        else:
            try:
                int(entry["chat_id"])
            except (ValueError, TypeError):
                entry["chat_id"] = str(chat_id)
                changed = True
    if changed:
        write_heartbeat_file(heartbeats)


# ---------------------------------------------------------------------------
# Schedule parser
# ---------------------------------------------------------------------------

DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def parse_schedule(schedule_str: str, tz: ZoneInfo) -> tuple[str, dict]:
    """Parse a schedule string and return (method_name, kwargs) for JobQueue."""
    parts = schedule_str.strip().split()

    if parts[0] == "daily":
        # daily HH:MM
        h, m = map(int, parts[1].split(':'))
        t = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0).timetz()
        return ("run_daily", {"time": t})

    elif parts[0] == "weekly":
        # weekly <day> HH:MM
        day_num = DAY_MAP[parts[1].lower()]
        h, m = map(int, parts[2].split(':'))
        t = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0).timetz()
        return ("run_daily", {"time": t, "days": (day_num,)})

    elif parts[0] == "every":
        # every <N>d HH:MM or every <N>w HH:MM
        interval_str = parts[1]
        h, m = map(int, parts[2].split(':'))
        if interval_str.endswith('d'):
            interval = timedelta(days=int(interval_str[:-1]))
        elif interval_str.endswith('w'):
            interval = timedelta(weeks=int(interval_str[:-1]))
        else:
            raise ValueError(f"Unknown interval unit in: {interval_str}")
        # Calculate next occurrence of HH:MM in the given timezone
        now = datetime.now(tz)
        first = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if first <= now:
            first += timedelta(days=1)
        return ("run_repeating", {"interval": interval, "first": first})

    elif parts[0] == "once":
        # once YYYY-MM-DD HH:MM
        date_str = parts[1]
        time_str = parts[2]
        h, m = map(int, time_str.split(':'))
        y, mo, d = map(int, date_str.split('-'))
        when = datetime(y, mo, d, h, m, tzinfo=tz)
        return ("run_once", {"when": when})

    else:
        raise ValueError(f"Unknown schedule format: {schedule_str}")


# ---------------------------------------------------------------------------
# Heartbeat scheduler
# ---------------------------------------------------------------------------

def sync_heartbeats(app: Application) -> None:
    """Read HEARTBEAT.md, clear existing heartbeat jobs, and schedule new ones."""
    global _heartbeat_mtime

    heartbeats = parse_heartbeat_file()

    # Remove all existing heartbeat jobs
    current_jobs = app.job_queue.get_jobs_by_name("heartbeat_marker")
    # We name jobs with a heartbeat_ prefix; get_jobs_by_name is exact match,
    # so we iterate all jobs and filter by name prefix.
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith("heartbeat_"):
            job.schedule_removal()

    default_tz = ZoneInfo(HEARTBEAT_TIMEZONE)

    for entry in heartbeats:
        if not entry.get("enabled", True):
            continue

        tz_name = entry.get("timezone", HEARTBEAT_TIMEZONE)
        tz = ZoneInfo(tz_name)
        chat_id = entry.get("chat_id")
        prompt = entry.get("prompt", "")
        name = entry.get("name", "unnamed")
        schedule = entry.get("schedule", "")
        job_name = f"heartbeat_{name}"

        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            logger.warning("Skipping heartbeat '%s': invalid or missing chat_id '%s'", name, chat_id)
            continue

        try:
            method_name, kwargs = parse_schedule(schedule, tz)
        except ValueError as e:
            logger.warning("Skipping heartbeat '%s': %s", name, e)
            continue

        job_data = {"chat_id": chat_id_int, "prompt": prompt, "name": name, "schedule": schedule}

        method = getattr(app.job_queue, method_name)
        method(callback=heartbeat_callback, name=job_name, data=job_data, **kwargs)
        logger.info("Scheduled heartbeat '%s': %s %s", name, method_name, schedule)

    # Update mtime
    if HEARTBEAT_PATH.exists():
        _heartbeat_mtime = HEARTBEAT_PATH.stat().st_mtime
    else:
        _heartbeat_mtime = 0.0


def maybe_sync_heartbeats(app: Application) -> None:
    """Check if HEARTBEAT.md changed (via mtime) and re-sync if so."""
    global _heartbeat_mtime
    if not HEARTBEAT_PATH.exists():
        if _heartbeat_mtime != 0.0:
            _heartbeat_mtime = 0.0
            sync_heartbeats(app)
        return
    current_mtime = HEARTBEAT_PATH.stat().st_mtime
    if current_mtime != _heartbeat_mtime:
        logger.info("HEARTBEAT.md changed (mtime %.2f -> %.2f), re-syncing", _heartbeat_mtime, current_mtime)
        sync_heartbeats(app)


async def heartbeat_callback(context) -> None:
    """Fired by JobQueue when a scheduled heartbeat triggers."""
    data = context.job.data
    chat_id = data["chat_id"]
    prompt = data["prompt"]
    name = data["name"]
    schedule = data["schedule"]

    logger.info("Heartbeat firing: '%s'", name)

    # Run prompt via claude --print as a fresh session (no resume)
    cmd = ["claude", "--output-format", "json", "--print", prompt]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("Heartbeat '%s' claude error (rc=%d): %s", name, proc.returncode, err)
        response = f"Heartbeat '{name}' error: {err or 'unknown error'}"
    else:
        try:
            result = json.loads(stdout.decode())
            response = result.get("result", stdout.decode().strip()) or "(empty response)"
        except (json.JSONDecodeError, KeyError):
            response = stdout.decode().strip() or "(empty response)"

    # Send response to chat, chunked at 4096
    for i in range(0, len(response), 4096):
        chunk = response[i : i + 4096]
        try:
            await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=chunk)

    # If this was a one-off schedule, remove it from HEARTBEAT.md
    if schedule.startswith("once "):
        heartbeats = parse_heartbeat_file()
        heartbeats = [h for h in heartbeats if h.get("name") != name]
        write_heartbeat_file(heartbeats)
        logger.info("Removed one-off heartbeat '%s' after firing", name)
        # Re-sync to clean up jobs
        sync_heartbeats(context.application)


# ---------------------------------------------------------------------------
# Claude interaction
# ---------------------------------------------------------------------------

async def ask_claude(chat_id, message):
    """ Forward a message to claude code and send the response back """
    session_id = load_session(chat_id)

    cmd = ["claude", "--output-format", "json"]

    # Resume session if present
    if session_id:
        cmd += ["--resume", session_id]

    cmd += ["--print", message]

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("claude error (rc=%d): %s", proc.returncode, err)
        return f"Error: {err or 'unknown error'}"

    try:
        result = json.loads(stdout.decode())
        save_session(chat_id, result.get("session_id", session_id))
        return result.get("result", stdout.decode().strip()) or "(empty response)"
    except (json.JSONDecodeError, KeyError):
        return stdout.decode().strip() or "(empty response)"


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------

@authorize_user
async def start(update, context):
    """Send a message when /start is issued"""
    await update.message.reply_text("Hey! I'm TinyClaude")


@authorize_user
async def reset(update, context):
    """Clear the context and start a new chat"""
    path = session_file(update.effective_chat.id)
    if path.exists():
        path.unlink()
    await update.message.reply_text("Session cleared")


@authorize_user
async def heartbeat(update, context):
    """Return the contents of HEARTBEAT.md"""
    try:
        content = HEARTBEAT_PATH.read_text().strip()
    except FileNotFoundError:
        content = ""
    await update.message.reply_text(content or "No heartbeats scheduled.")


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

@authorize_user
async def handle_message(update, context):
    """Handles incoming messages that are not slash commands"""
    response = await ask_claude(update.effective_chat.id, update.message.text)

    for i in range(0, len(response), 4096):
        chunk = response[i : i + 4096]
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)

    # Inject chat_id into any new entries missing it, then sync
    _inject_chat_id(update.effective_chat.id)
    maybe_sync_heartbeats(context.application)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    """Called after the application is initialized. Loads heartbeats from file."""
    sync_heartbeats(app)
    logger.info("Heartbeat scheduler initialized")


def main():
    """Start the bot"""
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register post-init to load heartbeats at startup
    app.post_init = post_init

    # Handle slash commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("heartbeat", heartbeat))

    # Handle messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run until quit
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
