import pytest

from main import extract_sms_code


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Код 123456", "123456"),
        ("Ваш код: 4321", "4321"),
        ("code 9999", "9999"),
        ("Пароль 5555 для входа", "5555"),
        # Ключевое слово важнее любого другого числа в тексте (сумма, дата).
        ("Списание 1500 руб. Код 246810 никому не сообщайте", "246810"),
        # Без ключевого слова берём отдельно стоящее 4-6-значное число.
        ("123456", "123456"),
        ("1234", "1234"),
    ],
)
def test_extract_sms_code_found(text, expected):
    assert extract_sms_code(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "нет цифр вовсе",
        "123",        # слишком короткое
        "1234567",    # слишком длинное, нет 4-6-значной границы
    ],
)
def test_extract_sms_code_none(text):
    assert extract_sms_code(text) is None
