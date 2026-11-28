#!/usr/bin/env bash
# build-ucrt64.sh — сборка FrameTee под Windows в MSYS2 UCRT64 одной командой.
#
# Запуск (в терминале «MSYS2 UCRT64», из корня дерева с исходниками):
#   ./scripts/build-ucrt64.sh
#
# Полезные переменные окружения:
#   SRC=/c/dev/frametee      каталог с исходниками (по умолчанию — каталог, откуда запущен скрипт)
#   REPO=<url>               откуда клонировать, если исходников ещё нет
#   BUILD=$SRC/build         каталог сборки
#   PATCH=/path/to/x.patch   применить только этот патч
#   NO_PATCH=1               не применять патчи вовсе (чистая сборка апстрима)
#   DEPS=0                   не ставить пакеты pacman'ом
#   TMUF=OFF                 не собирать модуль TrackMania
#   FFMPEG=0                 не требовать FFmpeg (MP4-экспорт отключится)
#   INSTALL=1                собрать переносимую папку dist/ (exe + DLL)
#   JOBS=8                   число параллельных задач
set -euo pipefail

die() { printf '\n[ошибка] %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# Каталог этого репозитория (в нём лежат scripts/ и patches/):
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Windows-путь (C:\dev\frametee) → MSYS-путь (/c/dev/frametee)
winpath() { case "$1" in [A-Za-z]:[\\/]*) cygpath -u "$1" ;; *) printf '%s' "$1" ;; esac; }

SRC="$(winpath "${SRC:-$PWD}")"
REPO="${REPO:-https://github.com/Teero888/frametee.git}"
BUILD="$(winpath "${BUILD:-$SRC/build}")"
DEPS="${DEPS:-1}"
TMUF="${TMUF:-ON}"
FFMPEG="${FFMPEG:-1}"
INSTALL="${INSTALL:-0}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 8)}"
CMAKE_GEN="${CMAKE_GEN:-Ninja}"

step "0/6 окружение"
[ "${MSYSTEM:-}" = "UCRT64" ] || die "нужен терминал «MSYS2 UCRT64» (сейчас MSYSTEM='${MSYSTEM:-<пусто>}').
    Открой ярлык «MSYS2 UCRT64» (C:\\msys64\\ucrt64.exe) и запусти скрипт там."
note "MSYSTEM=$MSYSTEM  gcc=$(command -v gcc || true)"
note "исходники: $SRC"
note "сборка:    $BUILD"
for tool in gcc g++ cmake git; do command -v "$tool" >/dev/null || die "нет $tool — см. шаг 1 (DEPS=1) или поставь вручную"; done
[ "$CMAKE_GEN" = "Ninja" ] && { command -v ninja >/dev/null || die "нет ninja: pacman -S mingw-w64-ucrt-x86_64-ninja (или CMAKE_GEN='MinGW Makefiles')"; }
command -v glslangValidator >/dev/null || die "нет glslangValidator (обязателен на этапе конфигурации): pacman -S mingw-w64-ucrt-x86_64-glslang"

if [ "$DEPS" = "1" ]; then
  step "1/6 пакеты"
  PKGS=(git mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-cmake
        mingw-w64-ucrt-x86_64-ninja mingw-w64-ucrt-x86_64-pkgconf
        mingw-w64-ucrt-x86_64-vulkan-headers mingw-w64-ucrt-x86_64-vulkan-loader
        mingw-w64-ucrt-x86_64-glslang mingw-w64-ucrt-x86_64-curl
        mingw-w64-ucrt-x86_64-freetype)
  [ "$FFMPEG" = "1" ] && PKGS+=(mingw-w64-ucrt-x86_64-ffmpeg)
  note "pacman -S --needed --noconfirm ${PKGS[*]}"
  if ! pacman -S --needed --noconfirm "${PKGS[@]}"; then
    die "pacman не смог поставить пакеты: обычно помогает 'pacman -Syu', затем перезапустить терминал и запустить скрипт снова"
  fi
else
  step "1/6 пакеты — пропущено (DEPS=0)"
fi

step "2/6 исходники"
# У вложенного сабмодуля (libs/ddnet_demo/ddnet_maploader) адрес задан как
# git@github.com:... — без SSH-ключей такой clone падает. Подменяем SSH на https
# только на время этой команды, глобальный git config не трогаем.
GIT_URL_FIX=(-c url."https://github.com/".insteadOf="git@github.com:")
if [ -f "$SRC/CMakeLists.txt" ]; then
  note "дерево уже есть: $SRC"
  SUBMODULES=""
  [ -d "$SRC/.git" ] && SUBMODULES="$(git -C "$SRC" submodule status --recursive 2>/dev/null || true)"
  if [ -z "$SUBMODULES" ]; then
    note "в дереве нет зарегистрированных сабмодулей"
  elif printf '%s\n' "$SUBMODULES" | grep -q '^-'; then
    note "подтягиваю сабмодули (git@github.com → https)…"
    git "${GIT_URL_FIX[@]}" -C "$SRC" submodule update --init --recursive ||
      die "сабмодули не скачались. Попробуй ещё раз, либо:
    git -c url.\"https://github.com/\".insteadOf=\"git@github.com:\" -C \"$SRC\" submodule update --init --recursive"
  else
    note "сабмодули на месте"
  fi
else
  note "клонирую $REPO → $SRC"
  mkdir -p "$(dirname "$SRC")"
  git "${GIT_URL_FIX[@]}" clone --recursive "$REPO" "$SRC" ||
    die "клон не удался (нет сети/доступа к github.com?)"
fi
[ -f "$SRC/libs/cimgui/cimgui.h" ] || die "нет сабмодулей: $SRC/libs/cimgui пуст или отсутствует.
    Так выглядит зеркало без сабмодулей — собирай полный клон в стороне:
      git -c url.\"https://github.com/\".insteadOf=\"git@github.com:\" clone --recursive $REPO /c/dev/frametee
      SRC=/c/dev/frametee ./scripts/build-ucrt64.sh
    (патчи из patches/ этого репозитория подхватятся сами)
    Если дерево своё и просто без сабмодулей:
      git -c url.\"https://github.com/\".insteadOf=\"git@github.com:\" -C \"$SRC\" submodule update --init --recursive"

step "3/6 патчи"
PATCHES=()
if [ "${NO_PATCH:-0}" = "1" ]; then
  :
elif [ -n "${PATCH:-}" ]; then
  PATCHES=("$(winpath "$PATCH")")
else
  # Патчи берём и из дерева исходников, и из этого репозитория: так скрипт
  # работает, когда рядом с ним лежит зеркало, а сборка идёт в отдельном клоне.
  shopt -s nullglob
  for cand in "$SRC"/patches/*.patch "$SCRIPT_ROOT"/patches/*.patch; do
    [ -f "$cand" ] || continue
    skip=0
    for have in ${PATCHES+"${PATCHES[@]}"}; do
      [ "$(basename "$have")" = "$(basename "$cand")" ] && skip=1 && break
    done
    [ "$skip" = 1 ] || PATCHES+=("$cand")
  done
  shopt -u nullglob
fi
if [ ${#PATCHES[@]} -eq 0 ]; then
  note "патчей нет — собираю как есть"
else
  for p in "${PATCHES[@]}"; do
    if git -C "$SRC" apply --reverse --check "$p" 2>/dev/null; then
      note "уже применён: $(basename "$p")"
    elif git -C "$SRC" apply "$p"; then
      note "применён: $(basename "$p")"
    else
      die "не удалось применить $p — дерево изменено? (git -C \"$SRC\" status)"
    fi
  done
fi

step "4/6 конфигурация CMake"
REQ_VIDEO=OFF; [ "$FFMPEG" = "1" ] && REQ_VIDEO=ON
note "генератор: $CMAKE_GEN, компилятор: gcc/g++"
cmake -S "$SRC" -B "$BUILD" -G "$CMAKE_GEN" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
  -DBUILD_GAMES=ON -DFRAMETEE_BUILD_TMUF="$TMUF" -DFRAMETEE_BUILD_SM64=OFF \
  -DFRAMETEE_BUILD_CUBE_EXAMPLE=OFF -DFRAMETEE_BUILD_PLATFORMER_EXAMPLE=OFF -DFRAMETEE_BUILD_RUST_EXAMPLE=OFF \
  -DFRAMETEE_REQUIRE_VIDEO_EXPORT="$REQ_VIDEO"

step "5/6 сборка (−j$JOBS)"
cmake --build "$BUILD" --parallel "$JOBS"

EXE="$BUILD/frametee.exe"
[ -f "$EXE" ] || die "сборка прошла, но нет $EXE — смотри лог выше"

if [ "$INSTALL" = "1" ]; then
  step "6/6 переносимая папка"
  DIST="$BUILD/dist"
  cmake --install "$BUILD" --prefix "$DIST" --component Runtime --strip
  cp -f /ucrt64/bin/vulkan-1.dll "$DIST"/
  for dll in $(ldd "$DIST/frametee.exe" "$DIST"/games/*.dll 2>/dev/null | awk '$3 ~ /^\/ucrt64\/bin\/.*\.dll$/ {print $3}' | sort -u); do
    cp -f "$dll" "$DIST"/ || true
  done
  ldd "$DIST/frametee.exe" | grep -q "not found" && die "в $DIST не хватает DLL (см. ldd)"
  note "готово: $DIST/frametee.exe"
else
  step "6/6 переносимая папка — пропущено (INSTALL=1, чтобы собрать dist/)"
fi

printf '\n[готово] %s\n' "$EXE"
printf 'Запуск — из каталога сборки: движок открывает data/shaders/*.spv относительно cwd,\n'
printf 'иначе в логе будут ошибки "Failed to open file: data/shaders/...":\n'
printf '  cd "%s" && ./frametee.exe\n' "$BUILD"
printf 'Из Проводника нужен C:\\msys64\\ucrt64\\bin в PATH, либо сборка с INSTALL=1.\n'
