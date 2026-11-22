# Real outputs from Ubuntu 24.04, after the GHDL repair

The same WSL2 machine as `../ubuntu-24.04/`, captured on 2026-11-22 after the
author ran `python3 -m esim_toolman install ghdl`. That planned a repair, ran
`sudo apt-get install -y ghdl-gcc`, and verified it (action log:
`ghdl  repair (apt)  ok, exit 0, verification passed`). Now `ghdl-llvm` and
`ghdl-gcc` are both installed, `GHDL_BACKEND` is not set, and `/usr/bin/ghdl`
runs `ghdl-gcc`: `ghdl --version` says "GCC 13.3.0 code generator", where it
said "llvm 18.1.8 code generator" before.

Each `*.json` file is one command exactly as it ran, captured the same way as
in `../ubuntu-24.04/` (UTF-8, stdin closed; `apt-get install -s` with
`LC_ALL=C`). `ghdl-analyze` and `ghdl-elaborate` ran in a temporary folder
holding the doctor's `t.vhdl` test file. Nothing was installed or changed to
capture these.

| File | Command |
|---|---|
| `ghdl-version.json` | `ghdl --version` |
| `ghdl-analyze.json`, `ghdl-elaborate.json` | `ghdl -a t.vhdl`, `ghdl -e t` (both exit 0 now) |
| `dpkg-query-<package>.json` | `dpkg-query -W --showformat=${Status} <package>` for ghdl-llvm, ghdl-gcc (both installed) and ghdl-mcode (not installed) |
| `apt-get-install-s-ghdl-gcc.json` | `apt-get install -s ghdl-gcc` ("ghdl-gcc is already the newest version") |
