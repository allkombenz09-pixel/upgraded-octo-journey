# How They Built It — студия канала

Здесь собирается канал «How They Built It»: англоязычные документальные истории компаний в рисованном стиле, голос — Mila Sterling.

Каждый выпуск описан одним файлом `episodes/<выпуск>/episode.json`: текст по фразам (beats), раскадровка (кадры, движение камеры, графика, переходы, звук), Shorts и упаковка. Из него автоматически собираются:

- **пакет для исполнителя** (`package/*.docx`): озвучка, промпты картинок, имена файлов, чек-листы;
- **аниматик**: черновой фильм с заглушками вместо картинок и черновым голосом, чтобы проверить ритм до генерации;
- **финальный монтаж** 1080p: движение камеры по каждому кадру, карты, даты, цифры, главы, переходы, музыка с приглушением под голос, конечная заставка;
- **4 Shorts** 9:16 с субтитрами по словам;
- `captions.srt`, `description.txt` с главами, `thumbnail.png`, монтажный лист `edit_decision_list.csv`.

## Выпуск 01 — IKEA

| Файл | Что это |
|---|---|
| `episodes/01-ikea/episode.json` | сценарий v2 + раскадровка (источник правды) |
| `episodes/01-ikea/package/IKEA_production_v2.docx` | пакет для Антона (озвучка, промпты, чек-лист) |
| `episodes/01-ikea/package/IKEA_production_v1_original.docx` | исходная версия |
| `episodes/01-ikea/FACTCHECK.md` | что проверено, что исправлено, источники |

## Как собрать фильм

```bash
pip install -r requirements.txt
bash tools/setup_scratch_voice.sh          # черновой голос для аниматика (один раз)

python tools/film.py episodes/01-ikea status     # чего не хватает
python tools/film.py episodes/01-ikea animatic   # 720p, заглушки + черновой голос
python tools/film.py episodes/01-ikea final      # 1080p из frames/ + audio/ + music/
python tools/film.py episodes/01-ikea shorts-final
python tools/build_package.py episodes/01-ikea   # пересобрать .docx после правок episode.json
```

Куда класть материалы (эти папки в git не попадают):

```
episodes/01-ikea/frames/   01.png … 25a.png 25b.png … thumbnail.png  (+ 10.mp4 и т.п. из Kling)
episodes/01-ikea/audio/    part1.mp3 part2.mp3 part3.mp3 short1.mp3 … short4.mp3
episodes/01-ikea/music/    один трек из YouTube Audio Library
```

Если реальной картинки ещё нет, в монтаже стоит заглушка с номером кадра, так что фильм можно собирать по мере готовности материалов. Реальный голос раскладывается по фразам автоматически, по паузам. Точные тайминги после сборки лежат в `out/timing.json`.

Шрифты: Playfair Display и Inter (SIL OFL). Карты: world-atlas / Natural Earth (ISC, public domain). Черновой голос: Kokoro-82M (Apache-2.0), только для аниматика.
