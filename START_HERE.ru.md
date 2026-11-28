# Что в этой ветке

Ветка `arena/974f297b-change` — зеркало апстрима
[`Teero888/frametee`](https://github.com/Teero888/frametee) (commit `8afb79c`,
20.11.2026) с одной правкой и документацией к ней.

## Содержимое

| Путь | Что это |
| --- | --- |
| `src/...`, `games/`, `plugins/`, `data/`, ... | исходники FrameTee как в апстриме |
| `src/user_interface/timeline/timeline_interaction.c` | **единственный изменённый файл**: перемотка (`C`) во время записи обрезает тейк по плейхеду |
| `docs/rewind-trim.ru.md` | разбор правки: где обработчик `C`, что делает функция обрезки, почему вызов стоит именно там, как проверить |
| `docs/build-windows-msys2-ucrt64.ru.md` | сборка под Windows через MSYS2 UCRT64 (пакеты, CMake, типовые ошибки) |
| `patches/0001-timeline-rewind-cuts-recording.patch` | та же правка одним патчем — для чистого клона апстрима |

## Важно про сабмодули

В зеркало **не входят** каталоги-сабмодули: `libs/cimgui`, `libs/cglm`,
`libs/nfd`, `libs/toml`, `libs/ddnet_physics`, `libs/ddnet_demo`,
`libs/ddnet_ghost`, `libs/tmuf_physics`, `libs/sm64_physics` (они тянутся с
GitHub и весят десятки мегабайт). Поэтому собирать нужно так:

```bash
git clone --recursive https://github.com/Teero888/frametee.git frametee
cd frametee
git apply /путь/к/patches/0001-timeline-rewind-cuts-recording.patch
```

и дальше по `docs/build-windows-msys2-ucrt64.ru.md`.

Либо, если хотите собирать именно это дерево, положите сабмодули рядом вручную:

```bash
git clone https://github.com/cimgui/cimgui.git libs/cimgui          # внутри нужен ещё imgui
git -C libs/cimgui submodule update --init --recursive
git clone https://github.com/recp/cglm.git libs/cglm
git clone https://github.com/btzy/nativefiledialog-extended.git libs/nfd
git clone https://github.com/cktan/tomlc17.git libs/toml
git clone https://github.com/Teero888/ddnet_physics.git libs/ddnet_physics
git clone https://github.com/Teero888/ddnet_demo_c99.git libs/ddnet_demo
git clone https://github.com/Teero888/ddnet_ghost.git libs/ddnet_ghost
git clone https://github.com/Teero888/tmuf_physics.git libs/tmuf_physics
# SM64 (52 МБ) нужен только при -DFRAMETEE_BUILD_SM64=ON
# git clone https://github.com/Teero888/sm64_physics.git libs/sm64_physics
```
