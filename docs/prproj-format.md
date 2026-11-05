# Структура `.prproj` (разбор реального монтажного проекта, Premiere 14.0.1, Project Version 38)

Файл — gzip с XML. Объекты лежат плоским списком на верхнем уровне (`<Тип ObjectID="N" ...>` или
`ObjectUID="uuid"`), ссылки между ними — `ObjectRef` (N) и `ObjectURef` (uuid). Время — тики,
254 016 000 000 в секунде. Кадр 25 fps = 10 160 640 000 тиков, сэмпл 48 кГц = 5 292 000 тиков на сэмпл-тик
(`AudioStream/FrameRate`).

Правило для сборщика: шаблон делать из файла **старой** версии. Новую версию проекта старый Premiere
не откроет, старую новый откроет и сам обновит.

## Дерево проекта

    Project
    └─ RootProjectItem  (Items: BinProjectItem | ClipProjectItem по ObjectURef)
       ├─ BinProjectItem  (ProjectItemContainer → Items)  — корзины, вложенные сколько угодно
       └─ ClipProjectItem → MasterClip

Порядок в корзинах задаёт `project.icon.view.grid.order`; цвет метки — `Column.PropertyText.Label`
(`BE.Prefs.LabelColors.N`).

## Медиа (мастер-клип), 18 объектов на файл с видео и звуком

    ClipProjectItem ─ MasterClip ─┬ ClipLoggingInfo      (имя, MediaIn/OutPoint = TC файла, MediaFrameRate)
                                  ├ AudioComponentChain  (громкость мастер-клипа)
                                  ├ Clips: VideoClip, AudioClip   (Clip.Source → *MediaSource, Markers)
                                  ├ AudioClipChannelGroups → ClipChannelGroupVectorSerializer
                                  │     → ClipChannelVectorSerializer → ClipChannelSerializer × число каналов
                                  └ Markers
    VideoMediaSource / AudioMediaSource → Media (общий)
    AudioClip.SecondaryContents → SecondaryContent × канал (ChannelIndex → AudioMediaSource)
    Media: FilePath (абсолют), RelativePath, ActualMediaFilePath, Title, FileKey, Start (TC старта, тики),
           ConformedAudioRate; потоки VideoStream (FrameRate, FrameRect, Duration, CodecType) и
           AudioStream (FrameRate=5292000, Duration, AudioChannelLayout, PeakFilePath)

Синтетические клипы (Black Video, Adjustment Layer, Title): тот же MasterClip, но Media без файла:
`FilePath` = число, `<Infinite>true`, свой `ImplementationID`; у титра `IsStill` и `CodecType`
`1416197228`, внутри `ImporterPrefs`.

## Секвенция

    Sequence ─ TrackGroups ─┬ VideoTrackGroup (FrameRect, FrameRate, Tracks→VideoClipTrack)
                            └ AudioTrackGroup (Tracks→AudioClipTrack, FrameRate=5292000)
              + AudioMixTrack / AudioTrackInlet / AudioFader / AudioMeter / панорамные процессоры на каждую
                аудиодорожку; PersistentGroupContainer (Link-и видео↔звук)
    ClipTrack: ClipItems→TrackItems (по порядку слева направо), TransitionItems

Каждая секвенция в проекте ещё и клип: свой ClipProjectItem → MasterClip → Video/AudioSequenceSource
(`Sequence ObjectURef`). Вложенных секвенций внутри дорожек в этом проекте нет.

## Клип на дорожке

- `VideoClipTrackItem` / `AudioClipTrackItem`: `Start`/`End` на таймлайне (тики), `SubClip`,
  `ComponentOwner → Components` (цепочка эффектов).
- `SubClip` → `Clip` (`VideoClip`/`AudioClip` с `InPoint`/`OutPoint` в исходнике и `PlaybackSpeed`, если не 1)
  и `MasterClip`.
- Скорость: `OutPoint − InPoint = длина на таймлайне × PlaybackSpeed` (видео в тиках, звук так же).
- Видео и звук одного исходника связаны объектом `Link` (`TrackItemGroup`).
- Аудио-клип несёт `SecondaryContent` на каждый канал и `AudioChannelLayout`.
- Метка цвета клипа — `asl.clip.label.color` + `asl.clip.label.name` в `Clip`.

## Эффекты, встреченные в проекте

| MatchName | что |
|---|---|
| AE.ADBE Motion | Position/Scale/Rotation/Anchor; в 139 клипах не по умолчанию (прокси ≠ размеру секвенции, Scale 179 и т.п.) |
| Volume / Channel Volume (аудио) | Level, Bypass; ключевые кадры в 97 параметрах |
| Lowpass | аудиофильтр |
| AE.ADBE Opacity | Opacity, Blend Mode |
| AE.ADBE SubspaceStabilizer | Warp Stabilizer, ~30 параметров |
| AE.ADBE AEMask | маска с `ArbVideoComponentParam` |
| Constant Gain | аудио-переход (`AudioTransitionTrackItem`) |

Параметр = `VideoComponentParam`/`AudioComponentParam`/`PointComponentParam`; значение лежит в
`StartKeyframe` (`время,значение,...`), анимация — список ключей, `IsTimeVarying`.
Видеопереходов, Lumetri и вложенных секвенций в проекте нет.

## Маркеры

`Markers` объект есть у каждого клипа; сами маркеры — `Marker` с JSON в `DVAMarker`
(`mStartTime.ticks`, `mType`: Comment и т.д.).

## Что значит для сборщика из Resolve

Минимум для рабочей секвенции: мастер-клип на каждый файл, `Sequence` с дорожками, на каждый клип
`TrackItem` + `SubClip` + `VideoClip`/`AudioClip` + цепочка (пустая допустима), `Link` для пар, в корзинах
`ClipProjectItem` на мастер-клипы и на секвенции. Дальше по приоритету: скорость, громкость и ключи,
Motion (Scale/Position), метки, цвета, аудио-переходы.

## Сборщик: `core/prproj_build.py`

Resolve -> `core/adapters/resolve_spec.py` (только чтение) -> spec -> `core.prproj_build.build()`.

    PRPROJ_OUT=out.prproj PRPROJ_TEMPLATE=шаблон.prproj [PRPROJ_TIMELINES="Edit A|Edit B"] \
      python -m core.adapters.resolve_run exec core/adapters/resolve_prproj_job.py

Объекты клонируются из шаблонного проекта с новыми id; шаблон даёт настройки проекта, его содержимое
отбрасывается. Шаблон должен быть сохранён самым старым Premiere, в котором результат надо открыть.
Переносится: корзины по папкам медиапула, медиа (видео, звук, видео+звук, моно/стерео, стоп-кадры),
секвенции с дорожками, клипы (позиция, in/out, скорость), Motion (позиция, масштаб, поворот),
связь видео-звук, скрытые видеодорожки и выключенные звуковые. Цвет и эффекты не создаются: клип
получает цветовую метку, причина попадает в отчёт.

Проверено на живом проекте (таймлайн из 638 видео- и 1121 звуковых клипов, 359 файлов): граф ссылок
замкнут, id уникальны, позиции, in/out и скорость всех 1759 клипов совпали со spec. В Premiere
файл не открывался (на этом Mac его нет).

Не переносится и попадает в отчёт: автоматизация громкости (ключи), затухания и кроссфейды, переходы, маркеры (таймлайна и клипов),
прозрачность, отражение, кроп, режим наложения, составные клипы, титры и заливки без файла,
одиночные выключенные клипы. Цвет метки клипа из Resolve тоже пока не переносится.

### Громкость клипа

API Resolve громкость не отдаёт (`GetProperty()` у аудиоклипа пуст), поэтому уровень читается из FCP7 XML,
который Resolve выгружает для того же таймлайна в том же запуске. Там `Audio Levels` лежит линейным
множителем (1.122 = +1 дБ). Клипы рядом с переходами имеют `start/end = -1`, поэтому XML и API
сопоставляются по порядку имён на дорожке, а результат принимается только если явные старты в XML
совпали с API не меньше чем в 90 % случаев (883 из 883).

В Premiere уровень лежит в компоненте `Volume` (`Internal Volume Stereo/Mono`), параметр `Level`:
`значение = 10^((дБ - 15) / 20)`. Это 0.1778 для 0 дБ и 1.0 для +15 дБ, верхнего края шкалы;
подтверждено двумя проектами (Channel Volume по умолчанию в одном, моно-Volume по умолчанию в другом).
Выше +15 дБ значение обрезается, в отчёт идёт строка. Ключевых кадров громкости пока нет: формата
нет ни в одном образце. На том же таймлайне записано 987 уровней, все совпали с XML.
