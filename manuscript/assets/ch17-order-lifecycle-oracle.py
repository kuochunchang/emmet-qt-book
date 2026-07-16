"""第 17 章固定訂單生命週期 oracle。

只呼叫 emmet-qt-bt1 v0.3.0 已發布的訂單模型與狀態機；不送單、不連交易所，
也不複製狀態轉移規則。
"""

from decimal import Decimal
from itertools import pairwise

from quant.common.errors import InvalidOrderTransition
from quant.common.models import (
    ALLOWED_TRANSITIONS,
    Market,
    Order,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Side,
    TimeInForce,
    validate_transition,
)

TS = 1_700_000_000_000


def update(
    status: OrderStatus,
    *,
    timestamp: int,
    filled: str,
    last_fill: str,
) -> OrderUpdate:
    has_fill = Decimal(last_fill) > 0
    return OrderUpdate(
        timestamp=timestamp,
        market=Market.FUTURES,
        symbol="BTCUSDT",
        client_order_id="lesson-17-a",
        order_id="BT-F-00000001",
        status=status,
        filled_qty=Decimal(filled),
        last_fill_qty=Decimal(last_fill),
        last_fill_price=Decimal("100.00") if has_fill else None,
        avg_price=Decimal("100.00") if Decimal(filled) > 0 else None,
        fee=Decimal("0.04") if has_fill else Decimal("0"),
        fee_asset="USDT" if has_fill else None,
    )


def expect_invalid(current: OrderStatus, new: OrderStatus, marker: str) -> None:
    try:
        validate_transition(current, new)
    except InvalidOrderTransition:
        print(f"{marker}=FAIL-CLOSED,InvalidOrderTransition")
        return
    raise AssertionError(f"illegal transition unexpectedly accepted: {current}->{new}")


def main() -> None:
    limit_gtc = Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("1.000"),
        price=Decimal("100.00"),
        time_in_force=TimeInForce.GTC,
        created_at=TS,
    )
    limit_gtx = Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=Side.SELL,
        type=OrderType.LIMIT,
        qty=Decimal("1.000"),
        price=Decimal("101.00"),
        post_only=True,
        created_at=TS,
    )
    reduce_market = Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=Side.SELL,
        type=OrderType.MARKET,
        qty=Decimal("0.500"),
        reduce_only=True,
        created_at=TS,
    )
    assert limit_gtc.time_in_force is TimeInForce.GTC
    assert limit_gtx.time_in_force is TimeInForce.GTX and limit_gtx.post_only
    assert reduce_market.price is None and reduce_market.reduce_only
    print(
        "intents=LIMIT/GTC,LIMIT/GTX-post-only,MARKET/reduce-only,decimal-source=string"
    )
    print("intent-model-status=NEW,exchange-ack=false")

    try:
        Order(
            market=Market.SPOT,
            symbol="BTCUSDT",
            side=Side.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("1.000"),
            price=Decimal("100.00"),
            post_only=True,
            created_at=TS,
        )
    except ValueError:
        print("spot-post-only-model=FAIL-CLOSED")
    else:
        raise AssertionError("spot post-only unexpectedly accepted")

    legal = {
        (current, new)
        for current, targets in ALLOWED_TRANSITIONS.items()
        for new in targets
    }
    assert len(legal) == 10
    assert all(
        not ALLOWED_TRANSITIONS[terminal]
        for terminal in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )
    )
    print("legal-transition-count=10,terminal-exits=0")

    trace = (
        update(OrderStatus.NEW, timestamp=TS, filled="0", last_fill="0"),
        update(
            OrderStatus.PARTIALLY_FILLED,
            timestamp=TS + 1,
            filled="0.400",
            last_fill="0.400",
        ),
        update(
            OrderStatus.PARTIALLY_FILLED,
            timestamp=TS + 2,
            filled="0.700",
            last_fill="0.300",
        ),
        update(
            OrderStatus.CANCELED,
            timestamp=TS + 3,
            filled="0.700",
            last_fill="0",
        ),
    )
    for current, new in pairwise(trace):
        validate_transition(current.status, new.status)
    assert [event.filled_qty for event in trace] == [
        Decimal("0"),
        Decimal("0.400"),
        Decimal("0.700"),
        Decimal("0.700"),
    ]
    print(
        "trace=NEW>PARTIALLY_FILLED>PARTIALLY_FILLED>CANCELED,"
        "cumulative=0>0.400>0.700>0.700"
    )
    print("same-status-partial=LEGAL-ONLY-WITH-CUMULATIVE-PROGRESS")

    validate_transition(OrderStatus.NEW, OrderStatus.FILLED)
    print("direct-fill=NEW>FILLED,PASS")
    expect_invalid(OrderStatus.NEW, OrderStatus.NEW, "duplicate-new")
    expect_invalid(OrderStatus.CANCELED, OrderStatus.FILLED, "terminal-late-fill")
    print("initial-rejected=canonical-first-event-not-a-transition")
    print("chapter-17-order-lifecycle-oracle=PASS")


if __name__ == "__main__":
    main()
