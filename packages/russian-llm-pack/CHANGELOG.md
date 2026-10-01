# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.2.0] — 2026-10-01

### Added
- **Нативный `YandexGPTAdapter`** (Phase 0 по вердикту): протокол Yandex Foundation Models —
  `modelUri`, `{role, text}`-сообщения, `x-folder-id`; авторизация `Api-Key` (env
  `YANDEXGPT_API_KEY`, предпочтительно) или `Bearer` IAM-токен (`YANDEXGPT_IAM_TOKEN`, ~12ч,
  без авто-refresh); `YANDEXGPT_FOLDER_ID` обязателен; модель с `://` в имени проходит как
  есть (файнтюны `ds://…`); usage-строки → int; маппинг статусов (FINAL→stop,
  TRUNCATED→length, CONTENT_FILTER→content_filter).
- **Нативный `GigaChatAdapter`**: OAuth Basic (`GIGACHAT_AUTH_KEY` → access-токен с
  авто-refresh за 60с до истечения, RqUID per request) либо готовый токен
  (`GIGACHAT_ACCESS_TOKEN`, без OAuth); TLS-политика: верификация по умолчанию, при ошибке
  российского CA — понятный ProviderRequestError с двумя фиксами (`GIGACHAT_CA_BUNDLE` /
  `GIGACHAT_ALLOW_INSECURE=1`).
- `providers/_http.py` — общий stdlib-транспорт (urllib, JSON in/out, injectable seam для
  тестов): нативные адаптеры не тянут `openai`-SDK.
- `yandexgpt` замыкает все builtin-цепи (coding/reasoning/cheap/judge): сетап с одним лишь
  Яндекс-аккаунтом работоспособен — санкционная устойчивость.
- Live smoke для обоих нативных провайдеров (`pytest -m live`, скипаются без ключей).
- 62 новых юнит-теста на фейковом транспорте (итого 125).

### Changed
- Пресет `gigachat`: primary env теперь `GIGACHAT_AUTH_KEY` (OAuth), `GIGACHAT_ACCESS_TOKEN`
  — альтернатива; пресет `yandexgpt`: primary env `YANDEXGPT_API_KEY` (было `YAIAM_TOKEN`).
- `rlp check` честно показывает наличие любого из кредов нативного провайдера (API key или
  IAM-токен / auth key или access-токен).

## [0.1.0] — 2026-09-30

### Added
- `LLMPort` — стабильный интерфейс LLM-слоя (complete / stream), sync-first.
- Generic `OpenAICompatibleAdapter` — один движок для всех OpenAI-compatible провайдеров, с маппингом ошибок (auth / transient / request).
- Пресеты провайдеров: `deepseek` (готов), `zai` (готов), `gigachat` (experimental, access-token), `yandexgpt` (native, v0.2).
- `Router` — маршрутизация задач (coding / reasoning / cheap / judge) по fallback-цепочкам с retry-политикой и событиями (skip / retry / error / ok).
- YAML-конфиг с env-подстановкой `${VAR}`, автодискавери (`$RLP_CONFIG` → `./rlp.config.yaml` → builtin).
- CLI `rlp`: `check` (статус провайдеров и цепочек), `chat` (one-shot / stream / --model / --task / --verbose).
- Юнит-тесты на моках (без сети), live smoke-тесты (`pytest -m live`).
- CI на GitHub Actions: Python 3.10–3.13.
