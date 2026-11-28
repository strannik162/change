# Перемотка во время записи обрезает трек по плейхеду

Документ описывает правку в `src/user_interface/timeline/timeline_interaction.c`,
зачем она нужна и как проверить её в редакторе.

## 1. Где находится обработчик клавиши `C`

Клавиша `C` — это не «отмотка назад» как тасование, а действие **Rewind (Hold)**:
оно зажато → плейхед едет назад в два раза быстрее обычной скорости.

Цепочка вызовов:

| Что | Где |
| --- | --- |
| Действие и его id | `src/user_interface/keybinds.h` → `ACTION_REWIND_HOLD` |
| Человекочитаемое имя | `src/user_interface/keybinds.c:282` → `"rewind_hold"`, «Rewind (Hold)», категория «Playback» |
| Привязка по умолчанию | `src/user_interface/keybinds.c:342` → `keybinds_add(..., ACTION_REWIND_HOLD, (key_combo_t){ImGuiKey_C, false, false, false})` |
| Обработчик | `src/user_interface/timeline/timeline_interaction.c` → `interaction_handle_playback_and_shortcuts()` |

Обработчик устроен так:

```c
void interaction_handle_playback_and_shortcuts(timeline_state_t *ts) {
  ts->playback_speed = ts->gui_playback_speed;

  // Detect rewind (press or hold)
  bool reverse_down = keybinds_is_action_down(&ts->ui->keybinds, ACTION_REWIND_HOLD) || ...;
  ...
  ts->is_reversing = reverse_down;
  if (ts->is_reversing) ts->is_playing = false;

  // +++ сюда добавлена обрезка трека по плейхеду +++

  // Always update inputs for ALL tracks ...
  if (ts->recording) { interaction_update_recording_input(ts->ui); interaction_apply_linked_inputs(ts->ui); }

  // Playback tick advancement   <-- «возобновление/движение тиков»
  if ((ts->is_playing || ts->is_reversing) && ts->playback_speed > 0) { ... model_advance_tick(ts, dir); ... }
}
```

Живая запись (`Record`) живёт в том же файле: `interaction_toggle_recording()`
(включение/выключение записи), `interaction_start_recording_on_track()` (новый
«тейк»-сниппет на треке), `interaction_update_recording_input()` (сэмплирование
клавиш в `track->current_input`), `model_advance_tick()`
(`src/user_interface/timeline/timeline_model.c`) — дописывание тика в тейк.

## 2. Функция обрезки, которая вызывается

```c
void interaction_trim_recording_snippet(timeline_state_t *ts, bool only_controlled);
```

`src/user_interface/timeline/timeline_interaction.c` (реализация — сразу после
блока «Recording Helpers»), объявление — `timeline_interaction.h`.

Что делает:

* идёт по всем записываемым трекам; для каждого тейка считает
  `trim_to = model_group_playhead_tick(ts, ts->active_group_index)`;
* если тейк начался **после** плейхеда — удаляет тейк целиком;
* иначе обрезает входы до `trim_to - start_tick` и выбрасывает опустевшие тейки;
* при фактической обрезке вызывает `model_invalidate_group_physics(group, playhead)` —
  чтобы выбросить симуляцию, посчитанную по удалённым входам;
* заново собирает список активных тейков (`ts->recording_snippets`) так, чтобы
  запись продолжалась с плейхеда (при необходимости стартует новый тейк на
  `interaction_start_recording_on_track`).

Это ровно то, что делает «Trim Recording» (по умолчанию `F`, и `Ctrl+F` —
только у контролируемого игрока), только теперь ещё и на перемотке.

## 3. Сама правка

```c
  ts->is_reversing = reverse_down;
  if (ts->is_reversing) ts->is_playing = false;

  // Rewinding while recording cuts the takes back to the playhead, tick by tick, so the ticks the
  // rewind goes back over are recorded afresh instead of replayed from the take. It runs before the
  // inputs are sampled and the ticks advance, so the tick the playhead lands on is part of the new
  // take. (No only_controlled: the linked tees record the same passage and are cut with it.)
  if (ts->is_reversing) interaction_trim_recording_snippet(ts, false);
```

Почему именно здесь:

1. **После** того, как решено, что перемотка активна (`ts->is_reversing`) — иначе
   непонятно, обрезать или нет.
2. **До** блока `Playback tick advancement`, то есть до `model_advance_tick()` —
   «перед возобновлением тиков»: плейхед, на который перемотка приедет, сразу
   становится концом тейка, и следующий же тик пишется в новый тейк, а не
   переигрывается из старого.
3. **До** `interaction_update_recording_input()` / `interaction_apply_linked_inputs()`,
   чтобы сэмплирование клавиш и раскладка связанных ти (linked) шли уже по
   обрезанному состоянию, а не по «хвосту», который мы только что выкинули.

Вызов идемпотентен и дешёвый: если плейхед не сдвинулся, обрезать нечего
(`new_duration == input_count`), аллокаций нет. Поэтому он и стоит без
дополнительных условий на «нажатие»: пока `C` зажата, тейк укорачивается ровно
на столько тиков, на сколько уехал плейхед, — как `F` при удержании.

`only_controlled = false` — обрезаются все пишущие треки: связанные ти продолжают
писать тот же участок, и если их не обрезать, они бы «отстали» от контролируемого.

## 4. Что это значит на практике: Record больше не надо выключать

Сценарий, ради которого всё делалось:

1. `Record` включён, тейк пишется вперёд.
2. Зажал `C` — вместе с tee назад едет и запись: правый край тейка идёт за
   плейхедом, `ts->recording` при этом **не меняется**, это тот же самый тейк,
   он просто укорачивается.
3. Отпустил `C`, нажал `X` — запись продолжается с плейхеда, поверх старого
   участка пишутся новые входы.

То есть перезапускать рекординг (`Record` → `Record`) не нужно: флаг записи не
трогается, а тейк после обрезки заканчивается ровно на плейхеде, поэтому
`model_advance_tick()` снова дописывает тики (`relative_tick > input_count`),
вместо того чтобы переигрывать уже записанное.

## 5. Что меняется в поведении

Было: запись включена → тейк пишется вперёд. Зажали `C` — плейхед уехал назад,
но тейк остался целым, и после `X` (Play) тики внутри тейка **переигрывались**
из уже записанных входов, то есть перезаписать участок было нельзя без `F`.

Стало: зажали `C` во время записи — тейк обрезается по плейхеду, «хвост»
исчезает. `X` — запись продолжается с плейхеда и пишет заново.

Отсюда следствия, которые полезно знать:

* `C` во время записи **уничтожает** записанное после плейхеда (это и есть фича).
* Обрезка во время записи, как и `F`, не кладётся в undo-историю — откатить
  можно только предварительным `Esc` (завершить запись) + `Ctrl+Z` по
  зафиксированным сниппетам.
* `Esc` во время записи — завершить запись; `F4` — отменить запись (выбрасывает тейки).
* Вне записи `interaction_trim_recording_snippet()` выходит на первой строке, так
  что обычная перемотка `C` не меняется.

Если нужно, чтобы обрезка срабатывала **только в момент нажатия** (один раз за
нажатие, а не пока клавиша зажата), замените условие на

```c
  if (keybinds_is_action_pressed(&ts->ui->keybinds, ACTION_REWIND_HOLD, false) && ts->recording)
    interaction_trim_recording_snippet(ts, false);
```

но тогда при удержании `C` хвост за плейхедом останется, и после `X` участок
будет переигрываться, а не перезаписываться.

## 6. Как проверить руками

1. Запустить редактор, открыть уровень (DDNet/TMUF), встать на трек.
2. Нажать `Record` на панели таймлайна (`timeline_renderer.c`), поехать `X`.
3. Проехать ~100 тиков, зажать `C` секунду и отпустить: правый край сниппета
   едет назад вместе с плейхедом — тейк обрезан.
4. Нажать `X` и поехать снова: с плейхеда пишутся новые входы (правый край снова
   растёт), старое продолжение не воспроизводится.
5. Для сравнения: то же самое вручную делает `F`; `Ctrl+F` режет только
   контролируемого игрока, связанные ти продолжают свой тейк.

## 7. Патч

Готовая заплатка для чистого апстрима лежит в
`patches/0001-timeline-rewind-cuts-recording.patch`. Сделана по master `8afb79c`
(20.11.2026); на последнем релизе `v0.7.5-alpha` (`b19b5c2`) файл
`timeline_interaction.c` идентичен, поэтому патч ложится и туда — проверено
`git apply --check`:

```bash
git apply patches/0001-timeline-rewind-cuts-recording.patch   # или git am < ...
```

Изменён один файл, 6 добавленных строк.
