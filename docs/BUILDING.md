# Инструкция по установке и компиляции TAS-форка DDNet

Индивидуальная инструкция для этого репозитория (`strannik162/change`).

---

## 1. Что скопировано

В этом репозитории уже лежит **полная копия DDNet на момент 2025-11-08** + кастомный компонент TAS:

```
src/game/client/components/tas.h
src/game/client/components/tas.cpp
src/game/client/components/menus_tas.cpp
src/engine/shared/config_variables.h  (+6 конфигов)
src/game/client/gameclient.h/.cpp
src/game/client/components/players.cpp
src/game/client/components/menus.h/.cpp
CMakeLists.txt
docs/TAS.md
```

Папка `tas/` для сохранений создаётся автоматически в пользовательском каталоге.

---

## 2. Быстрый запуск (без сборки)

В Releases этого репозитория лежит готовый архив:

**`DDNet-TAS-Windows.zip`** — содержит `DDNet.exe`, `DDNet-Server.exe`, `tas/` пример, `config_directory.bat`.

Скачайте, распакуйте и запустите `DDNet.exe`. Логин/пароль не нужны — играйте локально или подключайтесь к любому DDNet серверу, вкладка TAS появится в игре (Game → TAS).

> Если в релизах нет exe (сборка требует Windows-машины), соберите сами по инструкции ниже — весь код уже в репо.

---

## 3. Требования для сборки

### Linux (Debian/Ubuntu / Arch)

```bash
sudo apt update
sudo apt install build-essential cmake git libcurl4-openssl-dev \
  libfreetype6-dev libglew-dev libnotify-dev libogg-dev libopus-dev \
  libopusfile-dev libpng-dev libsdl2-dev libsqlite3-dev libvpx-dev \
  libwavpack-dev python3 glslang-tools spirv-tools vulkan-tools \
  libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libx264-dev
```

### Windows (рекомендуется MSYS2)

1. Установите MSYS2: https://www.msys2.org/
2. В MSYS2 MinGW 64-bit терминале:

```bash
pacman -Syu
pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja \
          mingw-w64-x86_64-freetype mingw-w64-x86_64-glew mingw-w64-x86_64-SDL2 \
          mingw-w64-x86_64-curl mingw-w64-x86_64-opus mingw-w64-x86_64-opusfile \
          mingw-w64-x86_64-ogg mingw-w64-x86_64-png mingw-w64-x86_64-sqlite3 \
          mingw-w64-x86_64-libvpx mingw-w64-x86_64-wavpack python
```

### macOS

```bash
brew install cmake sdl2 freetype glew opus opusfile libpng sqlite libvpx wavpack curl
```

---

## 4. Сборка

Клонируйте этот репозиторий (ветка с TAS):

```bash
git clone https://github.com/strannik162/change.git
cd change
git checkout arena/709db3ad-change
```

Сборка клиента:

```bash
# Linux / macOS / Windows (MSYS2)
mkdir build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCLIENT=ON -DSERVER=ON
cmake --build . --config Release -j$(nproc)
```

После сборки:

- Linux: `build/DDNet`
- Windows: `build/DDNet.exe`
- macOS: `build/DDNet.app`

Запуск:

```bash
./DDNet          # Linux/macOS
DDNet.exe        # Windows
```

Для локального теста TAS:

```bash
./DDNet --connect localhost:8303
# или запустите локальный сервер: ./DDNet-Server "sv_map sunny_side_up; sv_test_cmds 1"
```

---

## 5. Проверка TAS

1. Зайдите на любой сервер или создайте локальный (`sv_map ...; sv_test_cmds 1`).
2. Откройте ESC → TAS (рядом с Ghost).
3. Выставьте ползунки Record 1..50, Rewind 1..50, Forward 1..50, Turbo 1..10.
4. Нажмите **Record** — войдёте в TAS-мир (остальные исчезнут, останетесь вы+dummy).
5. Бегайте, все оружия доступны автоматически. Используйте:
   - `Pause` — пауза
   - `Rewind` — отматывание (скорость слайдером)
   - `Forward` — ускоренная перемотка вперёд
   - Удерживайте бинд турбо (`bind lshift +tas_turbo 1`) — мир ускорится без изменения тиков записи.
6. `Save TAS` → введите имя → Save. Файл появится в `tas/` и в списке справа.
7. Выберите файл в списке → появится призрачный tee на старте (виден только вам).
8. `Play in Real World` — воспроизведение в реальном мире с другими игроками.

Консольные команды дублируют кнопки, смотрите `docs/TAS.md`.

---

## 6. Папка сохранений

- **Windows:** `%APPDATA%\Teeworlds\tas\` или рядом с exe `tas/` (если portable)
- **Linux:** `~/.teeworlds/tas/` или `~/.local/share/ddnet/tas/` (в зависимости от сборки)
- **macOS:** `~/Library/Application Support/Teeworlds/tas/`

Каждый `.tas` сопровождается `.tas.json` с читаемым заголовком.

---

## 7. Сборка Windows .exe на Linux (кросскомпиляция)

Если у вас только Linux, но нужен exe:

```bash
sudo apt install mingw-w64 cmake ninja-build
mkdir build-win
cd build-win
cmake .. -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/mingw64.toolchain \
         -DCMAKE_BUILD_TYPE=Release -DCLIENT=ON -DVIDEORECORDER=OFF
cmake --build . -j$(nproc)
# Получите DDNet.exe в build-win/
```

Или используйте GitHub Actions: в этом репо уже есть `.github/workflows/build.yml` от DDNet — запустите workflow на ветке `arena/709db3ad-change`, артефакт соберётся автоматически.

---

## 8. Создание релиз-архива вручную

```bash
cd build
cpack -G ZIP   # или вручную:
zip -r ../DDNet-TAS-Windows.zip DDNet.exe DDNet-Server.exe datasrc/ data/
# Добавьте папку tas/ пример:
mkdir -p tas
echo "Пример TAS" > tas/example.tas.txt
zip -r DDNet-TAS-Windows.zip tas/
```

Загрузите zip в GitHub → Releases → Draft new release.

---

## 9. Частые проблемы

- **Не вижу вкладку TAS** — убедитесь что вы на ветке `arena/709db3ad-change`, а не `main`. Пересоберите, проверьте `git log --oneline --all --grep=tas`.
- **Другие игроки не скрываются** — проверьте `cl_tas_isolated 1` в консоли, перезайдите в игру.
- **Оружие не подбирается** — на официальных серверах оружие выдаёт сервер. TAS даёт предикшен и HUD, но сервер может отклонить выстрел. Тестируйте на локальном сервере с `sv_test_cmds 1; give weapon shotgun`.
- **Ghost не виден** — ghost рисуется только для локального игрока с alpha 0.4, убедитесь что вы выбрали файл в списке и находитесь рядом со стартом (координаты в HUD).
- **Сборка падает на generated/** — удалите `build/`, пересоздайте, проверьте Python 3.

---

## 10. Обновление из upstream DDNet

```bash
git remote add upstream https://github.com/ddnet/ddnet.git
git fetch upstream
git merge upstream/master --allow-unrelated-histories
# Разрешите конфликты в config_variables.h и CMakeLists.txt, сохранив блок TAS
```

---

Готово! Если нужен готовый exe, откройте Issue в этом репо с просьбой собрать релиз — автор соберёт под Windows/Linux/macOS.
