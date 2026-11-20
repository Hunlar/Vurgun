KVP GAME - HEROKU DEPLOYMENT

Included files:
- bot.py
- languages.py
- requirements.txt
- Procfile

HEROKU CONFIG VARS
Required:
- BOT_TOKEN = Telegram bot token

Subscription channel (keep your existing values if already configured):
- REQUIRED_CHANNEL = @sancakmain
- CHANNEL_JOIN_URL = https://t.me/sancakmain

Optional:
- OWNER_IDS = Telegram user IDs separated by commas
- SUPPORT_URL = support link
- DB_PATH = mafia.db

DEPLOY
1. Replace/add these files in the root of your GitHub repository.
2. Deploy that repository to Heroku.
3. In Heroku, scale the worker process to 1 (worker=1).
4. The bot does not require a web dyno.

IMPORTANT
- The bot must be an administrator in @sancakmain for Telegram to reliably check other users' channel membership.
- The database currently uses SQLite. Heroku dyno storage is ephemeral, so statistics can be lost after a dyno restart/redeploy. Persistent statistics require a PostgreSQL migration; this package does not claim to provide that migration.
- bot.py and languages.py must be deployed together. The updated bot imports get_achievement_text and uses formatted get_text(..., **values) calls from languages.py.
