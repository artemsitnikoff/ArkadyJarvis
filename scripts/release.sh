#!/usr/bin/env bash
# Релиз ArkadyJarvis: проверяет синк версии и пушит main.
#
# На сервере:
#   git pull && docker compose up --build -d && docker compose logs -f bot
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep '^version' pyproject.toml | head -1 | sed -E 's/.*"(.+)".*/\1/')
PY_VER=$(grep '^__version__' app/version.py | sed -E 's/.*"(.+)".*/\1/')
if [[ "$PY_VER" != "$VERSION" ]]; then
  echo "❌ pyproject.toml = $VERSION, app/version.py = $PY_VER — синхронизируй сначала"
  exit 1
fi

echo "▶ Релиз v${VERSION} → main"
git push origin main
echo "✓ Запушено"
echo
echo "На сервере:"
echo "  cd /opt/arkadyjarvis && git pull && docker compose up --build -d"
echo "  docker compose logs -f bot"
