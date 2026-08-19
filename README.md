# T1000 FW Updater

CM7 firmware upgrade tool for the T1000 tester (Ethernet, port 7777).

## Files

| File | Purpose |
|------|---------|
| `fw_upgrade_gui.py` | Tkinter GUI updater (main app) |
| `fw_upgrade_cli.py` | Command-line version |
| `T1000FW_Updater.spec` | PyInstaller spec |
| `ico.ico` | App icon |

Build artifacts (`build/`, `dist/`, `*.exe`, firmware `*.bin`) are intentionally
not tracked in git — release `.exe` files are published via GitHub Releases.

## Usage (GUI)

```bash
python fw_upgrade_gui.py
```

1. Enter target IP (default `169.254.10.102`, port fixed at 7777)
2. Browse and select the firmware `.bin`
3. Start Upgrade — the tool switches the device to IAP mode, erases, flashes,
   verifies CRC32, activates and resets back to APP mode.

## Check Update

The GUI's **Check Update** button queries this repo's latest GitHub Release,
compares its tag against the app's `__version__`, and offers to download the
attached `.exe`.

## Build (Windows)

```bash
pyinstaller --clean --noconsole --onefile --name T1000FW_Updater --icon=ico.ico fw_upgrade_gui.py
```

## Releasing a new version

1. Bump `__version__` in `fw_upgrade_gui.py` (e.g. `1.2.0`)
2. Build the `.exe` with PyInstaller
3. Create a GitHub Release tagged `v1.2.0` and attach the `.exe` as an asset

Requires only the Python standard library (tkinter included).
