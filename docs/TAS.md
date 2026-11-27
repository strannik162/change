# TAS (Tool-Assisted Speedrun) для DDNet — документация

## Описание

TAS — отдельный изолированный мир внутри клиента DDNet, где движутся только **вы и ваш dummy**, все остальные игроки исчезают с карты.  
Позволяет записать точные инпуты, отмотать, поставить паузу, ускорить, сохранить в файл и воспроизвести уже в реальном мире с другими игроками. При выборе сохранённого файла появляется **призрачный tee** (видимый только вам) на месте старта записи.

Все виды оружия автоматически доступны во время записи TAS (требование #6). Совместим с KRX: `showall 1` автоматом, `krx_tastps` и т.д.

---

## KRX Parity — добавлено по запросу (3 пункта)

По [KRX TAS docs](https://krxclient.gitbook.io/docs/ultimate-features/tas) добавлено:

### 1) Ticks / Step — покадровый контроль
- **Ticks per press** (`cl_tas_ticks_per_press` 1..50, KRX `krx_tastickcontrolticks`) — сколько тиков прыгает Rewind/Forward за одно нажатие. Слайдер в меню TAS → Tools.
- **Step** (`cl_tas_step` 0/1, KRX `krx_tickcontrolstep`) — если ON, то Rewind/Forward идёт строго 1 тик за нажатие, игнорируя Ticks. Чекбокс в меню. Позволяет покадрово выставлять инпуты как в KRX.
- Бинды KRX работают 1:1: `toggle krx_tasrewind 1 0` (Hold), `toggle krx_tasforward 1 0`, `krx_tastickcontrolticks 5`, `krx_tickcontrolstep 1`.

### 2) Auto Rewind / Auto Forward / Auto Pause + ShowAll
- **Auto Rewind** (`cl_tas_auto_rewind`, KRX `krx_tasautorewind`) — при влёте в фриз во время записи автоматом отматывает к последнему сейф-тику до фриза (`m_LastSafeFrame`). Срабатывает в `CheckAutoRewindForward()`.
- **Auto Forward** (`cl_tas_auto_forward`, KRX `krx_tasautoforward`) — автоматом проматывает фриз-ожидание до первого тика после анфриза.
- **Auto Pause** (`cl_tas_auto_pause`, KRX `krx_tickcontrolpause`) — после Auto Rewind/Forward ставит паузу, чтобы ты успел поправить инпут.
- **Ensure ShowAll** (`cl_tas_showall_auto`, KRX prerequisite `showall 1` + `cl_run_on_join "showall 1"`) — при `tas_record` автоматом шлёт `say /showall 1` и прописывает `cl_run_on_join` чтобы энтити (пушки/щиты/сердечки) рендерились до старта TAS, иначе десинк как в KRX FAQ.

### 3) Validate + Remove Useless + Get Time + Clear
- **Validate Replay** — `tas_validate` / кнопка Validate: прогоняет буфер и сравнивает позиции тик-в-тик, считает `max jump` и mismatches (аналог KRX Validate). Вывод: `Validate OK: 120 frames, max jump 45.0` или WARNING про телепорты/стопперы.
- **Remove Useless** — `tas_remove_useless` / кнопка Remove Useless: чистит лишние инпуты где нет hook/jump/fire/movement и позиция не меняется + оптимизирует aim под Robot Fake Aim (оставляет aim только на нужных тиках) как в KRX.
- **Get Replay Time** — считает время из `ticks / TPS` → `MM:SS.mmm (ticks @TPS)`.
- **Clear Replay** — `krx_tasrespawn` аналог: чистит буфер и респавнит на старте TAS-мира.

Все KRX хоткеи забинжены как алиасы: `krx_recordreplay` → `tas_record`, `krx_loadreplay` → `tas_play`, `krx_taspause`, `krx_tastps` → `cl_tas_record_speed` и т.д.

---

## Оптимизации и новые фичи (v2)

### Сжатие сохранений (zlib v2)
- `SaveToFile` пробует `compress()` в `DDNET_TAS_v2` (магик `DDNET_TAS_v2` + header + `CompressedSize/UncompressedSize` + payload). Если сжатие <90% от оригинала — сохраняет v2, иначе fallback v1.
- `LoadFromFile` понимает оба формата (v1 прямой dump, v2 `uncompress`). Логирует `v2 frames=N`.
- Использует `engine/external/zlib/zlib.h` bundled.
- `OptimizeMemory()` после load/save: `shrink_to_fit` если `capacity >8192 && size < cap/2`.

### Память и тик-путь
- `RefreshFileList` `reserve(64)`, `StartRecord` `reserve(50*60*8 ≈24000)` избегает realloc на длинных ранах (8 мин).
- `DoRecordTick` fast-path `TPS==50` без float, иначе `m_RecordAccum += speed/50.0`.
- `GiveAllWeapons` early-out если не isolated/recording.
- `m_LastRecordTick` член вместо `static` внутри функции (фиксит ODR/Werror).

### Timeline scrubber
- Панель Timeline в TAS меню: `DoScrollbarOption` на `m_CurrentFrame` 0..N-1, таскание скруббера обновляет `m_GhostPos/DummyPos` + лейбл `frame / total + GetReplayTime`.

### HUD velocity graph
- `RenderTasHud` рисует фон, одну строку `TPS/Rew/Fwd/Frames/Turbo` и справа график скорости (последние 60 фреймов, `distance*50/32`, цвет `0.4+0.6*speed` градиент, max label).
- Показывает `Frame X/N` во время record/playback.

### Ghost trail
- `players.cpp`: кроме стартового ghost (alpha 0.45) рендерит последние 20 фреймов как faded trail alpha 0.08..0.2, `ScreenRect.Inside` чек.

### Vault snapshot
- `SaveVaultSnapshot()` — timestamp `vault_<map>_<YYYYMMDD_HHMMSS>` через `SaveToFile` + `RefreshFileList`, кнопка Sync now.

### Keyframes / Bookmarks + Smooth playback
- `m_vKeyframes: vector<int>` bookmarked frames, `AddKeyframe()` dedup+sort, `JumpToKeyframe()` clamp, `ClearReplay` чистит и их.
- Консоль: `tas_bookmark`, `tas_bookmark_jump i[idx]`, `tas_smooth i[on/off]` (лерп ghost между фреймами через `mix()` когда `m_PlaybackAccum<1`).
- Панель Bookmarks: Add bookmark / Jump last, список первых 4 фреймов.

### Настройки
- Settings панель теперь корректно мапит: ShowAll auto / Auto Rewind / Auto Forward / Auto Pause / Step mode (раньше были Enable sound/effects).

### Рендер/изо
- `ShouldHideOtherPlayers` via `m_aLocalIds[0]/[1]` + `PredictDummy()` + `DummyConnected()`.
- `OnUpdate` turbo не трогает RECORD тик (KRX spec), `CheckAutoRewindForward` остаётся.

---

## Требования выполнены

1. **Отдельный мир TAS**  
   - При нажатии **Record** клиент переходит в изолированный мир (`cl_tas_isolated 1`).  
   - В `CPlayers::OnRender()` скрываются все `ClientId`, кроме локального и dummy — `CTas::ShouldHideOtherPlayers()`.  
   - Серверная часть не затрагивается, фильтрация — чисто клиентская (ghost-like).

2. **Ползунок скорости записи 1–50 тиков/сек**  
   - Конфиг `cl_tas_record_speed` (1..50). По умолчанию 20.  
   - В `CTas::DoRecordTick()` используется аккумулятор: `m_RecordAccum += recordSpeed/50.0f`. При <1 — пропуск кадра, иначе запись. 50 = каждый тик, 1 = раз в 50 тиков.  
   - Ползунок в меню TAS.

3. **Кнопки управления**  
   - **Запись** — `tas_record` / кнопка “Record (Isolated World)” — вход в TAS-мир, очистка буфера.  
   - **Воспроизведение в реальном мире** — `tas_play` — выходит из изолированного мира, инжектит записанные `CNetObj_PlayerInput` в `CControls::m_aInputData`, остальные игроки снова видны.  
   - **Реверс (отматывание)** — `tas_rewind` — `m_RewindSpeed` 1..50 тиков/сек, ползунок `cl_tas_rewind_speed`. Идёт назад по `m_vFrames`, обновляет ghost позицию. Турбо-умножение учитывается.  
   - **Пауза** — `tas_pause` — флаг `m_Paused`, останавливает любой режим (record/rewind/forward/playback). Повторный вызов — resume.  
   - **Форвард** — `tas_forward` — движение вперёд по записанным кадрам со скоростью `cl_tas_forward_speed` 1..50.  
   - **Ускорение мира без изменения тиков записи** — бинд `+tas_turbo` (`cl_tas_turbo_speed` 1..10 множитель). При удержании `m_TurboActive` в `OnUpdate()` выполняется `TurboMult` итераций тиков, но `m_RecordSpeed` не меняется. Бинд: `bind x +tas_turbo 1` (удержание) или `bind f3 tas_turbo 1`.

4. **Сохранение в папку**  
   - Папка `tas/` в `TYPE_SAVE` (рядом с `settings_ddnet.cfg`): `~/.teeworlds/tas/` (Linux) или `%APPDATA%\Teeworlds\tas\` (Windows).  
   - Автоматически создаётся в `CTas::EnsureTasFolder()`.  
   - Формат файла `DDNET_TAS_v1` (12 байт магика + `CTasHeader` + массив `CTasFrame`). Рядом сохраняется `.json` для отладки.  
   - Консоль: `tas_save my_run` → `tas/my_run.tas`. Кнопка “Save TAS” берёт имя из поля ввода.  
   - Список файлов в правом панели меню TAS, обновляется по `tas/` директории.

5. **Призрачный tee при выборе TAS**  
   - При клике в списке `SetSelectedIndex()` грузит заголовок, берет `m_Header.m_StartPos`, ставит `m_ShowGhost = true`.  
   - Рендер в `CPlayers::OnRender()` — `RenderTools()->RenderTee(..., alpha=0.4)` на позиции `m_GhostPos`, видимой только локально. Проверка `IsGhostVisible()`.

6. **Все виды оружия при записи**  
   - В `OnNewSnapshot()` если `Isolated && Recording` → `GiveAllWeapons()`. Проставляется `m_aAmmoCount[NUM_WEAPONS]=10`, флаг `m_HasWeapon[] = true` в каждом `CTasFrame`, что позволяет переключать оружие клиентским предикшеном даже без серверного пикапа.

---

## Конфиги

```
cl_tas_record_speed 20      // 1..50 TPS записи (KRX krx_tastps)
cl_tas_rewind_speed 20      // 1..50 отмотка (tas_rewind / krx_tasrewind)
cl_tas_forward_speed 20     // 1..50 форвард (tas_forward)
cl_tas_turbo_speed 3        // 1..10 турбо множитель (hold)
cl_tas_isolated 0           // флаг изо-мира
cl_tas_ticks_per_press 1    // 1..50 (KRX krx_tastickcontrolticks)
cl_tas_step 0               // step mode 0/1 (KRX krx_tickcontrolstep)
cl_tas_auto_rewind 0        // auto rewind 0/1 (KRX krx_tasautorewind)
cl_tas_auto_forward 0       // auto forward 0/1
cl_tas_auto_pause 0         // auto pause 0/1 (KRX krx_tickcontrolpause)
cl_tas_showall_auto 1       // auto ensure showall 1
```

### Новые команды
```
tas_bookmark              // добавить закладку на текущем фрейме
tas_bookmark_jump <idx>   // прыгнуть к закладке
tas_smooth 0/1            // плавный ghost lerp при playback
tas_validate              // Validate replay (max jump, mismatches)
tas_remove_useless        // Remove useless inputs (clean aim/hook)
tas_ticks <1..50>         // krx_tastickcontrolticks alias
```

## Команды

```
tas_record                // начать запись в изолированном мире
tas_play                  // воспроизвести в реальном мире
tas_pause                 // пауза / resume
tas_stop                  // стоп
tas_rewind                // реверс (toggle)
tas_forward               // форвард (toggle)
tas_save <name>           // сохранить, напр. tas_save run1
tas_load <filename>       // загрузить
tas_isolated 0/1          // вкл/выкл изолированный мир
tas_turbo 0/1             // турбо (для бинда)
```

Бинды:
```
bind r tas_record
bind p tas_play
bind space tas_pause
bind z tas_rewind
bind x tas_forward
bind lshift +tas_turbo 1   // ускорение TAS мира без изменения записи
bind f3 "tas_save my_tas"
```

---

## Меню

В игре (онлайн) в верхней панели рядом с Game / Players / Server info / Browser / Ghost появилась вкладка **TAS**.

Левая панель — управление:
- Слайдеры Record / Rewind / Forward / Turbo
- Кнопки Record / Play / Pause / Rewind / Forward
- Поле имени файла + Save
- Подсказки про оружие и бинд

Правая панель — список сохранённых TAS:
- Клик выбирает файл, сразу показывает ghost-tee на старте (только для вас)
- Load & Show Ghost / Refresh

HUD (вверху экрана во время TAS):
- Состояние: RECORDING [TAS WORLD] / REWIND / FORWARD / PLAYBACK [REAL WORLD] / PAUSED
- Скорости и количество кадров
- Индикатор изоляции и турбо

---

## Технические детали для разработчика

- Компонент `CTas` наследуется от `CComponent`, добавлен в `CGameClient::m_vpAll` после `CGhost` (порядок рендера важен).
- `CMakeLists.txt` — добавлены `components/tas.cpp`, `components/tas.h`, `components/menus_tas.cpp`.
- `config_variables.h` — 6 новых переменных.
- `gameclient.h` — поле `CTas m_Tas;`.
- `players.cpp` — фильтрация рендера others + ghost.
- `menus.h/.cpp` — `PAGE_TAS`, `RenderTas()`, вкладка в `RenderMenubar()`.
- `menus_tas.cpp` — делегирование `GameClient()->m_Tas.DoTasMenu()`.
- Файлы `tas/*.tas` — бинарный формат, совместим с будущими версиями (проверка магика `DDNET_TAS_v1`).

### Формат файла

```
magic[12] = "DDNET_TAS_v1"
CTasHeader { char mapName[64]; char author[32]; int recordSpeed; int startTick; vec2 startPos; vec2 dummyPos; int numFrames; }
CTasFrame[numFrames] { int tick; CNetObj_PlayerInput input; CNetObj_PlayerInput dummyInput; vec2 pos; vec2 dummyPos; int weapon; int dummyWeapon; bool hasWeapon[NUM_WEAPONS]; bool dummyHasWeapon[NUM_WEAPONS]; }
```

---

## Ограничения и будущее

- Изоляция — клиентская (сервер не знает). Для полноценной изоляции нужен серверный мод / локальный сервер (`sv solo`).
- Телепорты/переключение оружия эмулируются, но сервер всё равно валидирует оружие. На некоторых серверах без `sv_test_cmds` оружие не выдастся — TAS проставит только визуально/предикшен.
- Можно расширить: сохранение `m_pGhost` совместно с TAS для точного реплея, экспорт в demo, редактор траектории.

