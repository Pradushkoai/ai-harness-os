# agents-md

Генератор **AGENTS.md** для AI-кодинг-агентов (Cline / opencode / Claude Code / Codex / GLM): анализирует проект → собирает AGENTS.md на русском → валидирует по базовым требованиям стандарта.

Часть экосистемы **ai-harness-os**. Зависимости: **чистый stdlib** — ни сети, ни шаблонизаторов, ни секретов.

## Зачем

- **AGENTS.md пишут руками — и пишут плохо:** не та структура, нет команд, устаревает. Генератор снимает рутину: файл-черновик за секунды, человек только правит и коммитит.
- **1С-проекты — первый класс.** Детект EDT-проектов (`.mdo`) и XML-выгрузок Конфигуратора: структура метаданных по-русски (`Catalogs → Справочники`), стандарты 1С, bsl-language-server в Code Style.
- **Каскадный лимит — всерьёз.** Валидатор следит за 32 KiB (жёсткий лимит consumers стандарта agents.md) и наличием разделов, которые агент реально ищет.
- **Синергия с харнессом:** сгенерированный AGENTS.md — это `--context-file` для `harness-loop run` (контекст проекта в промпте цикла).

## Установка

```bash
cd ai-harness-os/packages/agents-md
pip install -e .
```

## Использование

```bash
cd my-project

agents-md init                # анализ → AGENTS.md в корне проекта
agents-md init --stdout       # только напечатать, не писать файл
agents-md init --force        # перезаписать существующий

agents-md validate            # проверить AGENTS.md (разделы + 32 KiB + UTF-8)
agents-md validate --file docs/AGENTS.md   # явный путь
```

Что печатает `init`:

```
Анализ проекта: /home/me/my-1c-config
  my-1c-config: kind=1c-edt langs=BSL/1С (EDT) lint=bsl-language-server
Сгенерировано: /home/me/my-1c-config/AGENTS.md (3182 bytes)
Проверь, поправь под реальность и закоммить. Валидация: agents-md validate
```

Коды выхода: `0` — готово/валидно, `1` — валидация не прошла, `2` — ошибка использования (нет директории, перезапись без `--force`).

## Что умеет анализатор

| Тип проекта | Маркеры |
|---|---|
| `1c-edt` | `Configuration.mdo`, кластер `.mdo`-файлов |
| `1c-xml` | `Configuration.xml` (в корне / `src/`) |
| `python` | `pyproject.toml` (имя + requires-python), `requirements.txt`, `setup.py` |
| `js-ts` | `package.json` (имя, devDeps), `tsconfig.json` |
| `generic` | ничего из вышеперечисленного — честный шаблон «дополни руками» |

Поверх типа — маркеры тулинга: тест-раннер (`pytest`/`vitest`/`jest`), CI (GitHub Actions / GitLab CI / CircleCI), Docker, линтеры (`ruff`/`black`/`mypy`/`eslint`/`prettier`; для 1С — `bsl-language-server`), структура каталогов (счётчик файлов, доминирующие расширения).

Приоритет типа: `1c-edt > 1c-xml > python > js-ts > generic` — 1С-проект с питон-тулингом остаётся 1С-проектом.

## Что генерируется

AGENTS.md на русском: `Project Overview` (тип, язык, заметки), `Setup` (prerequisites + команды для типа), `Architecture` (дерево каталогов с комментариями), `Code Style` (стандарты типа + найденные линтеры с командами), `Testing` (раннер + команды), `Common Tasks` (шаблонные сценарии: новый справочник, внешняя обработка, деплой — для 1С), `Do NOT` (запреты типа + общие), `Log`.

Сгенерированный файл **проходит `agents-md validate`** — это гарантировано тестами для всех пяти типов проектов.

## Разработка

```bash
pip install -e "packages/agents-md[dev]"
pytest -q                      # герметично: tmp-проекты, без сети
```

## Roadmap

- **v0.1** — init/validate, 5 типов, RU-шаблоны ✅
- **v0.2** — каскад: module-level AGENTS.md для крупных директорий; `update` (diff при изменениях), git-hook
- **v0.3** — 1С-глубина: подсчёт объектов метаданных из `.mdo`/XML (справочники/документы/регистры) в Architecture

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
