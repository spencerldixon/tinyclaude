# TinyClaude

Personal Telegram bot that forwards messages to Claude CLI.

## Heartbeat Scheduling

Users can ask you to schedule recurring or one-off reminders/tasks. When they do, write entries to the heartbeat file at `tinyclaude/HEARTBEAT.md`. The bot watches this file and automatically schedules jobs from it.

### File format

Each entry is a markdown section with `## ` header (the name) followed by key-value fields:

```markdown
## Morning Tasks Reminder
- **schedule:** daily 09:00
- **prompt:** Give me a prioritized list of my tasks for today.
- **timezone:** Europe/London
- **enabled:** true
```

### Required fields
- **schedule** - when to run (see formats below)
- **prompt** - what to send to Claude when the schedule fires
- **DO NOT include `chat_id`** - the bot injects this automatically
- **timezone** - IANA timezone (e.g. `Europe/London`, `America/New_York`, `UTC`)
- **enabled** - `true` or `false`

### Schedule formats
| Format | Example | Meaning |
|---|---|---|
| `daily HH:MM` | `daily 09:00` | Every day at that time |
| `weekly <day> HH:MM` | `weekly monday 10:00` | Every week on that day |
| `every <N>d HH:MM` | `every 10d 08:30` | Every N days at that time |
| `every <N>w HH:MM` | `every 2w 09:00` | Every N weeks at that time |
| `once YYYY-MM-DD HH:MM` | `once 2026-02-20 09:00` | One-time, auto-removed after firing |

### Important notes
- Separate entries with a blank line between them
- One-off (`once`) entries are automatically removed from the file after they fire
- To pause a schedule, set `enabled: false`
- To cancel a schedule, remove the entire entry from the file
- The bot detects file changes automatically after each message
