# Pi Deck

A small web control room for existing Telegram bots on a Raspberry Pi 3. Python + Flask + Waitress, with no Node runtime or frontend build needed on the Pi.

Features: password login, service status, start/stop/restart, independent startup toggles, environment and service file editors, fast-forward Git pulls, and recent journal logs. Demo data is explicitly labelled and never touches real services.

## Add bots from the dashboard

Choose **+ Add bot** and select one of two options:

- **From GitHub:** enter a bot ID, GitHub URL, existing non-root Linux user, new checkout directory, launch command, and environment values. The checkout must be inside that user's home directory, with its parent already present. Optionally create a Python virtual environment and install `requirements.txt`. The wizard creates `.env` with private permissions, excludes `.env` and `.venv` from Git's untracked-file list, validates and creates the system service, and registers the bot. It stays stopped with startup disabled; use the dashboard controls when ready.
- **Add manually:** enter an existing system service name, Git checkout, environment file, and Linux owner. The wizard verifies they exist and registers the bot without changing files or running state.

Both paths save to the active configuration and appear immediately, without restarting the dashboard. Duplicate IDs, services, and checkout paths are rejected. If configuration was edited externally, restart the dashboard before adding another bot. Demo additions exist only in memory.

Manual setup reads the existing service's effective `Description`, `User`, `WorkingDirectory` and `EnvironmentFiles` when you enter its name or click **Read service settings**. **Discover bots → Import manually** loads these automatically. Review and edit the detected fields before adding. If multiple environment files are configured, choose which file Pi Deck should manage; missing or unsupported settings stay blank with an explanation. Environment contents are never read by detection. Demo lookup uses the sample `weather-bot.service`.

GitHub setup supports HTTPS and `git@github.com:owner/repo` URLs; private repositories require Git credentials already configured for the Linux owner. Python setup requires `python3-venv` on the Pi. Dependencies run as the bot owner and can execute repository/package code, so use repositories you trust. Other runtimes must already be installed; uncheck Python setup and enter the appropriate absolute launch command. Shell operators are not supported in the launch command.

If setup fails after cloning starts, the new checkout is kept for inspection and the bot is not registered. Any newly created service is removed. Finish setup on the Pi and register manually, or retry with a different new directory. Existing directories, environment files, and services are never overwritten. Repositories that contain a committed `.env` must be configured manually. Live provisioning needs verification on your Pi; the demo performs no actual installation.

## Preview on this PC

Environment files are optional. For manual setup, leave the environment path blank if the bot does not use a file. For GitHub setup, uncheck **Create an environment file**. Pi Deck then omits `EnvironmentFile` from the generated service, skips environment backups, and shows **No environment file** on the bot card. Set a path later in registration settings to manage an existing file; service runtime settings remain separately editable.

Choose a bot icon from the list when adding a bot, or change it later in **Settings & maintenance**. Icons are stored with the registration; older registrations default to the robot icon.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py --demo
```

Open http://127.0.0.1:8080 and select **Enter demo workspace**. Demo changes live in memory and reset on restart. Do not use `--demo` for a real installation.

## Install on your Pi

Follow [the GitHub deployment guide](deploy/GITHUB.md) for a fresh installation, updates, and recovery. It installs application code in `/opt/pi-deck` and keeps private configuration in `/etc/pi-deck`.

The main file is `/etc/pi-deck/config.json`: login username, password hash, session secret, session duration, secure-cookie setting and registered bots. Generate it with `setup.py --config /etc/pi-deck/config.json`; it prompts for your password and starts with an empty bot list. Add existing bots through **Add bot → Add manually** after signing in. `config.example.json` only documents the format; never put real credentials in it.

`/etc/pi-deck/management-state.json` is created automatically beside the active configuration and stores backups, schedules, jobs and alert settings. Neither private file belongs on GitHub.

If you already installed using `/opt/pi-deck/config.json`, stop Pi Deck and move that file and its sibling management-state.json into a root-owned `/etc/pi-deck` directory (mode 700) before installing the updated service. Preserve file permissions (600) and keep a private backup. Do not regenerate existing credentials. Custom service installations can retain their existing `--config` path instead.

The manager runs as root because it edits system services and controls systemd. Treat its login as full administrator access to the Pi. Keep its application files and configuration root-owned. Only bots listed in the local configuration are exposed. Git runs as the configured non-root bot owner, using that owner's existing Git/SSH credentials. Set up GitHub access and the branch upstream in each checkout before using Pull latest; interactive credential prompts are disabled.

## Update Pi Deck itself

Open **Pi Deck updates** for update checks, diff previews, normal pulls, confirmed force updates, requirements installation, rollback and return-to-branch controls. Updates target the running dashboard's source checkout and its configured Git upstream. Normal pulls require a clean checkout; force updates use the same reviewed-commit checks and recovery refs as bot updates.

The supplied root service performs these operations as root, including dependency installation. Private config and management state must live outside the checkout and are backed up before code changes. Application updates do not change bot services. After installing any changed requirements, choose **Restart Pi Deck** and type `pi-deck`. Restart is refused while jobs are queued or running. Reload the browser after reconnecting. Demo actions never update or restart the actual application.

The optional private config key `manager_service` identifies the dashboard's unit (default `pi-deck.service`). The service's MainPID is checked before restart. See the [deployment and recovery guide](deploy/GITHUB.md) for SSH recovery if updated code cannot start, and for private backup locations. Code rollback does not roll back dependencies or private state.

## Connect from your PC

The supplied service listens only on the Pi's loopback interface. Use an encrypted SSH tunnel from your PC (replace the username and address):

```powershell
ssh -N -L 8080:127.0.0.1:8080 pi@raspberrypi.local
```

Leave that terminal open, then visit **http://127.0.0.1:8080** in your PC browser and sign in. If your local demo is still running, stop it first or use local port `8081` in the tunnel and browser.

For direct LAN access without an SSH tunnel, place a trusted HTTPS reverse proxy on the Pi in front of the loopback server, set `"secure_cookie": true` in config.json, and restart Pi Deck. Do not expose the administration port directly to the internet. Reverse proxy setup is not included.

## Behaviour and limitations

- **Stop** stops the running process; **Start on boot** independently controls whether systemd enables the service.
- Environment edits preserve raw file syntax, including comments and quoting. Values start hidden in the editor. Revealing is a visual privacy feature; authenticated admins can retrieve the file contents.
- Saving does not restart a bot. Service saves reload systemd; use Restart when ready. Edits are atomic, and stale revisions are rejected. The latest 20 versions per file are kept automatically; keep external backups as well.
- Pull latest runs `git pull --ff-only` on the current branch and configured upstream. It refuses dirty repositories. It does not install dependencies or restart automatically. Periodic checks can be configured in Schedules.
- Service actions, file saves, and pulls are logged without file contents. Journal output itself can contain secrets if bots print them.
- Session cookies are HttpOnly and SameSite Strict. Writes require a session CSRF token. Sessions expire after the configured duration (one hour by default, without extending through background polling); login failures are rate-limited within this process.
- This is a single-administrator tool. Live systemd/Git integration still needs verification on your actual Pi. The Windows demo cannot verify those operations.

## Checks

```bash
python -m unittest discover -s tests -v
```

Security/session behaviour follows [Flask's security guidance](https://flask.palletsprojects.com/en/stable/web-security/). Service control follows [systemctl semantics](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html).

## Management features

- **Pi health:** CPU and memory from Linux `/proc`, root filesystem disk usage, thermal sensor temperature, uptime and load. Readings refresh every five seconds while the page is visible; the first CPU sample is unavailable until the next read. Unsupported sensors show a dash. Demo readings are explicitly marked.
- **Jobs:** setup, service actions, maintenance, removal and bulk operations use a single bounded background queue, limiting load on a Pi 3. The browser may be closed while jobs run. Recent results persist on a real installation. Interrupted jobs are marked interrupted after a manager restart and are never silently replayed. A failed bulk operation lists individual bot outcomes.
- **Live logs:** a rolling window of 200 journal entries polls every four seconds while the dialog and browser are visible. Pause with Live, search messages, filter journal severity or download matching entries. Timestamps are UTC. Severity is journald metadata, so plain stdout text containing ERROR may still have an info priority. This is a recent window, not an unlimited historical log search.
- **Settings and backups:** Settings & maintenance edits registration metadata and paths; the service editor changes the launch command and runtime settings. Settings changes preserve an old registration snapshot. File edits create private backups and support current/proposed previews. Restore opens a backup for review; Save applies it with the usual revision check and creates another backup. File backups tied to an old path cannot be restored to a different path through the UI. Latest 20 copies per bot/file are retained.
- **Updates:** Check updates fetches remote refs, showing current commit, branch and ahead/behind counts. Pull latest uses fast-forward only. Update and restart additionally restarts the service; it does not automatically install dependencies. Install requirements runs the checkout's existing `.venv/bin/python -m pip install -r requirements.txt` as the bot owner. Review job failures and logs after updating.
- **Rollback:** the last pre-update commit and branch are recorded. Roll back checks out that commit in detached mode, without forcing through local changes. Return to branch restores the recorded branch. Restart separately after either operation. Dependencies, external data and database migrations are not rolled back.
- **Discovery/removal:** discovery lists unregistered regular service files in `/etc/systemd/system`; verify they are bots before importing. Unregister only removes dashboard registration. Optional uninstall backs up the files, stops and disables the service, and removes its regular unit file. Repository and environment files are deliberately retained. Backups for an unregistered bot remain in the private state file for manual recovery. No recursive bot-data deletion is implemented.
- **Schedules:** persistent interval schedules for restarts, configuration backups and update checks, from 5 minutes to 7 days. The CLI starts a scheduler that checks every 60 seconds. The manager must remain running; after downtime, one missed run is queued, never a burst. Overlapping runs are skipped. Disable/delete prevents future scheduling but does not cancel an already queued job. Times in the UI use the viewing device's timezone.
- **Telegram alerts:** off by default. Configure a separate notification bot token and numeric chat ID to enable crash/unknown-state, high-temperature, disk-usage and recovery alerts. Fixed 15-minute cooldown; failures are shown in Alerts. No message is sent when saving settings. Messages contain bot IDs and alert types, never file contents or logs. Demo mode simulates sends without network access.
- **Account:** password changes, configurable 5–1440 minute session expiry and revocation of all sessions. A real account change requires the current password and forces re-login. Account username remains configured locally.
- **Power:** explicitly type REBOOT or POWEROFF before submitting. Demo mode only simulates the action. Health polling retries after disconnect; shutting down requires manually powering the Pi on again.

### Private storage and operation

### Review local code before a force update

Open **Settings & maintenance → Review changes / force update**. The preview fetches the configured upstream and shows staged edits, unstaged edits, local commits, and incoming changes with colored diff lines and line numbers. The normal Pull latest button still refuses dirty checkouts.

After reviewing, type the bot ID and choose **Back up and force update**. This queues a replacement of tracked code with the exact reviewed upstream commit (`git reset --hard`), including replacing local commits. A changed checkout or upstream invalidates the preview. Avoid editing the checkout while the job runs. Dependencies are not installed and the service is not restarted.

Before replacement, Pi Deck records a local recovery ref under `refs/pi-deck/recovery/`. It preserves the original commit and, when present, staged and unstaged tracked edits. The ref is shown in Jobs and on the next review. On the Pi, recover the original commit first, then use the displayed `git stash apply --index REF` command for saved edits. Recovery refs remain in the checkout until manually removed; they are not pushed to GitHub. Keep external backups too.

Untracked and ignored files are retained. Force update is blocked for conflicting untracked paths, tracked environment files, submodules, unfinished Git operations, hidden index changes, binary diffs, or previews exceeding 4,000 lines / 250,000 characters. Those cases require review on the Pi. Diffs may contain sensitive code or configuration and are only available to the signed-in administrator. Demo previews and force updates are simulations.

See the Git documentation for [reset](https://git-scm.com/docs/git-reset) and [stash recovery snapshots](https://git-scm.com/docs/git-stash).

### Private state

Background jobs include a live step history with timestamps, command durations, and failure exit codes. Open Jobs to follow setup, Git operations, dependency installation, service actions and backups. The latest 200 steps per job are retained with private management state; older jobs created before this feature have no step history. Command descriptions are recorded without raw arguments, environment values or stdout/stderr. Demo steps are labelled as simulations.

`management-state.json` is created beside the active configuration, atomically written with mode 0600 on Linux, and excluded from Git. It contains backups (which may include secrets), alert credentials, recent jobs/activity, version metadata and schedules. Protect it as carefully as `.env` and config.json; do not upload it or commit it. Demo state stays in memory. Run only one Pi Deck process per configuration/state directory. No external cloud service is used for management state.

Configuration changes are rejected if config.json was edited externally after startup. Restart the manager to pick up external edits. On Linux command timeouts kill the command process group, including subprocesses launched through runuser. Failed installations can leave partial dependencies or checkouts; inspect those before retrying.

### Validation status

Local automated tests cover authentication/CSRF, session revocation, backup retention and restore, settings conflicts, job failures and interruption, schedule dispatch, secret masking, bulk results, demo isolation, and a real temporary Git repository update/rollback/return-to-branch cycle. Browser checks cover the management dashboard, settings, jobs, log filters and responsive layout.

Real Raspberry Pi telemetry, systemd edits/discovery/uninstall, package installation on ARM, Telegram delivery and device reboot/shutdown require on-device validation. None are performed by the local demo or test suite. No real Pi has been connected or changed during development.

Journal filtering follows [journalctl documentation](https://www.freedesktop.org/software/systemd/man/255/journalctl.html); code rollback uses [git switch --detach](https://git-scm.com/docs/git-switch).
