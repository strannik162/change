# Сборка FrameTee под Windows: MSYS2 UCRT64

Инструкция для MinGW-w64 из MSYS2 (окружение **UCRT64**). MSVC проект не
поддерживает — в README прямо сказано «use mingw». Команды ниже повторяют то,
чем собирается официальный Windows-релиз в
`.github/workflows/build.yml` (`build-windows`).

## Быстрый старт (одна команда)

В дереве с исходниками (или в этой ветке) лежит `scripts/build-ucrt64.sh` — он
ставит пакеты, клонирует/обновляет сабмодули, применяет патч, конфигурирует и
собирает:

```bash
# в терминале «MSYS2 UCRT64», из корня дерева
./scripts/build-ucrt64.sh                 # DEPS=1, TMUF=ON, FFmpeg обязателен
INSTALL=1 ./scripts/build-ucrt64.sh       # ещё и переносимая папка build/dist
TMUF=OFF FFMPEG=0 ./scripts/build-ucrt64.sh
```

То же из Проводника двойным щелчком: `scripts\build-ucrt64.cmd`.

Дальше — что этот скрипт делает, по шагам (если хочется руками или что-то упало).

## 0. Что нужно заранее

* Windows 10/11 x64, видеокарта с рабочим Vulkan-драйвером.
* ~3 ГБ на MSYS2 + пакеты и ~2 ГБ на сборку.
* Интернет при первой конфигурации CMake: на Windows GLFW 3.4 скачивается
  автоматически (`FetchContent`, `CMakeLists.txt`), плюс нужны сабмодули с GitHub.

## 1. Установка MSYS2 и правильный терминал

1. Поставить MSYS2 с <https://www.msys2.org/> (по умолчанию `C:\msys64`).
2. Открывать **только** ярлык **«MSYS2 UCRT64»** (или `C:\msys64\ucrt64.exe`).

   > Не `MSYS2 MSYS`, не `MINGW64`, не `CLANG64`. В шелле MSYS компилятор
   > линкуется с `msys-2.0.dll` — такая сборка тормозит и не видит Vulkan;
   > путь к `C:\msys64\usr\bin` в `PATH` тоже лучше не держать.

Проверка, что вы в нужном окружении:

```bash
echo $MSYSTEM      # UCRT64
which gcc          # /ucrt64/bin/gcc
```

## 2. Обновление пакетов

```bash
pacman -Syu
```

Если `pacman` попросит закрыть терминал (обновлялся `msys2-runtime`) — закрыть,
открыть «MSYS2 UCRT64» снова и повторить `pacman -Syu`.

## 3. Пакеты

```bash
pacman -S --needed --noconfirm \
  git \
  mingw-w64-ucrt-x86_64-toolchain \
  mingw-w64-ucrt-x86_64-cmake \
  mingw-w64-ucrt-x86_64-ninja \
  mingw-w64-ucrt-x86_64-pkgconf \
  mingw-w64-ucrt-x86_64-vulkan-headers \
  mingw-w64-ucrt-x86_64-vulkan-loader \
  mingw-w64-ucrt-x86_64-glslang \
  mingw-w64-ucrt-x86_64-curl \
  mingw-w64-ucrt-x86_64-freetype \
  mingw-w64-ucrt-x86_64-ffmpeg
```

То же самое в нотации `pacboy` (как в CI; `:p` = текущее окружение, здесь UCRT64):

```bash
pacboy -S toolchain:p cmake:p ninja:p pkgconf:p glslang:p vulkan-headers:p \
          vulkan-loader:p curl:p freetype:p ffmpeg:p
```

Зачем каждый:

| Пакет | Зачем |
| --- | --- |
| `toolchain` | gcc/g++/binutils/make — компилятор |
| `cmake`, `ninja` | система сборки |
| `pkgconf` | поиск FFmpeg через `pkg-config` (MP4-экспорт) |
| `vulkan-headers`, `vulkan-loader` | `find_package(Vulkan REQUIRED)` + `vulkan-1.dll` в рантайме |
| `glslang` | **обязателен**: `glslangValidator` нужен на этапе конфигурации для компиляции шейдеров |
| `curl`, `freetype` | нужны игровому модулю DDNet (`games/ddnet/CMakeLists.txt`) |
| `ffmpeg` | опционально: вкладка Render / экспорт MP4. Без него сборка проходит, MP4 отключается |

## 4. Исходники

```bash
cd /c/dev
git clone --recursive https://github.com/Teero888/frametee.git
cd frametee
```

`--recursive` важен: `libs/cimgui` тянет собственный сабмодуль `imgui`, без него
CMake упадёт на `cimgui.h: No such file or directory`.

> **Грабли с сабмодулями.** У вложенного сабмодуля
> `libs/ddnet_demo/ddnet_maploader` адрес прописан как
> `git@github.com:Teero888/ddnet_maploader.git`, то есть через SSH. Без
> SSH-ключей на GitHub клон падает с `Connection reset by peer` /
> `Permission denied (publickey)`. Лечится подменой SSH на https **на одну
> команду** (глобальный конфиг не трогаем):
>
> ```bash
> git -c url."https://github.com/".insteadOf="git@github.com:" clone --recursive https://github.com/Teero888/frametee.git
> # в существующем клоне:
> git -c url."https://github.com/".insteadOf="git@github.com:" submodule update --init --recursive
> ```
>
> `scripts/build-ucrt64.sh` делает это сам.

Если репозиторий уже склонирован без сабмодулей:

```bash
git submodule update --init --recursive
```

Сабмодули: `cimgui`, `cglm`, `nfd`, `toml`, `ddnet_physics`, `ddnet_demo`,
`ddnet_ghost`, `tmuf_physics`, `sm64_physics` (последний — 52 МБ, нужен только
для SM64).

### Применить правку с перемоткой

```bash
git apply /путь/к/patches/0001-timeline-rewind-cuts-recording.patch
# либо с сохранением авторства и сообщения коммита:
git am /путь/к/patches/0001-timeline-rewind-cuts-recording.patch
```

Патч трогает один файл — `src/user_interface/timeline/timeline_interaction.c`.

## 5. Конфигурация

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
```

Что стоит знать:

* **Компилятор.** `CMakeLists.txt` сам предпочитает `clang`, если находит его в
  `PATH`. Если хотите строго gcc (как в релизе), задайте явно:
  `-DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++`.
* **GLFW.** На Windows всегда собирается скачанный 3.4 (со патчем цикла событий
  из `cmake/glfw/`). В логе будет `Fetching GLFW 3.4` — это нормально, нужен
  интернет. При первом конфигурировании может занять минуту.

  Патч не косметический: движок на Windows ссылается на символ
  `frametee_glfw_win32_mouse_motion_patch` (`src/renderer/graphics_backend.c`),
  поэтому GLFW **без** этого патча просто не слинкуется
  (`undefined reference to frametee_glfw_win32_mouse_motion_patch`). Если
  `github.com`/`objects.githubusercontent.com` недоступны, скачайте
  `glfw-3.4.zip` вручную, распакуйте, прогоните патч и подставьте каталог:

  ```bash
  cd /c/dev/glfw-3.4
  cmake -DREPLACEMENT_SHA256=207c2123cd27f740cd2a869d78608a467feacd0b295b622d12bf629c30e4e1af \
        -P /c/dev/frametee/cmake/glfw/patch_win32_mouse_motion.cmake
  cd /c/dev/frametee
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DFETCHCONTENT_SOURCE_DIR_GLFW=C:/dev/glfw-3.4
  ```
* **FFmpeg.** В конце конфигурации смотрите строку:
  `MP4 export: enabled (libavcodec ...)` — значит FFmpeg найден.
  `MP4 export: disabled (FFmpeg not found)` — вкладка Render будет недоступна.
  Если нужен именно релизный вариант «без FFmpeg не собираться», добавьте
  `-DFRAMETEE_REQUIRE_VIDEO_EXPORT=ON`.
* **Игровые модули** (`-DBUILD_GAMES=ON` по умолчанию):
  * `-DFRAMETEE_BUILD_TMUF=ON|OFF` — TrackMania (по умолчанию ON, нужен `libs/tmuf_physics`);
  * `-DFRAMETEE_BUILD_SM64=OFF` — SM64 (по умолчанию OFF, нужен большой сабмодуль);
  * примеры выключены: `FRAMETEE_BUILD_CUBE_EXAMPLE`, `FRAMETEE_BUILD_PLATFORMER_EXAMPLE`,
    `FRAMETEE_BUILD_RUST_EXAMPLE` (последнему ещё и Rust).
* **Плагины** (`-DBUILD_PLUGINS=ON` по умолчанию). Если не нужны все, можно
  ограничить, как в CI:
  `'-DFRAMETEE_PLUGIN_ALLOWLIST=example_c;example_cpp;physics_profiler;random_input_filler'`.
* **Тип сборки** по умолчанию и так `Release`; `Debug` — `-DCMAKE_BUILD_TYPE=Debug`
  (даёт `-g -DDEBUG` и отключённые оптимизации).

Минимальный вариант «только движок и DDNet»:

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
  -DFRAMETEE_BUILD_TMUF=OFF -DBUILD_PLUGINS=OFF
```

## 6. Сборка

```bash
cmake --build build --parallel
```

Первый полный билд на 8 ядрах — примерно 5–15 минут (ImGui, игровые модули,
плагины). Инкрементальная пересборка после нашей правки пересобирает один
объектник:

```
[2/5] Building C object CMakeFiles/frametee.dir/src/user_interface/timeline/timeline_interaction.c.o
```

Результат в `build/`:

```
build/frametee.exe
build/games/ddnet.dll, tmuf.dll, ...
build/plugins/<имя>/<имя>.dll
build/data/...
```

## 7. Запуск

Из терминала «MSYS2 UCRT64» (в нём `PATH` уже содержит `/ucrt64/bin`, поэтому
находятся `vulkan-1.dll`, `libcurl`, `freetype`, FFmpeg):

```bash
cd /c/dev/frametee/build
./frametee.exe
```

> **Важно: запускать из каталога сборки.** Шейдеры открываются по относительному
> пути `data/shaders/*.spv` (`renderer.c`), то есть относительно текущего каталога,
> а играм и плагинам запасного поиска рядом с exe нет. Если запустить
> `./build/frametee.exe` из корня дерева, редактор стартует, но засыплет лог
> `[ERROR] [Renderer] Failed to open file: data/shaders/atlas.vert.spv` и ничего
> не нарисует, а плагины отрапортуют
> `'plugins\<имя>' holds no library named after it; skipping it`.

Из Проводника / двойным щелчком `frametee.exe` в `build/` — Windows не знает,
где DLL из UCRT64, поэтому либо добавьте `C:\msys64\ucrt64\bin` в `PATH`, либо
соберите переносимую папку как в релизном workflow:

```bash
cmake --install build --prefix "$PWD/dist" --component Runtime --strip
cp /ucrt64/bin/vulkan-1.dll dist/
# добить остальные импорты (curl, freetype, libav*, libx264, ...):
for dll in $(ldd dist/frametee.exe dist/games/*.dll | awk '$3 ~ /^\/ucrt64\/bin\/.*\.dll$/ {print $3}' | sort -u); do
  cp "$dll" dist/
done
./dist/frametee.exe
```

Проверить, что все зависимости на месте:

```bash
ldd dist/frametee.exe | grep "not found"   # пусто — хорошо
```

Полезные флаги:

```bash
./build/frametee.exe --list-games
./build/frametee.exe --game ddnet --level "C:/maps/level.map"
```

## 8. Если что-то не собирается

| Сообщение | Причина и лечение |
| --- | --- |
| `glslangValidator not found. Please ensure Vulkan SDK is installed.` | не установлен `glslang`: `pacman -S mingw-w64-ucrt-x86_64-glslang` |
| `Could NOT find Vulkan` | `mingw-w64-ucrt-x86_64-vulkan-headers` и `-vulkan-loader` |
| `cimgui.h: No such file or directory` | сабмодули не инициализированы: `git submodule update --init --recursive` (именно `--recursive`) |
| `Connection reset by peer` / `Permission denied (publickey)` при сабмодулях | `libs/ddnet_demo/ddnet_maploader` задан по SSH; клонируйте с `git -c url."https://github.com/".insteadOf="git@github.com:" submodule update --init --recursive` |
| `undefined reference to frametee_glfw_win32_mouse_motion_patch` | GLFW собран без патча из `cmake/glfw/`; см. пункт «GLFW» в разделе 5 |
| `games/sm64 needs libs/sm64_physics` | либо `git submodule update --init libs/sm64_physics`, либо `-DFRAMETEE_BUILD_SM64=OFF` |
| `Could not find CURL` / `Freetype` | `pacman -S mingw-w64-ucrt-x86_64-curl mingw-w64-ucrt-x86_64-freetype` |
| `Fetching GLFW 3.4` … ошибка скачивания | нет сети/прокси на `github.com`; скачайте `glfw-3.4.zip` вручную и положите туда, куда укажет CMake (`build/_deps/glfw-subbuild/...`) |
| CMake взял `clang`, а нужен gcc | `-DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++` (удалить `build/` перед этим) |
| `ninja: command not found` | `pacman -S mingw-w64-ucrt-x86_64-ninja` или убрать `-G Ninja` |
| Собралось, но при старте `не найден vulkan-1.dll` / `libcurl-4.dll` | запуск вне шелла UCRT64: добавьте `C:\msys64\ucrt64\bin` в `PATH` или соберите `dist/` (шаг 7) |
| Сборка идёт, но очень медленно, бинарник тянет `msys-2.0.dll` | вы в шелле MSYS/MINGW64 вместо UCRT64 — пересоберите в «MSYS2 UCRT64» с чистого `build/` |
| Путь с кириллицей/пробелами | проект лучше держать в `C:\dev\frametee` |
| Хочу пересобрать с нуля | `rm -rf build && cmake -S . -B build -G Ninja ...` |

## 9. Короткая шпаргалка

```bash
# один раз
pacman -Syu
pacboy -S toolchain:p cmake:p ninja:p pkgconf:p glslang:p vulkan-headers:p \
          vulkan-loader:p curl:p freetype:p ffmpeg:p
git clone --recursive https://github.com/Teero888/frametee.git /c/dev/frametee
cd /c/dev/frametee && git apply /путь/patches/0001-timeline-rewind-cuts-recording.patch

# каждый раз
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
./build/frametee.exe
```
