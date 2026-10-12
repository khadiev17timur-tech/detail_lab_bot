import pytest

from content.catalog import (ADDONS_BY_ID, CAR_CLASSES, SERVICES, SERVICES_BY_ID)
from services.pricing import PricingError, calculate_estimate, format_coef, format_rub


def test_base_price_for_compact_class_equals_catalog_price():
    for svc in SERVICES:
        est = calculate_estimate(svc.id, "compact")
        assert est.class_price == svc.price_from and est.total == svc.price_from and not est.addons


def test_class_coefficients_are_applied():
    # 18 000 × 1,00 / 1,15 / 1,35 / 1,50
    assert [calculate_estimate("polish", c.id).total for c in CAR_CLASSES] == [18000, 20700, 24300, 27000]


def test_rounding_to_hundreds_half_up():
    # 1500 × 1,15 = 1725 -> 1700;  3500 × 1,15 = 4025 -> 4000;  4000 × 1,15 = 4600
    assert calculate_estimate("wash", "middle").total == 1700
    assert calculate_estimate("detail_wash", "middle").total == 4000
    assert calculate_estimate("headlights", "middle").total == 4600
    # 1500 × 1,35 = 2025 -> 2000;   1500 × 1,5 = 2250 -> 2300 (половина — вверх)
    assert calculate_estimate("wash", "business").total == 2000
    assert calculate_estimate("wash", "suv").total == 2300


def test_addons_are_added_flat_and_deduplicated():
    est = calculate_estimate("polish", "business", ["decon", "decon", "trim_restore"])
    assert est.class_price == 24300
    assert [l.amount for l in est.addons] == [ADDONS_BY_ID["decon"].price, ADDONS_BY_ID["trim_restore"].price]
    assert est.total == 24300 + 4000 + 3500
    assert est.addon_titles == (ADDONS_BY_ID["decon"].title, ADDONS_BY_ID["trim_restore"].title)


def test_addon_order_does_not_change_result():
    a = calculate_estimate("interior", "suv", ["ozone", "headliner"])
    b = calculate_estimate("interior", "suv", ["headliner", "ozone"])
    assert a == b


@pytest.mark.parametrize("service,cls,addons", [
    ("nope", "compact", []),
    ("wash", "tank", []),
    ("wash", "compact", ["ozone"]),        # доп. работа не относится к услуге
    ("wash", "compact", ["unknown_addon"]),
    ("", "", []),
])
def test_invalid_input_is_rejected(service, cls, addons):
    with pytest.raises(PricingError):
        calculate_estimate(service, cls, addons)


def test_all_services_and_every_addon_calculate():
    for svc in SERVICES:
        for cls in CAR_CLASSES:
            est = calculate_estimate(svc.id, cls.id, svc.addon_ids)
            assert est.total >= svc.price_from
            assert est.total == est.class_price + sum(ADDONS_BY_ID[a].price for a in svc.addon_ids)


def test_formatting():
    assert format_rub(28300) == "28 300 ₽"
    assert format_coef(115) == "×1,15" and format_coef(100) == "×1,00"


def test_catalog_has_the_ten_required_services():
    assert [s.id for s in SERVICES] == ["wash", "detail_wash", "interior", "polish", "abrasive",
                                         "ceramic", "ppf", "headlights", "leather", "complex"]
    assert set(SERVICES_BY_ID) == {s.id for s in SERVICES}
