# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

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
