from __future__ import annotations

import argparse
import json
import logging
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from playwright.sync_api import Page

# ============================================================
# АРГУМЕНТЫ
# ============================================================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Установка лимитов на корпоративные карты Альфа-Бизнес")
    p.add_argument("--config",    default="holders_config.json", help="Путь к конфигу")
    p.add_argument("--port",      type=int, default=8888,        help="Порт SMS-сервера")
    p.add_argument("--host",      default="127.0.0.1",           help="Хост SMS-сервера (0.0.0.0 — слушать всю сеть)")
    p.add_argument("--debug",     action="store_true",           help="Подробные логи")
    p.add_argument("--headless",  action="store_true",           help="Браузер без интерфейса")
    p.add_argument("--sms-token", default="",                    help="Секретный токен для SMS-вебхука")
    return p.parse_args()

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
LOG_FILE = "sms_log.txt"

log = logging.getLogger("alfabank_limits")


def setup_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


# ============================================================
# КОНФИГ
# ============================================================
BASE = "https://link.alfabank.ru"


def validate_config(config: dict) -> None:
    """Проверяет структуру конфига. Бросает ValueError с понятным текстом."""
    if not isinstance(config, dict):
        raise ValueError("корень конфига должен быть объектом JSON")

    holders = config.get("holders")
    if not isinstance(holders, list) or not holders:
        raise ValueError("'holders' должен быть непустым списком")
    if not all(isinstance(h, str) and h.strip() for h in holders):
        raise ValueError("все элементы 'holders' должны быть непустыми строками")

    limits = config.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("'limits' должен быть объектом")

    for section in ("all_operations", "cash_withdrawal"):
        sub = limits.get(section)
        if not isinstance(sub, dict):
            raise ValueError(f"'limits.{section}' должен быть объектом")
        for period in ("day", "month"):
            value = sub.get(period)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(
                    f"'limits.{section}.{period}' должен быть неотрицательным числом"
                )


def load_config(path: str) -> dict:
    """Читает и валидирует конфиг. При ошибке логирует и завершает процесс."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error(f"Файл конфига не найден: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"Ошибка парсинга конфига: {e}")
        sys.exit(1)

    try:
        validate_config(config)
    except ValueError as e:
        log.error(f"Некорректный конфиг ({path}): {e}")
        sys.exit(1)

    return config


# ============================================================
# РАЗБОР SMS
# ============================================================
SMS_CODE_KEYWORD_RE = re.compile(r"(?:код|code|пароль)[:\s]*(\d{4,6})", re.IGNORECASE)
SMS_CODE_FALLBACK_RE = re.compile(r"\b(\d{4,6})\b")


def extract_sms_code(text: str) -> Optional[str]:
    """Достаёт 4-6-значный код из текста SMS.

    Сначала ищет код рядом с ключевым словом («код»/«code»/«пароль»), и только
    если не нашлось — берёт первое отдельно стоящее 4-6-значное число. Это
    снижает риск выхватить из текста сумму или дату вместо кода.
    """
    if not text:
        return None
    m = SMS_CODE_KEYWORD_RE.search(text) or SMS_CODE_FALLBACK_RE.search(text)
    return m.group(1) if m else None

# ============================================================
# КОНСТАНТЫ
# ============================================================
TOGGLE_ACTIVE_CLASS = "switch__checked_hbk30"

TIMEOUTS = {
    "sms_wait":       120,
    "sms_retry":       60,
    "sms_field_wait":  40,
    "limits_field":  10_000,   # мс, для page.wait_for_selector
    "navigation":    60_000,   # мс
}

LIMITS_URL_PATTERNS = [
    "{base}/cards/limits/{card_id}",
    "{base}/card/limits/{card_id}",
    "{base}/limits/{card_id}",
    "{base}/cards/card/{card_id}/limits",
]

SEARCH_SELECTORS = [
    'input[aria-label*="Ищите"]',
    'input[placeholder*="Поиск"]',
    '[data-test-id="card-dashboard__name-search"] input',
    'input[type="search"]',
]

CARD_ROW_SELECTORS = [
    '[data-test-id="card-dashboard__table-item"]',
    '[data-test-id*="card-item"]',
]

SMS_INPUT_SELECTORS = [
    'input[data-test-id*="sms"]',
    'input[data-test-id*="code"]',
    '[data-test-id*="confirmation"] input',
    'input[inputmode="numeric"]',
]

# ============================================================
# DATACLASSES
# ============================================================
@dataclass
class SmsRecord:
    time: str
    code: str
    text: str

@dataclass
class RunResults:
    ok:   list[str] = field(default_factory=list)
    fail: list[str] = field(default_factory=list)

# ============================================================
# SMS STATE
# ============================================================
class SmsState:
    """Потокобезопасное хранилище SMS-кода."""

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._code:    Optional[str] = None
        self._received = False
        self.history:  list[SmsRecord] = []

    def set(self, code: str, raw_text: str) -> None:
        with self._lock:
            self._code     = code
            self._received = True
            now = datetime.now().strftime("%H:%M:%S")
            self.history.append(SmsRecord(now, code, raw_text[:80]))
            log.info(f"📱 SMS! Код: {code} | Текст: {raw_text[:50]}")

    def get_and_clear(self) -> Optional[str]:
        with self._lock:
            code           = self._code
            self._code     = None
            self._received = False
            return code

    def reset(self) -> None:
        with self._lock:
            self._code     = None
            self._received = False

    @property
    def received(self) -> bool:
        with self._lock:
            return self._received

SMS = SmsState()

# ============================================================
# SMS HTTP-СЕРВЕР
# ============================================================
class SMSHandler(BaseHTTPRequestHandler):
    TOKEN = ""  # задаётся в main() из аргументов

    def do_POST(self) -> None:
        try:
            # Проверка токена (если задан)
            if self.TOKEN:
                auth = self.headers.get("X-SMS-Token", "")
                if auth != self.TOKEN:
                    self.send_response(403)
                    self.end_headers()
                    return

            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length).decode("utf-8")
            data   = json.loads(body)

            sms_text = data.get("content") or data.get("msg") or data.get("text") or ""

            code = extract_sms_code(sms_text)
            if code:
                SMS.set(code, sms_text)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        except Exception as e:
            log.debug(f"SMSHandler error: {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/check", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "status":       "ok",
                "sms_received": SMS.received,
                "history_count": len(SMS.history),
                "time":         datetime.now().strftime("%H:%M:%S"),
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_) -> None:
        pass


def start_sms_server(host: str, port: int) -> None:
    server = HTTPServer((host, port), SMSHandler)
    log.info(f"SMS-сервер запущен на {host}:{port}")
    server.serve_forever()

# ============================================================
# SMS HELPERS
# ============================================================
def wait_for_sms(timeout: int = TIMEOUTS["sms_wait"]) -> Optional[str]:
    if SMS.received:
        code = SMS.get_and_clear()
        log.info(f"✓ Код из буфера: {code}")
        return code

    log.info(f"⏳ Жду SMS (до {timeout} сек)...")
    start = time.time()
    while time.time() - start < timeout:
        if SMS.received:
            code    = SMS.get_and_clear()
            elapsed = int(time.time() - start)
            log.info(f"✓ Код: {code} (получен за {elapsed} сек)")
            return code
        time.sleep(0.5)

    log.warning(f"✗ SMS не получен за {timeout} сек")
    return None


def retry_sms(page: Page, max_retries: int = 2) -> Optional[str]:
    RETRY_BTN_TEXTS = ["Повторить", "Отправить", "ещё раз", "заново", "еще раз"]

    for attempt in range(1, max_retries + 1):
        log.info(f"🔄 Повторный запрос SMS ({attempt}/{max_retries})...")
        SMS.reset()

        sms_modal = page.locator('[data-test-id="sms-sing-module-modal"]')
        clicked   = False

        for text in RETRY_BTN_TEXTS:
            btn = sms_modal.locator(f'button:has-text("{text}")')
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                clicked = True
                log.info(f"✓ Нажата кнопка «{text}»")
                time.sleep(2)
                break

        if not clicked:
            all_btns = sms_modal.locator("button")
            if all_btns.count() > 0 and all_btns.first.is_visible():
                try:
                    label = all_btns.first.inner_text()
                    all_btns.first.click()
                    log.info(f"✓ Нажата первая кнопка: «{label}»")
                    time.sleep(2)
                except Exception as e:
                    log.debug(f"retry_sms click error: {e}")

        code = wait_for_sms(timeout=TIMEOUTS["sms_retry"])
        if code:
            return code

    log.warning("✗ SMS не пришёл после повторных запросов")
    return None

# ============================================================
# UI HELPERS
# ============================================================
def fill_input(page: Page, test_id: str, value) -> bool:
    sel   = f'[data-test-id="{test_id}"]'
    field = page.locator(sel)

    if field.count() == 0 or not field.first.is_visible():
        log.warning(f"  ⚠ Поле не найдено: {test_id}")
        return False

    field.first.click(click_count=3)
    time.sleep(0.1)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    time.sleep(0.1)
    field.first.type(str(value).replace(" ", ""), delay=20)
    time.sleep(0.1)
    page.keyboard.press("Tab")
    time.sleep(0.2)
    log.info(f"  ✓ {test_id}: {value}")
    return True


def close_finish_modal(page: Page) -> None:
    for _ in range(15):
        finish = page.locator('[data-test-id="card-limits__finish-modal"]')
        if finish.count() > 0 and finish.first.is_visible():
            # Пробуем крестик, потом последнюю кнопку
            for sel in [
                '[data-test-id="card-limits__finish-modal"] [aria-label*="закрыть" i]',
            ]:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    log.info("✓ Финальное окно закрыто")
                    time.sleep(2)
                    return

            close_btn = finish.locator("button").last
            if close_btn.is_visible():
                close_btn.click()
                log.info("✓ Финальное окно закрыто")
                time.sleep(2)
                return

        time.sleep(1)

    log.debug("Финальное окно не появилось — продолжаем")


def handle_sms(page: Page) -> bool:
    log.info("⏳ Жду поле ввода SMS-кода...")

    for _ in range(30):
        for sel in SMS_INPUT_SELECTORS:
            elem = page.locator(sel)
            if elem.count() > 0 and elem.first.is_visible():
                log.info("✓ Поле ввода найдено")

                code = wait_for_sms(timeout=TIMEOUTS["sms_field_wait"])
                if not code:
                    code = retry_sms(page)

                if code:
                    elem.first.fill(code)
                    log.info(f"✓ Введён код: {code}")
                else:
                    log.warning("⚠ Введи код вручную в браузере")
                    input("  Enter после ввода... ")

                time.sleep(1)

                # Подтверждение в модалке
                sms_modal = page.locator('[data-test-id="sms-sing-module-modal"]')
                if sms_modal.count() > 0:
                    last_btn = sms_modal.locator("button").last
                    if last_btn.is_visible():
                        last_btn.click()
                        log.info("✓ SMS подтверждён")

                time.sleep(4)
                page.wait_for_load_state("domcontentloaded")
                close_finish_modal(page)
                return True

        time.sleep(1)

    log.error("✗ Поле ввода SMS-кода не найдено")
    return False

# ============================================================
# ПОИСК КАРТЫ
# ============================================================
def _search_card_by_name(page: Page, holder_name: str) -> None:
    """Вводит фамилию в поисковую строку (если она есть на странице)."""
    last_name = holder_name.split()[0]

    for sel in SEARCH_SELECTORS:
        elem = page.locator(sel)
        if elem.count() > 0 and elem.first.is_visible():
            log.debug(f"Поле поиска найдено: {sel}")
            elem.first.click(click_count=3)
            page.keyboard.press("Backspace")
            time.sleep(0.3)
            elem.first.type(last_name, delay=80)
            time.sleep(2)
            return

    log.debug("Поле поиска не найдено — ищем без фильтра")


def _extract_card_id_from_url(url: str) -> Optional[str]:
    patterns = [
        r"/(?:card|cards|limits)/(\d+)",
        r"[?&]cardId=(\d+)",
        r"/(\d+)/limits",
        r"/(\d{5,})",          # любой длинный числовой сегмент
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def get_card_id(page: Page, holder_name: str) -> Optional[str]:
    log.info(f"🔍 Ищу карту: {holder_name}")
    _search_card_by_name(page, holder_name)

    name_parts = [p for p in holder_name.upper().split() if len(p) > 2]

    # Перебираем известные селекторы строк
    for selector in CARD_ROW_SELECTORS:
        rows = page.locator(selector)
        count = rows.count()
        if count == 0:
            continue

        log.debug(f"Найдено {count} строк по: {selector}")

        for i in range(count):
            try:
                elem = rows.nth(i)
                text = elem.inner_text().upper()

                if any(part in text for part in name_parts):
                    log.info(f"✓ Строка совпала: {text[:60].strip()}")
                    elem.click()

                    # Ждём навигации
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=TIMEOUTS["navigation"])
                    except Exception:
                        pass
                    time.sleep(2)

                    card_id = _extract_card_id_from_url(page.url)
                    if card_id:
                        log.info(f"✓ card_id: {card_id}")
                        return card_id

                    log.warning(f"Не удалось извлечь card_id из URL: {page.url}")
            except Exception as e:
                log.debug(f"Ошибка при обработке строки {i}: {e}")

    # Запасной вариант — ищем ссылки с числовым ID
    links = page.locator('a[href*="card"], a[href*="limits"]')
    for i in range(links.count()):
        try:
            link = links.nth(i)
            text = link.inner_text().upper()
            if any(part in text for part in name_parts):
                href    = link.get_attribute("href") or ""
                card_id = _extract_card_id_from_url(href)
                if card_id:
                    log.info(f"✓ Карта найдена по ссылке, card_id: {card_id}")
                    link.click()
                    time.sleep(2)
                    return card_id
        except Exception as e:
            log.debug(f"Ошибка при обработке ссылки {i}: {e}")

    log.error(f"✗ Карта не найдена для: {holder_name}")
    return None

# ============================================================
# УСТАНОВКА ЛИМИТОВ
# ============================================================
def _navigate_to_limits(page: Page, card_id: str) -> bool:
    """Открывает страницу лимитов, перебирая возможные URL."""
    for pattern in LIMITS_URL_PATTERNS:
        url = pattern.format(base=BASE, card_id=card_id)
        log.debug(f"Пробую URL: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUTS["navigation"])
            time.sleep(2)
            btn = page.locator('[data-test-id="card-limits__settings-button"]')
            if btn.count() > 0 and btn.first.is_visible():
                log.info(f"✓ Страница лимитов: {url}")
                return True
        except Exception as e:
            log.debug(f"Ошибка при загрузке {url}: {e}")

    log.error("✗ Не удалось загрузить страницу лимитов")
    return False


def _disable_forbid_toggle(page: Page) -> bool:
    """Выключает тумблер 'Запретить все операции', если он включён."""
    forbid_text = page.locator('span:has-text("Запретить все операции")')
    if forbid_text.count() == 0:
        log.debug("Тумблер 'Запретить все операции' не найден")
        return True  # не критично, продолжаем

    toggle = forbid_text.locator(
        f'xpath=./ancestor::label[contains(@class, "{TOGGLE_ACTIVE_CLASS}")]'
    )

    # Проверяем по aria-checked — надёжнее чем CSS-класс
    aria = None
    try:
        parent = page.locator('label:has(span:has-text("Запретить все операции"))')
        aria   = parent.first.get_attribute("aria-checked")
    except Exception:
        pass

    is_active = (aria == "true") or (
        toggle.count() > 0
    )

    if is_active:
        try:
            label = page.locator('label:has(span:has-text("Запретить все операции"))')
            label.first.click()
            log.info("✓ Тумблер 'Запретить все операции' выключен")
            time.sleep(2)
        except Exception as e:
            log.warning(f"Не удалось кликнуть по тумблеру: {e}")
            return False
    else:
        log.debug("Тумблер 'Запретить все операции' уже выключен")

    return True


def _fill_limit_fields(page: Page, limits: dict) -> bool:
    """Заполняет поля лимитов из конфига."""
    try:
        page.wait_for_selector(
            '[data-test-id="card-limits__input-outlayLimitDay"]',
            timeout=TIMEOUTS["limits_field"],
        )
    except Exception:
        log.error("✗ Поля лимитов не появились")
        return False

    results = [
        fill_input(page, "card-limits__input-outlayLimitDay",          limits["all_operations"]["day"]),
        fill_input(page, "card-limits__input-outlayLimitMonth",        limits["all_operations"]["month"]),
        fill_input(page, "card-limits__input-withdrawalMoneyLimitDay", limits["cash_withdrawal"]["day"]),
        fill_input(page, "card-limits__input-withdrawalMoneyLimitMonth", limits["cash_withdrawal"]["month"]),
    ]

    return all(results)


def _enable_last_toggles(page: Page, count: int = 2) -> None:
    """Включает последние N тумблеров на странице."""
    toggles     = page.locator("label.switch__component_hbk30")
    total       = toggles.count()
    log.debug(f"Всего тумблеров: {total}")

    if total < count + 1:
        log.warning(f"Недостаточно тумблеров (найдено {total}, нужно минимум {count + 1})")
        return

    for i in range(total - count, total):
        toggle     = toggles.nth(i)
        class_attr = toggle.get_attribute("class") or ""
        aria       = toggle.get_attribute("aria-checked")

        already_on = (aria == "true") or (TOGGLE_ACTIVE_CLASS in class_attr)
        if not already_on:
            try:
                toggle.click()
                log.info(f"  ✓ Включён тумблер #{i + 1}")
                time.sleep(0.5)
            except Exception as e:
                log.warning(f"  ⚠ Не удалось включить тумблер #{i + 1}: {e}")
        else:
            log.debug(f"  Тумблер #{i + 1} уже включён")


def set_limits(page: Page, card_id: str, limits: dict) -> bool:
    if not _navigate_to_limits(page, card_id):
        return False

    # Активируем режим редактирования
    settings_btn = page.locator('[data-test-id="card-limits__settings-button"]')
    settings_btn.first.click()
    log.info("✓ Режим редактирования активирован")
    time.sleep(2)

    if not _disable_forbid_toggle(page):
        return False

    if not _fill_limit_fields(page, limits):
        return False

    _enable_last_toggles(page, count=2)

    # Применить
    apply_btn = page.locator('[data-test-id="card-limits__button"]')
    if apply_btn.count() == 0 or not apply_btn.first.is_visible():
        log.error("✗ Кнопка «Применить» не найдена")
        return False

    apply_btn.first.click()
    log.info("💾 «Применить» нажато")
    time.sleep(3)

    return handle_sms(page)

# ============================================================
# PRINT INFO
# ============================================================
def print_connection_info(args: argparse.Namespace) -> None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    # Если сервер слушает все интерфейсы — показываем IP в локальной сети,
    # иначе адрес, к которому реально привязан сокет (например, 127.0.0.1).
    display_host = local_ip if args.host in ("0.0.0.0", "::") else args.host

    webhook = f"http://{display_host}:{args.port}/sms"
    check   = f"http://{display_host}:{args.port}/check"
    if args.sms_token:
        token_hint = f" (токен: {args.sms_token})"
    elif args.host in ("0.0.0.0", "::"):
        token_hint = " (токен не задан — небезопасно)"
    else:
        token_hint = " (только localhost)"

    lines = [
        "╔══════════════════════════════════════════════════╗",
        "║            НАСТРОЙКА SMS-ШЛЮЗА                   ║",
        "╠══════════════════════════════════════════════════╣",
        f"║  Локальный IP:    {display_host:<29}║",
        f"║  Порт:            {str(args.port):<29}║",
        f"║  Безопасность:{token_hint:<35}║",
        "╠══════════════════════════════════════════════════╣",
        f"║  Webhook (POST):  {webhook:<29}║",
        f"║  Проверка (GET):  {check:<29}║",
        "╠══════════════════════════════════════════════════╣",
        '║  Форматы тела запроса:                           ║',
        '║  {"content": "Код 123456"}                       ║',
        '║  {"msg":     "Ваш код: 123456"}                  ║',
        '║  {"text":    "123456"}                           ║',
        "╚══════════════════════════════════════════════════╝",
    ]
    print()
    for line in lines:
        print(f"  {line}")
    print()

# ============================================================
# MAIN
# ============================================================
def main() -> None:
    args = parse_args()
    setup_logging(args.debug)
    config = load_config(args.config)
    holders = [h.upper() for h in config["holders"]]
    limits = config["limits"]
    SMSHandler.TOKEN = args.sms_token

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("=" * 58)
    log.info(f"  ЗАПУСК: {now}")
    log.info(f"  Держателей: {len(holders)}")
    log.info("=" * 58)

    print_connection_info(args)

    Thread(target=start_sms_server, args=(args.host, args.port), daemon=True).start()
    time.sleep(0.5)  # даём серверу подняться

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            slow_mo=50,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
        )
        page = context.new_page()
        page.goto(
            f"{BASE}/cards/card-dashboard",
            wait_until="domcontentloaded",
            timeout=TIMEOUTS["navigation"],
        )

        print("  1. Залогинься в Альфа-Бизнес")
        print("  2. Дождись списка карт")
        print("  3. Нажми Enter")
        print()
        input("  Enter... ")
        time.sleep(2)

        SMS.reset()
        log.info("🗑  Старые SMS сброшены\n")

        results = RunResults()

        for idx, holder in enumerate(holders, 1):
            print(f"\n{'=' * 58}")
            print(f"  [{idx}/{len(holders)}] {holder}")
            print("=" * 58)

            page.goto(
                f"{BASE}/cards/card-dashboard",
                wait_until="domcontentloaded",
                timeout=TIMEOUTS["navigation"],
            )
            time.sleep(3)

            card_id = get_card_id(page, holder)
            if card_id is None:
                log.warning("✗ Карта не найдена — пропуск")
                results.fail.append(holder)
                SMS.reset()
                continue

            if set_limits(page, card_id, limits):
                results.ok.append(holder)
            else:
                results.fail.append(holder)

            SMS.reset()
            log.info("🗑  SMS сброшен\n")

        # Итоги
        print(f"\n{'=' * 58}")
        print("  ИТОГИ")
        print("=" * 58)

        if results.ok:
            print(f"\n  ✓ Успешно ({len(results.ok)}):")
            for h in results.ok:
                print(f"    • {h}")

        if results.fail:
            print(f"\n  ✗ Не обработаны ({len(results.fail)}):")
            for h in results.fail:
                print(f"    • {h}")

        if SMS.history:
            print(f"\n  📱 Получено SMS: {len(SMS.history)}")
            for s in SMS.history:
                print(f"    [{s.time}] {s.code}")

        log.info(f"Успешно: {len(results.ok)}, Ошибки: {len(results.fail)}")
        print(f"\n  Лог: {LOG_FILE}")
        print()
        input("  Enter для выхода... ")
        browser.close()


if __name__ == "__main__":
    main()
