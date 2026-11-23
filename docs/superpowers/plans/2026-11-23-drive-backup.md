# Drive Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background Python app for Linux and Windows that pushes new and changed files from user-chosen folders to Google Drive, never deletes anything on Drive, and keeps every version forever.

**Architecture:** A shared core library (config, ignore rules, SQLite state, scanner, settler, Drive client, uploader, rebuild) used by two entry points: a headless background service (watchdog events + periodic scan → settle → persistent queue → uploader) started at login by systemd --user or Task Scheduler, and a PySide6 settings window. They talk only through the config file and the SQLite database.

**Tech Stack:** Python 3.11+, `watchdog`, `google-api-python-client`, `google-auth-oauthlib`, `platformdirs`, `PySide6`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-11-23-drive-backup-design.md`

## Global Constraints

- Never call any Drive delete / trash / revision-delete / un-keep API. Drive only grows.
- OAuth scope exactly `https://www.googleapis.com/auth/drive.file`.
- Drive layout: `Drive Backup/<computer name>/<watched folder drive_name>/<relative path>`.
- Changed file → update the same Drive file ID; mark the new revision `keepForever: true`; at most 200 kept revisions per file, after which new revisions are not kept and the file gets a warning.
- Every uploaded file carries `appProperties`: `rel_path` (POSIX, relative to the watched folder), `folder` (drive_name), `size`, `mtime_ns`.
- Settle: debounce 5 s; max wait 1800 s, then upload a best-effort copy and record a warning.
- Periodic full scan every 2700 s (45 min) and at service start.
- Symlinks are never followed.
- Token file permissions 0600 on Linux.
- Paths from `platformdirs` with app name `drive-backup`.
- Core modules must not import PySide6; tests must not hit the network.

## Review Focus

1. **File changes while it is being uploaded** → the stored record must not claim the new content; the file is re-queued. (Task 7 test `test_changed_during_upload_requeues`)
2. **Service killed mid-queue** → pending items survive restart and are uploaded next run. (Task 3 test `test_queue_survives_reopen`)
3. **Watched folder missing / unmounted (USB, network share)** → no crash, no state wiped, a warning shows, uploads resume when it's back. (Task 4 test `test_missing_folder_returns_none`, Task 10 test `test_missing_folder_warns`)
4. **Names with spaces, unicode, Windows backslash paths** → `rel_path` is always POSIX and round-trips. (Task 4 test `test_rel_paths_are_posix_and_unicode`)
5. **User deletes the backed-up file in Drive's web UI** → next change recreates it instead of failing forever. (Task 7 test `test_recreates_when_remote_missing`)

---

## File Structure

```
pyproject.toml
README.md                         # setup incl. Google Cloud steps
src/drive_backup/
  __init__.py
  paths.py        # platform dirs + file locations
  config.py       # Config / WatchedFolder, load/save, add/remove folder
  ignore.py       # default ignore patterns, is_ignored()
  state.py        # StateDB (SQLite): files, folders, queue, status, warnings
  scanner.py      # scan_folder(): which files differ from state
  settle.py       # Settler: debounce + max-wait
  drive_client.py # DriveClient protocol, errors, GoogleDriveClient
  uploader.py     # Uploader: one file → Drive, layout, revisions, retries
  rebuild.py      # rebuild_from_drive()
  auth.py         # OAuth flow, token load/save
  service.py      # Service: wiring, tick loop, watchdog, config reload
  notify.py       # desktop notifications
  autostart.py    # systemd --user unit / Windows scheduled task
  gui.py          # PySide6 settings window
  cli.py          # entry points
tests/
  fake_drive.py   # in-memory DriveClient for tests
  test_*.py
```

Entry points (pyproject `[project.scripts]`): `drive-backup = drive_backup.cli:gui_main`, `drive-backup-service = drive_backup.cli:service_main`.

---

### Task 1: Project scaffold, paths, config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/drive_backup/__init__.py`, `src/drive_backup/paths.py`, `src/drive_backup/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `paths.config_dir() -> Path`, `paths.data_dir() -> Path` (create on call), `config_file()`, `db_file()`, `token_file()`, `client_secret_file()` (all in config dir except `db_file` and `log_file()` in data dir). All honour env var `DRIVE_BACKUP_HOME` (if set, both dirs = that path) for tests.
  - `@dataclass WatchedFolder(path: str, drive_name: str)`
  - `@dataclass Config(folders: list[WatchedFolder], ignore: list[str], paused: bool, computer_name: str)`
  - `load_config(path: Path) -> Config` (missing file → defaults: no folders, `ignore=[]`, `paused=False`, `computer_name=socket.gethostname()`), `save_config(cfg: Config, path: Path) -> None` (atomic: write temp + `os.replace`)
  - `add_folder(cfg: Config, path: str) -> Config` — resolves to absolute; raises `ValueError` if it is, contains, or is inside an already-watched folder; `drive_name` = folder basename, with ` (2)`, ` (3)`… if taken.
  - `remove_folder(cfg: Config, path: str) -> Config`

- [ ] **Step 1:** `git init`; create `pyproject.toml` (src layout, deps above, `[project.optional-dependencies] dev = ["pytest"]`), `.gitignore` (`.venv/`, `__pycache__/`, `*.egg-info`). Run `python -m venv .venv && .venv/bin/pip install -e '.[dev]'`. Expected: install succeeds.
- [ ] **Step 2: Write failing tests** in `tests/test_config.py`:
  - `test_defaults_when_missing`: `load_config(tmp/"c.json").folders == []` and `computer_name == socket.gethostname()`.
  - `test_roundtrip`: save then load gives equal `Config`.
  - `test_add_folder_disambiguates`: adding `tmp/a/Docs` then `tmp/b/Docs` → drive_names `"Docs"`, `"Docs (2)"`.
  - `test_add_nested_rejected`: adding `tmp/x` then `tmp/x/y` raises `ValueError`; adding `tmp/x/y` then `tmp/x` raises `ValueError`.
- [ ] **Step 3:** Run `.venv/bin/pytest tests/test_config.py -v` → FAIL (import error).
- [ ] **Step 4:** Implement `paths.py` and `config.py` (JSON on disk).
- [ ] **Step 5:** Run tests → PASS.
- [ ] **Step 6:** Commit `feat: scaffold project, paths and config`.

### Task 2: Ignore rules

**Files:** Create `src/drive_backup/ignore.py`; Test `tests/test_ignore.py`

**Interfaces:**
- Produces: `DEFAULT_IGNORES: list[str] = ["~$*", "*.tmp", "*.swp", "*.swx", ".~lock.*", "*.part", "*.crdownload", ".DS_Store", "Thumbs.db", "desktop.ini"]`; `is_ignored(rel_path: str, patterns: list[str]) -> bool` — `rel_path` is POSIX; matches if `fnmatch` of any pattern against the basename **or** any path component (so `node_modules` in user patterns ignores the whole subtree). Always applies `DEFAULT_IGNORES` plus `patterns`.

- [ ] **Step 1: Failing tests:** `is_ignored("a/~$doc.docx", [])` True; `is_ignored("a/report.docx", [])` False; `is_ignored("x/node_modules/y.js", ["node_modules"])` True; `is_ignored("notes.swp", [])` True.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: ignore rules`.

### Task 3: State database

**Files:** Create `src/drive_backup/state.py`; Test `tests/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass FileRecord(folder: str, rel_path: str, size: int, mtime_ns: int, drive_id: str, kept_revisions: int, uploaded_at: float)`
  - `@dataclass QueueItem(id: int, folder: str, rel_path: str, attempts: int, forced: bool)`
  - `class StateDB(path: Path)` — opens SQLite with WAL mode and `check_same_thread=False`, guarded by a `threading.Lock`; creates tables if missing. Methods:
    - `get_file(folder, rel_path) -> FileRecord | None`, `upsert_file(rec: FileRecord) -> None`, `all_files(folder) -> list[FileRecord]`
    - `enqueue(folder, rel_path, forced=False) -> None` (no duplicate rows for the same folder+rel_path; `forced` OR-ed)
    - `next_items(limit: int) -> list[QueueItem]` (oldest first), `remove_item(item_id) -> None`, `bump_attempt(item_id) -> None`, `queue_size() -> int`
    - `get_folder_id(drive_path: str) -> str | None`, `set_folder_id(drive_path: str, folder_id: str) -> None` (drive_path like `"Drive Backup/host/Docs/sub"`)
    - `set_status(key: str, value: str) -> None`, `get_status() -> dict[str, str]`
    - `add_warning(key: str, message: str) -> None`, `clear_warning(key: str) -> None`, `warnings() -> dict[str, str]`
    - `is_empty() -> bool` (no file records)

- [ ] **Step 1: Failing tests:** `test_file_roundtrip`; `test_enqueue_dedupes` (enqueue twice → `queue_size()==1`); `test_queue_survives_reopen` (enqueue, close, new `StateDB` on same path → item present); `test_warnings_set_and_clear`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: sqlite state db`.

### Task 4: Scanner

**Files:** Create `src/drive_backup/scanner.py`; Test `tests/test_scanner.py`

**Interfaces:**
- Consumes: `WatchedFolder`, `StateDB.get_file`, `is_ignored`.
- Produces:
  - `to_rel(folder_path: str, abs_path: str) -> str` (POSIX)
  - `scan_folder(folder: WatchedFolder, db: StateDB, patterns: list[str]) -> list[str] | None` — walks with `os.walk(followlinks=False)`, skips symlinked files and ignored paths, returns rel paths whose `(st_size, st_mtime_ns)` differ from the stored record or have no record. Returns `None` if the folder doesn't exist or isn't a directory. Per-file `OSError` (permission/locked) → skip that file, don't abort.

- [ ] **Step 1: Failing tests:** `test_new_and_changed_detected` (two files, one recorded with matching size/mtime → only the other returned; then modify recorded one → it's returned); `test_ignored_and_symlinks_skipped` (symlink test skipped on Windows); `test_missing_folder_returns_none`; `test_rel_paths_are_posix_and_unicode` (file `sub dir/résumé ✓.txt` → `"sub dir/résumé ✓.txt"`; `to_rel("C:\\x", "C:\\x\\a\\b.txt") == "a/b.txt"` run via `ntpath`-aware logic, test guarded to the current OS separator).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: folder scanner`.

### Task 5: Settler

**Files:** Create `src/drive_backup/settle.py`; Test `tests/test_settle.py`

**Interfaces:**
- Produces: `class Settler(debounce_s: float = 5.0, max_wait_s: float = 1800.0, clock: Callable[[], float] = time.monotonic)` with `touch(key: Hashable) -> None` (records first-seen and last-touched) and `ready() -> list[tuple[Hashable, bool]]` — returns and forgets keys with `now - last >= debounce_s` (`forced=False`) or `now - first >= max_wait_s` (`forced=True`). `pending_count() -> int`.

- [ ] **Step 1: Failing tests** with a fake clock: touched at t=0, `ready()` at t=4 → `[]`, at t=5 → `[(k, False)]`; touched every 3 s from 0 to 1800 → at t=1800 `[(k, True)]`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: settle/debounce`.

### Task 6: Drive client (protocol, errors, Google implementation, fake)

**Files:** Create `src/drive_backup/drive_client.py`, `tests/fake_drive.py`; Test `tests/test_fake_drive.py`

**Interfaces:**
- Produces:
  - `@dataclass UploadResult(file_id: str, revision_id: str)`
  - `@dataclass RemoteFile(file_id: str, parent_id: str, name: str, app_properties: dict[str, str])`
  - Errors: `DriveError`; subclasses `TransientDriveError` (network, 429, 5xx, `rateLimitExceeded`, `userRateLimitExceeded`), `QuotaExceededError` (`storageQuotaExceeded`), `AuthError` (invalid/expired refresh token, 401 after refresh), `RemoteNotFoundError` (404), `RevisionLimitError` (keepForever refused because 200 kept).
  - `class DriveClient(Protocol)`:
    - `find_folder(parent_id: str, name: str) -> str | None` (only non-trashed folders)
    - `create_folder(parent_id: str, name: str) -> str`
    - `create_file(parent_id: str, name: str, local_path: Path, app_properties: dict[str, str]) -> UploadResult`
    - `update_file(file_id: str, local_path: Path, app_properties: dict[str, str]) -> UploadResult`
    - `keep_revision(file_id: str, revision_id: str) -> None`
    - `list_tree(root_id: str) -> Iterator[RemoteFile]` (all non-trashed files below, recursively)
    - `storage_quota() -> tuple[int, int | None]` (used, limit)
  - `class GoogleDriveClient(creds)` implementing it with `googleapiclient.discovery.build("drive", "v3", credentials=creds, cache_discovery=False)`; uploads via `MediaFileUpload(resumable=True, chunksize=8*1024*1024)`; `create_file`/`update_file` request `fields="id,headRevisionId"`; `keep_revision` = `revisions().update(fileId, revisionId, body={"keepForever": True})`. Map `HttpError` reason/status and `OSError`/`TimeoutError`/`google.auth.exceptions.TransportError` to the error classes; `RefreshError` → `AuthError`.
  - `tests/fake_drive.py`: `FakeDrive` implementing the protocol in memory, storing files `{id: {parent, name, props, revisions: [ {id, content, keep} ]}}`; knobs `fail_next: list[Exception]` (raised in order by the next calls), `quota_limit: int | None`, `max_kept = 200` (raises `RevisionLimitError` beyond). Root id `"root"`. Also `delete_remote(file_id)` to simulate the user deleting in the web UI.

- [ ] **Step 1: Failing tests** for `FakeDrive` (it's used by every later test, so pin it): create → update → two revisions with the latest content; `keep_revision` past 200 raises `RevisionLimitError`; `update_file` on a deleted id raises `RemoteNotFoundError`; `list_tree` returns nested files with their props.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement `drive_client.py` and `fake_drive.py`. **Step 4:** Run → PASS.
- [ ] **Step 5:** Add `test_error_mapping` in `tests/test_drive_client.py`: build fake `HttpError`s (status 403 reason `storageQuotaExceeded` → `QuotaExceededError`; 429 → `TransientDriveError`; 404 → `RemoteNotFoundError`; 403 `userRateLimitExceeded` → `TransientDriveError`) through the module's `_map_http_error(e: HttpError) -> DriveError`. Run → PASS.
- [ ] **Step 6:** Commit `feat: drive client and fake`.

### Task 7: Uploader

**Files:** Create `src/drive_backup/uploader.py`; Test `tests/test_uploader.py`

**Interfaces:**
- Consumes: `Config`, `WatchedFolder`, `StateDB`, `DriveClient` + errors.
- Produces:
  - `class Outcome(Enum): UPLOADED, UNCHANGED, VANISHED, CHANGED_DURING_UPLOAD, UNREADABLE`
  - `class Uploader(drive: DriveClient, db: StateDB, sleep: Callable[[float], None] = time.sleep)`
  - `Uploader.upload(cfg: Config, folder: WatchedFolder, rel_path: str, forced: bool = False) -> Outcome`
  - `Uploader.ensure_folder(parts: list[str]) -> str` — resolves `["Drive Backup", host, drive_name, *subdirs]` from `"root"`, using cached ids in `StateDB` first, else `find_folder`, else `create_folder`; caches results.
- Behaviour (in order):
  1. Path is a symlink or missing → `VANISHED`. `stat` before.
  2. Record exists with same size+mtime_ns and has `drive_id` → `UNCHANGED`.
  3. Upload (create if no record, else update). Open errors (`PermissionError`, Windows sharing violation) → `db.add_warning(f"locked:{folder}/{rel}", ...)`, return `UNREADABLE`.
  4. `stat` after. If size/mtime differ from "before" → don't store the record; `db.enqueue(...)`; return `CHANGED_DURING_UPLOAD`.
  5. `keep_revision`; on `RevisionLimitError` → `add_warning(f"revlimit:{folder}/{rel}", "Older versions of <rel> beyond 200 are only kept ~30 days by Google")`, continue; else `kept_revisions += 1`.
  6. Store `FileRecord`; if `forced`, `add_warning(f"unsettled:{folder}/{rel}", "<rel> kept changing; uploaded a best-effort copy")`, else clear that warning and any `locked:` warning.
  - `RemoteNotFoundError` on update → drop `drive_id`, clear cached folder ids for that path, create again.
  - `TransientDriveError` → retry with sleeps `[1, 2, 4, 8, 16]`, then re-raise.
  - `QuotaExceededError`, `AuthError` → propagate unchanged.

- [ ] **Step 1: Failing tests** (all with `FakeDrive`, tmp files, `sleep=lambda s: None`):
  - `test_new_file_uploaded_to_layout`: file `Docs/a/b.txt` → FakeDrive path `Drive Backup/<host>/Docs/a/b.txt`, props `rel_path="a/b.txt"`, `folder="Docs"`, revision kept.
  - `test_change_updates_same_id_and_keeps_revision`: modify → same file id, 2 revisions, both `keep=True`.
  - `test_unchanged_skipped`.
  - `test_changed_during_upload_requeues`: wrap FakeDrive so `create_file` modifies the local file before returning → outcome `CHANGED_DURING_UPLOAD`, `get_file` is None, `queue_size()==1`.
  - `test_recreates_when_remote_missing`: upload, `delete_remote`, modify, upload → `UPLOADED`, new id present.
  - `test_transient_retried_then_succeeds` (two `TransientDriveError` in `fail_next`) and `test_quota_propagates`.
  - `test_revision_limit_warns` (FakeDrive `max_kept=1`, two changes → warning key `revlimit:Docs/b.txt` present, outcome `UPLOADED`).
  - `test_forced_upload_warns`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Grep check: `grep -rnE "delete|trash|keepForever.*False" src/drive_backup` → no Drive deletion calls.
- [ ] **Step 6:** Commit `feat: uploader`.

### Task 8: Rebuild state from Drive

**Files:** Create `src/drive_backup/rebuild.py`; Test `tests/test_rebuild.py`

**Interfaces:**
- Consumes: `Uploader.ensure_folder` cache semantics (via `StateDB.set_folder_id`), `DriveClient.find_folder`, `list_tree`.
- Produces: `rebuild_from_drive(drive: DriveClient, db: StateDB, cfg: Config) -> int` — only when `db.is_empty()`; finds `Drive Backup/<computer_name>` without creating; for each `RemoteFile` with `appProperties.folder` matching a configured `drive_name`, upserts `FileRecord(size, mtime_ns from props, drive_id, kept_revisions=0)`. Returns records restored (0 if no tree). The later scan then uploads only files whose size/mtime differ.

- [ ] **Step 1: Failing tests:** upload two files with an `Uploader` into FakeDrive, open a fresh empty `StateDB`, `rebuild_from_drive` → returns 2; then `scan_folder` returns `[]`; modify one file → scan returns it and `Uploader.upload` updates the **same** file id. Second test: no remote tree → returns 0, creates nothing.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: rebuild state from drive after reinstall`.

### Task 9: Google sign-in

**Files:** Create `src/drive_backup/auth.py`; Test `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `SCOPES = ["https://www.googleapis.com/auth/drive.file"]`
  - `load_credentials(token_path: Path) -> Credentials | None` — loads, refreshes if expired (saves refreshed), returns None if missing; `RefreshError` → raises `AuthError`.
  - `sign_in(client_secret_path: Path, token_path: Path) -> Credentials` — `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0, open_browser=True)`, then `save_token`.
  - `save_token(creds, token_path: Path) -> None` — write then `os.chmod(token_path, 0o600)` on POSIX.
  - `signed_in_email(creds) -> str | None` — via Drive `about().get(fields="user(emailAddress)")`.
  - Missing `client_secret.json` → `FileNotFoundError` with message pointing to the README section "Google Cloud setup".

- [ ] **Step 1: Failing tests:** `test_save_token_permissions` (POSIX only: mode `0o600`) using a `google.oauth2.credentials.Credentials(token="t", refresh_token="r", client_id="c", client_secret="s", token_uri="https://oauth2.googleapis.com/token")`; `test_load_missing_returns_none`; `test_missing_client_secret_message`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: google oauth`.

### Task 10: Background service

**Files:** Create `src/drive_backup/service.py`, `src/drive_backup/notify.py`; Test `tests/test_service.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `notify.notify(title: str, body: str) -> None` — Linux: `notify-send` via `subprocess` if on PATH; Windows: PowerShell toast (`[Windows.UI.Notifications]` one-liner) best-effort; never raises.
  - `class Service(config_path: Path, db: StateDB, drive_factory: Callable[[], DriveClient], clock=time.monotonic, notifier=notify.notify)`:
    - `reload_config_if_changed() -> None` (by file mtime; adding a folder triggers `full_scan` of it; restarts watchers)
    - `full_scan() -> None` — `scan_folder` each folder, `db.enqueue` results; folder `None` → `add_warning(f"missing:{drive_name}", "Folder <path> not found")`, otherwise clear it. Sets status `last_scan`.
    - `on_fs_event(abs_path: str) -> None` — map to folder + rel, skip ignored/dirs, `settler.touch((drive_name, rel))`.
    - `tick() -> None` — move `settler.ready()` into the queue (`forced` flag); unless `cfg.paused` or in backoff, process up to 2 queue items concurrently (`ThreadPoolExecutor(max_workers=2)`) via `Uploader.upload`; remove item on any `Outcome`; on `QuotaExceededError` → status `state="drive_full"`, notify once, back off 600 s; on `AuthError` → status `state="signin_needed"`, notify once, back off until token file mtime changes; on final `TransientDriveError` → `bump_attempt`, back off 60 s, and if uploads have been failing continuously for ≥ 3600 s set status `state="failing"`, `add_warning("failing", ...)` and notify once (cleared on next success); set statuses `state="ok"|"paused"|…`, `queue`, `last_upload`, `quota_used`, `quota_limit` (quota refreshed at most every 10 min).
    - `run_forever() -> None` — `rebuild_from_drive` if DB empty, `full_scan`, start `watchdog.observers.Observer` with a handler calling `on_fs_event` for created/modified/moved (dest path) events; loop: `tick()` every 1 s, `reload_config_if_changed()` every 5 s, `full_scan()` every 2700 s. Observer `OSError` (e.g. inotify limit) → log + `add_warning("watch_limit", ...)`, continue with scans only.
  - Logging: `logging.handlers.RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3)`.

- [ ] **Step 1: Failing tests** (FakeDrive, fake clock, no watchdog thread — call `on_fs_event`/`tick` directly):
  - `test_event_settles_then_uploads`: write file, `on_fs_event`, `tick` at t=1 → nothing on Drive; t=6 → uploaded.
  - `test_paused_queues_but_does_not_upload`.
  - `test_quota_sets_drive_full_and_keeps_queue`.
  - `test_auth_error_sets_signin_needed`.
  - `test_missing_folder_warns`: config folder path deleted → `full_scan` adds `missing:<name>` warning, does not raise, existing `FileRecord`s untouched; recreate folder → warning cleared.
  - `test_startup_scan_catches_offline_changes`: records exist, file modified "while off" → `full_scan` + ticks upload it.
  - `test_local_delete_does_nothing_remote`: upload, delete local file, `full_scan` + tick → remote file still present.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: background service`.

### Task 11: Autostart + CLI entry points

**Files:** Create `src/drive_backup/autostart.py`, `src/drive_backup/cli.py`; Test `tests/test_autostart.py`

**Interfaces:**
- Produces:
  - `autostart.systemd_unit_text(exe: str) -> str` — `[Unit] Description=Drive Backup` / `[Service] ExecStart=<exe> ; Restart=on-failure ; RestartSec=30` / `[Install] WantedBy=default.target`.
  - `autostart.install() -> None` — Linux: write `~/.config/systemd/user/drive-backup.service`, `systemctl --user daemon-reload`, `systemctl --user enable --now drive-backup.service`. Windows: `schtasks /Create /TN "Drive Backup" /TR "\"<pythonw>\" -m drive_backup.cli service" /SC ONLOGON /RL LIMITED /F` and start it.
  - `autostart.uninstall() -> None`, `autostart.is_installed() -> bool`, `autostart.is_running() -> bool`.
  - `cli.service_main()` (builds `Service` with `GoogleDriveClient(load_credentials(token_file()))`, calls `run_forever`), `cli.gui_main()` (launches `gui.main()`), and `python -m drive_backup.cli service|gui|install|uninstall`.
  - Linux: `install` also writes `~/.local/share/applications/drive-backup.desktop` (`Exec=<venv>/bin/drive-backup`, `Name=Drive Backup`) so it shows in the Omarchy launcher.

- [ ] **Step 1: Failing test:** `systemd_unit_text("/x/drive-backup-service")` contains `ExecStart=/x/drive-backup-service` and `WantedBy=default.target`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: autostart and cli`.

### Task 12: Settings window

**Files:** Create `src/drive_backup/gui.py`

**Interfaces:**
- Consumes: `load_config`/`save_config`/`add_folder`/`remove_folder`, `auth.sign_in`/`load_credentials`/`signed_in_email`, `StateDB.get_status`/`warnings`/`queue_size` (DB opened read-mostly), `autostart.*`.
- Produces: `gui.main() -> None`. One `QMainWindow` "Drive Backup":
  - **Google account** row: "Signed in as <email>" or "Not signed in" + `Sign in with Google` button (runs `sign_in` in a `QThread` so the window doesn't freeze).
  - **Folders** list + `Add folder…` (`QFileDialog.getExistingDirectory`) + `Remove`; `ValueError` from `add_folder` → `QMessageBox` with its message. Saves config immediately.
  - **Status** panel refreshed every 2 s by `QTimer`: service running (from `autostart.is_running()`), state (`ok` / `paused` / `Google Drive is full` / `Sign-in needed`), files waiting, last upload time, Drive used / total, warnings list.
  - `Pause uploads` checkbox → `cfg.paused`.
  - `Start automatically at login` checkbox → `autostart.install()` / `uninstall()`.

- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke check: `QT_QPA_PLATFORM=offscreen DRIVE_BACKUP_HOME=$(mktemp -d) .venv/bin/python -c "from drive_backup import gui; import sys; from PySide6.QtWidgets import QApplication; app=QApplication([]); w=gui.MainWindow(); print('ok')"` → prints `ok`.
- [ ] **Step 3:** Run full suite `.venv/bin/pytest -q` → all PASS.
- [ ] **Step 4:** Commit `feat: settings window`.

### Task 13: README and end-to-end check

**Files:** Create `README.md`

- [ ] **Step 1:** Write README: what it does / never deletes; install (Linux: `python -m venv .venv && .venv/bin/pip install -e .`; Windows: `py -m venv .venv` + `.venv\Scripts\pip install -e .`); **Google Cloud setup** step by step (create project → enable Google Drive API → OAuth consent screen, External, add own email, scope `drive.file` → **Publish app / In production** so sign-in doesn't expire in 7 days → Credentials → OAuth client ID → Desktop app → download JSON → save as `client_secret.json` in the config dir shown by `python -m drive_backup.cli paths`); first run (`drive-backup` → sign in → add folders → tick "Start automatically"); where logs live; how to restore (download from Drive web; older versions via "Manage versions").
- [ ] **Step 2:** Add `paths` subcommand to `cli.py` printing config/data dirs.
- [ ] **Step 3 (manual, with owner's Google account):** sign in, add a test folder, create a file → appears in Drive within ~10 s; edit it → Drive "Manage versions" shows 2 kept versions; delete locally → still on Drive; `systemctl --user restart drive-backup` with a pending change → uploaded after restart; reboot/re-login → service running.
- [ ] **Step 4:** Commit `docs: readme and setup guide`.
