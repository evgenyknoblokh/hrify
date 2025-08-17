import os
import time
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Tuple

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from langdetect import detect, DetectorFactory, LangDetectException

# =========================
# Инициализация
# =========================
load_dotenv()
DetectorFactory.seed = 42

app = Flask(__name__, static_folder="static", template_folder="templates")

# Rate limiting (простейшая реализация)
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "50")) # количество запросов
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # секунд
request_log = defaultdict(list)

# Запрещённые слова (пример)
BANNED_WORDS = [w.strip().lower() for w in os.getenv("BANNED_WORDS", "дурак,идиот").split(",") if w.strip()]

# Путь к файлу промптов
PROMPTS_PATH = Path(os.getenv("PROMPTS_PATH", "prompts.json")).resolve()
PROMPTS_RELOAD = os.getenv("PROMPTS_RELOAD", "1") == "1"  # автоперезагрузка при изменении файла

# =========================
# I18n для сообщений ошибок UI
# =========================
def tr(ui_lang: str, ru: str, en: str, es: str) -> str:
    ui_lang = (ui_lang or "ru").lower()
    if ui_lang.startswith("ru"):
        return ru
    if ui_lang.startswith("es"):
        return es
    return en

# =========================
# Вспомогательные функции
# =========================
def is_rate_limited(ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    log = request_log[ip]
    request_log[ip] = [t for t in log if t > window_start]
    if len(request_log[ip]) >= RATE_LIMIT_REQUESTS:
        return True
    request_log[ip].append(now)
    return False


def contains_banned_words(text: str) -> bool:
    lowered = (text or "").lower()
    return any(bad in lowered for bad in BANNED_WORDS)


def detect_input_lang(text: str) -> str:
    try:
        code = detect(text or "")
    except LangDetectException:
        return "unknown"
    if code.startswith("ru"):
        return "ru"
    if code.startswith("en"):
        return "en"
    if code.startswith("es"):
        return "es"
    return "unknown"


def pick_lang(input_lang: str, ui_lang: str) -> str:
    if input_lang in ("ru", "en", "es"):
        return input_lang
    return ui_lang if ui_lang in ("ru", "en", "es") else "ru"

# =========================
# Загрузка PROMPTS из JSON
# =========================
_prompts: Dict[str, Dict[str, str]] = {}
_prompts_mtime: float = 0.0

def _validate_prompts(p: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Проверяем структуру:
    {
      "<lang>": {
        "reject": "<text>",
        "hire": "<text>",
        "remind": "<text>"
      }, ...
    }
    """
    if not isinstance(p, dict):
        return False, "prompts root must be an object"
    for lang, mapping in p.items():
        if not isinstance(mapping, dict):
            return False, f"prompts['{lang}'] must be an object"
        for key in ("reject", "hire", "remind"):
            if key not in mapping or not isinstance(mapping[key], str) or not mapping[key].strip():
                return False, f"prompts['{lang}']['{key}'] must be a non-empty string"
    return True, ""

def load_prompts(force: bool = False) -> None:
    """Читает/перечитывает prompts.json при необходимости."""
    global _prompts, _prompts_mtime
    if not PROMPTS_PATH.exists():
        raise RuntimeError(f"Prompts file not found: {PROMPTS_PATH}")

    stat = PROMPTS_PATH.stat()
    if not force and not PROMPTS_RELOAD and _prompts:  # уже загружено и reload отключён
        return
    if not force and _prompts and _prompts_mtime >= stat.st_mtime:
        # файл не менялся
        return

    with PROMPTS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    ok, msg = _validate_prompts(data)
    if not ok:
        raise RuntimeError(f"Invalid prompts format: {msg}")

    _prompts = data
    _prompts_mtime = stat.st_mtime

def get_prompt(lang: str, scenario: str) -> str:
    """Возвращает текст промпта, учитывая наличие языка/сценария, с фоллбеком."""
    # пытаться перезагрузить при каждом запросе, если включено PROMPTS_RELOAD
    try:
        load_prompts(force=False)
    except Exception as e:
        # логируем, но не падаем: используем предыдущую успешную версию (если была)
        print("Prompts load error:", e)
        if not _prompts:
            # вообще ничего нет — фатально
            raise

    # основной язык
    if lang in _prompts and scenario in _prompts[lang]:
        return _prompts[lang][scenario]

    # фоллбек на RU → EN → ES
    for fallback in ("ru", "en", "es"):
        if fallback in _prompts and scenario in _prompts[fallback]:
            return _prompts[fallback][scenario]

    raise RuntimeError(f"No prompt found for scenario='{scenario}' in any language")

# =========================
# OpenAI клиент (ленивая инициализация)
# =========================
_openai_client = None

def get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(f"Failed to import openai SDK: {e}")

    _openai_client = OpenAI(api_key=api_key)
    return _openai_client

# =========================
# Маршруты
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

@app.get("/debug/env")
def debug_env():
    return jsonify(
        ok=True,
        openai_api_key_present=bool(os.getenv("OPENAI_API_KEY")),
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        prompts_path=str(PROMPTS_PATH),
        prompts_loaded=bool(_prompts),
        prompts_mtime=_prompts_mtime
    )

@app.get("/debug/prompts")
def debug_prompts():
    # показываем только доступные языки и ключи сценариев, без содержания
    try:
        load_prompts(force=False)
        summary = {lang: sorted(list(mapping.keys())) for lang, mapping in _prompts.items()}
        return jsonify(ok=True, languages=sorted(list(_prompts.keys())), scenarios_by_lang=summary)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.post("/process")
def process():
    try:
        data = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify(error="Bad request"), 400

    text = (data.get("text") or "").strip()
    scenario = (data.get("scenario") or "").strip()  # 'reject' | 'hire' | 'remind'
    ui_lang = (data.get("ui_lang") or "ru").strip().lower()
    ip = request.remote_addr or "anon"

    # rate limit
    if is_rate_limited(ip):
        return jsonify(error=tr(ui_lang,
                                "Слишком много запросов. Попробуйте позже.",
                                "Too many requests. Please try again later.",
                                "Demasiadas solicitudes. Inténtalo más tarde.")), 429

    # валидация
    if not text:
        return jsonify(error=tr(ui_lang, "Пустой текст.", "Empty text.", "Texto vacío.")), 400
    if scenario not in ("reject", "hire", "remind"):
        return jsonify(error=tr(ui_lang, "Неизвестный сценарий.", "Unknown scenario.", "Escenario desconocido.")), 400
    if contains_banned_words(text):
        return jsonify(error=tr(ui_lang, "Обнаружены запрещённые слова.", "Banned words detected.", "Se detectaron palabras prohibidas.")), 400

    # язык
    input_lang = detect_input_lang(text)
    lang = pick_lang(input_lang, ui_lang)

    # промпт из JSON
    try:
        system_prompt = get_prompt(lang, scenario)
    except Exception as e:
        print("Prompt error:", e)
        return jsonify(error=tr(ui_lang,
                                "Ошибка загрузки промптов. Проверь prompts.json.",
                                "Prompts loading error. Check prompts.json.",
                                "Error al cargar los prompts. Revisa prompts.json.")), 500

    # вызов OpenAI
    try:
        client = get_openai_client()
    except RuntimeError as e:
        msg = str(e)
        if "OPENAI_API_KEY" in msg:
            return jsonify(error=tr(ui_lang,
                                    "Ошибка: OPENAI_API_KEY не установлен.",
                                    "Error: OPENAI_API_KEY is not set.",
                                    "Error: OPENAI_API_KEY no está configurada.")), 500
        return jsonify(error=tr(ui_lang,
                                f"Ошибка инициализации OpenAI: {msg}",
                                f"OpenAI initialization error: {msg}",
                                f"Error de inicialización de OpenAI: {msg}")), 500

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        result_text = completion.choices[0].message.content.strip()
        if not result_text:
            raise RuntimeError("Empty response from model")
        return jsonify(result=result_text)
    except Exception as e:
        err = str(e).lower()
        if "invalid api key" in err or "authentication" in err:
            return jsonify(error=tr(ui_lang,
                                    "Неверный или отозванный OPENAI_API_KEY.",
                                    "Invalid or revoked OPENAI_API_KEY.",
                                    "OPENAI_API_KEY inválida o revocada.")), 500
        if "rate limit" in err or "quota" in err or "exceeded" in err:
            return jsonify(error=tr(ui_lang,
                                    "Достигнут лимит на стороне OpenAI. Попробуйте позже.",
                                    "OpenAI rate limit reached. Please try again later.",
                                    "Se alcanzó el límite de OpenAI. Inténtalo más tarde.")), 502
        if "model" in err and ("not found" in err or "does not exist" in err):
            return jsonify(error=tr(ui_lang,
                                    f"Модель '{model}' недоступна. Укажи существующую модель в OPENAI_MODEL.",
                                    f"Model '{model}' is unavailable. Set an existing model in OPENAI_MODEL.",
                                    f"El modelo '{model}' no está disponible. Configura un modelo existente en OPENAI_MODEL.")), 500
        if "timeout" in err or "timed out" in err:
            return jsonify(error=tr(ui_lang,
                                    "Таймаут запроса к OpenAI. Повтори попытку.",
                                    "Request to OpenAI timed out. Please retry.",
                                    "La solicitud a OpenAI agotó el tiempo. Inténtalo de nuevo.")), 504
        print("OpenAI error:", e)
        return jsonify(error=tr(ui_lang,
                                "Ошибка генерации ответа. Проверь ключ, модель и логи (/debug/env).",
                                "Generation failed. Check API key, model and logs (/debug/env).",
                                "Error al generar la respuesta. Revisa la clave, el modelo y los registros (/debug/env).")), 500

@app.route("/why")
def why_page() -> str:
    """Render the information page describing who hrify is for."""
    return render_template("why.html")

# =========================
# Запуск локально
# =========================
if __name__ == "__main__":
    # первая загрузка промптов — упадём сразу, если файла нет/битый
    try:
        load_prompts(force=True)
        print(f"[hrify] loaded prompts from {PROMPTS_PATH}")
    except Exception as e:
        print(f"[hrify] FAILED to load prompts from {PROMPTS_PATH}:", e)
        # не выходим — можно править файл и обновить без рестарта, но лучше знать об ошибке
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print("OPENAI_API_KEY set:", bool(os.getenv("OPENAI_API_KEY")))
    print("Model:", os.getenv("OPENAI_MODEL", "gpt-5-mini"))
    app.run(host="0.0.0.0", port=port, debug=debug)