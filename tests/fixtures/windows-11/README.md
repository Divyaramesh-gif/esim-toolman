# Real outputs from Windows 11

Captured on 2026-11-21 on Windows 11, after a real install test:
`python -m esim_toolman install iverilog`, which ran
`winget install --id Icarus.Verilog --exact --accept-package-agreements --accept-source-agreements`.
winget installed Bleyer's Icarus Verilog build (winget version
12.2022.06.11) into `C:\iverilog`. Its installer did not add `C:\iverilog\bin`
to PATH, neither the machine PATH nor the user PATH in the registry.

The file has the same format as the Ubuntu fixtures: `command`, `exit_code`,
`stdout` and `stderr`, captured with Python's `subprocess.run` the way
esim-toolman runs commands (UTF-8, stdin closed). Nothing was installed or
changed to capture it.

| File | Command |
|---|---|
| `iverilog-version.json` | `C:\iverilog\bin\iverilog.exe -V` |
