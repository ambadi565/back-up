"""Entry points: settings window, background service, install/uninstall autostart."""
from __future__ import annotations

import sys

from . import paths


def _drive_factory():
    from .auth import load_credentials
    from .drive_client import AuthError, GoogleDriveClient
    creds = load_credentials(paths.token_file())
    if creds is None:
        raise AuthError("not signed in")
    return GoogleDriveClient(creds)


def service_main() -> None:
    from .service import Service, setup_logging
    from .state import StateDB
    setup_logging(paths.log_file())
    svc = Service(paths.config_file(), StateDB(paths.db_file()), _drive_factory,
                  token_path=paths.token_file())
    svc.run_forever()


def gui_main() -> None:
    from . import gui
    gui.main()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "gui"
    if cmd == "service":
        service_main()
    elif cmd == "gui":
        gui_main()
    elif cmd == "install":
        from . import autostart
        autostart.install()
        print("Drive Backup will now start automatically at login.")
    elif cmd == "uninstall":
        from . import autostart
        autostart.uninstall()
        print("Automatic start removed.")
    elif cmd == "paths":
        print(f"config: {paths.config_dir()}")
        print(f"data:   {paths.data_dir()}")
        print(f"put client_secret.json at: {paths.client_secret_file()}")
    else:
        print("usage: python -m drive_backup.cli [gui|service|install|uninstall|paths]")
        sys.exit(2)


if __name__ == "__main__":
    main()
