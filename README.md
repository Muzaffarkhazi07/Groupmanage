# MUZA4R MANAGE

Telegram group-management bot starter for Python 3.11+ and aiogram 3.

## Included
- Welcome messages with photo/video media (`/setwelcome` by replying to media)
- Custom welcome text (`/setwelcometext`)
- Welcome on/off
- Member tracking and admin-only `@all` / `@admin` mentions
- Moderation: ban, unban, kick, mute, unmute, promote, demote, warn, unwarn, warnings
- Pin, unpin, purge, lock, unlock
- Rules, ID and user info
- Owner `/admin` panel and private `/broadcast`

## Setup
1. Create a bot with BotFather and copy its token.
2. Upload these files to GitHub.
3. In Railway, deploy the repository as a worker.
4. Add Railway Variables:
   - `BOT_TOKEN` = your BotFather token (keep it private)
   - `OWNER_ID` = `5839586443` (change if needed)
5. Ensure the bot has admin permissions in your group. Grant only permissions it needs:
   - Delete messages, ban/restrict members, invite users, pin messages, manage chat (as required).
6. In BotFather, disable privacy mode if you want the bot to observe ordinary group messages for member tracking.

## Welcome media
- Send a photo or video in the group.
- Reply to it with `/setwelcome`.
- Optional: `/setwelcometext Welcome {first_name} to {group_name}!`
- Use `/welcomeoff` and `/welcomeon`.

## Important limitations
- Telegram bots cannot fetch a complete list of all existing group members. `@all` only mentions members the bot has observed since it joined / started tracking.
- Mention batches are deliberately small and delayed to reduce spam and rate-limit risk.
- Broadcast only reaches users who have started the bot in private chat. Telegram does not allow unsolicited private messages.
- Bot must be an administrator with appropriate rights for moderation actions.
- SQLite data persists only if Railway storage is configured with a persistent volume mounted at the app's data path. Without a volume, data can reset on redeploy.
- Test commands in a private test group before using in a live group.
