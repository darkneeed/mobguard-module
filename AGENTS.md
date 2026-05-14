# Module Agent Guide

This repository is the collector side of MobGuard.

Start here:

1. Read `../ai-docs/README.md`
2. Read `../ai-docs/architecture.md`
3. Read `../ai-docs/workflows.md`
4. Read `../ai-docs/change-map.md`
5. Read local `README.md`

## Scope

- This repo owns access-log collection, local spool/state handling, and HTTP communication with the panel.
- The panel lives in sibling repo `../panel/`.
- If you change protocol payloads or remote config shape, inspect and usually update `../panel/` too.

## Primary Code Paths

- `mobguard_module/main.py`
- `mobguard_module/config.py`
- `mobguard_module/collector.py`
- `mobguard_module/state.py`
- `mobguard_module/protocol.py`
- `tests/`

## Do Not Treat As Source

- `state/`
- `runtime-logs/`
- `.env`
- `.env.local.dev`
- `__pycache__/`
- `.pytest_cache/`

## Verification

Run from this directory:

```bash
pytest -q
```

Useful local flows:

- Windows: `.\start-local-dev.ps1`, `.\stop-local-dev.ps1`
- Linux/macOS: `./start-local-dev.sh`, `./stop-local-dev.sh`

## Notes

- The workspace root `mobguard/` is not a git repository.
- Run git commands inside this repo, not from the workspace root.
- Keep the module thin. Do not move panel business logic into the collector.
