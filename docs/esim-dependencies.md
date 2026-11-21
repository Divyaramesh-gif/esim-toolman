# eSim external dependencies

Research notes for esim-toolman: what eSim needs installed, where each version
comes from, and how eSim's own installers install it.

**Source:** FOSSEE/eSim `master`, commit `d86c189` (2026-10-14), `VERSION` file = `2.6`.
Every `file:line` reference below is relative to the eSim repository root at that commit.

**Files read:** `ARCHITECTURE.md`, `INSTALL`, `requirements.txt`,
`Ubuntu/bootstrap.sh`, `Ubuntu/install-eSim.sh`, `nghdl/install-nghdl.sh`,
`nghdl/install-nghdl-scripts/GHDL-BACKEND-26.04.md`, `windows/deps-manifest.json`,
`windows/requirements-windows.txt`, `windows/esim.bat`, and the relevant parts of
`windows/build-windows.ps1`, `windows/windows_bootstrap.py` and `windows/installer.iss`.
I also searched `src/` to see which Python packages eSim actually imports.

**How to read the labels:**
- **UNVERIFIED** means the eSim files did not settle the question, so this is not confirmed.
- **(general knowledge)** marks a fact that does not come from the eSim files.
- None of eSim's installers or the version commands were run for this document.

## Key findings

1. **eSim does not use winget.** On Windows, eSim ships one Inno Setup installer
   (admin-only, `windows/installer.iss:47`) that bundles KiCad, ngspice, Python, MSYS2,
   GHDL and Verilator. winget appears only in hints for the packaging machine's own
   tools (`windows/build-windows.ps1:73`, `:97`).
2. **ngspice does not come from apt.** eSim builds its own ngspice ("nghdl-simulator",
   ngspice 45.2 plus eSim's code models) from a tarball in the repo. It also purges
   the apt `ngspice` package and replaces `/usr/bin/ngspice` with a symlink.
3. **The GHDL backend matters.** GHDL must use the llvm or gcc backend, never mcode.
   On Ubuntu 24.04 the apt `ghdl-llvm` package is broken, so the installer falls back
   to `ghdl-gcc`.
4. **Icarus Verilog needs libvvp**, which apt's `iverilog` lacks. eSim builds Icarus
   from a pinned commit. Without that build, d_cosim co-simulation is unavailable.
5. **Supported Ubuntu releases are 23.04, 24.04, 25.04 and 26.04, matched exactly.**
   22.04 and any other version are refused.

## Summary table

| Tool | What eSim uses it for | Version eSim installs or requires (exact quote) | Ubuntu install | Windows install (from `windows/`) | Version command | Env vars / paths eSim expects |
|---|---|---|---|---|---|---|
| **ngspice** (eSim's "nghdl-simulator" build) | SPICE simulation engine. eSim's build adds code models for GHDL (`ghdl.cm`), Verilator (`Ngveri`) and Icarus (`ivlng`, d_cosim) | `m4_define([ngspice_major_version], [45.2])`, from `configure.ac` line 19 inside `nghdl/nghdl-simulator-source.tar.xz`<br>Windows fallback: `"version": "45.2"`, from `windows/deps-manifest.json:26` | **Built from source** (`nghdl/install-nghdl.sh:339-348`: `--enable-xspice --disable-debug --disable-maintainer-mode`) into `~/nghdl-simulator/install_dir`. apt `ngspice` is purged (`:353`) | Built inside MSYS2 from the same tarball when the installer is packaged (`build-windows.ps1:633`), installed to `{app}\tools\nghdl\install_dir`. The official 45.2 build sits in `{app}\tools\ngspice` as a fallback (`:290-303`) | `ngspice --version` (used at `build-windows.ps1:746`). With the official Windows build: `ngspice_con --version` (see [Detection details](#detection-details-checked-for-esim-toolman)) | `/usr/bin/ngspice` → `~/nghdl-simulator/install_dir/bin/ngspice` (`install-nghdl.sh:356`)<br>`~/.nghdl/config.ini` `[NGHDL]` `NGHDL_HOME`, `DIGITAL_MODEL`, `RELEASE` (`:436-439`)<br>Windows: `SPICE_LIB_DIR`, `PATH` (`esim.bat:16-25`) |
| **KiCad** | Schematic capture. eSim converts KiCad netlists to SPICE (`ARCHITECTURE.md:23`, `:32`) | Minimum major version depends on the Ubuntu release. 24.04: `KICAD_SOURCE="ppa"; KICAD_PPA="kicad/kicad-9.0-releases"; KICAD_MIN_MAJOR=9` (`Ubuntu/install-eSim.sh:724`). 23.04 → 7 (`:720`), 25.04 → 8 (`:728`), 26.04 → 9 (`:731`)<br>Windows: `"version": "9.0.3"` (`deps-manifest.json:54`) | apt: `kicad kicad-footprints kicad-libraries kicad-symbols kicad-templates`, `--no-install-recommends` (`install-eSim.sh:922-923`). Comes from PPA `kicad/kicad-9.0-releases` on 23.04/24.04 and from universe on 25.04/26.04 | The official KiCad installer is unpacked with 7z, trimmed, and bundled at `{app}\tools\kicad`; the KiCad installer itself never runs (`build-windows.ps1:902-931`). A system KiCad under `%ProgramFiles%\KiCad\*` is the fallback (`esim.bat:30`) | `kicad-cli version` (`build-windows.ps1:936`)<br>Ubuntu installer uses `dpkg-query -W -f='${Version}' kicad` (`install-eSim.sh:891`) | `~/.config/kicad/<N.M>/sym-lib-table` (`install-eSim.sh:973-994`)<br>`~/.esim/kicad_static`, `~/.esim/kicad_symbols` (`:954`, `:965`)<br>`KicadLib` in `~/.esim/config.ini` (`:763`)<br>Windows: `%APPDATA%\kicad\<N.M>\sym-lib-table` (`windows_bootstrap.py:246`) |
| **GHDL** | Compiles VHDL for NGHDL mixed-signal co-simulation. Links eSim's socket server with `ghdl -e -Wl,...` (`GHDL-BACKEND-26.04.md:26-31`) | **Not pinned.** The backend is what matters: `sudo apt-get install -y ghdl-llvm`, falling back to `ghdl-gcc` (`install-nghdl.sh:222-224`). The doc records 5.0.1 on 26.04 (`GHDL-BACKEND-26.04.md:51`) and 4.1.0 on 24.04 (`:112`) | apt `ghdl-llvm`, else `ghdl-gcc`. Never the `ghdl` meta-package (`install-nghdl.sh:219-221`). `ghdl-mcode` is purged (`:229`). A compile smoke test runs after install (`:204-213`, `:254-266`) | MSYS2 package `mingw-w64-x86_64-ghdl-llvm` (`build-windows.ps1:403`), included only with the optional `hdl` component (`installer.iss:77`) | `ghdl --version` (output must not mention mcode; `install-nghdl.sh:243`) | `GHDL_BACKEND` is optional; if set it must be `llvm` or `gcc` (`install-nghdl.sh:246`; `GHDL-BACKEND-26.04.md:98`)<br>An old `/usr/local/bin/ghdl` takes precedence in PATH and breaks co-simulation (`install-eSim.sh:798-808`) |
| **Verilator** | Compiles Verilog into ngspice code models (NgVeri) (`ARCHITECTURE.md:65`) | Needs major version 5: `Its apt repositories ship Verilator 4 and KiCad 6, but this eSim needs` / `Verilator 5 and KiCad >= 7` (`install-eSim.sh:735-736`). No other pin | apt `verilator` (`install-nghdl.sh:189`) | MSYS2 `mingw-w64-x86_64-verilator` (`build-windows.ps1:403`), `hdl` component | `verilator --version` (`install-nghdl.sh:191`) | `VERILATOR_ROOT`, read with `verilator --getenv VERILATOR_ROOT`; falls back to `/usr/share/verilator/include` (`install-nghdl.sh:317-318`)<br>Windows: a comment says the app uses `VERILATOR_ROOT=<msys>\mingw64` (`build-windows.ps1:474`). Where the app sets it is **UNVERIFIED** (`src/` not read) |
| **Icarus Verilog** (with libvvp) | Verilog Verifier, plus d_cosim co-simulation through ngspice's `ivlng` adapter, which loads libvvp at run time (`install-nghdl.sh:21-27`) | `ICARUS_REF="de415b2f03c1b41ab5b46faa9632716d98c1cd86"` (`install-nghdl.sh:97`)<br>Windows: `"version": "de415b2f (post-v12, pinned)"` (`deps-manifest.json:40`) | **Built from source** with `--enable-libvvp` into `~/nghdl-simulator/iverilog` (`:93`, `:404`). Uses `nghdl/iverilog-source.tar.xz` if present (it is **not** in the git tree), otherwise fetches the pinned commit from GitHub (`:384-400`). Fallback: apt `iverilog`, which has no libvvp and so no d_cosim (`:428`) | Built in MSYS2 from the pinned source and installed to `{app}\library\bin\iverilog` (`deps-manifest.json:44`). With `-SkipSimBuild`, Bleyer's `v12-20220611` build is used instead (`:33-37`) | `iverilog -V`, exits with code 0 (see [Detection details](#detection-details-checked-for-esim-toolman)) | `~/.nghdl/config.ini` `[COSIM]` `IVERILOG`, `VVP`, `IVERILOG_LIB` (`install-nghdl.sh:450-457`; Windows: `windows_bootstrap.py:146-159`) |
| **Python 3** | Runs eSim itself (`src/frontEnd/Application.py`) | Ubuntu: the system `python3`, not pinned (`install-eSim.sh:838`)<br>Windows: `"version": "3.12.10"` (`deps-manifest.json:19`) | apt `python3-full python3-venv python3-virtualenv python3-pip python3-setuptools` (`:838-840`). Creates a venv at `~/.esim/env` with `--system-site-packages` so the apt Qt is visible (`:844-849`) | A private CPython from the nuget package, installed at `{app}\python` (`build-windows.ps1:227-237`) | `python3 --version` | `~/.esim/env`<br>The `/usr/bin/esim` launcher runs `~/.esim/env/bin/python3 <eSim>/src/frontEnd/Application.py` (`install-eSim.sh:1109`) |
| **PyQt6** (+ QtSvg) | GUI | Ubuntu apt, not pinned: `QT_PKGS="python3-pyqt6 python3-pyqt6.qtsvg pyqt6-dev-tools"` (`install-eSim.sh:71`)<br>`requirements.txt:19`: `PyQt6>=6.5.0`<br>Windows: `PyQt6>=6.6` (`requirements-windows.txt:14`) | apt (`install-eSim.sh:871`) | pip wheel installed into the private Python (`build-windows.ps1:239-240`) | `python3 -c "from PyQt6.QtCore import PYQT_VERSION_STR; print(PYQT_VERSION_STR)"` (general knowledge). eSim's own check is `python3 -c "import PyQt6.QtWidgets"` (`install-eSim.sh:878`) | The launcher sets these defaults (only if unset): `LIBGL_ALWAYS_SOFTWARE=1`, `QT_OPENGL=software`, `EGL_LOG_LEVEL=fatal` (`install-eSim.sh:1106-1108`) |
| **QScintilla** | Code editor for project files (`requirements.txt:22`). Optional | `QSCI_PKG="python3-pyqt6.qsci"` (`install-eSim.sh:72`), not pinned<br>Windows: `PyQt6-QScintilla>=2.14` (`requirements-windows.txt:15`) | apt. If the package is missing, the installer only warns (`install-eSim.sh:872-873`) | pip | `dpkg-query -W -f='${Version}' python3-pyqt6.qsci`; Windows: `python -m pip show PyQt6-QScintilla` | — |
| **matplotlib, numpy, scipy** | Plotting waveforms (`ARCHITECTURE.md:46`). No file in `src/` imports scipy, and `requirements-windows.txt:10-11` says the same | Ubuntu apt, not pinned: `python3-matplotlib python3-numpy python3-scipy` (`install-eSim.sh:841`)<br>`requirements.txt` pins `matplotlib==3.7.5` (`:11`), `numpy==1.24.4` (`:13`), `scipy==1.10.1` (`:26`). The snap build reads that file (`snap/snapcraft.yaml:304`); the Ubuntu installer does not<br>Windows: `matplotlib>=3.8`, `numpy>=1.26` (`requirements-windows.txt:16-17`), no scipy | apt | pip | `python3 -c "import matplotlib, numpy; print(matplotlib.__version__, numpy.__version__)"` | — |
| **psutil** | Process handling for the HDL flows (imported in `src/maker/hdl/procs.py`) | Ubuntu apt `python3-psutil`, not pinned (`install-eSim.sh:840`)<br>Windows: `psutil>=5.9` (`requirements-windows.txt:18`) | apt | pip | `python3 -c "import psutil; print(psutil.__version__)"` | — |
| **watchdog** | Imported in `src/frontEnd/Application.py` and `DockArea.py`. What eSim uses it for is **UNVERIFIED** | `"watchdog>=3.0"` (`install-eSim.sh:89`)<br>`requirements.txt:28`: `watchdog==4.0.2`<br>Windows: `watchdog>=3.0` (`requirements-windows.txt:19`) | pip, into `~/.esim/env` (`install-eSim.sh:863-865`). A failure only warns | pip | `~/.esim/env/bin/pip show watchdog` | — |
| **pyhdlparser** (module `hdlparse`) | Reads HDL ports (imported in `src/maker/hdl/ports.py`) | `PYHDLPARSER_REF="e1153ace8ca1e25f9fb53350c41058ef8eb8dacf"` (`install-eSim.sh:87`)<br>`requirements.txt:8`: `hdlparse==1.0.4` | pip from a GitHub tarball URL (`install-eSim.sh:90`). A failure only warns | pip, same URL (`requirements-windows.txt:34`) | `pip show hdlparse`. The pip distribution name is **UNVERIFIED** | — |
| **sandpiper-saas** | Converts TL-Verilog (`.tlv`) to Verilog (`ARCHITECTURE.md:127-128`; imported in `src/maker/ModelGeneration.py`) | `"sandpiper-saas>=1.1.0,<2"` (`install-eSim.sh:91`; `requirements-windows.txt:35`) | pip. A failure only warns | pip | `pip show sandpiper-saas` | Whether it needs network access at run time is **UNVERIFIED** |
| **volare** | **UNVERIFIED.** No file in `src/` imports it. It is a PDK version manager (general knowledge) and may be used by the IHP script, which I did not read | `"volare>=0.20.6,<0.21"` (`install-eSim.sh:92`; `requirements-windows.txt:37`) | pip. A failure only warns | pip | `pip show volare` | — |
| **SKY130 PDK** (`sky130_fd_pr`) | Device models for SKY130 analog simulation | A snapshot ships as `library/sky130_fd_pr.tar.xz`. Its exact upstream revision is **UNVERIFIED** | **Bundled tarball.** Extracted, checked and patched by `src/configuration/Sky130Prepare.py`, then moved to `/usr/share/local/sky130_fd_pr` (`install-eSim.sh:1052-1072`) | Extracted into `{app}\library\sky130_fd_pr` when the installer is packaged (`build-windows.ps1:250-288`) | None. Check that the directory exists | `/usr/share/local/sky130_fd_pr` |
| **IHP Open PDK** (optional) | Analog IC design with the IHP PDK (`install-eSim.sh:1079`) | **UNVERIFIED**. `ihp/ihp-install-script.sh` was not read | Asked at install time, then runs `ihp/ihp-install-script.sh --install` (`:1079-1087`) | Not mentioned in the `windows/` files I read (**UNVERIFIED**) | **UNVERIFIED** | **UNVERIFIED** |
| **xterm** | Used by `src/ngspiceSimulation/NgspiceWidget.py`. It probably shows ngspice output in a terminal window (**UNVERIFIED**) | apt `xterm`, not pinned (`install-eSim.sh:839`) | apt | Not in the `windows/` files I read | `xterm -version` (general knowledge) | — |
| **MSYS2 + mingw64 toolchain** (Windows only) | The tools the app runs when it builds HDL models (mingw32-make, gcc, verilator, ghdl). Also used to compile ngspice and Icarus during packaging | Base: `"version": "20250622"` (`deps-manifest.json:48`). Packages are then updated to latest by `pacman -Syu` (`build-windows.ps1:491`), so they are not pinned | n/a | `$MingwPkgs`: gcc, make, verilator, ghdl-llvm, dlfcn, readline (`build-windows.ps1:402-415`). `$MsysPkgs`: `make autoconf automake libtool bison flex gperf patch tar` (`:416`). Installed to `{app}\tools\msys64` with the `hdl` component (`installer.iss:213`) | `pacman -Q`, run in the bundled bash (`build-windows.ps1:530`) | `~/.nghdl/config.ini` `[COMPILER]` `MSYS_HOME` (`windows_bootstrap.py:138-141`) |
| **Build toolchain for ngspice/Icarus** (Ubuntu only) | Compiling the nghdl-simulator and Icarus from source | Not pinned: `make autoconf g++ flex bison patch git gperf xz-utils zlib1g-dev libreadline-dev libxaw7 libxaw7-dev $GTK_CANBERRA` (`install-nghdl.sh:181-186`). `GTK_CANBERRA` is `libcanberra-gtk3-module` on 26.04 and adds `libcanberra-gtk-module` on 23.04–25.04 (`:116-122`). Also `build-essential` (`install-eSim.sh:839`) | apt | MSYS2 (row above) | `gcc --version`; `dpkg-query -W <package>` | — |
| **Basic utilities** | Unpacking archives, copying the symbol library, adding PPAs, downloading | Not pinned: `xz-utils unzip rsync git` (`install-eSim.sh:839`), `software-properties-common` for `add-apt-repository` (`:903`). The bootstrap requires `curl` but does not install it (`bootstrap.sh:258`). The installer runs `gio`, which it does not install (`install-eSim.sh:1137`) | apt | n/a | `<tool> --version` | — |

### Packaging-machine tools (Windows; never on users' machines)

| Tool | Version (exact quote) | Source |
|---|---|---|
| PowerShell 7+ | `if ($PSVersionTable.PSVersion.Major -lt 7) {` | `windows/build-windows.ps1:71` |
| 7-Zip | Not pinned. The script suggests `winget install 7zip.7zip` | `windows/build-windows.ps1:97` |
| Inno Setup | `"version": "6.3.3"` | `windows/deps-manifest.json:61` |

## Environment variables and paths (full list)

### Files and paths the Ubuntu installers create

| Path | Created by | Contents |
|---|---|---|
| `~/eSim` (overridable with `ESIM_DIR`) | `Ubuntu/bootstrap.sh:33` | The eSim source tree. eSim runs from this tree, so it must not be deleted |
| `~/.esim/config.ini` | `install-eSim.sh:755-767` | `[eSim]` `eSim_HOME`, `LICENSE`, `KicadLib`, `IMAGES`, `VERSION`, `MODELICA_MAP_JSON` |
| `~/.esim/env/` | `install-eSim.sh:844-849` | Python venv (`--system-site-packages`) |
| `~/.esim/kicad_static/`, `~/.esim/kicad_symbols/` | `install-eSim.sh:954`, `:965` | eSim KiCad symbol libraries (fixed, and generated by eSim) |
| `~/.config/kicad/<N.M>/sym-lib-table` | `install-eSim.sh:973-994` | Registers the eSim libraries with KiCad |
| `~/.nghdl/config.ini` | `install-nghdl.sh:432-458` | `[NGHDL]` `NGHDL_HOME`, `DIGITAL_MODEL`, `RELEASE`; `[SRC]` `SRC_HOME`, `LICENSE`; `[COSIM]` `IVERILOG`, `VVP`, `IVERILOG_LIB` |
| `~/nghdl-simulator/` | `install-nghdl.sh:288-348`, `:93` | ngspice build tree, `install_dir/`, `iverilog/` |
| `/usr/bin/ngspice` (symlink) | `install-nghdl.sh:356` | → `~/nghdl-simulator/install_dir/bin/ngspice` |
| `/usr/local/bin/nghdl` (symlink) | `install-nghdl.sh:510-515` | → `<eSim>/nghdl/src/ngspice_ghdl.py` |
| `/usr/bin/esim`, `/usr/share/applications/esim.desktop`, `~/Desktop/esim.desktop` | `install-eSim.sh:1104-1142` | Launcher and desktop entries |
| `/usr/share/local/sky130_fd_pr` | `install-eSim.sh:1070-1072` | SKY130 PDK |
| `~/eSim-install.log` | `install-eSim.sh:1310-1311` | Full install log |

### Environment variables the Ubuntu installers read

| Variable | Where | Effect |
|---|---|---|
| `ESIM_DIR`, `ESIM_BRANCH`, `ESIM_REPO` | `bootstrap.sh:25-33` | Install location, branch or tag, and source repository |
| `ESIM_SKIP_DISK_CHECK` | `install-eSim.sh:672`, `install-nghdl.sh:146` | Skips the free-space check |
| `ESIM_NO_FANCY`, `NO_COLOR`, `ESIM_UI_STICKY` | `install-eSim.sh:152-153`, `:438` | Terminal display only |
| `NEEDRESTART_MODE` | `install-eSim.sh:1324` | Defaults to `l` so Ubuntu's needrestart prompt does not stop the install |
| `http_proxy`, `https_proxy`, `ftp_proxy` (and upper-case versions) | `install-eSim.sh:1233-1234` | Set from the interactive proxy questions |
| `GHDL_BACKEND` | `install-nghdl.sh:246` | Must be `llvm` or `gcc` if set |
| `VERILATOR_ROOT` | `install-nghdl.sh:317` | Read through `verilator --getenv` |

### Environment variables set when eSim starts

- **Ubuntu** (`/usr/bin/esim`, `install-eSim.sh:1106-1108`): `LIBGL_ALWAYS_SOFTWARE=1`,
  `QT_OPENGL=software`, `EGL_LOG_LEVEL=fatal`, each only if not already set.
- **Windows** (`windows/esim.bat`): `ESIM_HOME` (`:10`). Adds `tools\ngspice\bin`,
  `tools\nghdl\install_dir\bin`, `tools\nghdl\install_dir\lib\ngspice` and `tools\kicad\bin`
  to the front of `PATH` (`:16-31`). Also sets `SPICE_LIB_DIR` (`:25`) and `ESIM_ENV_READY=1` (`:38`).

### Windows paths

- Install directory: `DefaultDirName={sd}\FOSSEE\eSim` (`installer.iss:37`), and installing requires admin (`:47`).
- Per-user files are written by `windows_bootstrap.py`, which builds paths with `os.path.expanduser('~')` (`:53-54`):
  `~\.esim\config.ini`, `~\.esim\kicad_symbols\`, `~\.nghdl\config.ini` (which adds `[COMPILER]` `MSYS_HOME`).
- KiCad library table: `%APPDATA%\kicad\<N.M>\sym-lib-table` (`windows_bootstrap.py:246`). `<N.M>` comes from
  the `tools\kicad\KICAD-VERSION` file (`build-windows.ps1:928-931`).

## Detection details (checked for esim-toolman)

Checked on 2026-11-17 on Windows 11 with winget v1.29.380. None of the five tools is installed
on that machine, so the version commands were not run there. On 2026-11-20 they were run for real
on Ubuntu 24.04; see [Tested on](#tested-on). Each output format comes from the evidence named in
its row.

### Version output formats

| Tool | Command | Output looks like | Evidence |
|---|---|---|---|
| ngspice | `ngspice --version` | `** ngspice-45.2 : Circuit level simulation program` | The format string `"** %s-%s : %s\n"` in ngspice's `src/frontend/misccoms.c` (lines 187–199, inside `nghdl/nghdl-simulator-source.tar.xz`). Confirmed on Ubuntu 24.04: `** ngspice-42 : Circuit level simulation program` |
| KiCad | `kicad-cli version` | `9.0.3` | eSim's own build checks that this output contains the KiCad version from its manifest (`build-windows.ps1:936-938`). `kicad-cli` was added in KiCad 7 (general knowledge), so it is missing on KiCad 6 and older |
| GHDL | `ghdl --version` | First line `GHDL 4.1.0 (...)`. A later line names the backend (llvm, gcc or mcode) | Backend line: `GHDL-BACKEND-26.04.md:81`. Both confirmed on Ubuntu 24.04 (third line ` llvm 18.1.8 code generator`) |
| Verilator | `verilator --version` | `Verilator 5.020 2024-01-01 rev (Debian 5.020-1)` (`5.020` means 5.20) | Confirmed on Ubuntu 24.04 |
| Icarus Verilog | `iverilog -V` | Printed to stdout: `Icarus Verilog version 12.0 (stable) ()`. **Exit code 0** | Source of `driver/main.c` at eSim's pinned commit (see below); confirmed with apt's 12.0 on Ubuntu 24.04 |

**Why `iverilog -V` exits with 0.** This comes from reading
[`driver/main.c` at commit `de415b2f`](https://raw.githubusercontent.com/steveicarus/iverilog/de415b2f03c1b41ab5b46faa9632716d98c1cd86/driver/main.c),
not from running it:
- `-V` sets `version_flag = 1`.
- The banner is printed with `printf("Icarus Verilog version " VERSION " (" VERSION_TAG ")\n\n")`, which goes to stdout.
- The "no source files" error (`return 1`) is guarded by `source_count == 0 && !version_flag`, so `-V` skips it.
- `main` then does `if (version_flag) return t_version_only();`, and that function ends with `return 0;`.
- `t_version_only()` also runs `ivlpp -V` and `ivl -V`. If they fail, it prints to stderr but still returns 0.

Both real builds tested behave the same way: apt's iverilog 12.0 on Ubuntu 24.04 (run on
2026-11-20), and Bleyer's Windows build that winget installs (run on 2026-11-21; it prints
`Icarus Verilog version 12.0 (devel) (s20150603-1539-g2693dd32b)`).

### ngspice program name on Windows

- **Official ngspice Windows build:** the console program is `ngspice_con.exe`. The
  [ngspice manual](https://ngspice.sourceforge.io/docs/ngspice-html-manual/manual.xhtml), §14.2 "MS Windows Console",
  says: "its binary is distributed as ngspice_con.exe". Whether this build's plain `ngspice.exe`
  is the GUI version is **UNVERIFIED**; the manual describes the two builds by configure option
  (`--with-wingui`), not by file name.
- **eSim's own Windows bundle:** `tools\nghdl\install_dir\bin\ngspice.exe` is a console build,
  and `ngspice_gui.exe` is a separate GUI build used only for plot windows (`build-windows.ps1:695-729`).
  The official fallback copy is in `tools\ngspice\bin` (`:290-303`).
- **What this means for esim-toolman:** look for `ngspice_con` before `ngspice`, so detection
  does not start a GUI window by mistake.

### winget packages

Checked with `winget search <name> --source winget`, then `winget show --id <id> --exact --source winget`.

| Tool | winget ID | Version | Installer | Usable for eSim? |
|---|---|---|---|---|
| ngspice | — (no match for `ngspice`) | — | — | No package |
| KiCad | `KiCad.KiCad` | latest 10.0.8; `--versions` also lists 9.0.3 to 9.0.8 | nullsoft, `github.com/KiCad/kicad-source-mirror/releases/.../kicad-10.0.8-x86_64.exe` | Yes, **pinned to 9.0.3** in `manifest/tools.toml`. eSim 2.6 ships and tests KiCad 9.0.3 (`deps-manifest.json:54`), and whether it works with KiCad 10 is **UNVERIFIED**. `winget show --id KiCad.KiCad --version 9.0.3` lists installer SHA256 `582635ced8552c07e043ce9f2eba01dc0c42c96dcb87ce12ba85db1530da593f`, the same hash eSim pins for the same file (`deps-manifest.json:57`) |
| GHDL | `ghdl.ghdl.ucrt64.mcode` (the only match) | 6.0.0 | portable zip, `ghdl-mcode-6.0.0-ucrt64.zip` | **No.** This is the mcode backend, which cannot link eSim's VHDL server (`GHDL-BACKEND-26.04.md:34-44`) |
| Verilator | — (no match for `verilator`) | — | — | No package |
| Icarus Verilog | `Icarus.Verilog` | 12.2022.06.11 | inno, `https://bleyer.org/icarus/iverilog-v12-20220611-x64_setup.exe` | Partly. This is the same file eSim's `deps-manifest.json:33-37` says has **no libvvp**. The Verilog Verifier works, but d_cosim does not. A real install put it in `C:\iverilog` and did **not** add `C:\iverilog\bin` to PATH (see Tested on) |

## Tested on

### Ubuntu 24.04.5 LTS under WSL2, 2026-11-20

- **Machine:** Ubuntu 24.04.5 LTS under WSL2 on Windows 11 (kernel `6.18.58.1-microsoft-standard-WSL2`), Python 3.12.3.
- **Setup:** the author installed the tools beforehand with apt. esim-toolman only read; it ran no `sudo` and no apt.
- **Fixtures:** the real outputs are saved in `tests/fixtures/ubuntu-24.04/`. The tests replay them, so these results stay checked.

| Tool | Installed apt package | Newest in apt | Version output (the line the pattern reads) | Exit | Read as |
|---|---|---|---|---|---|
| ngspice | `ngspice 42+ds-3build1` | same | `** ngspice-42 : Circuit level simulation program` | 0 | 42 |
| KiCad | `kicad 7.0.11+dfsg-1build4` | same | `7.0.11` | 0 | 7.0.11 |
| GHDL | `ghdl-llvm 4.1.0+dfsg-0ubuntu2.1` | same (`ghdl-gcc` 4.1.0+dfsg-0ubuntu2.1 also available) | `GHDL 4.1.0 (Ubuntu 4.1.0+dfsg-0ubuntu2.1) [Dunoon edition]` | 0 | 4.1.0 |
| Verilator | `verilator 5.020-1` | same | `Verilator 5.020 2024-01-01 rev (Debian 5.020-1)` | 0 | 5.020 |
| Icarus Verilog | `iverilog 12.0-2build2` | same | `Icarus Verilog version 12.0 (stable) ()` | 0 | 12.0 |

What the real system showed, beyond the versions:

- **ghdl-llvm cannot compile.** `ghdl -a t.vhdl` exits with code 3: `/usr/lib/ghdl/llvm/ghdl1-llvm: error while loading shared libraries: libLLVM-18.so.18.1: cannot open shared object file`. eSim's `GHDL-BACKEND-26.04.md:108-124` describes this bug, and it is still present in `4.1.0+dfsg-0ubuntu2.1`.
- **Ubuntu's iverilog has no libvvp.** `dpkg -L iverilog` lists no libvvp, so d_cosim needs eSim's own Icarus build.
- **Ubuntu's ngspice has none of eSim's code models.** `dpkg -L ngspice` lists only `analog.cm`, `digital.cm`, `spice2poly.cm`, `table.cm`, `xtradev.cm` and `xtraevt.cm`: no `ghdl.cm`, no `Ngveri.cm` and no `ivlng`.
- **apt can't upgrade far enough.** It offers no KiCad newer than 7.0.11 and no ngspice newer than 42.
- **KiCad's library packages were missing.** `kicad-symbols`, `kicad-footprints`, `kicad-libraries` and `kicad-templates` were not installed, and `/usr/share/kicad/symbols` did not exist. That's despite `kicad` recommending `kicad-libraries`.
- **`dpkg-query` reports "not installed" two ways:** `unknown ok not-installed` with exit code 0 for a package it knows, and exit code 1 ("no packages found") for one it doesn't.

What esim-toolman got wrong on this machine at first, each now fixed with a test (`test_replay_of_the_real_ubuntu_2404_machine` replays the whole machine):

1. **Icarus Verilog was reported as plain OK**, but without libvvp d_cosim cannot work. Fix: an `own_build` check for iverilog.
2. **The ngspice warning listed only VHDL and Verilator co-simulation as broken.** Ubuntu's ngspice also lacks `ivlng`, so d_cosim is broken too. Fix: `verilog-cosim` added to its `breaks`.
3. **The fix for KiCad 7 and ngspice 42 was `apt-get install --only-upgrade`**, which changes nothing, because apt has nothing newer. Fix: the doctor asks `apt-cache policy` for apt's newest version first. If even that is too old, it says so and shows the manifest's note instead.
4. **The ngspice note showed users a rule meant for esim-toolman's developers.** Fix: the rule moved into a TOML comment.
5. **The missing KiCad library packages went unnoticed.** Fix: on Linux the doctor now checks that every apt package in the manifest is installed (or the fallback set). The check is skipped for eSim's own builds.

### Windows 11, a real install of Icarus Verilog with winget, 2026-11-21

The first real install with esim-toolman. The author ran `python -m esim_toolman install iverilog` on Windows 11. The action log (`C:\Users\<you>\.esim-toolman\actions.log`) recorded two attempts, both running `winget install --id Icarus.Verilog --exact --accept-package-agreements --accept-source-agreements`:

| Time (UTC) | Result | Verification |
|---|---|---|
| 17:21:48 | failed, exit code 2316632332, which is `0x8A15010C`. `winget error --input 0x8A15010C` names it `APPINSTALLER_CLI_ERROR_INSTALL_CANCELLED_BY_USER`, "You cancelled the installation" | skipped |
| 17:23:21 | ok, exit code 0 | **failed**: "Icarus Verilog is still not found: iverilog not found on PATH" |

What the real system showed:

- **Where winget put it:** Bleyer's build (winget version 12.2022.06.11) went into `C:\iverilog`, with `iverilog.exe` in `C:\iverilog\bin`.
- **It is not on PATH.** The installer did not add `C:\iverilog\bin` to PATH: neither the machine PATH nor the user PATH in the registry contains it. A new terminal cannot find `iverilog`.
- **`C:\iverilog\bin\iverilog.exe -V` exits with 0** and prints `Icarus Verilog version 12.0 (devel) (s20150603-1539-g2693dd32b)`, read as 12.0. It is saved as `tests/fixtures/windows-11/iverilog-version.json`.
- **No libvvp:** `C:\iverilog` holds `vvp.exe` but no libvvp library, as eSim's `deps-manifest.json:37` says.

What esim-toolman got wrong, now fixed with tests:

1. **Verification said "not found" although the install had worked.** esim-toolman searched only PATH and eSim's own folder. Fixes:
   - the manifest now also searches `%SystemDrive%\iverilog\bin` (after eSim's own folder);
   - the installer reports a program found only in such a folder as "installed, but not on PATH", with the doctor's command to add the folder, instead of "Verification FAILED", and offers no fallback for it.

   Tests: `test_installed_but_not_on_path_is_reported_with_the_fix`, `test_once_the_folder_is_on_path_verification_passes`, `test_not_on_path_does_not_trigger_the_fallback`, `test_real_manifest_searches_bleyers_icarus_folder_on_windows`, `test_iverilog_pattern_on_real_windows_output`.

After the fix, on the same machine:

- `status` shows `Icarus Verilog OK 12.0 C:\iverilog\bin\iverilog.EXE`.
- `doctor` warns that `C:\iverilog\bin` is not on PATH and gives the exact PowerShell command to add it.
- The installer's verification of this install returns `not-on-path` instead of `failed`.

Not tested yet (as of 2026-11-21): a real install with apt, a real (non-WSL) Ubuntu, Ubuntu 26.04, eSim's own ngspice and Icarus builds, the GHDL mcode backend, and Windows with KiCad, ngspice, GHDL or Verilator installed.

## Known installer problems

### Ubuntu version handling

1. **Exact match on the Ubuntu version.** `install-eSim.sh:716-742` accepts only `23.04`, `24.04`,
   `25.04` and `26.04`. Any other version, such as 24.10, 25.10, 26.10 or a future LTS, stops with
   `Unsupported Ubuntu version`. Each new Ubuntu release needs a script change.
2. **Ubuntu-based distributions are refused.** `bootstrap.sh:256` requires `ID=ubuntu` in
   `/etc/os-release`, so Linux Mint, Pop!_OS and similar are rejected. Running `install-eSim.sh`
   directly reads the derivative's own `VERSION_ID` and stops at the same version check.
3. **The two scripts handle unknown versions differently.** `install-nghdl.sh:123-126` continues
   with a warning, while `install-eSim.sh` stops. The nghdl header lists "24.04 / 25.x / 26.04"
   (`:10`) but its profile also handles 23.04 (`:120`).
4. **Two supported releases are end-of-life.** The script itself warns about 23.04 (`:717-721`).
   23.04 reached end of life in January 2024 and 25.04 in January 2026 (general knowledge).
   After end of life, Ubuntu moves a release's packages to `old-releases.ubuntu.com`, so `apt-get
   update` fails (general knowledge). The script does not handle this.

### Hardcoded versions

5. **The KiCad PPA name is hardcoded** as `kicad/kicad-9.0-releases` (`install-eSim.sh:720`, `:724`),
   and so is the minimum KiCad version for each Ubuntu release. KiCad 10 will need edits. If adding
   the PPA fails, the script falls back to universe (`:912-915`). If that gives a KiCad older than
   the minimum, it only warns (`:930`). Which KiCad version 24.04's universe has is **UNVERIFIED**.
6. **Fragile KiCad version parsing.** `grep -oP '^\d+'` on the dpkg version string (`:891`, `:926`,
   `:977`) returns nothing if the version has an epoch prefix (such as `1:9.0.3`), and the script
   then treats the version as 0. Whether any KiCad package uses an epoch is **UNVERIFIED**.
7. **The KiCad config folder is guessed.** `:974` picks the highest `N.M` folder that already exists
   in `~/.config/kicad`, not the folder for the KiCad version just installed. A folder left from a
   different KiCad version would receive eSim's library table instead. With no folder, it uses `9.0`.
8. **Pinned refs that need deliberate updates:** `ICARUS_REF` (`install-nghdl.sh:97`),
   `PYHDLPARSER_REF` (`install-eSim.sh:87`), the ngspice 45.2 tarball, and every entry in
   `windows/deps-manifest.json`.

### Steps that fail or misbehave on some machines

9. **apt `ghdl-llvm` is broken on 24.04.** It needs `libLLVM-18.so.18.1`, but Ubuntu 24.04 names the
   library differently (`GHDL-BACKEND-26.04.md:108-124`). The installer works around this with a
   compile test and a fallback to `ghdl-gcc` (`install-nghdl.sh:254-266`). A check that only runs
   `ghdl --version` would miss the problem.
10. **The Icarus build needs network access when installing from a git checkout.**
    `iverilog-source.tar.xz` is not in `nghdl/` (I checked), so `install-nghdl.sh:384-400` downloads
    the source from GitHub. If that fails, it falls back to apt `iverilog`, and d_cosim is lost with
    only a warning.
11. **Long source builds on every install.** ngspice is compiled with `make -j"$(nproc)"`
    (`install-nghdl.sh:347`), which uses one job per CPU regardless of RAM. The Icarus build limits
    its jobs because one job per CPU "OOM-kills on small VMs" (`:406-408`). Whether the ngspice build
    also runs out of memory is **UNVERIFIED**.
12. **Interactive prompts with no unattended mode.** The installer stops for keyboard answers
    (`read -rp`) at `install-eSim.sh:801`, `:897`, `:1079`, `:1226-1231` and `:1353`, and the
    bootstrap requires a terminal (`bootstrap.sh:140-144`). There is no `--yes` option, so it cannot
    run in CI.
13. **Proxy password handling.** The password is put into a URL and exported to every child process
    (`install-eSim.sh:1232-1234`). It is not URL-encoded, so a password containing `@` or `:` breaks
    the URL. Whether `sudo apt-get` sees these variables depends on the sudoers `env_keep` setting
    (**UNVERIFIED**).
14. **Desktop assumptions.** `~/Desktop` is hardcoded (`:1133-1134`), but localized systems often
    use a different folder name (general knowledge). `gio set ... metadata::trusted` (`:1137`) is
    GNOME-specific, and its errors are ignored.

### Changes to things eSim did not install

15. **The system ngspice is replaced.** `install-nghdl.sh:353-356` purges apt `ngspice` and points
    `/usr/bin/ngspice` into one user's home folder. Since Ubuntu 21.04, home folders are not readable
    by other users by default (general knowledge), so other users on the machine likely get a
    `/usr/bin/ngspice` they cannot run. Uninstalling deletes `/usr/bin/ngspice` (`:546`).
16. **KiCad sources and packages are removed broadly.** `cleanLegacyEsim` deletes every
    `/etc/apt/sources.list.d/kicad*` file (`install-eSim.sh:788-792`), including KiCad PPAs the user
    added. Uninstalling purges KiCad entirely (`:1156`), even if the user installed it before eSim.
    `ghdl-mcode` is purged without asking (`install-nghdl.sh:229`).
17. **Unusual path and ownership for SKY130.** The PDK goes to `/usr/share/local/sky130_fd_pr`
    (`install-eSim.sh:1071`) instead of the standard `/usr/local/share`. `chown -R "$USER:$USER"`
    (`:1072`) assumes the user has a group with the same name.

### Docs and files that disagree with the scripts

18. **The dry run does not match the real install.** The `--dry-run` plan says
    `14 static -> /usr/share/kicad/symbols (root)` (`install-eSim.sh:1261`), but `copyKicadLibrary`
    actually installs them to `~/.esim/kicad_static` (`:954`).
19. **`requirements.txt` is out of date.** It pins `numpy==1.24.4` and `scipy==1.10.1`
    (`requirements.txt:13`, `:26`). These versions do not support Python 3.12, the version Ubuntu
    24.04 ships (general knowledge). The Ubuntu installer installs these from apt instead, but the
    snap build still reads this file (`snap/snapcraft.yaml:304`). The file also mixes docs and lint
    tools (`sphinx`, `flake8`, lines 1–7) with runtime dependencies.
20. **Outdated references.** `GHDL-BACKEND-26.04.md:66` refers to `install-nghdl-26.04.sh`, which no
    longer exists. nghdl uninstall removes `/usr/share/kicad/library/eSim_Nghdl.lib` (`:545`), a
    path nothing installs anymore.
21. **The default install source changes over time.** The bootstrap defaults to
    `ESIM_BRANCH=master` (`bootstrap.sh:32`). `INSTALL:26-27` says the `v2.6` command works only
    "after FOSSEE tags the merged commit". Whether that tag exists is **UNVERIFIED**.

### Windows

22. **No package manager is involved.** Windows uses one admin-only installer that bundles
    everything (`installer.iss:47`), not winget. `INSTALL:89-92` tells users to disable antivirus
    and remove MinGW/MSYS from `PATH`.
23. **The MSYS2 tools are not pinned.** `pacman -Syu` (`build-windows.ps1:491`) installs the latest
    packages, so two installer builds a week apart can ship different gcc, Verilator and GHDL. The
    script says so itself (`:518-523`).
24. **The checksum for the Icarus fallback could not be checked against an official value**; it was
    recorded on first download (`deps-manifest.json:37`).
25. **The eSim repo does not check out cleanly on Windows.** `library/SubcircuitLibrary/...` and
    `library/subcircuitLibrary/...` differ only by letter case. Git warned about these files when I
    cloned on this Windows machine, because Windows treats them as the same folder.

## UNVERIFIED items (summary)

- Exact upstream revision of the bundled SKY130 snapshot.
- Everything about the IHP Open PDK (`ihp/ihp-install-script.sh` not read).
- What eSim uses `watchdog`, `volare` and `xterm` for. Whether `sandpiper-saas` needs network access.
- The pip distribution name for pyhdlparser.
- Where the Windows app sets `VERILATOR_ROOT` (only a comment mentions it).
- Which KiCad version 24.04's universe has. Whether any KiCad apt version has an epoch prefix.
- Whether the ngspice build runs out of memory on small VMs.
- Whether `sudo apt-get` receives the proxy variables.
- Whether the `v2.6` tag exists upstream.
- Whether the official ngspice Windows build's plain `ngspice.exe` is the GUI version.
- Whether `verilator` from eSim's bundled MSYS2 can run outside the MSYS2 shell on Windows.
- Whether eSim 2.6 works with KiCad 10, which is what winget installs by default.
