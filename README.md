# MobGuard Module

Collector-only node for the split MobGuard architecture.

## What this repo contains

- `mobguard_module/collector.py` — access log reader and event extraction
- `mobguard_module/state.py` — cursor, spool, and cached config persistence
- `mobguard_module/protocol.py` — HTTP client for `register`, `heartbeat`, `config`, and `events/batch`
- `mobguard_module/main.py` — long-running collector loop

This repo is the **module** only. Panel code lives in the separate `panel/` repo.

## Clone

```bash
git clone <module-repo-url> module
cd module
```

## Required `.env` keys

- `PANEL_BASE_URL`
- `MODULE_ID`
- `MODULE_TOKEN`
- `ACCESS_LOG_PATH`

## Build

Windows:

```powershell
.\build.ps1
```

Linux/macOS:

```bash
./build.sh
```

What the build script does:

1. creates `.env` from `.env.example` if needed
2. validates required env keys are present
3. ensures `state/` and `state/spool/` exist
4. runs `docker compose build`
5. runs a short config smoke-check

## Run

```bash
python mobguard-module.py
```

or with Docker:

```bash
docker compose up -d
```
# mobguard-module
