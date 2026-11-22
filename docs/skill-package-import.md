# Importing an existing Skill ZIP

This is **Task Manager Skill ZIP schema 1**, not an installer for arbitrary
SKILL.md repositories. The importer preserves user-supplied bytes; the model does
not rewrite the entry point, modules, assets, or SKILL.md. Import creates a draft,
runs the package's independent examples in the existing offline Docker runner,
and leaves approval to the user. Future `run_skill` calls execute only the chosen
approved version on new input, without rerunning its teaching examples.

Required root files: `skill.json`, `SKILL.md` (UTF-8 with YAML frontmatter), and
`main.py` (UTF-8). `skill.json` uses this shape:

```json
{
  "schemaVersion": 1,
  "title": "My existing conversion",
  "kind": "script",
  "entrypoint": "main.py",
  "runtimeFiles": ["app/__init__.py", "app/convert.py", "assets/template.xlsx"],
  "inputs": [{"name": "order.xlsx", "description": "Customer order"}],
  "outputs": [{"name": "result.xlsx", "description": "Company template", "comparison": "workbook"}],
  "examples": [
    {"name": "first", "kind": "success", "inputFiles": {"order.xlsx": "examples/a/input.xlsx"}, "expectedFiles": {"result.xlsx": "examples/a/expected.xlsx"}},
    {"name": "second", "kind": "success", "inputFiles": {"order.xlsx": "examples/b/input.xlsx"}, "expectedFiles": {"result.xlsx": "examples/b/expected.xlsx"}},
    {"name": "missing data", "kind": "failure", "inputFiles": {"order.xlsx": "examples/bad/input.xlsx"}, "expectedError": "Missing required data"}
  ]
}
```

`kind` is `script` or `hybrid`; output comparison is `workbook`, `table`, `text`,
or `bytes`. Examples use the same two-success/one-failure gate as authored skills.
An insufficient set remains a non-approvable draft. Missing-data rejection must
exit 2, write the declared explanation to stderr, and produce no partial output.
Only listed `runtimeFiles` go to `/work/package`, preserving their relative paths.
Examples, expected answers, and package metadata are not mounted as resources.
`main.py` is the original entry point at `/work/main.py`; an adapter may explicitly
add `/work/package` to `sys.path`, read `TM_SKILL_INPUT_DIR`, and write only declared
outputs to `TM_SKILL_OUTPUT_DIR`. Python remains isolated (`-I`), offline, and
without host files or credentials. Dependencies are the preinstalled runtime;
the package cannot choose images, Docker flags, network access or install steps.

Use `service.skills.import_package(file_id, scope, instruction='', parent_id=None,
proposal_id=None, teaching=None)` or RPC `skill:importPackage` with one object:
`{fileId, scope, instruction?, parentId?}`. Scope uses the existing precise customer,
course or personal identity contract. The optional instruction is a separate user
supplement; it cannot overwrite package code. RPC extra fields such as `code` and
`approve` are rejected. Source ZIPs and ancestors must be user imports or mail
attachments, not model-generated files. Existing imported code can be revised
only by importing a new ZIP with the prior version ID, never through model code
authoring.

ZIP checks reuse the existing path/link/duplicate/encryption/expansion limits:
10 MiB compressed, at most 200 members, 50 MiB expanded. Runtime resources allow
at most 128 files, and resources plus per-run input are capped at 24 MiB. Entry
point source follows the existing 64,000-character skill limit. Every original
member is preserved under the immutable version's `imported/` directory;
`SKILL.md` and `main.py` also occupy their normal root slots. Metadata at
`proposal.importedPackage` records source file ID, ZIP SHA-256, each original
member SHA-256, runtime paths and supplement. Existing package-integrity checks
also cover modules and assets before approval/reuse. The model-facing skill
catalog may add its normal application scope instructions in a virtual view;
the stored original SKILL.md is unchanged.

Repeated imports of the same managed ZIP and options return the original review.
A journaled import resumes without running the example code again. An approved
version is not automatically replaced; revisions use the existing review-base
and immutable-version mechanisms.

Validation in this change: 8 import unit tests; 2 added runner resource-boundary
tests; previous skill and runner contract checks; one opt-in real Docker package
test covering original entry point, local module, resource file, three examples,
approval, fresh input and cleanup. The Docker test requires host socket access;
its initial restricted-sandbox attempt could not connect, while the authorized
retry passed. This is infrastructure verification, separate from the original
customer-program acceptance performed by the main test driver.
