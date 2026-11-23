# Drive Backup — Design

Date: 2026-11-23
Status: draft, awaiting approval

## Intent

A personal safety net. The user picks a few important folders once and signs in
to Google Drive once. From then on, any file that is **added or changed** in those
folders is pushed to Google Drive automatically, so that if the computer crashes,
the disk dies, or the machine is lost, the important files are already safe in
the cloud. No commands, no manual syncing.

It must work on **Linux** (Omarchy / Hyprland primarily, other distros too) and
**Windows**.

## Who it's for

Only the owner (single user, their own machines). Not distributed publicly.

## Goals

- Watch user-selected folders (recursively) and upload new/changed files to Google Drive.
- Run in the background with no user action, starting automatically at login.
- Survive offline periods: pending changes are queued and uploaded when the
  network returns.
- Catch changes made while the app wasn't running (on start, compare folders
  against what's already uploaded).
- Keep older versions of changed files (via Google Drive's built-in revision history).
- Simple setup: one window to pick folders and click "Sign in with Google".

## Non-goals

- Two-way sync. Nothing is ever downloaded from Drive to the computer.
- Restoring files through the app. Restore = open Google Drive in a browser and
  download. (May be added later.)
- Public distribution, Google app verification, polished installers.
- macOS.
- Encryption of files before upload.

## Anti-goals (would make it a failure even if it "works")

- **Deleting anything on Drive.** Local deletes, renames' old names, and
  folder removals must never remove data on Drive. Drive only grows.
- Silently failing. If uploads have been failing for a long time, or sign-in has
  expired, the user must be able to see it.
- Uploading half-written files as if they were final, then never uploading the
  finished version.
- Hogging CPU/disk/battery in the background.

## Decisions made with the user

- Destination: **Google Drive**.
- Behaviour on local delete: **keep the file on Drive**.
- Changed files: **keep every older version forever** (Drive revisions marked
  `keepForever`), accepting that this uses Drive storage over time.
- Audience: **just the owner** → personal Google Cloud project, OAuth app set to
  "In production" (not "Testing") so the sign-in doesn't expire every 7 days.
- Approach: **pure Python, talking to the Google Drive API directly** (no rclone).

## Approaches considered

1. **Python app + rclone for uploads** — least custom code, rclone is battle-tested;
   cost: external binary dependency. *Not chosen.*
2. **Pure Python using the Google Drive API directly** — no outside tool, full
   control and understanding of every part; cost: more code for retries,
   resumable uploads, and state tracking. **Chosen by the user.**
3. **Single compiled binary (Go/Rust)** — no Python runtime needed; cost: far more
   work, slower to change. Overkill for a personal tool. *Not chosen.*

## How it works

### Components

1. **Settings window** (`drive-backup` / "Drive Backup" in the app launcher)
   - Add/remove watched folders (native folder picker).
   - "Sign in with Google" button / shows signed-in account.
   - Status: running or not, last successful upload time, number of files
     waiting, most recent errors.
   - Changes are saved to the config file; the background service picks them up
     without a restart.
2. **Background service** (headless, no window)
   - Started at login by the OS. Does all watching and uploading.
3. **Shared core library** used by both.

### Change detection

- **Live:** OS file-system events (`watchdog` library: inotify on Linux,
  ReadDirectoryChangesW on Windows) on every watched folder, recursively.
- **Safety-net scan:** a full scan of all watched folders at service start and
  periodically (builder's call, ~every 30–60 min) comparing each file's size and
  modification time against the local state database. This catches anything the
  live watcher missed (events dropped, changes while the service was off,
  watcher limits hit).
- **Settling:** a file is only uploaded after it has stopped changing for a few
  seconds (debounce, builder picks the value, ~5–10 s). If it's still changing,
  wait again.
- **Ignored:** obvious temp/lock files (e.g. `~$*`, `*.tmp`, `*.swp`, `.~lock.*`,
  `*.part`, `*.crdownload`), plus a user-editable ignore list in the config.
  Symlinks are not followed.

### Upload

- Layout on Drive: `Drive Backup/<computer name>/<watched folder name>/...`,
  mirroring the local folder structure. If two watched folders share a name, the
  builder disambiguates (e.g. append a short suffix).
- New file → create on Drive. Changed file → **update the existing Drive file**
  (same file ID) so Drive keeps the old content as a revision. **Every revision
  is marked `keepForever`** (owner's decision: keep all versions forever). The app
  never un-keeps or deletes revisions.
  - Drive allows at most 200 kept revisions per file. Once a file reaches that,
    new revisions are uploaded without `keepForever` (Drive keeps them ~30 days)
    and the file is listed as a warning in the status panel. *(My call.)*
  - Drive full (`storageQuotaExceeded`): uploads stop, the queue is kept, the
    status panel shows "Google Drive is full" and a desktop notification is sent.
    Uploads resume automatically when space is available. *(My call.)*
  - The status panel shows Drive storage used / total.
- Local rename/move → the file at the new path is uploaded as a new file; the
  old Drive copy stays (consistent with "never delete").
- Local delete → nothing happens on Drive.
- Large files use resumable uploads.
- Retries with exponential backoff on network errors and Drive rate limits
  (HTTP 429 / 5xx / `userRateLimitExceeded`).
- Uploads run with limited concurrency (builder's call, small number) to stay
  light on the machine and within Drive limits.

### Local state

- A small SQLite database in the app's data directory records, per local file:
  path, size, mtime, (optionally a content hash), Drive file ID, last uploaded
  time, and pending/failed status. Also Drive folder IDs per local folder.
- The upload queue is persisted, so a crash or reboot doesn't lose pending work.

### Google sign-in

- OAuth "installed app" flow (opens the browser, local loopback redirect),
  via `google-auth-oauthlib`.
- Scope: `https://www.googleapis.com/auth/drive.file` (the app can only see
  files it created). Non-sensitive scope → no Google verification needed.
- The owner creates a Google Cloud project once and drops the downloaded
  `client_secret.json` into the config directory. The README must walk through
  this step by step (enable Drive API, OAuth consent screen "External" +
  publish to "In production", create "Desktop app" credentials).
- The refresh token is stored in a file in the config directory, readable only
  by the user (chmod 600 on Linux).
- If the token stops working, the service pauses uploads, keeps queueing, and
  the settings window shows "Sign-in needed".

### Starting automatically

- **Linux:** a `systemd --user` service (`drive-backup.service`), enabled at
  login, restarting on failure. Installed/enabled by an install script or from
  the settings window.
- **Windows:** a Task Scheduler task "at logon" running the service with
  `pythonw` (no console window). Installed by an install script or from the
  settings window.

### Visibility

- Log file in the app's data directory (rotating).
- Settings window status panel (above).
- Desktop notification when sign-in is needed or uploads have been failing for a
  long time (Linux: `notify-send`; Windows: toast). Builder may keep this minimal.

## Constraints

- Python 3.11+ (owner's machine has 3.14).
- Settings window toolkit: **PySide6 (Qt)** — my call: Tk isn't installed on the
  owner's Omarchy machine and Qt works natively on Wayland and Windows. Installed
  via pip into a virtualenv.
- Config/data paths via `platformdirs` (Linux: `~/.config/drive-backup`,
  `~/.local/share/drive-backup`; Windows: `%APPDATA%` / `%LOCALAPPDATA%`).
- Linux inotify watch limits: if a large tree exceeds them, the app must log it
  and rely on the periodic scan rather than crash.

## Calls I made (owner can override)

- PySide6 settings window instead of a system-tray icon (tray support on
  Hyprland depends on the bar; a window from the app launcher is reliable).
- Drive layout `Drive Backup/<computer name>/<folder>/...` so a second computer
  later doesn't collide.
- Token stored as a protected file rather than in the OS keyring (Omarchy may not
  run a keyring service).
- Change detection by size + mtime; content hash optional.

- **Reconnect after reinstall / dead disk:** every uploaded file stores its
  relative path, size and mtime in Drive `appProperties`. On first start with an
  empty local database, if `Drive Backup/<computer name>/` already exists, the app
  rebuilds its database from Drive instead of re-uploading everything. This
  must be in the data model from day one.
- **Files that never settle or are locked** (SQLite DBs, VM disks, Windows-locked
  files): retry on the normal debounce; if a file still hasn't settled after a
  maximum wait (~30 min), upload a best-effort copy anyway (some copy beats none)
  and show it as a warning in the status panel. Locked files that can't be read at
  all are listed as warnings and retried on each scan.
- **Bandwidth:** no rate limit by default. The settings window has a
  "Pause uploads" toggle (queueing continues while paused).

## Left to the builder

- Project structure, module boundaries, packaging (`pyproject.toml`, venv).
- Exact debounce, scan interval, concurrency, retry limits.
- Database schema details.
- How the settings window and service communicate (shared config/DB file is
  enough; IPC optional).
- Test strategy (core logic must be testable without real Google Drive, e.g. a
  fake Drive client).
