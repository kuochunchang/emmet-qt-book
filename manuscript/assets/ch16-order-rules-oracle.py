#!/usr/bin/env python3
"""Chapter 16 deterministic oracle for the published v0.3.0 rule snapshot."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from quant.common.errors import OrderValidationError
from quant.common.models import Market, Order, OrderType, Side
from quant.common.rules import RulesRepository, check_max_num_orders, validate_order


def limit_order(price: str, qty: str) -> Order:
    return Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=Side.BUY,
        type=OrderType.LIMIT,
        price=Decimal(price),
        qty=Decimal(qty),
    )


def validate_case(name: str, price: str, qty: str, expected: str) -> None:
    order = limit_order(price, qty)
    original = (order.price, order.qty)
    try:
        validate_order(order, RULES)
    except OrderValidationError as exc:
        observed = f"{exc.filter_type},code-{exc.binance_code}"
    else:
        observed = "PASS"
    assert observed == expected, (name, observed, expected)
    assert (order.price, order.qty) == original
    print(f"{name}={observed},price-{price},qty-{qty},unchanged-true")


FIXTURES = Path.cwd() / "tests" / "fixtures" / "exchange_rules"
REPOSITORY = RulesRepository(FIXTURES)
RULES = REPOSITORY.load_symbol_rules(Market.FUTURES, "BTCUSDT")
BRACKETS = REPOSITORY.load_bracket_table("BTCUSDT")

assert RULES.snapshot_ts == BRACKETS.snapshot_ts == 1_700_000_000_000
print(
    "rules=futures/BTCUSDT,"
    f"snapshot-{RULES.snapshot_ts},status-{RULES.status},"
    f"tick-{RULES.tick_size},step-{RULES.step_size},"
    f"market-step-{RULES.market_step_size},min-notional-{RULES.min_notional},"
    f"max-orders-{RULES.max_num_orders}"
)

validate_case("valid-limit", "50000.10", "0.010", "PASS")
validate_case("bad-tick", "50000.15", "0.010", "PRICE_FILTER,code--4014")
validate_case("bad-step", "50000.10", "0.0015", "LOT_SIZE,code--4013")
validate_case("too-small", "50000.10", "0.001", "MIN_NOTIONAL,code--4164")

for open_count, expected in ((159, "OK"), (160, "WARN"), (200, "REJECT")):
    result = check_max_num_orders(open_count, RULES)
    assert result.level.value == expected
    print(f"open-orders-{open_count}={result.level.value},cap-{result.cap}")

for notional, requested, expected_max, expected_decision in (
    ("49999.99", 50, 125, "PASS"),
    ("50000", 100, 100, "PASS"),
    ("3000000", 50, 20, "FAIL-CLOSED"),
):
    maximum = BRACKETS.max_leverage_for(Decimal(notional))
    decision = "PASS" if requested <= maximum else "FAIL-CLOSED"
    assert (maximum, decision) == (expected_max, expected_decision)
    print(
        f"leverage-notional-{notional}=requested-{requested},"
        f"max-{maximum},{decision}"
    )

try:
    Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=Side.BUY,
        type=OrderType.LIMIT,
        price=Decimal("50000.10"),
        qty=0.010,  # type: ignore[arg-type]
    )
except TypeError:
    print("float-quantity=TYPE-ERROR,FAIL-CLOSED")
else:  # pragma: no cover - the published Decimal guard must reject this
    raise AssertionError("float quantity unexpectedly accepted")

print("chapter-16-order-rules-oracle=PASS")
