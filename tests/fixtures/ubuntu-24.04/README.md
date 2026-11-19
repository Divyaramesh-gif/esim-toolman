# Real outputs from Ubuntu 24.04

Captured on 2026-11-20 on Ubuntu 24.04.5 LTS under WSL2 (kernel
6.18.58.1-microsoft-standard-WSL2). These packages had been installed with apt
beforehand: ngspice 42+ds-3build1, kicad 7.0.11+dfsg-1build4,
ghdl-llvm 4.1.0+dfsg-0ubuntu2.1, verilator 5.020-1, iverilog 12.0-2build2.

Each `*.json` file is one command exactly as it ran: `command` (full argument
list), `exit_code`, `stdout` and `stderr`, kept separate. The commands were run
with Python's `subprocess.run` in the same way esim-toolman runs them (UTF-8,
stdin closed). The `apt-cache policy` commands ran with `LC_ALL=C`, as the
doctor runs them. `ghdl-analyze` and `ghdl-elaborate` ran in a temporary folder
holding the doctor's `t.vhdl` test file. `os-release` is a copy of that
machine's `/etc/os-release`.

Nothing was installed or changed to capture these: every command only reads.

| File | Command |
|---|---|
| `ngspice-version.json` | `ngspice --version` |
| `kicad-cli-version.json` | `kicad-cli version` |
| `ghdl-version.json` | `ghdl --version` |
| `verilator-version.json` | `verilator --version` |
| `iverilog-version.json` | `iverilog -V` |
| `ghdl-analyze.json`, `ghdl-elaborate.json` | `ghdl -a t.vhdl`, `ghdl -e t` |
| `dpkg-query-<package>.json` | `dpkg-query -W --showformat=${Status} <package>` |
| `apt-cache-policy-<package>.json` | `apt-cache policy <package>` |
| `os-release` | `/etc/os-release` |
