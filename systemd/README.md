# Home Mail Agent: systemd deployment and operations

## Architecture

The daily one-shot pipeline is deterministic around external side effects:

```text
Gmail fetch -> cached/DeepSeek classification -> Gmail AI labels
            -> actionable HA To-do + individual notification
            -> terminal digest + optional once-daily iPhone digest
```

DeepSeek only classifies mail. Python policy decides labels and Home Assistant
actions. The agent never sends, deletes, archives, or marks Gmail messages read.

The application reads these names from the repository `.env` file; never put
their values in systemd units or Git:

```text
DEEPSEEK_API_KEY
HOME_ASSISTANT_URL
HOME_ASSISTANT_TOKEN
HOME_ASSISTANT_NOTIFY_SERVICE
HOME_ASSISTANT_TODO_ENTITY
```

`.env`, `secrets/`, and `data/mail_agent.db` must remain outside Git.

## Manual operation

Preview classification, labels, HA actions, and the mobile digest without
Gmail or Home Assistant mutations:

```bash
.venv/bin/python -m app.mail.daily_run --hours 24 --dry-run --notify-digest
```

Run the production pipeline without a morning digest:

```bash
.venv/bin/python -m app.mail.daily_run --hours 24
```

Run it with the once-per-Tokyo-date morning digest:

```bash
.venv/bin/python -m app.mail.daily_run --hours 24 --notify-digest
```

## Scheduled operation

This deployment runs the Home Mail Agent pipeline as the general user who
installs it. The service is a one-shot job; the timer starts it every day at
08:00 according to the server's local timezone. `Persistent=true` allows a
missed run to start after the server comes back online.

Before installation, verify the server timezone:

```bash
timedatectl
```

If necessary, set it to Asia/Tokyo separately. The installer does not change
the timezone:

```bash
sudo timedatectl set-timezone Asia/Tokyo
```

Install from the repository as the general user that should run the pipeline:

```bash
./scripts/install_mail_systemd.sh
```

The installer checks the local virtual environment, `.env`, Gmail OAuth
credentials and four tokens, and the SQLite database. Secret values are not
copied into the systemd units.

The installed service includes `--notify-digest`. Run it immediately without
waiting for the timer:

```bash
sudo systemctl start home-ai-mail.service
```

Check the service, logs, and next scheduled run:

```bash
systemctl status home-ai-mail.service
journalctl -u home-ai-mail.service -n 100 --no-pager
systemctl list-timers home-ai-mail.timer
```

Uninstall only the systemd units and timer registration:

```bash
./scripts/uninstall_mail_systemd.sh
```

Uninstallation preserves the repository, `.env`, OAuth credentials and
tokens, SQLite database, and Gmail labels.

## Backup and rollback

Before upgrades, stop the timer and make a filesystem copy of
`data/mail_agent.db` while no pipeline run is active. Keep `.env` and
`secrets/` in a separate secure backup; do not commit them.

To roll back application code, stop the timer, check out the previously known
good revision, reinstall the units from that revision, run a dry-run, then
start the timer again. The schema additions use `CREATE TABLE IF NOT EXISTS`
and do not remove or rewrite existing classification/action records.

```bash
sudo systemctl stop home-ai-mail.timer
git checkout <known-good-revision>
./scripts/install_mail_systemd.sh
.venv/bin/python -m app.mail.daily_run --hours 24 --dry-run
```
