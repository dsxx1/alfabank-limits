import pytest

from main import _extract_card_id_from_url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://link.alfabank.ru/cards/limits/12345", "12345"),
        ("https://link.alfabank.ru/card/limits/98765", "98765"),
        ("https://link.alfabank.ru/limits/55555", "55555"),
        ("https://link.alfabank.ru/cards/card/4242/limits", "4242"),
        ("https://link.alfabank.ru/x?cardId=777888", "777888"),
        ("https://link.alfabank.ru/some/99999/limits", "99999"),
    ],
)
def test_extract_card_id_found(url, expected):
    assert _extract_card_id_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://link.alfabank.ru/cards/card-dashboard",
        "https://link.alfabank.ru/",
        "not-a-url-without-digits",
    ],
)
def test_extract_card_id_none(url):
    assert _extract_card_id_from_url(url) is None
