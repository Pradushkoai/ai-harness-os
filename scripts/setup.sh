#!/usr/bin/env bash
# =============================================================================
# ai-harness-os — установка и тестирование за один запуск.
#
# Где работает:  Linux, macOS, WSL, Git Bash (Windows).
# Что делает:
#   1. находит репо (или клонирует сам — флаг --clone)
#   2. находит Python 3.10+ (python3 / python / py -3)
#   3. создаёт .venv и ставит все 4 пакета editable (+dev)
#   4. гоняет юнит-тесты (без сети, java и ключей — маркеры live/integration
#      деселектятся автоматически)
#   5. CLI-smoke: версии, rlp check, harness-loop doctor
#   6. опционально: --live  — живой LLM-smoke (DeepSeek/Qwen, ~1 цента)
#                   --with-jar — скачать bsl-language-server (124 MB) для eval
#
# Использование:
#   bash scripts/setup.sh                  # репо уже склонирован (запуск из корня)
#   bash scripts/setup.sh --clone                     # сначала git clone в ./ai-harness-os
#   bash scripts/setup.sh --clone --live --with-jar
#   bash scripts/setup.sh --clone --repo <URL|путь>   # из форка/локальной копии
#   bash scripts/setup.sh --skip-tests                # только установка
#
# Ключи читаются из окружения или из ./.env в корне репо (KEY=VALUE, без
# экспорта вручную). Секреты НИКОГДА не попадают в репо и в лог скрипта.
# =============================================================================
set -u -o pipefail

REPO_URL="https://github.com/Pradushkoai/ai-harness-os.git"
CLONE_DIR="ai-harness-os"
BSL_JAR_URL="https://github.com/1c-syntax/bsl-language-server/releases/download/v1.0.7/bsl-language-server-1.0.7-exec.jar"
BSL_JAR_DIR="$HOME/.bsl-language-server"
BSL_JAR_FILE="bsl-language-server.jar"

DO_CLONE=0; DO_LIVE=0; DO_JAR=0; DO_TESTS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --clone)      DO_CLONE=1 ;;
    --live)       DO_LIVE=1 ;;
    --with-jar)   DO_JAR=1 ;;
    --skip-tests) DO_TESTS=0 ;;
    --repo)       shift; REPO_URL="${1:-}" ;;
    -h|--help)    sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Неизвестный флаг: $1 (см. --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ -z "${REPO_URL:-}" ]; then
  echo "--repo требует аргумент: URL или путь" >&2; exit 2
fi

# -- цветной вывод, только в терминале ----------------------------------------
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_INFO=$'\033[36m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_INFO=""; C_OFF=""
fi
step()  { printf '\n%s==> %s%s\n' "$C_INFO" "$*" "$C_OFF"; }
ok()    { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn()  { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
fail()  { printf '%s[FAIL]%s %s\n' "$C_ERR" "$C_OFF" "$*"; }
FAILED=0

# =============================================================================
step "1/6  Репозиторий"
# =============================================================================
if [ "$DO_CLONE" -eq 1 ]; then
  if [ -d "$CLONE_DIR/packages" ]; then
    warn "$CLONE_DIR уже существует — использую как есть (git pull --ff-only)"
    (cd "$CLONE_DIR" && git pull --ff-only 2>/dev/null) || warn "git pull не удался — продолжаю на текущем состоянии"
  else
    git clone "$REPO_URL" "$CLONE_DIR" || { fail "git clone $REPO_URL"; exit 1; }
  fi
  cd "$CLONE_DIR" || exit 1
else
  # ищем корень репо: от cwd вверх и от пути скрипта
  ROOT=""
  for base in "$PWD" "$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"; do
    d="$base"
    while [ "$d" != "/" ]; do
      if [ -d "$d/packages/russian-llm-pack" ]; then ROOT="$d"; break; fi
      d=$(dirname "$d")
    done
    [ -n "$ROOT" ] && break
  done
  if [ -z "$ROOT" ]; then
    fail "не нашёл корень репо (packages/russian-llm-pack). Запусти из корня: bash scripts/setup.sh — или добавь флаг --clone"
    exit 1
  fi
  cd "$ROOT" || exit 1
fi
ok "репо: $(pwd)"

# .env в корне репо подхватываем автоматически (CRLF-безопасно)
if [ -f ".env" ]; then
  _env_clean=$(mktemp)
  sed 's/\r$//' .env > "$_env_clean"
  set -a; . "$_env_clean"; set +a
  rm -f "$_env_clean"
  ok "подхватил ./.env (ключи не логируются)"
fi

# =============================================================================
step "2/6  Python 3.10+"
# =============================================================================
PY=""
for cand in python3 python py; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if [ "$cand" = "py" ]; then ver_cmd="py -3"; else ver_cmd="$cand"; fi
  if $ver_cmd -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$ver_cmd"; break
  fi
done
if [ -z "$PY" ]; then
  fail "Python 3.10+ не найден. Установи с python.org (Windows: отметь 'Add to PATH') или apt install python3 python3-venv"
  exit 1
fi
ok "python: $PY ($($PY -V 2>&1))"

# =============================================================================
step "3/6  Виртуальное окружение + пакеты"
# =============================================================================
if [ ! -d ".venv" ]; then
  $PY -m venv .venv || { fail "не смог создать .venv (Debian/Ubuntu: sudo apt install python3-venv)"; exit 1; }
  ok "создан .venv"
else
  ok ".venv уже существует — переиспользую"
fi

# платформенно-зависимый python внутри venv
if [ -x ".venv/Scripts/python.exe" ]; then
  VENV_PY=".venv/Scripts/python.exe"; VENV_BIN=".venv/Scripts"; IS_WINDOWS=1
else
  VENV_PY=".venv/bin/python"; VENV_BIN=".venv/bin"; IS_WINDOWS=0
fi

"$VENV_PY" -m pip install --quiet --upgrade pip || warn "pip upgrade не удался (не критично)"

PACKAGES="russian-llm-pack bsl-verify harness-loop agents-md"
for pkg in $PACKAGES; do
  step "  pip install -e packages/$pkg[dev]"
  if "$VENV_PY" -m pip install --quiet -e "packages/$pkg[dev]"; then
    ok "packages/$pkg установлен"
  else
    fail "установка packages/$pkg"; FAILED=1
  fi
done
if [ "$FAILED" -eq 1 ]; then
  fail "установка не завершена — дальше идти нет смысла"
  exit 1
fi

# CLI-обёртка: на Windows bash может не разрешить 'rlp' без .exe
run_cli() {
  name="$1"; shift
  if [ -x "$VENV_BIN/$name" ]; then
    "$VENV_BIN/$name" "$@"
  elif [ -x "$VENV_BIN/$name.exe" ]; then
    "$VENV_BIN/$name.exe" "$@"
  else
    "$VENV_PY" -c 'import sys, importlib; m = importlib.import_module(sys.argv[1] + ".cli"); sys.exit(m.main())' "$name" "$@"
  fi
}

# =============================================================================
step "4/6  Юнит-тесты (без сети / java / ключей)"
# =============================================================================
if [ "$DO_TESTS" -eq 1 ]; then
  TOTAL_PASSED=0
  for pkg in $PACKAGES; do
    printf '\n-- %s\n' "$pkg"
    out=$(cd "packages/$pkg" && "$OLDPWD/$VENV_PY" -m pytest -q 2>&1 | tail -1) || true
    echo "   $out"
    n=$(printf '%s' "$out" | sed -n 's/^\([0-9]*\) passed.*/\1/p')
    TOTAL_PASSED=$((TOTAL_PASSED + ${n:-0}))
    case "$out" in
      *"failed"*|*"error"*) fail "packages/$pkg"; FAILED=1 ;;
      *) ok "packages/$pkg зелёный" ;;
    esac
  done
  ok "итого юнит-тестов прошло: $TOTAL_PASSED"
else
  warn "тесты пропущены (--skip-tests)"
fi

# =============================================================================
step "5/6  CLI-smoke"
# =============================================================================
for cli in rlp bsl-check harness-loop agents-md; do
  if out=$(run_cli "$cli" --version 2>&1); then
    ok "$out"
  else
    fail "$cli --version: $out"; FAILED=1
  fi
done

echo
echo "-- rlp check (ключи и цепочки) --"
run_cli rlp check || true

echo
echo "-- harness-loop doctor (оба слоя: LLM-ключи + java/jar) --"
run_cli harness-loop doctor || true

# =============================================================================
step "6/6  Опционально: bsl-language-server.jar / live-smoke"
# =============================================================================
if [ "$DO_JAR" -eq 1 ]; then
  if command -v curl >/dev/null 2>&1; then FETCH="curl -L --fail --progress-bar -o"
  elif command -v wget >/dev/null 2>&1; then FETCH="wget --quiet -O"
  else FETCH=""; warn "нет ни curl, ни wget — скачай jar руками: $BSL_JAR_URL"; fi
  if [ -n "$FETCH" ]; then
    mkdir -p "$BSL_JAR_DIR"
    if [ -f "$BSL_JAR_DIR/$BSL_JAR_FILE" ]; then
      ok "jar уже лежит в $BSL_JAR_DIR/$BSL_JAR_FILE"
    else
      echo "качаю 124 МБ: $BSL_JAR_URL"
      if $FETCH "$BSL_JAR_DIR/$BSL_JAR_FILE.tmp" "$BSL_JAR_URL"; then
        mv "$BSL_JAR_DIR/$BSL_JAR_FILE.tmp" "$BSL_JAR_DIR/$BSL_JAR_FILE"
        size=$(wc -c < "$BSL_JAR_DIR/$BSL_JAR_FILE" | tr -d ' ')
        if [ "${size:-0}" -gt 50000000 ]; then
          ok "jar: $BSL_JAR_DIR/$BSL_JAR_FILE ($((size / 1024 / 1024)) МБ)"
        else
          rm -f "$BSL_JAR_DIR/$BSL_JAR_FILE"
          fail "jar скачался подозрительно маленьким — удалил, попробуй ещё раз"
        fi
      else
        rm -f "$BSL_JAR_DIR/$BSL_JAR_FILE.tmp"
        fail "не удалось скачать jar (см. URL выше)"
      fi
    fi
  fi
fi

if [ "$DO_LIVE" -eq 1 ]; then
  if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    step "  live-smoke DeepSeek (генератор по умолчанию)"
    if out=$(run_cli rlp chat --model deepseek/deepseek-chat --max-tokens 32 --temperature 0 "Ответь ровно одним словом: работает?" 2>&1); then
      ok "deepseek ответил:"
      printf '%s\n' "$out"
    else
      fail "deepseek: $out"
    fi
  else
    warn "DEEPSEEK_API_KEY не задан — live-smoke генератора пропущен"
  fi
  if [ -n "${QWEN_API_KEY:-}" ] || [ -n "${DASHSCOPE_API_KEY:-}" ]; then
    step "  live-smoke Qwen (судья по умолчанию с RLP v0.3)"
    if out=$(run_cli rlp chat --model qwen/qwen-max --max-tokens 32 --temperature 0 "Ответь ровно одним словом: работает?" 2>&1); then
      ok "qwen ответил:"
      printf '%s\n' "$out"
    else
      fail "qwen: $out (mainland-аккаунт? добавь в rlp.config.yaml: providers: {qwen: {base_url: https://dashscope.aliyuncs.com/compatible-mode/v1}})"
    fi
  else
    warn "QWEN_API_KEY / DASHSCOPE_API_KEY не заданы — live-smoke судьи пропущен"
  fi
fi

# =============================================================================
step "Итог"
# =============================================================================
echo "Репо:        $(pwd)"
if [ "$IS_WINDOWS" -eq 1 ]; then
  echo "venv:        .venv (Windows-раскладка)"
  echo "Активация:   Git Bash: source .venv/Scripts/activate | PowerShell: .venv\\Scripts\\Activate.ps1 | cmd: .venv\\Scripts\\activate.bat"
else
  echo "venv:        .venv (POSIX-раскладка)"
  echo "Активация:   source .venv/bin/activate"
fi
echo "Ключи:       export DEEPSEEK_API_KEY=... QWEN_API_KEY=...   (или файл ./.env)"
echo
if [ "$FAILED" -eq 0 ]; then
  ok "УСТАНОВКА И ТЕСТЫ ПРОШЛИ"
else
  fail "БЫЛИ ПРОБЛЕМЫ — см. [FAIL] выше"
fi
echo
echo "Что дальше (судейский прогон бенчмарка — то, ради чего всё затевалось):"
echo "  harness-loop eval --judge --save-report report.json --markdown report.md --verbose"
echo "  # судья по цепочке: qwen/qwen-max -> zai -> deepseek; генератор: deepseek-chat"
echo "  # A/B судьи без эталонов:     ... --no-judge-reference"
echo "  # срез по сложности:          ... --difficulty hard"
echo "  # перед eval нужен java 17+ и jar — harness-loop doctor подскажет"
exit "$FAILED"
