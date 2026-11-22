# esim-toolman: design

esim-toolman checks, diagnoses and installs the external tools that
[eSim](https://esim.fossee.in/) needs (ngspice, KiCad, GHDL, Verilator and
Icarus Verilog) on Ubuntu (apt) and Windows (winget). This document describes
the code as it is on 2026-11-22. It covers only what is implemented; where
something is implemented but not yet verified on a real system, it says so.

Contents: 1. Problem · 2. What the tool does · 3. Architecture · 4. Modules · 5. Design decisions · 6. Requirements coverage · 7. Testing · 8. Known limitations · 9. Future work

---

## 1. Problem

eSim 2.6 depends on several external programs. Installing them correctly is
harder than "install the package", for reasons found by reading eSim's own
installers and by running real systems. Each example below is recorded in
[`esim-dependencies.md`](esim-dependencies.md), with the file and line or the
real output it comes from.

**A program can be installed and still not work for eSim.**

- **GHDL's backend.** eSim's VHDL co-simulation needs GHDL's llvm or gcc
  backend. According to eSim's notes for Ubuntu 26.04, the `ghdl`
  meta-package installs the mcode backend, which cannot link eSim's VHDL
  server. On Ubuntu 24.04, `ghdl-llvm` installs and
  reports its version, but cannot compile: on 2026-11-20 `ghdl -a t.vhdl`
  exited with code 3 (`libLLVM-18.so.18.1: cannot open shared object file`).
  A version check alone reports this GHDL as fine. Until 2026-11-22,
  esim-toolman's own `install` made the same mistake (see decision 8).
- **Icarus Verilog without libvvp.** Ubuntu's `iverilog` package ships no
  libvvp (checked with `dpkg -L iverilog`), so eSim's d_cosim co-simulation
  cannot use it, although the Verilog Verifier can.
- **ngspice without eSim's code models.** Ubuntu's ngspice 42 has no
  `ghdl.cm`, `Ngveri.cm` or `ivlng`. `ghdl.cm` and `Ngveri.cm` exist only in
  eSim's own ngspice build. `ivlng` is part of upstream ngspice 42 and later,
  but Ubuntu's package leaves it out. eSim's installer compiles its own build
  into `~/nghdl-simulator` and links it at `/usr/bin/ngspice`; installing
  apt's ngspice afterwards would replace it.
- **Installed, but not on PATH.** A real `winget install --id Icarus.Verilog`
  on Windows 11 (2026-11-21) put Icarus in `C:\iverilog\bin` and did not add
  that folder to PATH. A new terminal, and any program that looks on PATH,
  cannot find it.
- **KiCad without its libraries.** On the test machine, `kicad` was installed
  but `kicad-symbols`, `kicad-footprints`, `kicad-libraries` and
  `kicad-templates` were not. eSim's KiCad symbol table refers to 206 of
  KiCad's standard symbol libraries, all of which were missing.

**The right version is often not where the package manager looks.**

- On Ubuntu 24.04, apt's newest KiCad is 7.0.11 and its newest ngspice is 42,
  while eSim 2.6 targets KiCad 9 and builds ngspice 45.2. A plain
  `apt-get install --only-upgrade` changes nothing.
- On Windows, winget's latest KiCad was 10.0.8 when checked (2026-11-17). eSim 2.6 ships and tests 9.0.3.
  The only winget GHDL package uses the mcode backend, and there are no winget
  packages for ngspice or Verilator.

**eSim's own installers are fragile in places** (`esim-dependencies.md`,
"Known installer problems"). Examples:

- The Ubuntu installer accepts only four exact release numbers (problem 1).
- It hardcodes the KiCad 9 PPA (problem 5).
- It stops for keyboard input with no unattended mode (problem 12).
- It removes packages it did not install (problems 15 and 16).
- Its `--dry-run` output does not match what `--install` does (problem 18).

## 2. What the tool does

| Command | Purpose | Changes the system? |
|---|---|---|
| `status [--json]` | For each tool: found or not, its version, and how that version compares with eSim's minimum and recommended versions | No |
| `doctor [--json]` | Finds problems like the ones in section 1 and gives a fix for this platform | No |
| `install <tool>` / `install --missing` | Shows a plan, asks, installs (or repairs an installed tool that fails a check) with apt or winget, verifies, offers a fallback | Yes, after confirmation; `--dry-run` only shows the plan |
| `log [-n N]` | Shows the commands `install` ran | No |

## 3. Architecture

```
  manifest/tools.toml  (all tool data: names, versions, packages, checks, sources)
          |
          v
   +--------------+       +-------------+
   | manifest.py  |------>| versions.py |   parse and compare versions
   +--------------+       +-------------+
          ^                      ^
          | load                 |
   +--------------+              |
   |    cli.py    |  status | doctor | install | log
   +--------------+
     |    |    |   \______________________________________
     |    |    |                                          \
     |    |    v                                           v
     |    |  +--------------+  plan, ask, run,     +---------------+
     |    |  | installer.py |--------------------->| backends/     |
     |    |  +--------------+  verify, fall back   |  base, apt,   |
     |    |    |    |    \                         |  winget       |
     |    |    |    |     \--> actionlog.py        +---------------+
     |    |    |    |          (~/.esim-toolman/actions.log)
     |    v    |    v
     |  +-------------+   check_tool(): planning and verification
     |  |  doctor.py  |<--------------------------------
     |  +-------------+
     |    |
     v    v
   +-------------+     +------------------+
   |  detect.py  |     | platform_info.py |  OS, distro, package managers
   +-------------+     +------------------+
          |
          v
   the computer: tool version commands, GHDL compile test (temporary folder),
   dpkg-query, apt-cache, apt-get -s (all read-only); sudo apt-get install,
   winget install (only from install, after confirmation); Windows registry (PATH)
```

The program flows from top to bottom. `cli.py` loads and checks the
manifest, asks `platform_info.py` what system this is, then calls the module
for the command.

- `status` uses `detect.py` and `versions.py` directly.
- `doctor` adds the manifest's checks and the apt checks.
- `install` builds on all of them: it uses detection and
  `doctor.check_tool()` to decide what to do, backends to build the
  commands, and `doctor.check_tool()` again to verify the result. It writes every command it runs to the action log.

Every place where the program meets the computer (finding programs, running
commands, the symlink resolver, reading the keyboard, the registry) is either
passed in as a function argument or looked up when it is called. The tests use
this to replace them with fakes.

## 4. Modules

| Module | What it does | Why it is separate |
|---|---|---|
| `manifest.py` | Reads `manifest/tools.toml` with `tomllib` and checks it strictly: unknown keys, missing keys, wrong types, bad regexes, non-numeric versions and unsafe names are errors that name the bad value's location. Returns frozen dataclasses. | The manifest is the only place tool data lives, so every mistake in it must stop the program before anything runs. |
| `versions.py` | Pulls the version out of a program's output with the manifest's regex. Compares versions as numbers (`10` > `9`, `9.0` = `9`). Gives one status: OK, BELOW_RECOMMENDED, TOO_OLD, NOT_FOUND or UNKNOWN. | Pure functions with no side effects; used by status, doctor, installer and the manifest's own checks. |
| `platform_info.py` | Reads the OS, the distribution (from `/etc/os-release`) and which of `apt-get` and `winget` are on PATH. An untested Ubuntu release or a non-Ubuntu Linux gives a warning, not an error. | Every command needs it, and the tests need to pretend to be another system. |
| `detect.py` | Finds a tool's program: first in the manifest's extra folders (with `~`, `%VAR%`, `${VAR}` and `*` expanded), then on PATH. Runs its version command. `run_command()` runs any command as an argument list, never through a shell, with stdin closed and a time limit. | One safe way to start programs, shared by the doctor and the apt backend. |
| `doctor.py` | Turns detection, versions, the manifest's checks and (on Linux) `dpkg-query`/`apt-cache` answers into findings. Each finding has a severity, the problem, the eSim features it breaks, and a fix for this platform. | Diagnosis is separate from installing, so it can stay read-only and be reused to verify installs. |
| `backends/` | `base.py` defines one interface: build a command from the manifest, run it, and (apt only) simulate what it would install. `apt.py` builds `sudo apt-get install -y <packages>`; `winget.py` builds `winget install --id <id> --exact [--version <v>] --accept-package-agreements --accept-source-agreements`. | Package managers differ; the installer only knows the interface. |
| `installer.py` | Builds the install plan (for an installed tool it also runs the doctor's checks, and plans a repair when one fails), prints it, asks, runs each command, verifies it with the doctor's checks (passed; installed but not on PATH; failed), offers the manifest's fallback after a failure, and logs every command it ran. | The only module that changes the system, kept apart so its safety rules sit in one place. |
| `actionlog.py` | Appends one JSON line per command run to `~/.esim-toolman/actions.log`; reads back the last N. | An audit trail that survives the program. |
| `cli.py`, `__main__.py` | Arguments, output formats (text and JSON) and exit codes. | Keeps printing and argument handling out of the logic. |

## 5. Design decisions

**1. Manifest-driven data.**
- *Decision:* everything about a tool lives in `manifest/tools.toml`: program names, extra search folders, version command and pattern, minimum and recommended versions, apt packages (with fallback and conflicts), winget ID (with an optional pinned version), and health checks. Each version, package and check entry has a required `source` field saying where it came from.
- *Reasons:*
  - Tools and versions change more often than code.
  - Every number can be traced to eSim's source or a real output.
  - CLAUDE.md forbids hardcoding tool names or versions.
- *Checked by:* a search of the package code finds tool names only in comments, docstrings and one `--help` example (`e.g. iverilog`), never in logic. 62 tests in `test_manifest.py` check the loader and the real manifest.

**2. Standard library only.**
- *Decision:* no third-party runtime dependency; `pyproject.toml` lists none. The package imports only standard-library modules (checked by scanning every import). pytest is needed only for the tests.
- *Reason:* the tool must run on a machine where eSim's environment is broken or not yet installed, so it must not need its own package installs.

**3. A read-only doctor.**
- *Decision:* `status` and `doctor` never install or change anything. The only programs they start are:
  - the tools' version commands;
  - the manifest's checks (the GHDL compile test runs in a temporary folder that is deleted afterwards);
  - `dpkg-query` and `apt-cache`, which only read.
- *Reason:* a diagnosis must be safe to run at any time, on any machine.
- *Checked by:* `test_doctor_never_runs_a_package_manager`.

**4. Plan, dry run and confirmation before any change.**
- *Decision:* `install` always prints a plan first: one line per tool with its exact command, or the reason none will run. `--dry-run` stops there. Otherwise it asks `[y/N]`, and only `y` or `yes` counts; no answer means no. `--yes` skips the question.
- *Reasons:* CLAUDE.md requires that nothing is installed and no `sudo` runs without showing the plan and asking. Showing the exact command lets the user check what will run.
- *Checked by:* `test_dry_run_runs_nothing_and_logs_nothing`, `test_answering_no_runs_nothing`, `test_anything_but_yes_means_no`, `test_no_answer_at_all_means_no`, `test_yes_flag_skips_the_question`.

**5. KiCad pinned to 9.0.3 for winget.**
- *Decision:* the manifest's winget entry pins `--version 9.0.3`.
- *Reasons:* winget's latest is KiCad 10, which eSim 2.6 has not been tested with. eSim 2.6 ships 9.0.3 on Windows. winget's 9.0.3 installer has the same SHA256 that eSim's `windows/deps-manifest.json` pins, so it is the same file eSim tests.
- *Checked by:* `test_real_manifest_pins_kicad_9_for_winget`, `test_winget_command_with_pinned_version`.

**6. Never replacing eSim's own ngspice.**
- *Decision:* an `own_build` check in the manifest lists where eSim's own builds live. esim-toolman follows symlinks to the program's real path and checks whether it is inside one of those folders. When it is:
  - `install` refuses to install the package over it;
  - `doctor`'s upgrade advice points to eSim's installer instead of apt.

  The same check tells users when their ngspice or Icarus lacks eSim's code models or libvvp.
- *Reason:* eSim's installer deliberately replaces `/usr/bin/ngspice` with its own build. Undoing that silently breaks co-simulation.
- *Checked by:* `test_refuses_apt_over_esims_own_ngspice`, `test_own_build_is_updated_with_its_own_installer_never_apt`, `test_own_build_found_through_a_symlink_is_ok`.

**7. The GHDL compile test.**
- *Decision:* besides the version, the doctor compiles a two-line VHDL file (`ghdl -a`, then `ghdl -e`) in a temporary folder with a 60-second limit. Any failure is an ERROR, with the command and the last lines of its output.
- *Reason:* on Ubuntu 24.04, `ghdl --version` works while compiling fails. Only compiling catches it.
- *Checked by:* real output on Ubuntu 24.04 (section 7); `test_ghdl_installs_but_cannot_compile` uses that real failure; `test_failed_verification_offers_the_fallback` shows the installer recovering with `ghdl-gcc`.

**8. Repairing an installed tool that fails a check.**
- *Decision:* for a tool that is installed with a good enough version, `install` also runs the doctor's checks. If a check gives an ERROR:
  - when every failed check is marked `repair = "fallback"` in the manifest, the plan says `repair` and runs the tool's apt fallback packages (GHDL's compile test: `sudo apt-get install -y ghdl-gcc`). Below the table, the plan shows which check failed and the first line of the error;
  - otherwise the plan says `manual` and shows the check's fix. The fix is text written for a person (for GHDL's mcode check it is `sudo apt-get purge ghdl-mcode && ...`), so esim-toolman never runs it;
  - a repair follows the same rules as an install: the apt simulation must not bring in a conflicting package. If the simulation shows the fallback is already installed, the plan says `manual`, because installing it again would change nothing.

  `install --missing` also includes every installed tool, required or optional, whose checks give an ERROR. After a repair, verification re-runs the checks and lists each one that passed (for example `passed: GHDL compiled a small test file (t.vhdl) in a temporary folder.`). A repair that still fails gets no second try. The action log names it `repair`, and the question before running says "repair".
- *Reason:* a real run on Ubuntu 24.04 (2026-11-22) showed `doctor` reporting GHDL's compile test as an ERROR while `install ghdl --dry-run` planned "skip: already installed". The installer looked only at the version.
- *How Ubuntu picks GHDL's backend* (checked on the real system, so the repair is not a guess): `/usr/bin/ghdl` is a shell script from `ghdl-common`, not an `update-alternatives` link. It runs `ghdl-$GHDL_BACKEND` if that variable is set and the program exists; otherwise the first of `/usr/bin/ghdl-mcode`, `ghdl-gcc` and `ghdl-llvm` that exists. `ghdl-gcc` installs `/usr/bin/ghdl-gcc` (its file list on packages.ubuntu.com), so once it is installed, plain `ghdl` uses the gcc backend. The one exception is `GHDL_BACKEND=llvm`, so the check's fix says to unset it.
- *Checked by:* `test_installed_tool_whose_check_fails_gets_a_repair` (with the real `ghdl --version`, the real compile failure and the real `apt-get install -s ghdl-gcc`), `test_installed_tool_whose_checks_pass_is_skipped`, `test_missing_includes_an_installed_tool_that_fails_a_check`, `test_repair_runs_the_fallback_and_verifies_it`, `test_repair_that_does_not_help_fails_and_shows_the_fix`, `test_failed_check_without_a_repair_is_manual`, `test_after_the_real_ghdl_repair_every_check_passes` (outputs captured after the real repair). **Run for real** on Ubuntu 24.04 (2026-11-22): `install ghdl` planned the repair, installed `ghdl-gcc` and passed verification; `ghdl --version` changed from "llvm 18.1.8 code generator" to "GCC 13.3.0 code generator", and `doctor` went from 1 error to 0.

**Further decisions:**
- **Conflicts are checked by simulation.** Before installing a tool that has conflicting packages, apt's simulation (`apt-get install -s`, no root, no change) is asked what would be installed. If a conflict like `ghdl-mcode` would come along, or the simulation fails, the install is refused.
- **Upgrade advice is checked against apt.** The doctor asks `apt-cache policy` for apt's newest version before suggesting an apt upgrade. If even that version is too old, it says so instead of suggesting a command that changes nothing.
- **Verification on Windows reads PATH from the registry.** An installer that adds itself to PATH changes the registry, not the running process.
- **"Installed, but not on PATH" is its own outcome.** A real winget install showed that an installer may succeed without putting the tool on PATH. When verification finds the tool only in one of the manifest's extra folders, it reports that, with the command to add the folder, instead of a failure. It offers no fallback in that case, because the install worked. The exit code is still 1, because a manual step is left.
- **Real outputs are test fixtures** (section 7).

## 6. Requirements coverage

The requirements come from the official FOSSEE Task 5 statement. A
submission must meet **any 2 of its 5 requirements**. It also lists optional
extras and the deliverables. The rules in `CLAUDE.md` are listed after them.
Status values:

- **Done** means implemented, tested automatically, and run for real.
- **Partly** means part of it is missing, or it has not been run for real.
- **Not done** means not implemented.

**In short:** requirement 4 is done. Requirements 1 and 5 are done except for
one part each (choosing versions on apt; showing available updates).
Requirements 2 and 3 are partly done.

**The five requirements.**

| Requirement | Status | Evidence and gaps |
|---|---|---|
| **1. Tool installation management** | | |
| 1a. Download and install required external tools automatically | Done | `install <tool>` and `install --missing` build the command from the manifest (`sudo apt-get install -y <packages>`, or `winget install --id <id> --exact ...`), show the plan, ask, run it (the package manager downloads), verify it by re-running the doctor's checks, and log it. **Two real installs:** Icarus Verilog with winget on Windows 11 (2026-11-21), and the GHDL repair with apt on Ubuntu 24.04 (2026-11-22: `ghdl-gcc` installed, verification passed). 45 installer and 17 backend tests. Gaps: ngspice, GHDL and Verilator have no usable winget package, so the plan says `manual`; KiCad has not been installed for real |
| 1b. Work on Linux and Windows | Done | Ubuntu 24.04 (WSL2) with apt and Windows 11 with winget, both run for real. Not tested: Ubuntu 26.04 and a non-WSL Ubuntu |
| 1c. Version control: install the correct versions | Partly | Each tool's minimum and recommended version comes from eSim's sources. `install --missing` installs tools that are `NOT_FOUND` or `TOO_OLD`. KiCad is pinned to 9.0.3 for winget, the version eSim 2.6 ships (same installer SHA256). Verification re-reads the installed version. Gap: apt installs whatever version Ubuntu ships. On 24.04 that is KiCad 7.0.11 and ngspice 42, below eSim's recommended 9 and 45.2. esim-toolman does not add PPAs; `doctor` says so |
| **2. Update and upgrade system** | Partly | `status` shows each installed version next to eSim's recommended one. `doctor` flags older versions (WARNING below recommended, ERROR below minimum) and gives the update command for this computer. On Ubuntu it first asks `apt-cache policy` for apt's newest version, and says when apt cannot reach the target. Real output: "apt cannot reach 9: the newest kicad it offers is 7.0.11". `install --missing` installs a `TOO_OLD` tool in one step. Gaps: "update" means "older than eSim's recommended version"; newer upstream releases are not looked up. esim-toolman runs neither `winget upgrade` nor `apt-get update`, and `--missing` does not upgrade a tool that is only below the recommended version. Libraries (Python packages, PDKs) are not checked |
| **3. Configuration handling** | Partly | `doctor` finds a tool that is installed but not on PATH (found only in one of the manifest's extra folders) and prints the exact command to add the folder to PATH: PowerShell `[Environment]::SetEnvironmentVariable(...)` on Windows, an `export PATH=...` line for `~/.bashrc` on Linux. The installer reports such an install as "installed, but not on PATH". Real: after the Icarus install, `doctor` found it in `C:\iverilog\bin` and printed that command. Gaps: esim-toolman does not apply the fix itself, sets no environment variables, and does not write eSim's configuration (`~/.nghdl/config.ini`) |
| **4. Dependency checker** | Done | `status` reports each tool as `OK`, `BELOW_RECOMMENDED`, `TOO_OLD`, `NOT_FOUND` or `UNKNOWN`. `doctor` gives each problem a severity, the eSim features it breaks, and a fix for this computer. Besides missing tools and old versions, it finds incompatible ones: GHDL that cannot compile, GHDL with the mcode backend, ngspice and Icarus builds without eSim's code models or libvvp, missing apt packages (KiCad's libraries), and conflicting packages. Real: on Ubuntu 24.04, 1 error and 5 warnings; after the GHDL repair, 0 errors. On Windows 11 it found Icarus outside PATH. 47 doctor tests, including a replay of the whole real Ubuntu machine. Gap: only the five tools in the manifest |
| **5. User interface** | | |
| 5a. A simple CLI or GUI | Done | A CLI: `python -m esim_toolman status / doctor / install / log`, with `--json` for `status` and `doctor`, and documented exit codes. No GUI (the task accepts a CLI). 23 CLI tests |
| 5b. View installed tools and versions | Done | `status`: tool, status, installed version, eSim's recommended version, path. Run for real on both systems |
| 5c. View available updates | Partly | As in requirement 2: `status` and `doctor` show which tools are older than eSim recommends and whether apt can reach a newer version. No lookup of newer releases |
| 5d. Log of actions taken | Done | Every command `install` runs is one JSON line in `~/.esim-toolman/actions.log`: time, tool, action (`install`, `install-fallback`, `repair`), command, result, exit code, and verification. `log` prints them. Real logs from both installs (section 7) |

**Optional extras.**

| Extra | Status | Evidence and gaps |
|---|---|---|
| Cross-platform (Linux, Windows, Mac) | Partly | Linux (Ubuntu) and Windows, both run for real. macOS is not supported: `install` has no package manager there and plans `manual` (tested with a pretend macOS; nothing has been run on a Mac) |
| Integration with package managers (apt, Homebrew, Chocolatey) | Partly | apt and winget (Windows' own package manager). No Homebrew or Chocolatey |

**Deliverables.**

| Deliverable | Status | Evidence |
|---|---|---|
| Design document | Done | This document; `docs/report.pdf` adds real terminal output from both systems |
| Code: a prototype demonstrating at least tool installation and version checking | Done | Installation: requirement 1a, with two real installs. Version checking: `status` and `doctor`, run for real on both systems |
| Instructions for execution | Done | `README.md`: requirements, setup, every command with real output, running the tests. Followed step by step on Ubuntu 24.04 with an empty home folder |
| Presentation (optional): a 5 to 10 minute video | Not done | |

**Project rules (`CLAUDE.md`).**

| Rule | Status | Evidence |
|---|---|---|
| Tool data only in `manifest/tools.toml` | Done | Decision 1 |
| Every system change supports `--dry-run` and is logged | Done | `install` is the only command that changes the system; it has `--dry-run` and writes the action log. Tested |
| No `sudo` or install without showing the plan and asking (unless `--yes`) | Done | Decision 4 |
| Python standard library only | Done | Decision 2 |
| Python 3.12+ | Done | `pyproject.toml` requires 3.12 or newer; the tests pass on 3.12.10 and 3.14.0 (Windows) and 3.12.3 (Ubuntu). `CLAUDE.md` says 3.12+ |
| Tests for each module | Done | 266 tests, one test file per module (`test_backends.py` covers `backends/`) |

## 7. Testing

**Automated tests: 266.** They pass on Windows 11 (Python 3.12.10 and 3.14.0) and on Ubuntu 24.04.5 under WSL2 (Python 3.12.3).

| Test file | Tests | Covers |
|---|---|---|
| `test_manifest.py` | 62 | The loader's checks; the real manifest's data; version patterns against real output |
| `test_doctor.py` | 47 | Every kind of finding; GHDL mcode and compile failures; GHDL after the real repair; apt checks; a replay of the whole Ubuntu 24.04 machine |
| `test_installer.py` | 45 | Plan building, repairs of installed tools that fail a check, every refusal, dry run, confirmation, verification (passed, not on PATH, failed), fallback, the log, Windows PATH |
| `test_detect.py` | 27 | Path expansion, search order, safe command running, timeouts and failures |
| `test_versions.py` | 25 | Version parsing, comparison and every status |
| `test_cli.py` | 23 | Text and JSON output, exit codes, `install` and `log` arguments |
| `test_backends.py` | 17 | apt and winget commands, apt simulation, running commands |
| `test_platform_info.py` | 13 | os-release parsing, the Ubuntu warnings, package managers |
| `test_actionlog.py` | 7 | Writing and reading the log |

**How the tests avoid depending on the machine.**
- `shutil.which`, `subprocess.run`, the symlink resolver, the keyboard and the registry are replaced by fakes. Folders are created in pytest's temporary directory.
- No test needs an EDA tool installed. No test installs anything or runs `sudo`. Tests write only to temporary folders: pytest's, and the doctor's own compile-test folder, which is deleted afterwards.
- In the installer tests, a fake install command changes the fake computer, for example making a program appear. That lets verification really pass or fail.

**Real outputs as fixtures.**
- `tests/fixtures/ubuntu-24.04/` holds 25 commands captured on Ubuntu 24.04.5 (24 on 2026-11-20, one on 2026-11-22), each with its exit code and separate stdout and stderr, plus that machine's `/etc/os-release`. The captured commands are:
  - the five version commands;
  - the GHDL compile commands;
  - `dpkg-query` for 11 packages;
  - `apt-cache policy` for 6 packages;
  - `apt-get install -s ghdl-gcc` (a simulation; it installs nothing).
- `tests/fixtures/ubuntu-24.04-ghdl-repaired/` holds 7 commands captured on the same machine on 2026-11-22, after the real GHDL repair: `ghdl --version` (now the gcc backend), the GHDL compile commands (now passing), `dpkg-query` for the three GHDL packages, and `apt-get install -s ghdl-gcc` ("already the newest version").
- `tests/fixtures/windows-11/` holds `iverilog -V` from Bleyer's build, captured on Windows 11 on 2026-11-21 after the real winget install.
- The version-pattern tests, the GHDL tests (doctor and repair), the platform test and the "not on PATH" installer tests use them.
- `test_replay_of_the_real_ubuntu_2404_machine` replays the whole machine, so any command the doctor runs without a captured answer fails the test.

**Runs on real systems.**
- `status` and `doctor` on Ubuntu 24.04 (WSL2) with all five tools installed from apt, and on Windows 11, first with none installed and then with Icarus installed.
- `install --dry-run --missing` on both, and `install ghdl --dry-run` on Ubuntu 24.04, which plans the repair.
- **Two real installs**, run by the author:
  - **Windows 11, `install iverilog` (2026-11-21), with winget.** The first attempt was cancelled at Windows' administrator prompt and logged as failed (exit code 2316632332, `0x8A15010C`, "installation cancelled by user"). The second installed Icarus into `C:\iverilog\bin`, but Bleyer's installer did not add that folder to PATH, so verification said "not found" (see the bugs below). After the fix, `doctor` finds Icarus in `C:\iverilog\bin`, warns that the folder is not on PATH, and prints the exact command to add it.
  - **Ubuntu 24.04, `install ghdl` (2026-11-22), with apt.** The plan said `repair` with `sudo apt-get install -y ghdl-gcc`; the install ran and verification passed. The log reads `ghdl  repair (apt)  ok, exit 0, verification passed`. `doctor` went from 1 error to 0 errors: "GHDL compiled a small test file (t.vhdl) in a temporary folder".
- The README's steps followed on Ubuntu 24.04 with an empty home folder, as a new user would. That run included cloning the GitHub repository and installing pytest from nothing.

**Bugs found by real runs, not by the tests.** The first real `doctor` run on Ubuntu 24.04 found five, all fixed with tests:
1. Icarus without libvvp was reported as plain OK.
2. ngspice's missing `ivlng` was not reported.
3. An apt upgrade that could change nothing was suggested.
4. A developer note was shown to users.
5. Missing KiCad library packages were not noticed.

A later run found a note printed twice. The two README walk-throughs found five problems in the README and the help text, and a missing "Nothing to install." message. The real installs found two more:

- **Icarus not on PATH (Windows).** Verification said "not found" after a successful winget install, because Bleyer's installer does not add `C:\iverilog\bin` to PATH and that folder was not searched. Fixed with a search path and the "installed, but not on PATH" outcome.
- **`install` ignored a failed doctor check (Ubuntu).** `doctor` reported GHDL's compile test as an ERROR, yet `install ghdl` planned "skip: already installed". Fixed with the repair action (decision 8), then run for real.

The real repair also showed three small gaps, fixed since: verification said only "found", not which checks passed; the question said "install" for a repair; and `doctor`'s apt line named `ghdl-llvm` but not the newly installed `ghdl-gcc`.

**Not tested:**
- a real apt install of a tool that is missing (the real apt run so far is the GHDL repair, which used the same apt backend);
- a real KiCad install with winget;
- a freshly installed Ubuntu (empty apt package lists, root user without `sudo`);
- a non-WSL Ubuntu, and Ubuntu 26.04;
- Windows with KiCad, ngspice, GHDL or Verilator installed;
- eSim's own ngspice and Icarus builds;
- the GHDL mcode backend on a real system (its test output is made up, following eSim's notes).

## 8. Known limitations

- **Two real installs so far:** Icarus with winget on Windows, and the GHDL repair (`ghdl-gcc`) with apt on Ubuntu. Other installs are tested with a simulated package manager and dry runs.
- **Configuration is only diagnosed.** `doctor` finds a tool outside PATH and prints the exact command to add its folder, but esim-toolman does not run it. It sets no environment variables and does not write eSim's configuration files.
- **Updates mean "older than eSim recommends".** esim-toolman does not look up newer releases, and does not run `apt-get update` or `winget upgrade`.
- **It does not build eSim's own ngspice or Icarus.** For VHDL, Verilator and d_cosim co-simulation, `doctor` points to eSim's installer.
- **apt cannot reach eSim's versions on Ubuntu 24.04** (KiCad 9, ngspice 45.2). esim-toolman does not add PPAs, so `doctor` explains this instead of fixing it.
- **It does not run `sudo apt-get update`.** On a new system the user must run it first; the README says so.
- **Install commands always start with `sudo`.** On a system without `sudo` (for example a container running as root) they would fail to start. Untested.
- **Three of the five tools have no winget package** (ngspice, GHDL, Verilator), and winget's Icarus has no libvvp.
- **winget's Icarus is not put on PATH by its installer** (found in a real install). esim-toolman finds it in `C:\iverilog\bin` and gives the command to add that folder to PATH, but does not change PATH itself.
- **It must be run from the repository folder.** The manifest is found next to the package folder, so a copy installed with `pip install .` would not find it. This follows from the code; it has not been tried.
- **Only Ubuntu is supported on Linux.** Other distributions get a warning, and their package names may differ.
- **Newer than tested is still OK.** Versions above the recommended one are reported as OK, for example KiCad 10.
- **Repairs use only the apt fallback packages.** A failed check whose fix is anything else (for example purging `ghdl-mcode`) is shown as `manual`. On Windows no repair is automatic, because winget has no fallback packages.
- **Only the five tools in the manifest are covered.**

## 9. Future work during the internship

These are planned, not done:

1. **More real installs:** a missing tool with apt on Ubuntu, and KiCad with winget on Windows. Record the results as fixtures and fix what they show, as was done for the two real installs so far.
2. **Fresh-machine test** on a newly installed Ubuntu 24.04: empty apt package lists, a root user, and tools that are really missing.
3. **A `configure` step (`envconfig.py`).** Apply the PATH fix that `doctor` already prints, and check that `~/.nghdl/config.ini` points at an Icarus with libvvp, with `--dry-run` and the action log.
4. **Support eSim's own builds:** read `~/.nghdl/config.ini` to find them, and guide the user through eSim's `nghdl` installer.
5. **Test Ubuntu 26.04 and a non-WSL Ubuntu,** and capture fixtures from Windows with the tools installed.
6. **More tools in the manifest** (Python packages, SKY130 PDK), each with sources and tests.
7. **Make it installable** (manifest as package data, an `esim-toolman` command), so it does not need to run from the repository folder.
