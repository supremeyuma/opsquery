from __future__ import annotations

from opsquery.seed import generator


def test_seed_is_deterministic() -> None:
    first = generator.build_dataset(42)
    second = generator.build_dataset(42)
    assert generator.dataset_counts(first) == generator.dataset_counts(second)
    assert generator.dataset_checksum(first) == generator.dataset_checksum(second)


def test_seed_differs_for_a_different_seed() -> None:
    assert generator.dataset_checksum(generator.build_dataset(42)) != generator.dataset_checksum(
        generator.build_dataset(43)
    )


def test_seed_shape_and_dgp() -> None:
    data = generator.build_dataset()
    counts = generator.dataset_counts(data)
    assert counts["orders"] == generator.N_ORDERS == 50_000
    assert counts["customers"] == 5_000
    # 24 months of orders, with a deliberate seasonality tilt to Nov/Dec.
    orders = data["orders"]
    months = [row[5].month for row in orders]
    assert set(months) == set(range(1, 13))
    december_share = months.count(12) / len(months)
    january_share = months.count(1) / len(months)
    assert december_share > january_share
    # Awkward rows exist in the generated data itself.
    assert sum(1 for row in data["customers"] if row[3] is None) > 0
    assert sum(1 for row in data["inventory"] if row[3] < 0) > 0
    assert sum(1 for row in data["payments"] if row[3] < 0) > 0
    assert any(row[3] is not None for row in data["support_tickets"])
    assert any(row[3] is None for row in data["support_tickets"])
