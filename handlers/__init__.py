from aiogram import Router

from handlers import (admin, booking, cabinet, calculator, cars, common, contacts, faq, fallback,
                      portfolio, prices, reviews, services)


def build_root_router() -> Router:
    """Порядок важен: общие команды и меню — раньше сценариев, запасной роутер — последним."""
    ordered = (
        common.router,
        admin.admin_router,
        admin.deny_router,
        services.router,
        prices.router,
        calculator.router,
        portfolio.router,
        faq.router,
        contacts.router,
        cabinet.router,
        cars.router,
        reviews.router,
        booking.router,
        fallback.router,
    )
    root = Router(name="root")
    for router in ordered:
        # Роутеры модульные; отвязываем от прежнего родителя, чтобы диспетчер можно было
        # собрать повторно (тесты создают их десятками).
        router._parent_router = None
    root.include_routers(*ordered)
    return root
