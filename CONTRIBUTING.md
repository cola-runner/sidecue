# Contributing

## Development

Follow the [README](README.md) for dependencies and launch commands. Use a virtual environment and keep personal preferences in `config.local.toml`. Do not put machine-specific paths in the public configuration.

Use `--preview-ui` for interface work. Automated tests must use fictional input and mocks, without microphone permission prompts or real model requests.

Before submitting changes:

```sh
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python scripts/check_public_tree.py --history
git diff --check
git status --short
```

For native UI changes, also run `RUN_GUI_TESTS=1` and check minimum/default window sizes, long text, and system appearance. Launcher changes require the real bundle tests:

```sh
# Close the app before running these tests.
RUN_GUI_TESTS=1 RUN_BUNDLE_TESTS=1 ./.venv/bin/python -m unittest discover -s tests -v
```

## Scope

The initial public history was reconstructed into subsystem-based batches from the development snapshot. Earlier commit dates retain development milestones; they do not indicate that these exact snapshots were previously released.

Keep UI, speech, retrieval, and generation responsibilities separate. Input and generation must not block each other. Shutdown must release resources and child processes created by the application.

Keep project copy, documentation, and synthetic examples in English. Supporting input in other languages does not require translating the interface. Never describe preview or mock results as successful real speech-to-cue acceptance tests.

## Privacy and Attribution

Commit only necessary source code and fictional examples. Do not force-add private notes, logs, credentials, or build artifacts. Pattern scans supplement, but do not replace, manual review of diffs and attachments.

Preserve third-party licenses and attribution. Record the source and license of new dependencies and assets. Do not include system crash reports or authentication diagnostics without redaction.
