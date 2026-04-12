#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
  printf '%s\n' "[INFO] Created .env from .env.example"
fi

missing=""
for key in PANEL_BASE_URL MODULE_ID MODULE_TOKEN ACCESS_LOG_PATH; do
  if ! grep -q "^${key}=" ".env"; then
    missing="${missing} ${key}"
  fi
done
missing=$(printf '%s' "$missing" | xargs)
if [ -n "$missing" ]; then
  printf '%s\n' "[ERROR] Missing required .env keys: $missing" >&2
  exit 1
fi

mkdir -p state state/spool

command -v docker >/dev/null 2>&1 || { printf '%s\n' "[ERROR] docker not found" >&2; exit 1; }
command -v python >/dev/null 2>&1 || { printf '%s\n' "[ERROR] python not found" >&2; exit 1; }

docker compose build
python - <<'PY'
from mobguard_module.config import ModuleConfig
cfg = ModuleConfig.from_env('.env')
assert cfg.panel_base_url
assert cfg.module_id
assert cfg.module_token
assert cfg.access_log_path
print(cfg.module_id)
PY

printf '%s\n' "[OK] Module build and smoke-check passed"
