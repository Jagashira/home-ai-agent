# systemd deployment

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

Run the pipeline immediately without waiting for the timer:

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

