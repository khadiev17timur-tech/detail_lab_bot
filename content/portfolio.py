"""Демонстрационные карточки портфолио.

Фото берутся из папки assets/portfolio/ по имени файла: <id>.jpg / .jpeg / .png / .webp
(например, assets/portfolio/ceramic.jpg). Нет файла — показывается текстовая карточка.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


@dataclass(frozen=True)
class PortfolioItem:
    id: str
    emoji: str
    title: str
    result: str
    works: tuple[str, ...]


PORTFOLIO: tuple[PortfolioItem, ...] = (
    PortfolioItem(
        "polish", "✨", "Полировка кузова",
        "Лакокрасочное покрытие стало заметно глубже и ровнее по блеску.",
        ("Мойка и деконтаминация кузова", "Оценка толщины ЛКП", "Двухэтапная полировка",
         "Обезжиривание и финишная защита"),
    ),
    PortfolioItem(
        "ceramic", "🛡", "Керамическое покрытие",
        "Поверхность получила выраженный глянец и стала проще в уходе.",
        ("Мойка и очистка от загрязнений", "Полировка кузова", "Обезжиривание",
         "Нанесение керамики в несколько слоёв"),
    ),
    PortfolioItem(
        "interior", "🪑", "Химчистка салона",
        "Салон очищен от пятен и въевшейся грязи, ткани освежены.",
        ("Пылесос и продувка труднодоступных мест", "Экстракторная чистка сидений и ковров",
         "Очистка пластика", "Чистка стёкол изнутри"),
    ),
    PortfolioItem(
        "ppf", "🧱", "Защитная плёнка",
        "Зоны риска закрыты прозрачной плёнкой без изменения цвета кузова.",
        ("Подготовка поверхности", "Подбор и раскрой плёнки", "Оклейка капота, бампера и крыльев",
         "Проверка стыков и кромок"),
    ),
    PortfolioItem(
        "complex", "👑", "Комплексный уход за автомобилем",
        "Автомобиль получил полный цикл ухода: кузов, салон и защита.",
        ("Детейлинг-мойка", "Полировка кузова", "Химчистка салона", "Защитное покрытие"),
    ),
)

PORTFOLIO_BY_ID = {item.id: item for item in PORTFOLIO}


def find_photo(portfolio_dir: Path, item_id: str) -> Path | None:
    """Ищет локальное фото для карточки; None, если файла нет."""
    for ext in PHOTO_EXTENSIONS:
        candidate = portfolio_dir / f"{item_id}{ext}"
        if candidate.is_file():
            return candidate
    return None
