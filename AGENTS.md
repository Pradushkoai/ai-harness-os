# AGENTS.md — ai-harness-os

Правила проекта для AI-агентов (Cline / opencode / Claude Code / GLM) и людей.

## Проект

`russian-llm-pack` (RLP) — LLM-слой харнесса для 1С: единый порт, YAML-роутинг задач, fallback-цепочки. Часть экосистемы ai-harness-os.

## Жёсткие правила

1. **Секреты — только env.** Ни API-ключей, ни токенов в коде, тестах, конфигах, коммит-месседжах. Ключи читаются через `api_key_env` в конфиге. `.env` в `.gitignore` навсегда.
2. **Харнесс знает только `LLMPort`.** Новые провайдеры = новые адаптеры в `providers/`, изменения в `PRESETS`. `ports/llm_port.py` меняется только с мажорной версией.
3. **Не переинженерь.** Новая фича добавляется только когда харнесс её реально потребил. «Понадобится потом» — не причина.
4. **Тесты без сети.** Юнит-тесты гоняются на моках (injected client / FakeProvider). Живые вызовы — только `pytest -m live`, только с env-ключами, минимум токенов.
5. **Формат — ruff-совместимый** (line length 100), docstrings на английском, README/сообщения для пользователей — на русском.
6. **Retry-политика — в Router, не в SDK-клиенте** (`max_retries=0` в адаптерах). Классификация ошибок: auth → skip провайдера; transient → retry; request → следующая модель.

## Структура

```
src/russian_llm_pack/
├── types.py        # сообщения, результаты, исключения — ядро домена
├── ports/          # LLMPort — единственный стабильный интерфейс
├── providers/      # generic OpenAI-compat движок + пресеты провайдеров
├── core/           # config (YAML + env), router (fallback, события)
└── cli.py          # rlp check / rlp chat
```

## Команды

```bash
pip install -e ".[dev]"
pytest -q                 # юнит
pytest -m live -v         # живой smoke (нужны env-ключи)
rlp check                 # что настроено
```

## Куда расти (по порядку)

v0.2: async-порт → GigaChat OAuth → нативный YandexGPT → embeddings. v0.3: Langfuse-хук, per-project data policies, upstream в LiteLLM.
