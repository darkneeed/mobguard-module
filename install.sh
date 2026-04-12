#!/usr/bin/env sh
set -eu

if [ ! -f .env ]; then
  cp .env.example .env
  printf '%s\n' "[INFO] Created .env from .env.example"
fi

mkdir -p state/spool
printf '%s\n' "[INFO] mobguard-module state directories are ready"
