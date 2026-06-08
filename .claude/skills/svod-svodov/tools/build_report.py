#!/usr/bin/env python3
"""Сборщик раздела «Ломбард» записки из выгрузки .xlsx «Сводный отчёт».

Читает месячную выгрузку, считает: вал, актив, Δ актива, выдано, перемещено,
клиентов, и топ-10 прирост/падение по подразделениям (Δ = Конечный − Начальный).
Рендерит фрагмент записки в .md и .docx (через md_to_docx).

Запуск:  python build_report.py <месяц.xlsx> [период]
"""
from __future__ import annotations

import sys

from md_to_docx import convert
from parse_svod import parse_num
from read_svod_xlsx import NAME_COL, read_svod

VAL = "Итого Вал/руб."
NACH = "Движение ТМЦ/Начальный остаток"
KON = "Движение ТМЦ/Конечный остаток"
GN = "Движение ТМЦ ВМ 585, гр./Начальный остаток"
GK = "Движение ТМЦ ВМ 585, гр./Конечный остаток"
VYD = "Детализация/Выдано займов/Сумма, руб"
TORG = "Детализация/Перемещено на торги/Сумма, руб"
CLI = "Статистика/Количество заемщиков, чел./Конец периода"


def num(rec, key):
    return parse_num(str(rec.get(key, "") or "")) or 0.0


def sp(x):  # 1 234 567
    return f"{x:,.0f}".replace(",", " ")


def spp(x):  # +1 234 567 / -1 234 567
    return f"{x:+,.0f}".replace(",", " ")


def movers(recs):
    out = []
    for r in recs:
        d_rub = num(r, KON) - num(r, NACH)
        d_gr = num(r, GK) - num(r, GN)
        out.append((r[NAME_COL], d_rub, d_gr))
    return out


def table_md(rows, period_col):
    lines = [
        f"| Подразделение | {period_col} | Изменение в 585 гр |",
        "| :-: | :-: | :-: |",
    ]
    for nm, d_rub, d_gr in rows:
        lines.append(f"| {nm} | **{spp(d_rub)}** | **{spp(d_gr)}** |")
    return "\n".join(lines)


def build(path, period="месяц"):
    recs = read_svod(path)
    aktiv_kon = sum(num(r, KON) for r in recs)
    aktiv_nach = sum(num(r, NACH) for r in recs)
    d_aktiv = aktiv_kon - aktiv_nach
    val = sum(num(r, VAL) for r in recs)
    vydano = sum(num(r, VYD) for r in recs)
    torgi = sum(num(r, TORG) for r in recs)
    clients = sum(num(r, CLI) for r in recs)

    mv = movers(recs)
    up = sorted(mv, key=lambda x: x[1], reverse=True)[:10]
    down = sorted(mv, key=lambda x: x[1])[:10]

    print(f"=== Ломбард — посчитано из {path} ({len(recs)} филиалов) ===")
    print(f"Вал:            {sp(val):>18} руб")
    print(f"Актив (конец):  {sp(aktiv_kon):>18} руб")
    print(f"Δ актива:       {spp(d_aktiv):>18} руб")
    print(f"Выдано займов:  {sp(vydano):>18} руб")
    print(f"Перемещено:     {sp(torgi):>18} руб")
    print(f"Клиентов:       {sp(clients):>18}")
    print("\nТОП-10 прирост за месяц:")
    for nm, d, g in up:
        print(f"  {nm:<26} {spp(d):>14} | гр {spp(g):>6}")

    md = "\n\n".join([
        "[CENTER]**Ломбардная деятельность — раздел собран из выгрузки**",
        f"Вал составил **{sp(val)}** руб., актив — **{sp(aktiv_kon)}** руб., "
        f"изменение актива за месяц **{spp(d_aktiv)}** руб. "
        f"Выдано займов **{sp(vydano)}** руб., перемещено на торги **{sp(torgi)}** руб., "
        f"клиентов **{sp(clients)}**.",
        "Максимальный прирост актива за месяц показали следующие подразделения:",
        table_md(up, "Изменение актива за месяц, руб."),
        "Максимальное падение актива за месяц показали следующие подразделения:",
        table_md(down, "Изменение актива за месяц, руб."),
    ])
    md_path = ".work/lombard_fragment.md"
    docx_path = ".work/lombard_fragment.docx"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    convert(md_path, docx_path)
    print(f"\nФрагмент записки: {docx_path}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else ".work/sample_may.xlsx")
