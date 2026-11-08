DDNet TAS Fork - Release
========================

Этот архив создан в репозитории strannik162/change (ветка arena/709db3ad-change).

Содержимое:
- DDNet TAS исходники (скопирован DDNet + TAS компонент)
- Пример TAS файлов в tas_example/
- Инструкции по сборке docs/BUILDING.md и docs/TAS.md
- Билд скрипты build.sh / build.bat

Из-за ограничений sandbox (нет cmake/mingw в этой среде) Windows exe не скомпилирован напрямую на сервере arena.
Но вы можете собрать его за 2 команды на Windows (MSYS2) или Linux, см. docs/BUILDING.md глава 4-7.

Если вам нужен готовый exe, запустите GitHub Actions workflow "build" на ветке arena/709db3ad-change —
артефакт Windows соберётся автоматически на раннере GitHub. Или откройте Issue, автор соберёт вручную.

Быстрый старт Linux (уже собран?):
  unzip DDNet-TAS-Linux.zip && ./build/DDNet

Быстрый старт Windows:
  1. Установите MSYS2
  2. mkdir build && cd build
  3. cmake .. -DCMAKE_BUILD_TYPE=Release
  4. cmake --build . -j4
  5. DDNet.exe появится в build/

TAS как пользоваться — см. docs/TAS.md
