# tinyclaude

A tiny Telegram + Claude Code assistant.

Send messages via Telegram, get responses from Claude Code with full tool access (file reading, web search, bash commands, etc), and schedule tasks and reminders.

## How it works

```
You (Telegram) → Bot Server → Claude Code CLI → Response → Telegram
```

Each Telegram chat maintains a conversation session with a 30-minute timeout. The bot shells out to the `claude` CLI with `--resume` to maintain context across messages. Send `/reset` to start a fresh conversation.

### Scheduling

We write reminders and recurring tasks to a `HEARTBEAT.md` file. When this file changes, Claude parses it and turns the tasks into scheduled Jobs that execute when required. This approach means no unnecessary polling and you can list your entire `HEARTBEAT.md` at any time with `/heartbeat`

## Installation

### Step 1: Create a Telegram bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Choose a display name (e.g. "tinyclaude")
4. Choose a username (must end in `bot`, e.g. `tinyclaude_bot`)
5. BotFather will reply with your **bot token** — save this, you'll need it shortly
6. Optionally send `/setdescription` to give your bot a description

### Step 2: Get your Telegram user ID

This locks the bot down so only you can use it.

1. Open Telegram and search for [@userinfobot](https://t.me/userinfobot)
2. Send it any message
3. It replies with your **user ID** (a number like `123456789`) — save this

### Step 3: Install and authenticate Claude Code

#### Setting up locally

Claude Code uses OAuth — you log in once via browser and credentials are stored in `~/.claude/`. The bot mounts this directory to reuse your auth.

```bash
# Install Node.js 22+ if you don't have it
# https://nodejs.org/

# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Run it once interactively — it opens a browser for OAuth login
claude

# Follow the prompts to log in with your Anthropic account.
# Once authenticated, credentials are saved to ~/.claude/
# and all future invocations (including from the bot) will use them.

# Verify it works:
claude --print "hello"
```

#### Running on a server

On a server or headless Raspberry Pi, run `claude` on a machine with a browser first, then copy `~/.claude/` to the Pi:

```bash
scp -r ~/.claude/ pi@your-pi:~/.claude/
```

### Step 4: Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_TOKEN=your-bot-token-from-step-1
ALLOWED_USERS=your-user-id-from-step-2
```

### Step 5: Run

See the Docker or local install sections below.

## Install with Docker

```bash
git clone https://github.com/spencerldixon/tinyclaude
cd tinyclaude

# Complete steps 1-4 above first, then:
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

## Running on a Raspberry Pi

The same Docker setup works on a raspberry pi:

```bash
# Install Docker if you haven't already
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in, then:

git clone https://github.com/spencerldixon/tinyclaude
cd tinyclaude

# Complete steps 1-4 above, then:
docker compose up -d
```

The image builds for `arm64` natively on the Pi — no cross-compilation needed.

## Install without Docker

Requires Python 3.14+, Node.js 22+, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/spencerldixon/tinyclaude
cd tinyclaude

# Install Claude Code CLI (if not done in step 3)
npm install -g @anthropic-ai/claude-code

# Install Python dependencies
uv sync

# Complete steps 1-4 above, then:
source .env && uv run tinyclaude
```

## Telegram Slash Commands

| Command | Description |
|---------|-------------|
| `/start` | Greeting and info |
| `/reset` | Clear conversation session and start fresh |
| `/heartbeat` | List scheduled jobs in the HEARTBEAT.md file |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_TOKEN` | Yes | - | Bot token from @BotFather |
| `ALLOWED_USERS` | No | (all) | Comma-separated Telegram user IDs |
| `CLAUDE_BIN` | No | `claude` | Path to claude CLI binary |
| `SESSION_TIMEOUT` | No | `1800` | Session timeout in seconds |
| `SESSIONS_DIR` | No | `~/.tinyclaude/sessions` | Where to store session files |
