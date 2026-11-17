# esim-toolman

Automated Tool Manager for eSim (FOSSEE Winter Internship 2026, Screening Task 5).

## Goal
A CLI that detects, diagnoses, installs and configures eSim's external tools
(Ngspice, KiCad, GHDL, Verilator, etc.) on Linux (apt) and Windows (winget).

## Rules
- Python 3.11+, standard library only. No third-party runtime dependencies.
- Tool data lives in manifest/tools.toml. Code must not hardcode tool names or versions.
- Every action that changes the system must support --dry-run and be written to the action log.
- Never run sudo or install anything without showing the plan and asking for confirmation (unless --yes).
- Small modules with type hints and docstrings. Each new module gets tests in tests/ (pytest).
- Do not claim features in README or docs that are not implemented and tested.
- Before writing code, explain the plan in plain words. After writing code, explain what each part does, so the author can explain every line in an interview.
- Commit after each working step with a clear message.

## Layout
esim_toolman/: cli, manifest, platform_info, detect, versions, doctor, installer, backends/(base, apt, winget), envconfig, actionlog
manifest/tools.toml, tests/, docs/
