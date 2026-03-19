from playwright.sync_api import sync_playwright
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import time
import json
import re
import socket
from datetime import datetime

# ============================================================
# НАСТРОЙКИ IP / ПОРТА
# ============================================================
SMS_HOST = "0.0.0.0"
SMS_PORT = 8888


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def print_connection_info():
    local_ip = get_local_ip()
    webhook_url = "http://" + local_ip + ":" + str(SMS_PORT) + "/sms"
    check_url = "http://" + local_ip + ":" + str(SMS_PORT) + "/check"

    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║            НАСТРОЙКА SMS-ШЛУЗА                   ║")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║  Локальный IP:    " + local_ip.ljust(29) + "║")
    print("  ║  Порт:            " + str(SMS_PORT).ljust(29) + "║")
    print("  ║  Webhook:         /sms (POST)".ljust(51) + "║")
    print("  ║  Проверка:        /check (GET)".ljust(51) + "║")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║  URL для шлюза:                                ║")
    print("  ║  " + webhook_url.ljust(47) + "║")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║  Формат POST запроса от шлюза:                 ║")
    print('  ║  {"content": "Код 123456"}                     ║')
    print('  ║  {"msg": "Ваш код: 123456"}                    ║')
    print('  ║  {"text": "123456"}                            ║')
    print("  ╚══════════════════════════════════════════════════╝")
    print()


# ============================================================
# КОНФИГ
# ============================================================
CONFIG = json.load(open("holders_config.json", "r", encoding="utf-8"))
HOLDERS = [h.upper() for h in CONFIG["holders"]]
LIMITS = CONFIG["limits"]
BASE = "https://link.alfabank.ru"
LOG_FILE = "sms_log.txt"


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
def write_log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def log(msg):
    print("  " + msg)
    write_log(msg)


# ============================================================
# ФУНКЦИЯ ОТЛАДКИ
# ============================================================
def debug_page_content(page, holder_name):
    """Функция для отладки - показывает что есть на странице"""
    log("🔍 Отладка страницы:")
    
    # Сохраняем скриншот
    screenshot_name = "debug_" + holder_name.replace(" ", "_") + ".png"
    page.screenshot(path=screenshot_name)
    log("  Скриншот сохранен: " + screenshot_name)
    
    # Проверяем заголовок
    title = page.title()
    log("  Заголовок: " + title)
    
    # Проверяем URL
    log("  URL: " + page.url)
    
    # Ищем все текстовые элементы
    try:
        all_text = page.locator('body').inner_text()
        log("  Текст на странице (первые 200 символов):")
        log("  " + all_text[:200].replace("\n", " "))
    except:
        pass
    
    # Проверяем наличие таблиц
    tables = page.locator('table').count()
    log("  Таблиц на странице: " + str(tables))
    
    # Проверяем наличие карточек
    cards = page.locator('[data-test-id*="card"]').count()
    log("  Элементов с data-test-id содержащим 'card': " + str(cards))
    
    # Проверяем все элементы с текстом
    try:
        # Ищем элементы, содержащие фамилию
        last_name = holder_name.split()[0]
        elements_with_name = page.locator('*:has-text("' + last_name + '")').count()
        log("  Элементов содержащих '" + last_name + "': " + str(elements_with_name))
    except:
        pass


def diagnose_page_state(page, card_id):
    """Диагностика состояния страницы при ошибке"""
    log("🔍 Диагностика страницы:")
    
    # Текущий URL
    log("  URL: " + page.url)
    
    # Заголовок
    log("  Title: " + page.title())
    
    # Скриншот
    page.screenshot(path="diagnostic_" + card_id + ".png")
    log("  Скриншот сохранен")
    
    # Проверяем наличие кнопки настроек
    settings_btn = page.locator('[data-test-id="card-limits__settings-button"]')
    log("  Кнопка настроек: " + ("есть" if settings_btn.count() > 0 else "нет"))
    
    # Проверяем наличие полей ввода
    fields = page.locator('input[data-test-id*="Limit"]')
    log("  Поля ввода: " + str(fields.count()))
    
    # Проверяем наличие ошибок
    errors = page.locator('.error, [class*="error"], [role="alert"]')
    if errors.count() > 0:
        log("  Ошибки на странице: " + str(errors.count()))
        try:
            log("  Текст ошибки: " + errors.first.inner_text())
        except:
            pass


# ============================================================
# SMS-СЕРВЕР
# ============================================================
SMS_CODE = None
SMS_RECEIVED = False
SMS_HISTORY = []


class SMSHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global SMS_CODE, SMS_RECEIVED
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            data = json.loads(body)

            sms_text = data.get("content", "") or data.get("msg", "") or data.get("text", "")

            m = re.search(r'(?:код|code)[:\s]*(\d{4,6})', sms_text, re.IGNORECASE)
            if not m:
                m = re.search(r'(\d{4,6})', sms_text)

            if m:
                code = m.group(1)
                SMS_CODE = code
                SMS_RECEIVED = True

                now = datetime.now().strftime("%H:%M:%S")
                text_preview = sms_text[:50]
                log_line = "[" + now + "] SMS! Код: " + code + " | Текст: " + text_preview
                print("\n  📱 " + log_line)
                write_log(log_line)

                SMS_HISTORY.append({"time": now, "code": code, "text": sms_text[:80]})

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
        except:
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        if self.path == "/check" or self.path == "/":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                "status": "ok",
                "sms_received": SMS_RECEIVED,
                "current_code": SMS_CODE,
                "history_count": len(SMS_HISTORY),
                "time": datetime.now().strftime("%H:%M:%S")
            }
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_sms_server():
    server = HTTPServer((SMS_HOST, SMS_PORT), SMSHandler)
    server.serve_forever()


Thread(target=start_sms_server, daemon=True).start()


# ============================================================
# SMS-ФУНКЦИИ
# ============================================================
def reset_sms():
    global SMS_CODE, SMS_RECEIVED
    SMS_CODE = None
    SMS_RECEIVED = False


def get_sms_and_mark_used():
    global SMS_CODE, SMS_RECEIVED
    code = SMS_CODE
    SMS_CODE = None
    SMS_RECEIVED = False
    return code


def wait_for_sms(timeout=120):
    global SMS_CODE, SMS_RECEIVED

    if SMS_RECEIVED and SMS_CODE:
        code = get_sms_and_mark_used()
        log("✓ Код из буфера: " + code)
        return code

    log("⏳ Жду SMS (до " + str(timeout) + " сек)...")
    start = time.time()
    while time.time() - start < timeout:
        if SMS_RECEIVED and SMS_CODE:
            code = get_sms_and_mark_used()
            elapsed = int(time.time() - start)
            log("✓ Код: " + code + " (получен за " + str(elapsed) + " сек)")
            return code
        time.sleep(0.5)
        print(".", end="", flush=True)

    print()
    log("✗ SMS не получен за " + str(timeout) + " сек")
    return None


def retry_sms(page, input_elem, max_retries=2):
    for attempt in range(1, max_retries + 1):
        log("🔄 Запрашиваю SMS повторно (" + str(attempt) + "/" + str(max_retries) + ")...")

        reset_sms()

        sms_modal = page.locator('[data-test-id="sms-sing-module-modal"]')

        retry_found = False
        for btn_sel in [
            'button:has-text("Повторить")',
            'button:has-text("Отправить")',
            'button:has-text("ещё раз")',
            'button:has-text("заново")',
            'button:has-text("еще раз")',
        ]:
            btn = sms_modal.locator(btn_sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                retry_found = True
                log("✓ Кнопка повторной отправки нажата")
                time.sleep(2)
                break

        if not retry_found:
            all_btns = sms_modal.locator('button')
            btn_count = all_btns.count()
            log("  Кнопок в модалке: " + str(btn_count))
            for i in range(btn_count):
                try:
                    btn_text = all_btns.nth(i).inner_text()
                    log("    [" + str(i) + "] «" + btn_text + "»")
                except:
                    pass

            if btn_count > 1:
                first_btn = all_btns.first
                if first_btn.is_visible():
                    btn_text = first_btn.inner_text()
                    first_btn.click()
                    log("✓ Нажал: «" + btn_text + "»")
                    time.sleep(2)

        code = wait_for_sms(timeout=60)
        if code:
            return code

    log("✗ SMS не пришёл после повторных запросов")
    return None


# ============================================================
# UI-ФУНКЦИИ
# ============================================================
def fill_input(page, test_id, value):
    sel = '[data-test-id="' + test_id + '"]'
    field = page.locator(sel)
    if field.count() == 0 or not field.first.is_visible():
        log("  ⚠ Не найдено: " + test_id)
        return False
    field.first.click(click_count=3)
    time.sleep(0.1)
    page.keyboard.press("Backspace")
    time.sleep(0.15)
    field.first.type(str(value).replace(" ", ""), delay=20)
    time.sleep(0.1)
    page.keyboard.press("Tab")
    time.sleep(0.2)
    log("  ✓ " + test_id + ": " + str(value))
    return True


def close_finish_modal(page):
    for _ in range(15):
        finish = page.locator('[data-test-id="card-limits__finish-modal"]')
        if finish.count() > 0 and finish.first.is_visible():
            close_btn = finish.locator('button').last
            if close_btn.is_visible():
                close_btn.click()
                log("✓ Финальное окно закрыто")
                time.sleep(2)
                return

        close_x = page.locator('[data-test-id="card-limits__finish-modal"] [aria-label*="закрыть" i]')
        if close_x.count() > 0 and close_x.first.is_visible():
            close_x.first.click()
            log("✓ Финальное окно закрыто (крестик)")
            time.sleep(2)
            return

        time.sleep(1)

    log("  (финальное окно не появилось)")


def handle_sms(page):
    log("⏳ Жду поле ввода кода...")

    found = False
    for _ in range(30):
        for sel in [
            'input[data-test-id*="sms"]',
            'input[data-test-id*="code"]',
            '[data-test-id*="confirmation"] input',
            'input[inputmode="numeric"]',
        ]:
            elem = page.locator(sel)
            if elem.count() > 0 and elem.first.is_visible():
                found = True
                log("✓ Поле найдено")

                code = wait_for_sms(timeout=40)

                if not code:
                    code = retry_sms(page, elem)

                if code:
                    elem.first.fill(code)
                    log("✓ Ввёл код: " + code)
                else:
                    log("⚠ Введи код вручную в браузере")
                    input("  Enter после ввода... ")

                time.sleep(1)

                sms_modal = page.locator('[data-test-id="sms-sing-module-modal"]')
                if sms_modal.count() > 0:
                    confirm_btn = sms_modal.locator('button')
                    last_btn = confirm_btn.last
                    if last_btn.is_visible():
                        last_btn.click()
                        log("✓ Подтверждено")

                time.sleep(4)
                page.wait_for_load_state("domcontentloaded")
                close_finish_modal(page)
                return True
        time.sleep(1)

    if not found:
        log("✗ Поле ввода не найдено")
        page.screenshot(path="no_sms_field.png")
    return False


# ============================================================
# РАБОТА С КАРТАМИ
# ============================================================
def get_card_id(page, holder_name):
    # Добавляем отладку
    debug_page_content(page, holder_name)
    
    log("🔍 Ищу карту для: " + holder_name)
    
    # Способ 1: Поиск через строку поиска
    try:
        # Ищем поле поиска
        search_selectors = [
            'input[aria-label*="Ищите"]',
            'input[placeholder*="Поиск"]',
            '[data-test-id="card-dashboard__name-search"] input',
            'input[type="search"]'
        ]
        
        search_field = None
        for sel in search_selectors:
            elem = page.locator(sel)
            if elem.count() > 0 and elem.first.is_visible():
                search_field = elem.first
                break
        
        if search_field:
            log("✓ Поле поиска найдено")
            search_field.click(click_count=3)
            time.sleep(0.1)
            page.keyboard.press("Backspace")
            time.sleep(0.5)
            
            # Ищем по фамилии (первое слово)
            last_name = holder_name.split()[0]
            log("🔍 Ввожу в поиск: " + last_name)
            search_field.type(last_name, delay=100)
            time.sleep(3)
    except Exception as e:
        log("⚠ Ошибка при поиске: " + str(e))
    
    # Способ 2: Прямой поиск карт в таблице
    card_selectors = [
        '[data-test-id="card-dashboard__table-item"]',
        '[data-test-id*="card-item"]',
        'div[class*="card"]:has(span:has-text("' + holder_name.split()[0] + '"))',
        'tr:has(td:has-text("' + holder_name.split()[0] + '"))',
        'div[class*="row"]:has(div:has-text("' + holder_name.split()[0] + '"))'
    ]
    
    for selector in card_selectors:
        try:
            rows = page.locator(selector)
            count = rows.count()
            if count > 0:
                log("✓ Найдено элементов по селектору " + selector + ": " + str(count))
                
                for i in range(count):
                    try:
                        # Получаем текст элемента
                        element = rows.nth(i)
                        text = element.inner_text().upper()
                        
                        # Проверяем, содержит ли текст фамилию или имя
                        name_parts = holder_name.upper().split()
                        found = False
                        
                        for part in name_parts:
                            if part in text and len(part) > 2:  # Не учитываем короткие части (инициалы)
                                found = True
                                break
                        
                        if found:
                            log("✓ Найдена карта для " + holder_name)
                            
                            # Пробуем кликнуть по карте
                            element.click()
                            time.sleep(4)
                            
                            # Ждем загрузки страницы
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except:
                                pass
                            
                            # Получаем ID карты из URL
                            url = page.url
                            log("Текущий URL: " + url)
                            
                            # Ищем ID карты в URL разными способами
                            card_id = None
                            
                            # Способ 1: /cards/limits/12345
                            m = re.search(r'/(?:card|cards|limits)/(\d+)', url)
                            if m:
                                card_id = m.group(1)
                            
                            # Способ 2: cardId=12345
                            if not card_id:
                                m = re.search(r'[?&]cardId=(\d+)', url)
                                if m:
                                    card_id = m.group(1)
                            
                            # Способ 3: /12345/limits
                            if not card_id:
                                m = re.search(r'/(\d+)/limits', url)
                                if m:
                                    card_id = m.group(1)
                            
                            if card_id:
                                log("✓ ID карты: " + card_id)
                                return card_id
                            else:
                                log("⚠ Не удалось извлечь ID карты из URL")
                                
                    except Exception as e:
                        log("⚠ Ошибка при обработке элемента: " + str(e))
                        continue
        except Exception as e:
            log("⚠ Ошибка с селектором " + selector + ": " + str(e))
    
    # Способ 3: Пробуем найти по ссылке на карту
    try:
        # Ищем все ссылки, которые могут вести на карты
        links = page.locator('a[href*="card"], a[href*="limits"]')
        for i in range(links.count()):
            try:
                link = links.nth(i)
                text = link.inner_text().upper()
                
                name_parts = holder_name.upper().split()
                for part in name_parts:
                    if part in text and len(part) > 2:  # Не короткие части
                        log("✓ Найдена ссылка на карту: " + text)
                        link.click()
                        time.sleep(4)
                        
                        url = page.url
                        m = re.search(r'/(\d+)', url)
                        if m:
                            card_id = m.group(1)
                            log("✓ ID карты: " + card_id)
                            return card_id
            except:
                continue
    except:
        pass
    
    # Если ничего не нашли, делаем скриншот для отладки
    page.screenshot(path="card_not_found_" + holder_name.replace(" ", "_") + ".png")
    log("✗ Карта не найдена для " + holder_name + " (скриншот сохранен)")
    
    return None


def set_limits(page, card_id, holder_name):
    # Пробуем разные варианты URL для лимитов
    limits_urls = [
        BASE + "/cards/limits/" + card_id,
        BASE + "/card/limits/" + card_id,
        BASE + "/limits/" + card_id,
        BASE + "/cards/card/" + card_id + "/limits",
    ]
    
    success = False
    for url in limits_urls:
        try:
            log("🔄 Пробую URL: " + url)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            
            settings_btn = page.locator('[data-test-id="card-limits__settings-button"]')
            if settings_btn.count() > 0 and settings_btn.first.is_visible():
                success = True
                log("✓ Страница лимитов загружена по URL: " + url)
                break
        except Exception as e:
            log("⚠ Ошибка при загрузке " + url + ": " + str(e))
            continue
    
    if not success:
        log("✗ Не удалось загрузить страницу лимитов")
        return False

    # Нажимаем кнопку настроек
    settings_btn = page.locator('[data-test-id="card-limits__settings-button"]')
    settings_btn.first.click()
    log("✓ Режим редактирования активирован")
    time.sleep(2)

    # ===== ВАЖНО: Сначала выключаем тумблер "Запретить все операции" =====
    log("🔄 Ищем тумблер 'Запретить все операции'...")
    
    # Находим тумблер по тексту
    forbid_toggle = page.locator('span:has-text("Запретить все операции")').first
    if forbid_toggle.count() > 0:
        # Находим родительский label (сам тумблер)
        toggle_label = forbid_toggle.locator('xpath=./ancestor::label[contains(@class, "switch__component")]')
        if toggle_label.count() > 0:
            # Проверяем, включен ли тумблер (есть ли класс switch__checked_hbk30)
            class_attr = toggle_label.first.get_attribute('class') or ''
            if 'switch__checked_hbk30' in class_attr:
                toggle_label.first.click()
                log("✓ Выключен тумблер 'Запретить все операции'")
                time.sleep(2)  # Ждем появления полей
            else:
                log("✓ Тумблер 'Запретить все операции' уже выключен")
    else:
        log("⚠ Тумблер 'Запретить все операции' не найден")

    # Теперь должны появиться поля для заполнения
    try:
        page.wait_for_selector('[data-test-id="card-limits__input-outlayLimitDay"]', timeout=10000)
        log("✓ Поля ввода появились")
    except:
        log("✗ Поля не появились после выключения тумблера")
        page.screenshot(path="no_fields_after_toggle_" + card_id + ".png")
        return False

    # ===== Заполняем лимиты =====
    log("📝 Заполняю лимиты...")
    fill_input(page, "card-limits__input-outlayLimitDay", LIMITS["all_operations"]["day"])
    fill_input(page, "card-limits__input-outlayLimitMonth", LIMITS["all_operations"]["month"])
    fill_input(page, "card-limits__input-withdrawalMoneyLimitDay", LIMITS["cash_withdrawal"]["day"])
    fill_input(page, "card-limits__input-withdrawalMoneyLimitMonth", LIMITS["cash_withdrawal"]["month"])

    # ===== Включаем два последних тумблера =====
    log("🔄 Включаю два последних тумблера...")
    
    # Находим все тумблеры
    toggles = page.locator('label.switch__component_hbk30')
    toggle_count = toggles.count()
    log(f"  Найдено тумблеров: {toggle_count}")
    
    if toggle_count >= 3:
        # Включаем два последних (индексы -2 и -1)
        for i in range(toggle_count - 2, toggle_count):
            toggle = toggles.nth(i)
            class_attr = toggle.get_attribute('class') or ''
            if 'switch__checked_hbk30' not in class_attr:
                toggle.click()
                log(f"  ✓ Включен тумблер {i+1}")
                time.sleep(0.5)
    else:
        log("⚠ Недостаточно тумблеров")

    # Нажимаем кнопку "Применить"
    btn = page.locator('[data-test-id="card-limits__button"]')
    if btn.count() == 0 or not btn.first.is_visible():
        log("✗ Кнопка Применить не найдена")
        return False

    btn.first.click()
    log("💾 Применить нажато")
    time.sleep(3)

    result = handle_sms(page)

    if result:
        time.sleep(2)
        page.screenshot(path="result_" + card_id + ".png")
        log("✓ Скриншот: result_" + card_id + ".png")

    return result

# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_log("\n" + "="*60)
    write_log("  ЗАПУСК: " + now)
    write_log("="*60 + "\n")

    print("=" * 60)
    print("  УСТАНОВКА ЛИМИТОВ НА КАРТЫ")
    print("  Держателей: " + str(len(HOLDERS)))
    print("=" * 60)

    print_connection_info()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ru-RU")
        page = context.new_page()
        page.goto(BASE + "/cards/card-dashboard", wait_until="domcontentloaded", timeout=60000)

        print("  1. Залогинься в Альфа-Бизнес")
        print("  2. Нажми Enter когда увидишь список карт")
        print()
        input("  Enter... ")
        time.sleep(2)

        reset_sms()
        log("🗑 Старые SMS сброшены\n")

        results = {"ok": [], "fail": []}

        for idx, holder in enumerate(HOLDERS, 1):
            print("\n" + "="*60)
            print("  [" + str(idx) + "/" + str(len(HOLDERS)) + "] " + holder)
            print("="*60)

            page.goto(BASE + "/cards/card-dashboard", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            card_id = get_card_id(page, holder)
            if card_id is None:
                log("✗ Пропуск — карта не найдена")
                results["fail"].append(holder)
                reset_sms()
                continue

            log("  card_id: " + card_id)

            if set_limits(page, card_id, holder):
                results["ok"].append(holder)
            else:
                results["fail"].append(holder)

            reset_sms()
            log("🗑 SMS сброшен перед следующей картой\n")

        print("\n" + "="*60)
        print("  ИТОГИ")
        print("="*60)

        if results["ok"]:
            print("\n  ✓ Успешно (" + str(len(results["ok"])) + "):")
            for h in results["ok"]:
                print("    • " + h)

        if results["fail"]:
            print("\n  ✗ Не обработаны (" + str(len(results["fail"])) + "):")
            for h in results["fail"]:
                print("    • " + h)

        if SMS_HISTORY:
            print("\n  📱 Получено SMS: " + str(len(SMS_HISTORY)))
            for s in SMS_HISTORY:
                print("    [" + s["time"] + "] " + s["code"])

        write_log("\n  Итого SMS: " + str(len(SMS_HISTORY)))
        write_log("  Успешно: " + str(len(results["ok"])) + ", Ошибки: " + str(len(results["fail"])) + "\n")

        print("\n  Лог сохранён: " + LOG_FILE)
        print()
        input("  Enter для выхода... ")
        browser.close()


if __name__ == "__main__":
    main()