# esim-toolman

A command-line tool that checks, diagnoses and installs the external tools
[eSim](https://esim.fossee.in/) needs: **ngspice, KiCad, GHDL, Verilator and
Icarus Verilog**. It works on Ubuntu (with apt) and Windows (with winget).

Built for the FOSSEE Winter Internship 2026, Screening Task 5.

| Command | What it does | Changes the system? |
|---|---|---|
| `status` | Is each tool installed, which version, is that version OK for eSim? | No |
| `doctor` | Finds problems and says how to fix each one on this computer | No |
| `install` | Installs a tool, or every missing required tool, after showing a plan and asking | Yes (with your confirmation) |
| `log` | Shows what `install` has run | No |

All tool data (program names, versions, packages, checks) lives in
[`manifest/tools.toml`](manifest/tools.toml). Its version, package and check
entries each name the source they come from.

## Documents

- [`docs/design.md`](docs/design.md): the design. Covers the problem, architecture, modules, design decisions, which requirements are covered, testing, known limitations and future work.
- [`docs/report.pdf`](docs/report.pdf): the design document plus real terminal output of `status`, `doctor`, `install --dry-run` and the test runs, from Ubuntu 24.04 and Windows 11.
- [`docs/esim-dependencies.md`](docs/esim-dependencies.md): research into what eSim needs, how eSim's own installers install it, and what was found on a real Ubuntu 24.04 and in a real Windows install.

## What has been tested

- **Automated tests:** the test suite passes on Windows 11 (Python 3.12 and 3.14) and on Ubuntu 24.04 under WSL2 (Python 3.12). It needs no EDA tools: it fakes every command, using outputs captured on real Ubuntu 24.04 and Windows 11 machines.
- **`status` and `doctor` on real systems:**
  - Ubuntu 24.04 (WSL2), with ngspice, KiCad, GHDL, Verilator and Icarus Verilog installed from apt;
  - Windows 11, first with none of them installed, then with Icarus Verilog installed.
- **`install`:** one real install so far, Icarus Verilog with winget on Windows 11. It worked, and it showed that the Icarus installer does not add itself to PATH, which esim-toolman now handles. **A real apt install has not been run yet.** Everything else in `install` is tested with simulated package managers, and `--dry-run` was run on both systems.
- **Not tested:** Ubuntu 26.04 (listed in the manifest, but never run), a non-WSL Ubuntu, and eSim's own ngspice and Icarus builds.

## What it does not do

- It does not build eSim's own ngspice or Icarus Verilog. `doctor` tells you when you need them and how to get them.
- It does not add PPAs or run `sudo apt-get update`.
- It does not set environment variables or configure eSim.

## Requirements

- **Python 3.12 or newer.** esim-toolman uses only Python's standard library, so it needs no `pip install`.
- **Ubuntu** with apt (tested on 24.04), or **Windows** with winget, which is part of "App Installer" from the Microsoft Store (tested on Windows 11).
- **git**, to download the code.
- For `install` on Ubuntu, a user that can run `sudo`.

## Setup

Download the code, then run every command from inside the `esim-toolman` folder:

**Ubuntu**

```bash
git clone https://github.com/Divyaramesh-gif/esim-toolman.git
cd esim-toolman
python3 -m esim_toolman --help
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Divyaramesh-gif/esim-toolman.git
cd esim-toolman
python -m esim_toolman --help
```

There is no install step for esim-toolman itself. `python3 -m esim_toolman` finds the code in the current folder, so it must be run from the `esim-toolman` folder. Anywhere else, Python says `No module named esim_toolman`.

The examples below use `python3`, as on Ubuntu. On Windows, type `python` instead.

## Commands

### `status`: what is installed

```bash
python3 -m esim_toolman status
python3 -m esim_toolman status --json
```

Real output on Ubuntu 24.04 (WSL2) with the tools installed from apt:

```
Platform: Linux (Ubuntu 24.04.5 LTS)
Package managers: apt-get

TOOL            STATUS             INSTALLED  RECOMMENDED  PATH
Ngspice *       BELOW_RECOMMENDED  42         45.2         /usr/bin/ngspice
KiCad *         BELOW_RECOMMENDED  7.0.11     9            /usr/bin/kicad-cli
GHDL            OK                 4.1.0      -            /usr/bin/ghdl
Verilator       OK                 5.020      -            /usr/bin/verilator
Icarus Verilog  OK                 12.0       -            /usr/bin/iverilog

* required: the exit code is 0 only if every required tool is OK

Details:
  ngspice: version 42 works but is older than the recommended 45.2
  kicad: version 7.0.11 works but is older than the recommended 9
```

Each tool gets one status:

| Status | Meaning |
|---|---|
| `OK` | Found, and at least the recommended version |
| `BELOW_RECOMMENDED` | Found and usable, but older than eSim recommends |
| `TOO_OLD` | Found, but older than eSim's minimum |
| `NOT_FOUND` | Not found |
| `UNKNOWN` | Found, but its version could not be read |

Exit code: **0** if every required tool (marked `*`) is `OK`, otherwise **1**.

### `doctor`: what is wrong and how to fix it

```bash
python3 -m esim_toolman doctor
python3 -m esim_toolman doctor --json
```

For each tool, `doctor`:

- checks the version, and whether the program is on PATH;
- runs the manifest's checks:
  - GHDL must not use the mcode backend, and must be able to compile a small VHDL file;
  - for ngspice and Icarus Verilog, whether they are eSim's own builds;
- on Ubuntu, checks that the tool's apt packages are installed and that no conflicting package is.

Each problem is an `ERROR` or a `WARNING`. It names the eSim features it breaks, if any, and gives a fix for this computer. Checks that pass are listed as `OK`. Part of the real output from Ubuntu 24.04:

```
GHDL
  ERROR    GHDL is installed but cannot compile VHDL (known for ghdl-llvm on Ubuntu 24.04), so VHDL co-simulation hangs. `ghdl -a t.vhdl` exited with code 3. Output: /usr/lib/ghdl/llvm/ghdl1-llvm: error while loading shared libraries: libLLVM-18.so.18.1: cannot open shared object file: No such file or directory | /usr/bin/ghdl-llvm:error: exec error
           Breaks: vhdl-cosim
           Fix: Switch to the gcc backend: install ghdl-gcc.
                sudo apt-get install ghdl-gcc
```

The last line of the report counts the findings, for example `1 error, 5 warnings, 10 OK`.

Exit code: **1** if there is any `ERROR`, otherwise **0**.

### `install`: install tools

```bash
python3 -m esim_toolman install iverilog --dry-run
python3 -m esim_toolman install iverilog
python3 -m esim_toolman install --missing
python3 -m esim_toolman install --missing --yes
```

- `install <tool>` installs one tool. The tool names are `ngspice`, `kicad`, `ghdl`, `verilator` and `iverilog`.
- `install --missing` installs every required tool (ngspice, KiCad) that is `NOT_FOUND` or `TOO_OLD`.
- `--dry-run` only prints the plan.
- `--yes` (or `-y`) skips the question.

First, `install` prints a plan with one line per tool: an exact command to run, or the reason it won't run one. Real `install --dry-run --missing` output on Windows 11 with no tools installed:

```
Platform: Windows
Package manager: winget (winget)

Install plan:
  TOOL       STATUS     ACTION   BACKEND  COMMAND OR REASON
  Ngspice *  NOT_FOUND  manual   -        No winget package. Install eSim for Windows (it bundles ngspice), or download the official Windows build from https://ngspice.sourceforge.io/download.html and add its bin folder to PATH.
  KiCad *    NOT_FOUND  install  winget   winget install --id KiCad.KiCad --exact --version 9.0.3 --accept-package-agreements --accept-source-agreements

1 to install, 1 manual, 0 refused, 0 skipped. (* required)

Dry run: nothing was installed.
```

What happens after the plan:

1. **The commands come only from the manifest:**
   - Ubuntu: `sudo apt-get install -y <packages>`.
   - Windows: `winget install --id <id> --exact [--version <pinned>] --accept-package-agreements --accept-source-agreements`.

   esim-toolman never invents a package name or ID. Where the manifest has none, the plan says `manual` and shows how to install the tool by hand.
2. **It refuses (`refuse`) when:**
   - the package manager is missing;
   - eSim's own build of the tool is installed (installing the package would replace it);
   - apt's simulation (`apt-get install -s`, which changes nothing) shows the install would also bring in a conflicting package, such as `ghdl-mcode` for GHDL.
3. **It asks** `Run the N install commands above? [y/N]`. Only `y` or `yes` counts as yes.
4. **It runs each command,** with apt's or winget's own output on screen, then **verifies** the result by re-running that tool's `doctor` checks. There are three possible results:
   - **passed;**
   - **installed, but not on PATH:** the tool was found only in one of the manifest's extra folders. This happened in a real test: winget installed Icarus Verilog into `C:\iverilog\bin` without adding that folder to PATH. esim-toolman prints the command to add it;
   - **failed.**
5. **If verification fails** and the manifest has a fallback (GHDL: `ghdl-llvm`, then `ghdl-gcc`), it offers the fallback, and asks again.

On a new Ubuntu system, run `sudo apt-get update` once before installing; esim-toolman does not run it.

Exit code: **0** if everything planned was installed and verified on PATH (or nothing was needed). **1** if anything was refused, manual, declined, failed or not verified, or was installed but is not on PATH.

### `log`: what `install` has run

```bash
python3 -m esim_toolman log
python3 -m esim_toolman log -n 5
```

Every command that `install` actually ran is added as one JSON line to `~/.esim-toolman/actions.log` (on Windows, `C:\Users\<you>\.esim-toolman\actions.log`). Each line holds:

- the time, the tool and the command;
- the result and exit code;
- the verification result, with what it found.

A dry run, a refused step or a "no" runs nothing, so it is not logged. `log` prints the last 20 entries, or the last `-n N`.

For example, after installing Icarus Verilog with apt on Ubuntu 24.04, the entry would look like this. The first indented line is what verification found; the second is a warning the doctor kept:

```
2026-11-20T10:00:00+00:00  iverilog  install (apt)  ok, exit 0, verification passed: sudo apt-get install -y iverilog
    Icarus Verilog 12.0 found at /usr/bin/iverilog.
    warning: This Icarus Verilog is not eSim's own build. Ubuntu's iverilog package and Bleyer's Windows build have no libvvp, so d_cosim co-simulation will not work. The Verilog Verifier still works. Detected: /usr/bin/iverilog
```

This Ubuntu entry is an example, built the same way the tests build one; no real apt install has been run yet (see [What has been tested](#what-has-been-tested)).

### Using a different manifest

By default esim-toolman reads `manifest/tools.toml`. To use another manifest file, for example an edited copy, put `--manifest` and the file's path before the command. This example keeps the copy in your home folder, out of the repository:

```bash
cp manifest/tools.toml ~/my-tools.toml
python3 -m esim_toolman --manifest ~/my-tools.toml status
```

Exit code **2** from any command means esim-toolman could not run: a wrong argument, an unknown tool name, or a manifest with errors (the message says which value is wrong).

## Running the tests

The tests need pytest, installed into a virtual environment so nothing else on the computer changes. They need no EDA tools.

**Ubuntu**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install "pytest>=8"
.venv/bin/python -m pytest
```

**WSL, using the Windows copy of the folder (`/mnt/c/...`):** that folder may already hold Windows' `.venv`, and creating a Linux one in the same place would break it. Create the Linux one in your WSL home instead:

```bash
python3 -m venv ~/.venvs/esim-toolman
~/.venvs/esim-toolman/bin/python -m pip install "pytest>=8"
~/.venvs/esim-toolman/bin/python -m pytest
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "pytest>=8"
.venv\Scripts\python -m pytest
```

## Project layout

```
esim_toolman/
  cli.py            the commands and their arguments
  manifest.py       reads and checks manifest/tools.toml
  platform_info.py  which system this is; which package managers exist
  detect.py         finds each program and runs its version command
  versions.py       reads and compares versions
  doctor.py         the checks behind `doctor` (also used by `install` to verify)
  installer.py      the install plan, confirmation, verification and fallback
  backends/         apt and winget: turning manifest data into commands
  actionlog.py      ~/.esim-toolman/actions.log
manifest/tools.toml the tools, versions, packages and checks, with sources
docs/               research notes on eSim's dependencies
tests/              pytest tests; tests/fixtures/ holds real Ubuntu 24.04 outputs
```
