# harness-mcp

**MCP-выход репозитория ai-harness-os** (дорожная карта 2.1, фаза B): stdio-сервер
[Model Context Protocol](https://modelcontextprotocol.io), который отдаёт харнесс
любому агенту — Claude Code, opencode, Cline — как пять тулингов поверх готовых портов.
Новой движковой логики нет: это тонкие адаптеры к `harness-loop`, `bsl-verify` и
`russian-llm-pack`.

## Тулинги

| Тулинг | Что делает | Скорость |
|---|---|---|
| `ping` | Health-check: версии пакетов, наличие java и OneScript | мгновенно |
| `benchmark_info` | Профиль бенчмарка: размер, категории, сложность, покрытие L1 и честные ограничения | мгновенно |
| `verify_module` | L0-проверка текста BSL-модуля через bsl-language-server: синтаксис + диагностика с номерами строк | секунды |
| `run_loop` | Полный цикл «генерация → верификация → (судья)»: возвращает финальный код и лог итераций; `run_id` позволяет переспросить детали без перезапуска цикла (кэш 15 минут) | десятки секунд |
| `eval_summary` | Прогон бенчмарка с краткой сводкой L0/L1/L2; по умолчанию **10 задач** (MCP-тулинг обязан отвечать за секунды), полный набор — явным `full: true` | минуты |

## Подключение (три готовых конфига)

Установка (клон репо + venv одной командой): `bash scripts/setup.sh --clone` —
пакеты ставятся из подпапок, в корне нет `pyproject.toml`.

### Claude Code

`~/.claude.json` (или проектный `.mcp.json`):

```json
{
  "mcpServers": {
    "ai-harness-os": {
      "command": "/путь/к/ai-harness-os/.venv/bin/harness-mcp",
      "env": {
        "RLP_CONFIG": "/путь/к/rlp.yaml",
        "BSL_JAVA_PATH": "/путь/к/java",
        "BSL_JAR_PATH": "/путь/к/bsl-language-server.jar",
        "OSCRIPT_PATH": "/путь/к/oscript"
      }
    }
  }
}
```

### opencode

`opencode.json` в корне проекта:

```json
{
  "mcp": {
    "ai-harness-os": {
      "type": "local",
      "command": ["/путь/к/ai-harness-os/.venv/bin/harness-mcp"],
      "environment": {
        "RLP_CONFIG": "/путь/к/rlp.yaml",
        "BSL_JAVA_PATH": "/путь/к/java",
        "BSL_JAR_PATH": "/путь/к/bsl-language-server.jar",
        "OSCRIPT_PATH": "/путь/к/oscript"
      }
    }
  }
}
```

### Cline

`cline_mcp_settings.json` (VS Code):

```json
{
  "mcpServers": {
    "ai-harness-os": {
      "command": "/путь/к/ai-harness-os/.venv/bin/harness-mcp",
      "env": {
        "RLP_CONFIG": "/путь/к/rlp.yaml",
        "BSL_JAVA_PATH": "/путь/к/java",
        "BSL_JAR_PATH": "/путь/к/bsl-language-server.jar",
        "OSCRIPT_PATH": "/путь/к/oscript"
      },
      "disabled": false,
      "autoApprove": ["ping", "benchmark_info", "verify_module"]
    }
  }
}
```

Windows: замените путь на `...\ai-harness-os\.venv\Scripts\harness-mcp.exe`.
Все `env`-переменные опциональны: без java `verify_module` честно скажет
«недоступен», без `RLP_CONFIG` `run_loop`/`eval_summary` потребуют настроить
russian-llm-pack, без OneScript отключается только L1-оракул.

## Окружение

| Переменная | Назначение | Дефолт |
|---|---|---|
| `RLP_CONFIG` | YAML-конфиг роутера LLM (секреты — только env, см. russian-llm-pack) | дискавери пакета |
| `BSL_JAVA_PATH` | java для bsl-language-server | PATH |
| `BSL_JAR_PATH` | jar bsl-language-server | дискавери |
| `OSCRIPT_PATH` | движок OneScript для L1-оракула | PATH / `OSCRIPT_HOME` |
| `HARNESS_STRICT_VERIFY` | `1` = верификатор не игнорирует LS-ложные срабатывания (`InvalidCharacterInFile`, em-dash в комментариях) | не задан |

Секреты никогда не попадают в YAML и на провод — только имена env-переменных
(правило репозитория №1).

## Протокол

JSON-RPC 2.0 поверх stdio, одно сообщение на строку (транспорт MCP). Поддерживается
минимальный вертикальный срез: `initialize`, `notifications/*` (без ответа),
`tools/list`, `tools/call`, стандартный `ping`. Ошибки тулинга приходят как
`isError: true` в контенте (протокол жив), краши — как `-32603`.

## Разработка

```bash
pip install -e "packages/harness-mcp[dev]"
pytest -q          # юниты: фейки, без сети/java/ключей; subprocess-smoke поднимает сервер
ruff check --select E4,E7,E9,F,E501 --line-length 100 .
```

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
