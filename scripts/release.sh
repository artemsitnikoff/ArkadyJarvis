#!/usr/bin/env bash
# Релиз ArkadyJarvis: тегирует версию + пушит в Github + пересобирает контейнер.
#
# Usage:
#   ./scripts/release.sh                # подтянет версию из pyproject.toml и тегнет
#   ./scripts/release.sh 4.22.1         # форсит конкретную версию (только тег)
#
# После пуша тэга на сервере:
#   git pull && docker compose up --build -d && docker compose logs -f bot
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  VERSION=$(grep '^version' pyproject.toml | head -1 | sed -E 's/.*"(.+)".*/\1/')
fi
if [[ -z "$VERSION" ]]; then
  echo "❌ Не смог определить версию"
  exit 1
fi

TAG="v${VERSION}"
echo "▶ Релиз ${TAG}"

if ! git diff --quiet HEAD; then
  echo "❌ Есть незакоммиченные изменения — закоммить или стэшни сначала"
  git status --short
  exit 1
fi

# проверим что версия в коде совпадает
PY_VER=$(grep '^__version__' app/version.py | sed -E 's/.*"(.+)".*/\1/')
if [[ "$PY_VER" != "$VERSION" ]]; then
  echo "❌ app/version.py содержит $PY_VER, а тег будет $TAG. Сначала синхронизируй."
  exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "❌ Тег $TAG уже существует"
  exit 1
fi

git tag -a "$TAG" -m "Release $TAG"
echo "✓ Тег создан"

git push origin main
git push origin "$TAG"
echo "✓ Запушено"

echo
echo "Теперь на сервере:"
echo "  cd /opt/arkadyjarvis && git fetch && git checkout $TAG && docker compose up --build -d"
echo "  docker compose logs -f bot"
