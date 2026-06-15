"""Hand-checked tests for change / materiality detection."""

from __future__ import annotations

from decimal import Decimal

from app.analysis.changes import probability_change

MATERIAL = Decimal("0.01")


def test_first_observation_has_zero_delta_and_immaterial() -> None:
    change = probability_change(None, Decimal("0.62"), material_threshold=MATERIAL)
    assert change.delta == Decimal(0)
    assert change.material is False


def test_material_move_flagged() -> None:
    # 0.62 - 0.60 = 0.02 >= 0.01 -> material
    change = probability_change(Decimal("0.60"), Decimal("0.62"), material_threshold=MATERIAL)
    assert change.delta == Decimal("0.02")
    assert change.material is True


def test_sub_threshold_move_not_material() -> None:
    # 0.605 - 0.60 = 0.005 < 0.01 -> not material
    change = probability_change(Decimal("0.60"), Decimal("0.605"), material_threshold=MATERIAL)
    assert change.delta == Decimal("0.005")
    assert change.material is False


def test_exact_threshold_is_material() -> None:
    change = probability_change(Decimal("0.60"), Decimal("0.61"), material_threshold=MATERIAL)
    assert change.material is True  # >= threshold


def test_downward_move_uses_absolute_value() -> None:
    change = probability_change(Decimal("0.62"), Decimal("0.60"), material_threshold=MATERIAL)
    assert change.delta == Decimal("-0.02")
    assert change.material is True
