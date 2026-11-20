# esim-toolman: design

esim-toolman checks, diagnoses and installs the external tools that
[eSim](https://esim.fossee.in/) needs (ngspice, KiCad, GHDL, Verilator and
Icarus Verilog) on Ubuntu (apt) and Windows (winget). This document describes
the code as it is on 2026-11-20. It covers only what is implemented; where
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
  backend. Ubuntu's `ghdl` meta-package installs the mcode backend, which
  cannot link eSim's VHDL server. On Ubuntu 24.04, `ghdl-llvm` installs and
  reports its version, but cannot compile: on 2026-11-20 `ghdl -a t.vhdl`
  exited with code 3 (`libLLVM-18.so.18.1: cannot open shared object file`).
  A version check alone reports this GHDL as fine.
- **Icarus Verilog without libvvp.** Ubuntu's `iverilog` package ships no
  libvvp (checked with `dpkg -L iverilog`), so eSim's d_cosim co-simulation
  cannot use it, although the Verilog Verifier can.
- **ngspice without eSim's code models.** Ubuntu's ngspice 42 has no
  `ghdl.cm`, `Ngveri.cm` or `ivlng`. Those exist only in eSim's own ngspice
  build, which eSim's installer compiles into `~/nghdl-simulator` and links at
  `/usr/bin/ngspice`. Installing apt's ngspice afterwards would replace it.
- **KiCad without its libraries.** On the test machine, `kicad` was installed
  but `kicad-symbols`, `kicad-footprints`, `kicad-libraries` and
  `kicad-templates` were not. eSim's KiCad symbol table refers to 206 of
  KiCad's standard symbol libraries, all of which were missing.

**The right version is often not where the package manager looks.**

- On Ubuntu 24.04, apt's newest KiCad is 7.0.11 and its newest ngspice is 42,
  while eSim 2.6 targets KiCad 9 and builds ngspice 45.2. A plain
  `apt-get install --only-upgrade` changes nothing.
- On Windows, winget's latest KiCad is 10.0.8. eSim 2.6 ships and tests 9.0.3.
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
| `install <tool>` / `install --missing` | Shows a plan, asks, installs with apt or winget, verifies, offers a fallback | Yes, after confirmation; `--dry-run` only shows the plan |
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
     |  +-------------+   check_tool() for verification
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
- `install` builds on all of them: it uses detection to decide what to do,
  backends to build the commands, and `doctor.check_tool()` to verify the
  result. It writes every command it runs to the action log.

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
| `installer.py` | Builds the install plan, prints it, asks, runs each command, verifies it with the doctor's checks, offers the manifest's fallback, and logs every command it ran. | The only module that changes the system, kept apart so its safety rules sit in one place. |
| `actionlog.py` | Appends one JSON line per command run to `~/.esim-toolman/actions.log`; reads back the last N. | An audit trail that survives the program. |
| `cli.py`, `__main__.py` | Arguments, output formats (text and JSON) and exit codes. | Keeps printing and argument handling out of the logic. |

## 5. Design decisions

**1. Manifest-driven data.**
- *Decision:* everything about a tool lives in `manifest/tools.toml`: program names, extra search folders, version command and pattern, minimum and recommended versions, apt packages (with fallback and conflicts), winget ID (with an optional pinned version), and health checks. Each value records where it came from.
- *Reasons:*
  - Tools and versions change more often than code.
  - Every number can be traced to eSim's source or a real output.
  - CLAUDE.md forbids hardcoding tool names or versions.
- *Checked by:* a scan of the package code finds tool names only in two help or comment strings, never in logic. 56 tests in `test_manifest.py` check the loader and the real manifest.

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

**Further decisions:**
- **Conflicts are checked by simulation.** Before installing a tool that has conflicting packages, apt's simulation (`apt-get install -s`, no root, no change) is asked what would be installed. If a conflict like `ghdl-mcode` would come along, or the simulation fails, the install is refused.
- **Upgrade advice is checked against apt.** The doctor asks `apt-cache policy` for apt's newest version before suggesting an apt upgrade. If even that version is too old, it says so instead of suggesting a command that changes nothing.
- **Verification on Windows reads PATH from the registry.** An installer that adds itself to PATH changes the registry, not the running process.
- **Real outputs are test fixtures** (section 7).

## 6. Requirements coverage

The official task statement is not part of this repository. The
requirements below come from the project goal and rules in `CLAUDE.md`, plus
the requirements stated while building it: version checking, installation,
and this design document. Status values:

- **Done** means implemented, tested automatically, and run for real.
- **Partly** means part of it is missing or not run for real.
- **Not done** means not implemented.

| Requirement | Status | Evidence and gaps |
|---|---|---|
| Detect eSim's external tools | Done | `status`; run for real on Ubuntu 24.04 (all five found) and Windows 11 (none installed) |
| Check versions against what eSim needs | Done | `versions.py`; minimum and recommended versions from eSim's sources; version patterns tested against real Ubuntu 24.04 output |
| Diagnose problems | Done on Ubuntu; partly on Windows | `doctor` run for real on Ubuntu 24.04 (found the GHDL compile failure and 5 warnings). On Windows it has only run with no tools installed |
| Install tools | Partly | `install` implemented with plan, dry run, confirmation, refusals, verification and fallback; 31 installer and 17 backend tests; dry runs on both systems. **No real install has been run yet** |
| Configure tools | Not done | No configuration step exists (no environment variables, no eSim config files) |
| Linux with apt | Done for Ubuntu 24.04 | Tested under WSL2. Ubuntu 26.04 is in the manifest's supported list but has not been tested |
| Windows with winget | Partly | Winget IDs confirmed with `winget show` for KiCad and Icarus only; ngspice, GHDL and Verilator have no usable winget package (the plan shows a manual fix). No real winget install has been run |
| Tools: ngspice, KiCad, GHDL, Verilator, "etc." | Partly | The five named tools are covered. Other eSim dependencies (Python packages, SKY130 and IHP PDKs, xterm) are not |
| Every system change supports `--dry-run` and is logged | Done | `install` is the only command that changes the system; it has `--dry-run` and writes `~/.esim-toolman/actions.log`. Tested |
| No `sudo` or install without showing the plan and asking (unless `--yes`) | Done | Tested (decision 4) |
| Python standard library only | Done | No dependencies; imports checked |
| Python 3.11+ (CLAUDE.md) | Partly | The project requires 3.12+ (`pyproject.toml`), as decided during development; 3.11 is untested and CLAUDE.md still says 3.11+ |
| Tests for each module | Done | 244 tests, one test file per module (`test_backends.py` covers `backends/`) |
| Design document | Done | This document |

## 7. Testing

**Automated tests: 244.** They pass on Windows 11 (Python 3.12.10 and 3.14.0) and on Ubuntu 24.04.5 under WSL2 (Python 3.12.3).

| Test file | Tests | Covers |
|---|---|---|
| `test_manifest.py` | 56 | The loader's checks; the real manifest's data; version patterns against real output |
| `test_doctor.py` | 45 | Every kind of finding; GHDL mcode and compile failures; apt checks; a replay of the whole Ubuntu 24.04 machine |
| `test_installer.py` | 31 | Plan building, every refusal, dry run, confirmation, verification, fallback, the log, Windows PATH |
| `test_detect.py` | 27 | Path expansion, search order, safe command running, timeouts and failures |
| `test_versions.py` | 25 | Version parsing, comparison and every status |
| `test_cli.py` | 23 | Text and JSON output, exit codes, `install` and `log` arguments |
| `test_backends.py` | 17 | apt and winget commands, apt simulation, running commands |
| `test_platform_info.py` | 13 | os-release parsing, the Ubuntu warnings, package managers |
| `test_actionlog.py` | 7 | Writing and reading the log |

**How the tests avoid depending on the machine.**
- `shutil.which`, `subprocess.run`, the symlink resolver, the keyboard and the registry are replaced by fakes. Folders are created in pytest's temporary directory.
- No test needs an EDA tool installed. No test installs anything, runs `sudo`, or writes outside the temporary directory.
- In the installer tests, a fake install command changes the fake computer, for example making a program appear. That lets verification really pass or fail.

**Real outputs as fixtures.**
- `tests/fixtures/ubuntu-24.04/` holds 24 commands captured on Ubuntu 24.04.5 on 2026-11-20, each with its exit code and separate stdout and stderr, plus that machine's `/etc/os-release`. The captured commands are:
  - the five version commands;
  - the GHDL compile commands;
  - `dpkg-query` for 11 packages;
  - `apt-cache policy` for 6 packages.
- The version-pattern tests, the GHDL tests and the platform test use them.
- `test_replay_of_the_real_ubuntu_2404_machine` replays the whole machine, so any command the doctor runs without a captured answer fails the test.

**Runs on real systems.**
- `status` and `doctor` on Ubuntu 24.04 (WSL2) with all five tools installed from apt, and on Windows 11 with none installed.
- `install --dry-run --missing` on both.
- The README's steps followed on Ubuntu 24.04 with an empty home folder, as a new user would. That run included cloning the GitHub repository and installing pytest from nothing.

**Bugs found by real runs, not by the tests.** The first real `doctor` run on Ubuntu 24.04 found five, all fixed with tests:
1. Icarus without libvvp was reported as plain OK.
2. ngspice's missing `ivlng` was not reported.
3. An apt upgrade that could change nothing was suggested.
4. A developer note was shown to users.
5. Missing KiCad library packages were not noticed.

A later run found a note printed twice. The two README walk-throughs found five problems in the README and the help text, and a missing "Nothing to install." message.

**Not tested:**
- a real install (apt or winget);
- a freshly installed Ubuntu (empty apt package lists, root user without `sudo`);
- a non-WSL Ubuntu, and Ubuntu 26.04;
- Windows with the tools installed;
- eSim's own ngspice and Icarus builds;
- the GHDL mcode backend on a real system (its test output is made up, following eSim's notes).

## 8. Known limitations

- **No real install has been verified.** The install logic is tested with simulated package managers and dry runs only.
- **No configuration.** esim-toolman does not set environment variables, PATH, or eSim's configuration files.
- **It does not build eSim's own ngspice or Icarus.** For VHDL, Verilator and d_cosim co-simulation, `doctor` points to eSim's installer.
- **apt cannot reach eSim's versions on Ubuntu 24.04** (KiCad 9, ngspice 45.2). esim-toolman does not add PPAs, so `doctor` explains this instead of fixing it.
- **It does not run `sudo apt-get update`.** On a new system the user must run it first; the README says so.
- **Install commands always start with `sudo`.** On a system without `sudo` (for example a container running as root) they would fail to start. Untested.
- **Three of the five tools have no winget package** (ngspice, GHDL, Verilator), and winget's Icarus has no libvvp.
- **Windows verification may report "not found"** if Bleyer's Icarus installer does not add itself to PATH. Untested.
- **It must be run from the repository folder.** The manifest is found next to the package, so `pip install .` would not give a working command.
- **Only Ubuntu is supported on Linux.** Other distributions get a warning, and their package names may differ.
- **Newer than tested is still OK.** Versions above the recommended one are reported as OK, for example KiCad 10.
- **Only the five tools in the manifest are covered.**

## 9. Future work during the internship

These are planned, not done:

1. **Run the real installs** (iverilog with apt on Ubuntu; Icarus with winget on Windows). Record the results as fixtures, and fix what they show, for example a missing search path for Bleyer's Icarus folder.
2. **Fresh-machine test** on a newly installed Ubuntu 24.04: empty apt package lists, a root user, and tools that are really missing.
3. **A `configure` step (`envconfig.py`).** For example: check that `~/.nghdl/config.ini` points at an Icarus with libvvp, and add extra tool folders to PATH, with `--dry-run` and the action log.
4. **Support eSim's own builds:** read `~/.nghdl/config.ini` to find them, and guide the user through eSim's `nghdl` installer.
5. **Test Ubuntu 26.04 and a non-WSL Ubuntu,** and capture fixtures from Windows with the tools installed.
6. **More tools in the manifest** (Python packages, SKY130 PDK), each with sources and tests.
7. **Make it installable** (manifest as package data, an `esim-toolman` command), so it does not need to run from the repository folder.
