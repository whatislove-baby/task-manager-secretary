# Repository health fixes

> Execute inline; obtain one independent review after verification.

**Goal:** Resolve the three findings approved by the user: worksheet preview,
the minimatch advisory, and missing main/PR regression checks.

**Architecture:** Keep Electron/IPC/Python contracts and storage intact. Normalize
XLSX scalar values only at the display boundary. Scope dependency overrides to
the affected minimatch version range. Add a separate non-publishing CI workflow.

**Spec:** Repository audit and the user's instruction to fix items 1, 2 and 3.
Node 24 / Python 3.12 remain required. No real mailbox/model credentials in CI.

## 1. Worksheet preview

- [ ] Add UI regressions for a real worksheet response containing a merged title,
  blank cells, numeric zero, decimals, boolean true/false and a leading-zero ID.
- [ ] Observe failure; accept only finite XLSX scalars, normalize them for display,
  preserve CSV string validation and pagination, reject objects/arrays.
- [ ] Run document UI tests and typecheck.

## 2. Build dependency advisory

- [x] Keep electron-builder 26.18.0; it is the latest published release and still
  pulls affected minimatch versions. Override only minimatch >=7.0.0 <11.0.1
  to patched 11.0.1; leave legacy v3/v5
  consumers unchanged because their API differs and this advisory excludes them.
- [ ] Refresh the lock and install; test the real builder file filters using the
  application allowlist and excluded private/test files, plus universal matching.
- [ ] Require npm audit to report zero vulnerabilities and a clean npm ci.

## 3. Automated regression

- [ ] Add main push / main pull_request / manual workflow on macOS and Windows.
- [ ] Install locked dependencies and LibreOffice; run typecheck, complete Python
  and frontend suites, build and dependency audit with read-only GitHub permissions.
- [ ] Use an explicit test-only ephemeral key on macOS to avoid interactive
  Keychain access; leave normal local tests and production credential code intact.
- [ ] Validate workflow syntax, failure propagation and local full regression;
  exercise the hosted workflow when the repair branch is pushed.

## Review focus and verification

Preserve CSV validation and zero/false display; don't evaluate XLSX formulas;
keep packaging exclusions functional; ensure CI failures block job success;
ensure pull requests cannot publish releases or access production secrets.
Docker opt-in and signed app/native credential acceptance remain separate.

## Progress

- Baseline: main a3f314b, clean working tree. Worksheet defect reproduced using
  the real backend response and FileCard. Audit: one advisory affects six build
  dependencies. Existing 440 frontend / 685 backend tests passed (12 skips total).
