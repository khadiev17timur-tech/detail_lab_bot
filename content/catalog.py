"""Каталог услуг, категории, классы авто и дополнительные работы.

ЕДИНЫЙ источник цен: каталог, прайс, калькулятор и заявки берут данные отсюда.
Все значения демонстрационные.

Как менять:
  * цена «от»   — Service.price_from (для компактного класса, в рублях);
  * коэффициенты — CarClass.coef_pct (100 = ×1,00; 135 = ×1,35);
  * доп. работы  — ADDONS + Service.addon_ids.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CarClass:
    id: str
    title: str
    examples: str
    coef_pct: int  # процент от базовой цены: 100 = ×1,00


@dataclass(frozen=True)
class Addon:
    id: str
    title: str
    price: int


@dataclass(frozen=True)
class Category:
    id: str
    title: str


@dataclass(frozen=True)
class Service:
    id: str
    emoji: str
    title: str
    short_title: str
    category_id: str
    summary: str
    description: str
    benefits: tuple[str, ...]
    price_from: int
    duration: str
    addon_ids: tuple[str, ...] = ()


CAR_CLASSES: tuple[CarClass, ...] = (
    CarClass("compact", "Компактный", "хэтчбеки и малый класс", 100),
    CarClass("middle", "Средний", "седаны, универсалы, кроссоверы", 115),
    CarClass("business", "Бизнес-класс", "седаны и купе бизнес-класса", 135),
    CarClass("suv", "Внедорожник", "полноразмерные SUV и минивэны", 150),
)

CATEGORIES: tuple[Category, ...] = (
    Category("care", "🧼 Мойка и уход"),
    Category("interior", "🪑 Салон"),
    Category("body", "✨ Кузов и ЛКП"),
    Category("protection", "🛡 Защита"),
    Category("complex", "👑 Комплексные программы"),
)

ADDONS: tuple[Addon, ...] = (
    Addon("engine_bay", "Мойка подкапотного пространства", 2000),
    Addon("wheel_care", "Глубокая очистка дисков и арок", 1500),
    Addon("glass_coat", "Гидрофобное покрытие на стёкла", 1500),
    Addon("trunk_clean", "Чистка багажника", 1500),
    Addon("trim_restore", "Восстановление чёрного пластика", 3500),
    Addon("decon", "Деконтаминация ЛКП (глина, очистка от металла)", 4000),
    Addon("glass_polish", "Полировка стёкол", 5000),
    Addon("ozone", "Озонирование салона", 2000),
    Addon("ac_clean", "Чистка системы кондиционирования", 3000),
    Addon("headliner", "Чистка потолка", 2500),
    Addon("leather_coat", "Защитная пропитка кожи", 3000),
    Addon("leather_restore", "Локальное восстановление окраса кожи", 5000),
    Addon("wheel_ceramic", "Керамическое покрытие на диски", 8000),
    Addon("glass_ceramic", "Керамическое покрытие на стёкла", 4000),
    Addon("ppf_headlights", "Защитная плёнка на фары", 5000),
    Addon("ppf_sills", "Плёнка на пороги и под ручки", 3500),
    Addon("headlight_polish", "Полировка фар", 2500),
)

SERVICES: tuple[Service, ...] = (
    Service(
        id="wash", emoji="🧼", title="Мойка кузова и бережный уход", short_title="Мойка кузова",
        category_id="care",
        summary="Бесконтактная мойка и аккуратная сушка.",
        description=(
            "Бережная ручная мойка с профессиональной химией, очистка дисков и "
            "порогов, аккуратная сушка кузова без разводов."
        ),
        benefits=("Безопасно для лакокрасочного покрытия", "Чистые диски и арки",
                  "Кузов без разводов и следов воды"),
        price_from=1500, duration="40–60 минут",
        addon_ids=("wheel_care", "glass_coat", "trunk_clean"),
    ),
    Service(
        id="detail_wash", emoji="💧", title="Детейлинг-мойка", short_title="Детейлинг-мойка",
        category_id="care",
        summary="Многоэтапная мойка с очисткой труднодоступных мест.",
        description=(
            "Многоэтапная мойка: предварительная очистка, ручная мойка, обработка "
            "стыков, проёмов и дисков, нанесение защитного состава."
        ),
        benefits=("Проработка проёмов, стыков и порогов", "Защитный состав после мойки",
                  "Заметный эффект свежего автомобиля"),
        price_from=3500, duration="2–3 часа",
        addon_ids=("engine_bay", "wheel_care", "decon", "glass_coat", "trim_restore"),
    ),
    Service(
        id="interior", emoji="🪑", title="Глубокая химчистка салона", short_title="Химчистка салона",
        category_id="interior",
        summary="Глубокая очистка сидений, потолка, пластика и ковров.",
        description=(
            "Комплексная очистка салона: пылесос, экстракторная чистка тканей и ковров, "
            "очистка пластика и стёкол изнутри."
        ),
        benefits=("Удаление пятен и въевшейся грязи", "Свежий салон без резких запахов",
                  "Бережные материалы для тканей и пластика"),
        price_from=9000, duration="6–8 часов",
        addon_ids=("ozone", "ac_clean", "headliner", "trunk_clean", "leather_coat"),
    ),
    Service(
        id="polish", emoji="✨", title="Полировка кузова", short_title="Полировка кузова",
        category_id="body",
        summary="Восстановление блеска и глубины цвета.",
        description=(
            "Полировка лакокрасочного покрытия для возвращения блеска и "
            "выравнивания лёгкой паутины и матовости."
        ),
        benefits=("Глубокий блеск и насыщенный цвет", "Снижение заметности паутины",
                  "Подготовка к нанесению защиты"),
        price_from=18000, duration="1–2 дня",
        addon_ids=("decon", "trim_restore", "headlight_polish", "glass_polish"),
    ),
    Service(
        id="abrasive", emoji="🔧", title="Абразивная полировка и устранение мелких дефектов ЛКП",
        short_title="Абразивная полировка", category_id="body",
        summary="Работа с царапинами, потёртостями и голограммами.",
        description=(
            "Многоступенчатая абразивная полировка для устранения мелких царапин, "
            "потёртостей и голограмм. Глубокие повреждения оцениваются на осмотре."
        ),
        benefits=("Устранение мелких дефектов ЛКП", "Контроль толщины покрытия при работе",
                  "Подходит перед нанесением защиты"),
        price_from=28000, duration="2–3 дня",
        addon_ids=("decon", "trim_restore", "glass_polish"),
    ),
    Service(
        id="ceramic", emoji="🛡", title="Керамическое покрытие", short_title="Керамика",
        category_id="protection",
        summary="Защитное покрытие для блеска и лёгкой мойки.",
        description=(
            "Подготовка кузова и нанесение керамического покрытия: поверхность "
            "дольше остаётся чистой, а мойка становится проще."
        ),
        benefits=("Выраженный глянец и гидрофобность", "Защита от реагентов и загрязнений",
                  "Проще уход за автомобилем"),
        price_from=35000, duration="2–3 дня",
        addon_ids=("decon", "wheel_ceramic", "glass_ceramic", "trim_restore"),
    ),
    Service(
        id="ppf", emoji="🧱", title="Оклейка кузова защитной плёнкой", short_title="Защитная плёнка",
        category_id="protection",
        summary="Прозрачная плёнка для зон риска или всего кузова.",
        description=(
            "Оклейка полиуретановой защитной плёнкой: капот, бампер, крылья, зеркала "
            "и другие зоны риска или весь кузов."
        ),
        benefits=("Защита от сколов и мелких царапин", "Прозрачная плёнка сохраняет цвет авто",
                  "Объём оклейки подбирается индивидуально"),
        price_from=60000, duration="3–5 дней",
        addon_ids=("ppf_headlights", "ppf_sills", "decon"),
    ),
    Service(
        id="headlights", emoji="💡", title="Полировка и защита фар", short_title="Фары",
        category_id="protection",
        summary="Возвращаем прозрачность и защищаем оптику.",
        description=(
            "Шлифовка и полировка помутневших фар, нанесение защитного покрытия "
            "для сохранения прозрачности."
        ),
        benefits=("Прозрачная оптика и лучше свет", "Защитное покрытие после полировки",
                  "Свежий вид передней части авто"),
        price_from=4000, duration="1,5–2 часа",
        addon_ids=("ppf_headlights",),
    ),
    Service(
        id="leather", emoji="🧴", title="Уход за кожаным салоном", short_title="Кожаный салон",
        category_id="interior",
        summary="Очистка, питание и защита кожи.",
        description=(
            "Мягкая очистка кожаных поверхностей, нанесение питательного и "
            "защитного состава для сохранения эластичности."
        ),
        benefits=("Бережная очистка без пересушивания", "Питание и защита кожи",
                  "Опрятный вид сидений и панелей"),
        price_from=6000, duration="2–4 часа",
        addon_ids=("leather_coat", "leather_restore"),
    ),
    Service(
        id="complex", emoji="👑", title="Комплексный детейлинг", short_title="Комплекс",
        category_id="complex",
        summary="Кузов, салон и защита в одной программе.",
        description=(
            "Программа «под ключ»: детейлинг-мойка, полировка кузова, очистка салона "
            "и защитное покрытие. Состав обсуждается индивидуально."
        ),
        benefits=("Всё необходимое за один визит", "Единый план работ и сроков",
                  "Выгоднее, чем заказывать услуги по отдельности"),
        price_from=65000, duration="3–5 дней",
        addon_ids=("engine_bay", "glass_coat", "ozone", "ac_clean", "wheel_ceramic"),
    ),
)

# --- быстрый доступ -------------------------------------------------------------
SERVICES_BY_ID = {s.id: s for s in SERVICES}
CAR_CLASSES_BY_ID = {c.id: c for c in CAR_CLASSES}
ADDONS_BY_ID = {a.id: a for a in ADDONS}
CATEGORIES_BY_ID = {c.id: c for c in CATEGORIES}


def _check_integrity() -> None:
    """Ловим опечатки в конфигурации при импорте."""
    assert len(SERVICES_BY_ID) == len(SERVICES), "повторяющиеся id услуг"
    assert len(ADDONS_BY_ID) == len(ADDONS), "повторяющиеся id доп. работ"
    for svc in SERVICES:
        assert svc.category_id in CATEGORIES_BY_ID, f"{svc.id}: неизвестная категория"
        for addon_id in svc.addon_ids:
            assert addon_id in ADDONS_BY_ID, f"{svc.id}: неизвестная доп. работа {addon_id}"
        assert len(set(svc.addon_ids)) == len(svc.addon_ids), f"{svc.id}: повтор доп. работ"
        assert svc.price_from > 0


_check_integrity()
