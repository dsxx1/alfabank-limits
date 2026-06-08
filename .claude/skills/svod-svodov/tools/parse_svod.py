#!/usr/bin/env python3
"""Парсер «Сводного отчёта по ломбарду» (выгрузка в md-таблицы) и расчёт топ-10.

Читает текстовое представление листа (pipe-таблицы), находит таблицы
по заголовку «Территориальное образование», возвращает по каждому
подразделению словарь {колонка: значение}. Демонстрирует расчёт топ-10
прирост/падение по «Движение ТМЦ/Изменение» — прямо из данных таблицы.
"""
from __future__ import annotations

import re
import sys


def parse_num(s: str):
    s = (s or "").strip()
    if s in ("", "#ДЕЛ/0!", "\\#ДЕЛ/0\\!"):
        return None
    s = s.replace("\\-", "-").replace(" ", "").replace(" ", "")
    s = s.replace(" ", "").replace("%", "").replace("\\", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def split_row(line: str):
    parts = line.split("|")
    # убрать ведущий/замыкающий пустые элементы от обрамляющих "|"
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def parse_tables(path: str):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    tables = []
    i = 0
    while i < len(lines):
        if "Территориальное образование" in lines[i] and lines[i].lstrip().startswith("|"):
            header = split_row(lines[i])
            i += 1
            if i < len(lines) and set(lines[i].replace("|", "").replace(":", "").strip()) <= {"-", " "}:
                i += 1  # пропустить строку-разделитель
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|") and "Территориальное образование" not in lines[i]:
                cells = split_row(lines[i])
                if len(cells) >= len(header) - 2:
                    rows.append(cells)
                i += 1
            tables.append((header, rows))
        else:
            i += 1
    return tables


def col_index(header, name):
    for idx, h in enumerate(header):
        if h.strip() == name:
            return idx
    return None


def records(header, rows):
    name_i = 0
    out = []
    for cells in rows:
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        name = cells[name_i].strip()
        if not name or name.startswith("_") or name.startswith("#") or name.startswith("\\#"):
            continue
        if not re.match(r"^\d{3}-", name):  # только «NNN-Название»
            continue
        out.append({header[j].strip(): cells[j] for j in range(len(header))})
    return out


def top_by(recs, col, n=10, reverse=True):
    vals = [(r["Территориальное образование"], parse_num(r.get(col, ""))) for r in recs]
    vals = [(nm, v) for nm, v in vals if v is not None]
    vals.sort(key=lambda x: x[1], reverse=reverse)
    return vals[:n]


def fmt(x):
    return f"{x:+,.0f}".replace(",", " ")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ".work/vyp_iun.txt"
    tables = parse_tables(path)
    print(f"Найдено таблиц «Сводный отчёт»: {len(tables)}")
    CH_RUB = "Движение ТМЦ/Изменение"
    CH_GR = "Движение ТМЦ ВМ 585, гр./Изменение"
    for t_idx, (header, rows) in enumerate(tables):
        recs = records(header, rows)
        total = sum(parse_num(r.get(CH_RUB, "")) or 0 for r in recs)
        print(f"\n================= ТАБЛИЦА #{t_idx} =================")
        print(f"подразделений (NNN-...): {len(recs)} | сумма Δ актива: {fmt(total)} руб")
        gr = {r["Территориальное образование"]: parse_num(r.get(CH_GR, "")) for r in recs}
        print("--- ТОП-10 ПРИРОСТ за месяц (по Движение ТМЦ/Изменение) ---")
        for nm, v in top_by(recs, CH_RUB, 10, reverse=True):
            print(f"  {nm:<28} {fmt(v):>14}  | гр {fmt(gr.get(nm) or 0):>7}")
        print("--- ТОП-10 ПАДЕНИЕ за месяц ---")
        for nm, v in top_by(recs, CH_RUB, 10, reverse=False):
            print(f"  {nm:<28} {fmt(v):>14}  | гр {fmt(gr.get(nm) or 0):>7}")
