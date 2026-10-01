# Russian LLM Pack (RLP)

Единый LLM-порт для «санкционно-устойчивых» провайдеров — **DeepSeek, Z.ai (GLM), GigaChat, YandexGPT** — с декларативной маршрутизацией задач по моделям и автоматическим fallback.

Часть проекта **ai-harness-os**: LLM-слой харнесса для 1С-разработки, вынесенный в чистый переиспользуемый модуль.

## Зачем это

- **Один интерфейс — много провайдеров.** Харнесс знает только `LLMPort`. Смена модели = строчка в YAML, смена провайдера = адаптер на сотню строк.
- **Fallback из коробки.** Упал DeepSeek → поехал Z.ai → GigaChat. Retry-политика как у Stripe: мало попыток, никаких бесконечных.
- **Ключи только через env.** YAML не содержит секретов вообще. Коммит-хистори чистая по построению.
- **Задачи вместо моделей.** `coding` / `reasoning` / `cheap` / `judge` — роутер сам выбирает цепочку. Судья ≠ модель, писавшая код.

## Установка

```bash
git clone https://github.com/Pradushkoai/ai-harness-os
cd ai-harness-os
pip install -e ".[dev]"
```

Ключи — в переменных окружения (см. `.env.example`):

```bash
export DEEPSEEK_API_KEY=sk-...
export ZAI_API_KEY=...                    # опционально
export YANDEXGPT_API_KEY=...              # опционально, нативный адаптер v0.2
export YANDEXGPT_FOLDER_ID=b1g...         # обязателен для YandexGPT
export GIGACHAT_AUTH_KEY=...              # опционально, OAuth сам получит токен
```

## Быстрый старт

```bash
# что настроено, какие ключи видны, какие цепочки
rlp check

# разовый запрос через роутер (задача coding по умолчанию)
rlp chat "Напиши функцию на BSL для расчёта скидки"

# явно указать задачу / модель / стрим / параметры
rlp chat --task reasoning "почему этот запрос медленный?" --stream
rlp chat --model deepseek/deepseek-chat "привет" --temperature 0 --max-tokens 64
rlp chat --system "Ты 1С-разработчик" "отрефактори модуль" --verbose
```

## Использование как библиотека

```python
from russian_llm_pack import Router, load_config, ChatMessage

config, _ = load_config()
router = Router.from_config(config)

result = router.complete("coding", [
    ChatMessage.system("Ты senior 1С-разработчик"),
    ChatMessage.user("Отрефактори этот модуль: ..."),
])

print(result.text)          # ответ
print(result.provider)      # кто реально ответил ("deepseek")
print(result.usage.total_tokens)
```

События роутинга (skip / retry / error / ok) — колбэком:

```python
router = Router.from_config(config, on_event=lambda e: print(e))
# {'event': 'ok', 'task': 'coding', 'ref': 'deepseek/deepseek-chat', ...}
```

Дальше этот же хук уходит в Langfuse — интерфейс уже готов.

## Конфигурация

RLP ищет конфиг так: `$RLP_CONFIG` → `./rlp.config.yaml` → builtin-дефолты.

```yaml
# rlp.config.yaml
providers:
  zai:
    base_url: https://api.z.ai/api/paas/v4   # override при необходимости
  deepseek:
    api_key_env: DEEPSEEK_API_KEY            # имя env-переменной
  yandexgpt:
    api_key_env: YANDEXGPT_API_KEY           # + YANDEXGPT_FOLDER_ID в env
  gigachat:
    api_key_env: GIGACHAT_AUTH_KEY           # OAuth внутри адаптера

routing:
  tasks:
    coding:      [deepseek/deepseek-chat, zai/glm-4.6, gigachat/GigaChat-Pro, yandexgpt/yandexgpt]
    reasoning:   [deepseek/deepseek-reasoner, zai/glm-4.6, yandexgpt/yandexgpt]
    cheap:       [zai/glm-4.5-air, deepseek/deepseek-chat]
    judge:       [zai/glm-4.6, deepseek/deepseek-chat]   # судья ≠ кодер
  params:
    coding:    {temperature: 0.2}
    judge:     {temperature: 0.0}

defaults:
  task: coding
  retries: 2        # повторов на transient-ошибку (429/5xx/timeout)
  timeout_s: 60
```

Полный пример — `config.example.yaml`.

**Провайдер без ключа не ломает систему** — роутер просто пропускает его в цепочке. Только DeepSeek ключ? Всё работает. Появился Z.ai — добавился в fallback без единой правки кода.

## Провайдеры

| Провайдер | Статус | Заметки |
|---|---|---|
| `deepseek` | ✅ работает | OpenAI-compatible; `deepseek-reasoner` — chain-of-thought |
| `zai` | ✅ работает | GLM; международный endpoint, есть mainland-альтернатива |
| `yandexgpt` | ✅ адаптер v0.2 | нативный протокол: Api-Key/IAM + `YANDEXGPT_FOLDER_ID`, `modelUri = gpt://<folder>/<model>`; модель с `://` в имени проходит как есть (`ds://…` файнтюны) |
| `gigachat` | ⚠️ адаптер v0.2, live-проверки ждут ключа | OAuth Basic `GIGACHAT_AUTH_KEY` → токен с авто-refresh (~30 мин); TLS российского CA — при ошибке хендшейка см. `GIGACHAT_CA_BUNDLE` / `GIGACHAT_ALLOW_INSECURE` |

Нативные адаптеры (`yandexgpt`, `gigachat`) написаны на чистом stdlib (`urllib`, без `openai`-SDK): OAuth-танцы и TLS-политики — внутреннее дело адаптера, наружу — тот же `LLMPort`. `yandexgpt` замыкает все builtin-цепи: **только Яндекс-аккаунт — уже рабочий сетап**, санкционная устойчивость по построению.

## Архитектура

```
                ┌─────────────────────────┐
                │   HARNESS (будущее)      │
                │   знает ТОЛЬКО LLMPort   │
                └───────────┬─────────────┘
                            │
            ┌───────────────▼───────────────┐
            │          Router (core)        │
            │  task → chain → fallback      │
            │  retry / skip / events        │
            └───────┬───────────┬───────────┘
                    │           │
        ┌───────────▼──┐  ┌─────▼──────────────┐
        │ OpenAI-compat│  │ native adapters     │
        │  deepseek    │  │  yandexgpt (v0.2)   │
        │  zai         │  │  gigachat oauth(0.2)│
        │  gigachat*   │  └────────────────────┘
        └──────────────┘
```

Hexagonal: `ports/` (стабильный интерфейс) ← `providers/` (заменяемые адаптеры) + `core/` (домен: роутинг, fallback) + `cli.py` (тонкая обвязка).

## Разработка

```bash
pip install -e ".[dev]"
pytest -q                      # юнит-тесты, без сети и ключей
DEEPSEEK_API_KEY=... pytest -m live -v                        # живой smoke DeepSeek
YANDEXGPT_API_KEY=... YANDEXGPT_FOLDER_ID=... pytest -m live -v   # + нативный YandexGPT
GIGACHAT_AUTH_KEY=... pytest -m live -v                       # + нативный GigaChat
```

## Roadmap

- **v0.1** — порт, generic-адаптер, DeepSeek + Z.ai, роутер с fallback, CLI ✅
- **v0.2** — нативный YandexGPT-адаптер (Api-Key/IAM + folder), GigaChat OAuth-flow с авто-refresh, stdlib-транспорт для нативных адаптеров, yandexgpt-fallback во всех цепях ✅
- **v0.3** — async-порт (`acomplete`/`astream`), embeddings (`embed()`), cost-таблица в usage, Langfuse-хук из коробки, upstream PR'ы в LiteLLM

## Лицензия

MIT. См. [LICENSE](LICENSE).
