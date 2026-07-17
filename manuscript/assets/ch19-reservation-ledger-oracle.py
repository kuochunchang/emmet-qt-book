"""第 19 章：用已發布 AccountingLedger 核對預留、部分成交與唯一帳本。"""

from decimal import Decimal

from quant.common.engine import AccountingLedger
from quant.common.fill import FillConfig, FillDecision, FillSimulator, Liquidity
from quant.common.models import (
    Balance,
    ExecutionOpenEvent,
    MarginType,
    Market,
    Order,
    OrderEvent,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Side,
    SpotWallet,
    dec,
)
from quant.common.rules import LeverageBracket, LeverageBracketTable, SymbolRules

TS = 1_700_000_000_000


def rules(market: Market) -> SymbolRules:
    return SymbolRules(
        market=market,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT" if market is Market.FUTURES else None,
        status="TRADING",
        tick_size=dec("0.01"),
        price_min=dec("0.01"),
        price_max=dec("1000000"),
        step_size=dec("0.001"),
        qty_min=dec("0.001"),
        qty_max=dec("1000"),
        min_notional=dec("1"),
        max_num_orders=200,
        snapshot_ts=TS,
    )


def brackets() -> LeverageBracketTable:
    return LeverageBracketTable(
        symbol="BTCUSDT",
        snapshot_ts=TS,
        brackets=(
            LeverageBracket(
                bracket=1,
                notional_floor=dec("0"),
                notional_cap=dec("100000000"),
                maint_margin_rate=dec("0.01"),
                maint_amount=dec("0"),
                initial_leverage=100,
            ),
        ),
    )


def ledger(*, spot_usdt: str = "1000", futures_usdt: str = "1000") -> AccountingLedger:
    return AccountingLedger(
        spot=SpotWallet(
            balances={
                "BTC": Balance(free=dec("0"), locked=dec("0")),
                "USDT": Balance(free=dec(spot_usdt), locked=dec("0")),
            }
        ),
        futures_wallet_balance=dec(futures_usdt),
        positions=(),
        leverage_by_symbol={"BTCUSDT": 10},
        margin_type_by_symbol={"BTCUSDT": MarginType.CROSS},
        brackets={"BTCUSDT": brackets()},
    )


def order(market: Market, *, qty: str = "2", price: str | None = "100") -> Order:
    return Order(
        market=market,
        symbol="BTCUSDT",
        side=Side.BUY,
        type=OrderType.LIMIT if price is not None else OrderType.MARKET,
        qty=dec(qty),
        price=dec(price) if price is not None else None,
        created_at=TS,
    )


def order_event(
    order_id: str,
    status: OrderStatus,
    market: Market,
    *,
    filled: str = "0",
    last_fill: str = "0",
    price: str | None = None,
    fee: str = "0",
    fee_asset: str | None = None,
    timestamp: int = TS,
) -> OrderEvent:
    fill_price = dec(price) if price is not None else None
    return OrderEvent(
        timestamp=timestamp,
        update=OrderUpdate(
            timestamp=timestamp,
            market=market,
            symbol="BTCUSDT",
            client_order_id=None,
            order_id=order_id,
            status=status,
            filled_qty=dec(filled),
            last_fill_qty=dec(last_fill),
            last_fill_price=fill_price,
            avg_price=fill_price,
            fee=dec(fee),
            fee_asset=fee_asset,
        ),
    )


def fill_decision(
    order_id: str,
    market: Market,
    *,
    qty: str,
    price: str,
    fee: str,
) -> FillDecision:
    return FillDecision(
        order_id=order_id,
        market=market,
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=dec(qty),
        base_price=dec(price),
        final_price=dec(price),
        fee_rate=dec("0"),
        fee=dec(fee),
        fee_asset="BTC" if market is Market.SPOT else "USDT",
        liquidity=Liquidity.TAKER,
        accounting_ts=TS + 1,
        reason="chapter-19-fixed-case",
    )


def spot_partial_then_cancel() -> tuple[AccountingLedger, tuple[str, ...]]:
    accounting = ledger()
    submitted = order(Market.SPOT)
    reservation = accounting.reserve(
        "spot-buy",
        submitted,
        rules(Market.SPOT),
        ref_price=dec("100"),
        fee_rate=dec("0"),
        reserve_bps=dec("0"),
    )
    accounting.apply_order_event(order_event("spot-buy", OrderStatus.NEW, Market.SPOT))
    admitted = accounting.snapshot().spot.balances["USDT"]
    assert reservation.amount == dec("200")
    assert admitted == Balance(free=dec("800"), locked=dec("200"))

    accounting.authorize_and_apply_fill(
        fill_decision("spot-buy", Market.SPOT, qty="1", price="90", fee="0.01")
    )
    accounting.apply_order_event(
        order_event(
            "spot-buy",
            OrderStatus.PARTIALLY_FILLED,
            Market.SPOT,
            filled="1",
            last_fill="1",
            price="90",
            fee="0.01",
            fee_asset="BTC",
        )
    )
    partial = accounting.snapshot()
    partial_reservation = accounting.reservations()[0]
    assert partial.spot.balances["USDT"] == Balance(free=dec("810"), locked=dec("100"))
    assert partial.spot.balances["BTC"] == Balance(free=dec("0.99"), locked=dec("0"))
    assert partial_reservation.amount == dec("100")
    assert partial_reservation.remaining_qty == dec("1")

    accounting.apply_order_event(
        order_event(
            "spot-buy",
            OrderStatus.CANCELED,
            Market.SPOT,
            filled="1",
            timestamp=TS + 2,
        )
    )
    canceled = accounting.snapshot()
    assert canceled.spot.balances["USDT"] == Balance(free=dec("910"), locked=dec("0"))
    assert canceled.spot.balances["BTC"] == Balance(free=dec("0.99"), locked=dec("0"))
    assert accounting.reservations() == ()
    assert canceled.spot.balances["USDT"].free + canceled.spot.balances[
        "USDT"
    ].locked == dec("1000") - dec("1") * dec("90")
    assert canceled.spot.balances["BTC"].free + canceled.spot.balances[
        "BTC"
    ].locked == dec("1") - dec("0.01")
    return accounting, (
        "spot-admit=USDT-free:800,locked:200,reservation:200",
        "spot-partial=qty:1@90,fee:0.01BTC,USDT-free:810,locked:100,BTC-free:0.99,reservation-qty:1,amount:100",
        "spot-cancel=USDT-free:910,locked:0,BTC-free:0.99,reservations:0",
        "spot-conservation=USDT-total:910,BTC-total:0.99,PASS",
    )


def futures_partial_then_cancel() -> tuple[str, str]:
    accounting = ledger()
    submitted = order(Market.FUTURES)
    reservation = accounting.reserve(
        "futures-buy",
        submitted,
        rules(Market.FUTURES),
        ref_price=dec("100"),
        fee_rate=dec("0.001"),
        reserve_bps=dec("0"),
    )
    accounting.apply_order_event(
        order_event("futures-buy", OrderStatus.NEW, Market.FUTURES)
    )
    assert reservation.amount == dec("20.200")
    assert accounting.snapshot().futures.reservation_margin == dec("20.200")

    accounting.authorize_and_apply_fill(
        fill_decision("futures-buy", Market.FUTURES, qty="1", price="100", fee="0.1")
    )
    accounting.apply_order_event(
        order_event(
            "futures-buy",
            OrderStatus.PARTIALLY_FILLED,
            Market.FUTURES,
            filled="1",
            last_fill="1",
            price="100",
            fee="0.1",
            fee_asset="USDT",
        )
    )
    partial = accounting.reservations()[0]
    assert partial.amount == dec("10.100")
    assert partial.remaining_qty == dec("1")
    assert accounting.snapshot().futures.reservation_margin == dec("10.100")

    accounting.apply_order_event(
        order_event(
            "futures-buy",
            OrderStatus.CANCELED,
            Market.FUTURES,
            filled="1",
            timestamp=TS + 2,
        )
    )
    assert accounting.reservations() == ()
    assert accounting.snapshot().futures.reservation_margin == dec("0")
    return (
        "futures-admit=qty:2@100,leverage:10,fee-rate:0.001,reservation:20.200",
        "futures-partial-cancel=remaining-qty:1,reservation-before-cancel:10.100,reservation-after-cancel:0",
    )


def simulator_does_not_write_ledger() -> str:
    accounting = ledger()
    submitted = order(Market.FUTURES, qty="1", price=None)
    accounting.reserve(
        "simulator-boundary",
        submitted,
        rules(Market.FUTURES),
        ref_price=dec("100"),
        fee_rate=dec("0.0005"),
        reserve_bps=dec("100"),
    )
    simulator = FillSimulator(
        market=Market.FUTURES,
        rules={(Market.FUTURES, "BTCUSDT"): rules(Market.FUTURES)},
        config=FillConfig(),
    )
    accepted = simulator.accept("simulator-boundary", submitted, accepted_ts=TS)
    assert accepted is not None
    accounting.apply_order_event(accepted)
    before = accounting.snapshot()
    before_reservations = accounting.reservations()
    decision = simulator.propose_next(
        ExecutionOpenEvent(
            timestamp=TS,
            market=Market.FUTURES,
            symbol="BTCUSDT",
            interval="1m",
            open=100.0,
        ),
        before,
        consumed_volume=Decimal("0"),
        handled_order_ids=frozenset(),
    )
    assert isinstance(decision, FillDecision)
    assert accounting.snapshot() == before
    assert accounting.reservations() == before_reservations
    assert decision.order_id == "simulator-boundary"
    return "simulator-ledger-boundary=decision-only,ledger-unchanged:true"


def main() -> None:
    _, spot_lines = spot_partial_then_cancel()
    futures_lines = futures_partial_then_cancel()
    for line in (*spot_lines, *futures_lines):
        print(line)
    print(simulator_does_not_write_ledger())
    print("chapter-19-reservation-ledger-oracle=PASS")


if __name__ == "__main__":
    main()
