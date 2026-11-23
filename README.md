# Drive Backup

Pick folders once and sign in with Google once. After that, every file that is **added or changed**
in those folders is pushed to your Google Drive automatically, in the background, from login
onwards. If your computer crashes or the disk dies, your files are already safe on Drive.

- Works on **Linux** (Omarchy / Hyprland and other distros) and **Windows**.
- **Never deletes anything on Drive.** Deleting or renaming a file on your computer leaves the
  Drive copy where it is.
- **Keeps every version.** When a file changes, the old version is kept forever on Drive
  (Google allows 200 kept versions per file; after that, newer old versions are kept ~30 days and
  the app shows a warning).
- Changes made while offline or while the computer was off are uploaded later.
- After a reinstall or a new disk, it reconnects to your existing backup instead of uploading
  everything again (keep the same computer name).

On Drive, files are stored as `Drive Backup/<computer name>/<folder name>/...`.

## 1. Install

You need Python 3.11 or newer.

**Linux**

```bash
cd ~/Documents/Projects/back-up
python -m venv .venv
.venv/bin/pip install -e .
```

**Windows** (PowerShell)

```powershell
cd path\to\back-up
py -m venv .venv
.venv\Scripts\pip install -e .
```

## 2. Google Cloud setup (one time)

Google requires every app that uses Drive to have its own "OAuth client". Since this app is only
for you, you create a free personal one. It takes about 5 minutes.

1. Go to <https://console.cloud.google.com/> and sign in with the Google account whose Drive you
   want to use.
2. Top bar → project picker → **New project** → name it `Drive Backup` → **Create**, then select it.
3. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
4. **APIs & Services → OAuth consent screen** (may be called **Google Auth Platform → Branding /
   Audience**):
   - User type: **External**.
   - App name `Drive Backup`, your email as support and developer contact. Save.
   - Under **Data access / Scopes**, add `.../auth/drive.file` (lets the app see only files it
     created itself).
   - Under **Audience**, click **Publish app** so the status is **In production**.
     *Important:* if you leave it in "Testing", Google expires your sign-in every 7 days and
     backups stop. Because `drive.file` is a non-sensitive scope, no Google review is needed.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   Application type **Desktop app** → **Create** → **Download JSON**.
6. Find out where to put it:

   ```bash
   .venv/bin/python -m drive_backup.cli paths
   ```

   (Windows: `.venv\Scripts\python -m drive_backup.cli paths`)

   Save the downloaded file there as **`client_secret.json`**.

## 3. First run

```bash
.venv/bin/python -m drive_backup.cli gui
```

(Windows: `.venv\Scripts\pythonw -m drive_backup.cli gui`)

1. Click **Sign in with Google** and approve access in the browser. Google may show
   "Google hasn't verified this app": that's your own app, so click **Advanced → Go to Drive Backup**.
2. Click **Add folder…** for each folder you want protected.
3. Tick **Start automatically at login**. This starts the background service now and at every
   login (Linux: a `systemd --user` service, plus a "Drive Backup" entry in your app launcher;
   Windows: a Task Scheduler task).

That's it. You can close the window; the background service keeps running.

## Using it

- Open **Drive Backup** from the app launcher any time to add/remove folders, pause uploads, or
  check status (files waiting, last upload, Drive storage used, warnings).
- **Pause uploads** stops uploading; changes keep being collected and upload when you un-pause.
- Temporary and lock files (`~$*`, `*.tmp`, `*.swp`, `*.part`, …) are skipped. To skip more,
  add patterns to `"ignore"` in `config.json` (e.g. `"node_modules"`, `"*.iso"`).
- Files that never stop changing (open databases, virtual machine disks) are uploaded as-is after
  30 minutes and flagged with a warning.

## Restoring files

Open <https://drive.google.com>, go to `Drive Backup/<computer name>/...` and download what you
need. For an older version: right-click the file → **File information → Manage versions**.

## Logs and troubleshooting

- Logs: `drive-backup.log` in the data folder shown by `python -m drive_backup.cli paths`.
- Linux service status: `systemctl --user status drive-backup`, live log:
  `journalctl --user -u drive-backup -f`.
- Remove autostart: untick it in the window, or `python -m drive_backup.cli uninstall`.
- "Sign-in needed": open the window and click **Sign in again**.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Design: `docs/superpowers/specs/2026-11-23-drive-backup-design.md`.
