# TAS (Tool-Assisted Speedrun) для DDNet — документация

## Описание

TAS — отдельный изолированный мир внутри клиента DDNet, где движутся только **вы и ваш dummy**, все остальные игроки исчезают с карты.  
Позволяет записать точные инпуты, отмотать, поставить паузу, ускорить, сохранить в файл и воспроизвести уже в реальном мире с другими игроками. При выборе сохранённого файла появляется **призрачный tee** (видимый только вам) на месте старта записи.

Все виды оружия автоматически доступны во время записи TAS (требование #6).

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
cl_tas_record_speed 20      // 1..50 тиков/сек записи
cl_tas_rewind_speed 20     // 1..50 скорость отмотки
cl_tas_forward_speed 20    // 1..50 скорость форварда
cl_tas_turbo_speed 3       // 1..10 множитель ускорения при удержании бинда
cl_tas_isolated 0          // флаг изолированного мира (только чтение)
cl_tas_bind_turbo ""       // информационное поле для бинда
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

