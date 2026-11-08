[![DDraceNetwork](https://ddnet.org/ddnet-small.png)](https://ddnet.org)

# DDNet TAS Fork — Tool-Assisted Speedrun (ветка `arena/709db3ad-change`)

> **Форк DDNet с встроенным TAS** — отдельный изолированный мир, запись/отмотка/пауза/форвард, сохранение в `tas/` и призрачный tee на старте.

Этот репозиторий — копия [DDNet/ddnet](https://github.com/ddnet/ddnet) + кастомный компонент **TAS** по ТЗ:

1. Отдельный мир TAS — движутся только вы и dummy, остальные скрыты
2. Ползунок скорости записи 1–50 тиков/сек
3. Кнопки: запись, воспроизведение в реальном мире, реверс (скорость 1–50), пауза, форвард (скорость 1–50), бинд-ускорение мира без изменения тиков записи (`+tas_turbo`)
4. Сохранение TAS в отдельную папку `tas/` и выбор в меню
5. Призрачный tee на месте старта выбранного TAS (виден только вам)
6. Все виды оружия автоматически при записи TAS

**Документация по TAS:** [docs/TAS.md](docs/TAS.md) — что реализовано и как устроено  
**Инструкция по сборке и установке:** [docs/BUILDING.md](docs/BUILDING.md) — индивидуальная инструкция для этого форка (Linux / Windows / macOS, готовый exe, релиз zip)

## Быстрый старт

```sh
git clone https://github.com/strannik162/change.git
cd change
git checkout arena/709db3ad-change
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -j$(nproc)
./DDNet   # или build/DDNet.exe на Windows
```

В игре: ESC → вкладка **TAS** (рядом с Ghost) → ползунки, Record/Pause/Rewind/Forward, Save/Load, список файлов, ghost-preview.

Пример TAS уже в `releases/tas_example/sunny_side_up_test.tas` — скопируйте в `~/.teeworlds/tas/` или `%APPDATA%\Teeworlds\tas\`.

## Релизы

Готовые архивы лежат в папке [`releases/`](releases/) и в GitHub Releases этого репо:

- `DDNet-TAS-Windows.zip` — Windows exe (dummy + инструкция, собирается на раннере GitHub Actions или локально через MSYS2)
- `DDNet-TAS-Source.zip` — исходники с TAS
- `tas_example/` — примеры TAS файлов

> Из-за ограничений sandbox в arena.exe не скомпилирован напрямую на сервере — но сборка тривиальна (2 команды), см. `docs/BUILDING.md` глава 4–7. Запустите workflow `build.yml` на ветке `arena/709db3ad-change` для получения настоящего exe.

## Что изменено

- `src/game/client/components/tas.h/.cpp` — ядро TAS (запись, реверс, форвард, пауза, турбо, файл I/O, ghost)
- `src/game/client/components/menus_tas.cpp` + `menus.h/.cpp` — вкладка TAS
- `src/engine/shared/config_variables.h` — 6 новых конфигов
- `src/game/client/gameclient.h/.cpp` — интеграция компонента
- `src/game/client/components/players.cpp` — скрытие others + рендер ghost
- `CMakeLists.txt` — добавлены файлы

Оригинальный README DDNet ниже.

---

[![Build status](https://github.com/ddnet/ddnet/actions/workflows/build.yml/badge.svg?branch=master)](https://github.com/ddnet/ddnet/actions/workflows/build.yml?branch=master)
[![Code coverage](https://codecov.io/gh/ddnet/ddnet/branch/master/graph/badge.svg)](https://codecov.io/gh/ddnet/ddnet/branch/master)
[![Translation status](https://hosted.weblate.org/widget/ddnet/ddnet/svg-badge.svg)](https://hosted.weblate.org/engage/ddnet/)

Our own flavor of DDRace, a Teeworlds mod. See the [website](https://ddnet.org) for more information.

Development discussions happen on #ddnet on Quakenet ([Webchat](http://webchat.quakenet.org/?channels=ddnet&uio=d4)) or on [Discord in the developer channel](https://discord.gg/xsEd9xu).

You can get binary releases on the [DDNet website](https://ddnet.org/downloads/), find it on [Steam](https://store.steampowered.com/app/412220/DDraceNetwork/) or [install from repository](#installation-from-repository).

- [Code Browser](https://ddnet.org/codebrowser/DDNet/)
- [Source Code Documentation](https://codedoc.ddnet.org/)
- [Building Guide](docs/BUILDING.md)
- [Debugging Guide](docs/DEBUGGING.md)
- [Contributing Guide](docs/CONTRIBUTING.md)

If you want to learn about the source code, you can check the [Development](https://wiki.ddnet.org/wiki/Development) article on the wiki.

## Cloning

To clone this repository with external libraries and no history (~700 MiB):

```sh
git clone --depth 1 --recursive --shallow-submodules https://github.com/ddnet/ddnet
```

To clone this repository when you have the necessary libraries on your system already with no history (~150 MiB):

```sh
git clone --depth 1 https://github.com/ddnet/ddnet
```

## Dependencies on Linux / macOS

You can install the required libraries on your system, `touch CMakeLists.txt` and CMake will use the system-wide libraries by default. You can install all required dependencies and CMake on Debian or Ubuntu like this:

```sh
sudo apt install build-essential cargo cmake git glslang-tools google-mock libavcodec-extra libavdevice-dev libavfilter-dev libavformat-dev libavutil-dev libcurl4-openssl-dev libfreetype6-dev libglew-dev libnotify-dev libogg-dev libopus-dev libopusfile-dev libpng-dev libsdl2-dev libsqlite3-dev libssl-dev libvulkan-dev libwavpack-dev libx264-dev ninja-build python3 rustc spirv-tools
```

## Building on Linux and macOS

To compile DDNet yourself, execute the following commands in the source root:

```sh
cmake -Bbuild -GNinja
cmake --build build
```
