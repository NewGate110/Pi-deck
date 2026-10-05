# Deploy Pi Deck from GitHub

GitHub stores the dashboard source. The Python server runs on your Pi, not GitHub Pages. Commands below assume Raspberry Pi OS with systemd and Python 3.9 or newer, and a fresh installation. Replace OWNER and REPOSITORY with your public repository details.

## Publish from your PC

Create an empty public repository on GitHub, without adding a README, license or .gitignore. In PowerShell, from this project folder:

```powershell
git init -b main
git add .gitignore .gitattributes README.md app.py features.py management.py onboarding.py git_review.py self_update.py setup.py requirements.txt config.example.json deploy static templates tests
git diff --cached --stat
git diff --cached
git commit -m "Initial Pi Deck dashboard"
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

Review the staged content before committing: the public repository must contain only source and examples, never real tokens, config.json, management-state.json or .env. Generated previews, logs and the virtual environment are excluded. Git may ask you to configure your commit name/email and authenticate to GitHub; do so locally, without putting credentials in the remote URL.

## Install on the Pi

SSH into your Pi as your normal administrator account. Run the commands in order, stopping if any command fails. `/opt/pi-deck` must not already exist; an existing installation should use the update steps instead.

```bash
sudo apt update
sudo apt install -y python3-venv git
sudo git clone https://github.com/OWNER/REPOSITORY.git /opt/pi-deck
sudo chmod 700 /opt/pi-deck
sudo python3 -m venv /opt/pi-deck/.venv
sudo /opt/pi-deck/.venv/bin/python -m pip install -r /opt/pi-deck/requirements.txt
sudo install -d -m 700 -o root -g root /etc/pi-deck
sudo /opt/pi-deck/.venv/bin/python /opt/pi-deck/setup.py --config /etc/pi-deck/config.json
sudo cp /opt/pi-deck/deploy/pi-deck.service /etc/systemd/system/pi-deck.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-deck
sudo systemctl status pi-deck --no-pager
```

Setup asks for a new dashboard password (12+ characters). The username defaults to `admin`; supply `--username YOURNAME` during setup to change it. Do not share the password in chat. The config is created with mode 600 and setup refuses to overwrite it. The server starts with no bots; adding a registration manually does not start or stop the bot.

For errors, inspect `sudo journalctl -u pi-deck -n 80 --no-pager` on the Pi. Do not post logs containing secrets.

## Open on your PC

Use your actual SSH username and Pi address. Port 8081 avoids colliding with the local design demo:

```powershell
ssh -N -L 8081:127.0.0.1:8080 YOURUSER@raspberrypi.local
```

Keep this terminal open and visit http://127.0.0.1:8081. Sign in and choose **Add bot → Add manually** for existing bots, or **From GitHub** to provision new ones. The service binds only to loopback; SSH carries the connection securely. Keep `secure_cookie` false for this HTTP-over-SSH setup; use true only when the browser connects over HTTPS.

## Update the dashboard

After installing a version with **Pi Deck updates**, you can check upstream, review diffs, pull, install requirements, roll back, and restart directly from the dashboard. The source path is fixed to the running application's checkout. The supplied installation uses root's Git credentials and the checkout's configured upstream. Configure those on the Pi first. `manager_service` defaults to `pi-deck.service`; change it in private config only for a custom unit. In-app restart verifies the configured service's MainPID matches this dashboard and requires an empty background job queue.

Code updates leave the current process running. Install any changed requirements, then use **Restart Pi Deck** and type `pi-deck`. A transient systemd timer performs the restart after the response returns. Refresh after a few seconds. Bot services are not restarted. If the updated application fails to start, recover over SSH using the commands below and the recorded original commit; a failed application cannot repair itself through its web UI.

Private config/state must be outside the source checkout for in-app updates. Each modifying operation saves a private snapshot under `/etc/pi-deck/update-backups/` (or alongside your active config). These backups are retained until you remove them manually. Dependency rollback and automatic service-template installation are not included. The manual procedure below remains available for initial upgrades or recovery.

Push reviewed source changes from the PC to GitHub. On the Pi, wait for dashboard jobs to finish. Stop the dashboard before backing up its private state; this pauses its schedules but leaves bot services running. Use a new backup directory each time:

```bash
sudo systemctl stop pi-deck
backup_dir="/root/pi-deck-backup-$(date +%Y%m%d-%H%M%S)"
sudo cp -a /etc/pi-deck "$backup_dir"
sudo git -C /opt/pi-deck rev-parse HEAD
sudo git -C /opt/pi-deck pull --ff-only
sudo /opt/pi-deck/.venv/bin/python -m pip install -r /opt/pi-deck/requirements.txt
sudo sh -c 'cd /opt/pi-deck && .venv/bin/python -m unittest discover -s tests -v'
sudo systemctl start pi-deck
sudo systemctl status pi-deck --no-pager
```

Save the commit printed before pulling for recovery. Stop on failures; do not start unverified code. If the service template changed, review it and copy it again followed by `sudo systemctl daemon-reload` before starting. Do not rerun setup.py during updates. Configuration and state stay outside the checkout.

`pull --ff-only` refuses divergent history instead of creating a merge; see the [Git documentation](https://git-scm.com/docs/git-pull). Resolve changes deliberately rather than force-resetting the Pi checkout.

To recover a failed code update, stop Pi Deck and check out the saved commit using `sudo git -C /opt/pi-deck switch --detach SAVED_COMMIT`, reinstall that version's requirements and start the service. This does not undo dependency or state-format changes; consult release notes and the private backup if required. Return to your deployment branch before the next normal pull. Keep backups root-only: they contain bot secrets.

## Configuration fields

| Field | Purpose |
| --- | --- |
| `username` | Dashboard login name; default admin |
| `password_hash` | Generated password hash, never the raw password |
| `secret_key` | Generated session-signing secret |
| `session_minutes` | Session lifetime, default 60 |
| `secure_cookie` | true for HTTPS, false for the SSH tunnel above |
| `bots` | Registrations: id, name, description, service, repo, env, user |

Change passwords/session settings in **Account**, and bot registrations in **Settings & maintenance**. For manual config edits, stop Pi Deck first, edit with `sudo nano /etc/pi-deck/config.json`, then start it. Keep valid JSON and preserve root ownership and mode 600. Do not hand-edit management-state.json while running.

The dashboard runs as root to manage systemd and protected files. Only trusted administrators should have dashboard access or permission to push code that you deploy. This guide has been checked locally; actual Pi service startup, hardware readings and bot operations still require on-device verification.
