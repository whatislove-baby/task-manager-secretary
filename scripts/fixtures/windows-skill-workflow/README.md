# Windows Skill workflow fixture

This is a fixed synthetic Skill, using Python's standard `csv` and `decimal`
libraries. It is imported byte-for-byte, not authored by the model during the
test. Expected outputs come from the previously published independent CSVs in
`examples/script-skills`; the new-order oracle is withheld from the package.

The acceptance script builds the ZIP, runs two successful examples and one
failure inside the real Docker runtime, approves via the user API, then exercises
a fresh Deep Agents conversation from MIME attachment download through reply
draft creation and restart. Model decisions and mail-server transport are
controlled. Docker, business tools, SQLite and output checks execute for real.

On Windows, start Docker Desktop in Linux-container mode and prepare the runtime
with `scripts/prepare-skill-runtime.py`. Run from the repository root:

```powershell
& '.\release-windows\win-unpacked\resources\python\python.exe' `
  '.\scripts\verify-windows-skill-workflow.py' `
  --backend-dir '.\release-windows\win-unpacked\resources\backend' `
  --output '.\handover\windows-release\skill-workflow'
```

`skill-workflow.json`, per-conversation traces, original MIME messages, the
imported ZIP and the actual output CSV are retained. No account, API credential
or real mail server is needed. The script refuses non-Windows interpreters,
development Python, remote Docker endpoints and Windows containers.
