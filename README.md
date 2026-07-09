# Whisper Bot Control

A powerful Telegram Whisper Bot with advanced management features.

## Features

- Anonymous whisper messages
- Reply system
- Admin panel
- Dashboard
- REST API
- Enterprise features
- Statistics
- Rate limiting
- Plugin system
- Automated tests

### Public Whispers
Send whispers visible to everyone in the group, with support for `everyone`, `first_one`, `first_three`, and `custom` target modes. Group admins can enable or disable public whispers per group.

### Read Notifications
Get notified when someone reads your whisper. Supports detailed HTML notifications for `first_one` whispers and simple read receipts for other types. Notifications can be toggled globally or per group.

### Auto Delete Settings
Whispers can be set to auto-delete after a configurable number of hours by the sender. Group admins can also set a default `auto_delete_minutes` policy that applies to all whispers created in the group, with the sender's explicit value taking priority.

### Admin Panel
Full administrative interface for user management (ban/unban/search), managing mandatory channels, viewing statistics, broadcasting messages, and configuring per-group settings for public whispers, read notifications, anonymous mode, and auto-delete defaults.

### Statistics
Comprehensive stats including total users, active vs. banned users, total whispers, total reads, new users today, whispers today, per-user breakdowns (sent, received, read, curious, locked), and type distribution.

### Enterprise Features
Extended capabilities including XP and achievements system, invite tracking, activity logs, user reporting, ban system with temp bans and audit trails, favorites, archive, search, whisper self-destruct, stats snapshots, and automated database backups.

## Requirements

- Python 3.13+
- python-telegram-bot
- SQLite

## Installation

```bash
git clone git@github.com:kihjhgjdrrhfd34d-hub/Whisper-Bot-Control.git
cd Whisper-Bot-Control
pip install -r requirements.txt
```

## Configuration

Create a `.env` file:

```
BOT_TOKEN=your_token
ADMIN_ID=your_admin_id
```

## Run

```bash
python main.py
```

## Tests

```bash
pytest
```

## License

MIT
