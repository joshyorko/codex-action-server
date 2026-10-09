# Repository Guidelines

## Project structure and module organization

This Python package exposes typed MCP/API actions for existing native Codex app-server daemons.

- `src/codex_actions.py` defines action entrypoints and Pydantic request models.
- `src/codex_rpc.py` handles native RPC connections and subscriptions.
- `src/boundary.py` resolves operator-owned logical targets.
- `src/dispatch_receipts.py` persists dispatch acknowledgements and replay protection.
- `tests/` contains Python tests, protocol fixtures, and a native Go parity test.
- `scripts/` contains startup and preflight checks. `config/` and `deploy/` contain configuration and service templates.
- `docs/` covers design, operations, and supervision. `package.yaml` defines the Actions Runtime environment.

## Build, test, and development commands

Run from the repository root on Linux with `action-server` on `PATH`. Use the development tasks in `package.yaml`; Action Server resolves the pinned Python environment and development dependencies through RCC:

```sh
action-server devenv task test
action-server devenv task lint
```

These tasks run tests with skip reasons and match CI lint/format checks. To format changes, run `action-server devenv task prettify`.

For local API startup, follow `README.md` under "Run independently" to configure `CODEX_ACTION_TARGETS`, `CODEX_ACTION_RECEIPTS`, and `CODEX_ACTION_DATA`, then run `bash scripts/run.sh`. Startup requires Actions Runtime 1.0.1 and an existing native daemon.

## Coding style and naming conventions

Use four-space Python indentation and Ruff formatting. Use `snake_case` for modules and functions, `PascalCase` for classes, and uppercase constants. Keep request schemas typed and strict. Preserve explicit native protocol mappings and stable error codes.

## Testing guidelines

Pytest runs both pytest functions and `unittest.TestCase` suites. Name Python test files `test_*.py` and test functions `test_<behavior>`. Add regression tests for changed target validation, RPC lifecycle, or receipt semantics. No numerical coverage threshold is configured.

HTTP/MCP tests use disposable native fixtures and require `action-server` on `PATH`. Report skipped tests explicitly; fixture success does not prove live remote acceptance.

## Commit and pull request guidelines

History uses descriptive imperative subjects, such as "Harden startup and proxy cleanup". Keep commits focused. Include the problem, changed behavior, verification commands, and skipped checks in PR descriptions. Link relevant issues and update affected operational documentation.

## Security and configuration

Keep the API bound to loopback. Never commit credentials, `.env`, or `config/targets.local.json`. Preserve operator-controlled target selection and private, persistent receipt storage. Do not introduce automatic daemon restarts, authentication, or blind dispatch retries.
