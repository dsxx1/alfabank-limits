#!/usr/bin/env python3
"""Проверка движка: считаем Δ актива по филиалам ИЗ таблицы и сверяем с Маем.

Δ за месяц = «Движение ТМЦ/Конечный остаток» − «Движение ТМЦ/Начальный остаток»
(колонку «Изменение» в листе не используем — там местами #ERROR!).
"""
from parse_svod import parse_tables, records, parse_num

K_NACH = "Движение ТМЦ/Начальный остаток"
K_KON = "Движение ТМЦ/Конечный остаток"
G_NACH = "Движение ТМЦ ВМ 585, гр./Начальный остаток"
G_KON = "Движение ТМЦ ВМ 585, гр./Конечный остаток"

# из «Свод Сводов за Май 2026» (золотой), Δ за месяц: руб / граммы
GOLDEN = {
    "002-Спортивная": (1_633_819, 371),
    "007-Российская": (935_268, 175),
    "018-Цюрупа": (1_041_716, 257),
    "019-Чишмы": (1_378_881, 239),
    "028-Давлеканово": (879_177, 190),
}


def f(x):
    return f"{x:+,.0f}".replace(",", " ")


tables = parse_tables(".work/svodbook.txt")
recs = records(*tables[0])  # таблица №0 = Май 2026
by_name = {r["Территориальное образование"]: r for r in recs}

print(f"Филиалов в доступной части майской таблицы: {len(recs)}")
print(f"{'Филиал':<20} {'Δ руб (из таблицы)':>20} {'Δ гр':>8}   сверка с Маем")
print("-" * 70)
ok = 0
for name, (g_rub, g_gr) in GOLDEN.items():
    r = by_name.get(name)
    if not r:
        print(f"{name:<20} {'— нет в доступной части —':>30}")
        continue
    d_rub = parse_num(r[K_KON]) - parse_num(r[K_NACH])
    d_gr = parse_num(r[G_KON]) - parse_num(r[G_NACH])
    match = abs(round(d_rub) - g_rub) <= 2 and abs(round(d_gr) - g_gr) <= 1
    ok += match
    flag = "✓ совпало" if match else f"✗ ждали {f(g_rub)}/{g_gr:+d}"
    print(f"{name:<20} {f(d_rub):>20} {f(d_gr):>8}   {flag}")
print("-" * 70)
print(f"Совпало {ok} из {len(GOLDEN)} проверяемых филиалов")
