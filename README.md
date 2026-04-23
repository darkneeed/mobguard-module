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

## Primary install flow

1. Create the module card in the panel.
2. Copy the generated `docker-compose.yml` from the install bundle.
3. Reveal the module token in the panel and replace `MODULE_TOKEN=__PASTE_TOKEN__`.
4. Run:

```bash
docker compose up -d && docker compose logs -f -t
```

The panel-generated compose file is the canonical install path.

## Remote config contract

The module keeps protocol `v1` and accepts remote config from the panel without changing the HTTP surface.

Remote config precedence for tags is:

1. `rules.inbound_tags`
2. fallback to `rules.mobile_tags`

Runtime controls are still delivered through `module_runtime`:

- `heartbeat_interval_seconds`
- `config_poll_interval_seconds`
- `flush_interval_seconds`
- `event_batch_size`
- `max_spool_events`

## Spool and retry guarantees

- Buffered events are still persisted as JSONL on disk.
- Spool writes are append-only in the steady state.
- Spool depth is tracked via metadata and does not require a full file scan.
- Dropped or acknowledged items advance the logical spool head and are compacted opportunistically.
- `register`, `heartbeat`, `fetch_config`, and `events/batch` retry automatically on transient transport and `429/502/503/504` panel errors.
- Event uploads remain safe to retry because the panel deduplicates by `event_uid`.

## Device awareness

- The current collector extracts IP, inbound tag, and identity fields from the access log.
- It does not derive `client_device_id` from Xray access logs yet, so the updated panel will usually work in `ip-only` fallback mode.
- If upstream log format starts exposing stable device identifiers, the module should be extended to pass `client_device_*` fields so the panel can use full `IP + device` scope.

## Local `.env` fallback

If you want to build or run the module manually without the panel-generated compose file, keep using `.env`.

Required keys:

- `PANEL_BASE_URL`
- `MODULE_ID`
- `MODULE_TOKEN`
- `ACCESS_LOG_PATH`

## Test

```bash
pytest -q
```

## Build

Windows:

```powershell
.uild.ps1
```

Linux/macOS:

```bash
./build.sh
```

What the build script does:

1. creates `.env` from `.env.example` if needed
2. validates required env keys are present for the fallback flow
3. ensures `state/` and `state/spool/` exist
4. runs `docker compose build`
5. runs a short smoke-check inside the built `mobguard-module` container

## Run

```bash
python mobguard-module.py
```

or with Docker:

```bash
docker compose up -d
```
