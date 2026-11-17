# Installing LithoMask

There are three ways to get LithoMask running. Pick **one**.

| Option | Needs Python? | Admin rights? | Best for |
|---|---|---|---|
| [**A. Windows installer**](#option-a--windows-installer-recommended) | No | No | Most users |
| [**B. Portable `.exe`**](#option-b--portable-exe) | No | No | USB sticks, lab PCs |
| [**C. Run from source**](#option-c--run-from-source-python) | Yes (3.10+) | No | Developers, scripting, other OSes |

**System requirements:** Windows 10 or 11 (64-bit) · 4 GB RAM (8 GB recommended for large OPC windows) · ~300 MB free disk space · a display of 1366×768 or larger.

---

## Option A — Windows installer (recommended)

1. **Download.** Go to the **[Releases page](https://github.com/AtharvSagare/LithoMask/releases/latest)**, scroll to **Assets** and click **`LithoMask-Setup.exe`**.
2. **(Optional) Verify the download.** Open PowerShell in your *Downloads* folder and run:
   ```powershell
   Get-FileHash .\LithoMask-Setup.exe -Algorithm SHA256
   ```
   The hash must match the one printed in the release notes.
3. **Run the installer.** Double-click `LithoMask-Setup.exe`.
   - If you see **“Windows protected your PC”**, click **More info**, then **Run anyway**. ([Why?](#windows-smartscreen-warning))
4. **Accept the licence.** The MIT licence page appears — choose **I accept the agreement** and click **Next**.
5. **Choose a folder.** The default (`%LOCALAPPDATA%\LithoMask`) needs no administrator rights. Click **Next**.
6. **Install.** Click **Install**, wait a few seconds, keep **Launch LithoMask** ticked and click **Finish**.
7. **First start.** A splash screen appears and the main window opens. Use **Start menu → LithoMask** or the desktop shortcut next time.

**Try it:** *File → Open…* → pick an example project from the [`examples/projects`](../examples/projects) folder
(download them from the repository), then press `Ctrl+R` to run OPC.

**Uninstall:** *Settings → Apps → Installed apps → LithoMask → Uninstall*. Your own `.mop` / `.gds` files are never touched.

---

## Option B — Portable `.exe`

1. Download **`LithoMask.exe`** from the [latest release](https://github.com/AtharvSagare/LithoMask/releases/latest).
2. Put it in any folder (for example `D:\Tools\LithoMask\`).
3. Double-click it. The first start takes a few seconds longer because the program unpacks itself to a temporary folder.
4. To remove it, delete the file. Settings (theme, window layout) are stored by Qt in the current user's profile.

---

## Option C — Run from source (Python)

### 1. Install the prerequisites
- **Python 3.10 or newer** — from [python.org](https://www.python.org/downloads/). Tick **“Add python.exe to PATH”** in the installer.
- **Git** (optional) — from [git-scm.com](https://git-scm.com/download/win). Without it, use *Code → Download ZIP* on the repository page and unzip.

### 2. Get the code
```bash
git clone https://github.com/AtharvSagare/LithoMask.git
cd LithoMask
```

### 3. Install the dependencies
**Windows:** double-click **`install.bat`** (or run it in a terminal). It creates a `.venv` and installs
`numpy, scipy, gdstk, shapely, pyclipper, PySide6, matplotlib, pytest`, then prints `LithoMask install OK`.

**Any OS (manual):**
```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Start LithoMask
```bash
run_lithomask.bat                # Windows shortcut
python -m lithomask.gui          # any OS
python -m lithomask.gui examples/projects/contacts.mop   # open a file directly
```

### 5. (Optional) Run the test suite
```bash
python -m pytest -q              # 155 tests, about 10 minutes
```

### 6. (Optional) Build your own executable and installer
```bash
build_exe.bat                    # creates dist\LithoMask.exe  (PyInstaller)
```
Then install [Inno Setup 6](https://jrsoftware.org/isinfo.php) and compile the installer script:
```bash
ISCC.exe installer\LithoMask-Setup.iss     # creates dist\LithoMask-Setup.exe
```

---

## Troubleshooting

### Windows SmartScreen warning
LithoMask is a free open-source project and the executables are **not code-signed** (a code-signing certificate costs
several hundred dollars a year), so Windows shows a warning for any new, unsigned program. Click **More info → Run anyway**.
If you want certainty, build it yourself from source (Option C) or compare the SHA-256 hash with the one in the release notes.

### Antivirus flags the `.exe`
Single-file Python executables built with PyInstaller are occasionally flagged by heuristic scanners. This is a false
positive. Please compare the SHA-256 hash with the release notes, or build from source.

### `ImportError: DLL load failed while importing QtCore: The specified procedure could not be found`
This was a packaging bug in early test builds (an old Visual C++ runtime was bundled ahead of the one Qt needs). It is
fixed in **v1.0.0** — download the latest release. If you build from source and see it, make sure your environment has only
one Qt binding (`pip uninstall PyQt5 PyQt6`) and update the runtime from
[Microsoft's Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).

### The window is blank or tiny on a high-DPI display
Right-click the shortcut → **Properties → Compatibility → Change high DPI settings → Override high DPI scaling behaviour → Application**.

### “A grid over 6 M pixels is refused” when running OPC
The simulation window is too large for the chosen pixel size. In *Run OPC…* increase **Simulation pixel (nm)** or reduce **Simulation margin (nm)**.

### Still stuck?
Open an **[issue](https://github.com/AtharvSagare/LithoMask/issues/new/choose)** and include your Windows version, the LithoMask version
(*Help → About*) and what you clicked right before the problem.
