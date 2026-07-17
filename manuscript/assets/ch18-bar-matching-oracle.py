"""第 18 章：用已發布 FillSimulator／BarFillMode 核對固定撮合假設。"""

from decimal import Decimal

from quant.common.fill import (
    FILL_ASSUMPTION_METADATA,
    BarFillMode,
    FillConfig,
    FillDecision,
    FillProposal,
    FillSimulator,
    MatchingPolicy,
    OrderBook,
)
from quant.common.models import (
    AccountState,
    Balance,
    Bar,
    ExecutionOpenEvent,
    FuturesWallet,
    Market,
    MarketEvent,
    Order,
    OrderType,
    Side,
    SpotWallet,
    TimeInForce,
    dec,
)
from quant.common.rules import SymbolRules

TS = 1_700_000_000_000
MINUTE = 60_000
ZERO = Decimal("0")


def account() -> AccountState:
    return AccountState(
        spot=SpotWallet(
            balances={
                "BTC": Balance(free=dec("0"), locked=dec("0")),
                "USDT": Balance(free=dec("10000"), locked=dec("0")),
            }
        ),
        futures=FuturesWallet(
            wallet_balance=dec("10000"), initial_margin=dec("0"), positions=()
        ),
    )


def rules() -> SymbolRules:
    return SymbolRules(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
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


def order(
    side: Side,
    *,
    order_type: OrderType = OrderType.LIMIT,
    price: str = "100",
    qty: str = "1",
    created_at: int = TS,
) -> Order:
    return Order(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        side=side,
        type=order_type,
        qty=dec(qty),
        price=dec(price) if order_type is OrderType.LIMIT else None,
        time_in_force=TimeInForce.GTC,
        created_at=created_at,
    )


def close_event(
    *,
    open_: float = 100.0,
    high: float = 110.0,
    low: float = 90.0,
    close: float = 105.0,
    volume: float = 10.0,
) -> MarketEvent:
    bar = Bar(
        market=Market.FUTURES,
        symbol="BTCUSDT",
        interval="1m",
        open_time=TS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=volume * 100,
        is_closed=True,
    )
    return MarketEvent(timestamp=bar.close_time, bar=bar)


def next_open(*, open_: float = 100.0) -> ExecutionOpenEvent:
    return ExecutionOpenEvent(
        timestamp=TS,
        market=Market.FUTURES,
        symbol="BTCUSDT",
        interval="1m",
        open=open_,
    )


def proposal(
    book: OrderBook,
    event: MarketEvent,
    *,
    touch: bool = False,
    handled: frozenset[str] = frozenset(),
) -> FillProposal | None:
    result = BarFillMode().next_candidate(
        book.view(),
        event,
        account(),
        policy=MatchingPolicy(optimistic_limit_touch=touch),
        handled_order_ids=handled,
    )
    assert result is None or isinstance(result, FillProposal)
    return result


def market_open_case() -> FillDecision:
    simulator = FillSimulator(
        market=Market.FUTURES,
        rules={(Market.FUTURES, "BTCUSDT"): rules()},
        config=FillConfig(),
    )
    simulator.accept(
        "market-buy",
        order(Side.BUY, order_type=OrderType.MARKET),
        accepted_ts=TS,
    )
    decision = simulator.propose_next(
        next_open(),
        account(),
        consumed_volume=ZERO,
        handled_order_ids=frozenset(),
    )
    assert isinstance(decision, FillDecision)
    assert decision.base_price == dec("100.0")
    assert decision.final_price == dec("100.05000")
    assert decision.fee_rate == dec("0.0005")
    assert decision.fee == dec("0.050025000")
    assert decision.liquidity.value == "taker"
    assert decision.fee_asset == "USDT"
    assert decision.accounting_ts == TS
    assert decision.reason == "market-open"
    return decision


def threshold_cases() -> tuple[FillProposal, FillProposal, FillProposal]:
    touch_book = OrderBook({})
    touch_book.accept("touch", order(Side.BUY), accepted_ts=TS)
    event = close_event(open_=105.0, low=100.0)
    assert proposal(touch_book, event) is None
    touch = proposal(touch_book, event, touch=True)
    assert isinstance(touch, FillProposal)
    assert touch.reason == "limit-touch"

    equality_book = OrderBook({})
    equality_book.accept("open-equality", order(Side.BUY), accepted_ts=TS)
    equality_event = close_event(open_=100.0, low=100.0)
    assert proposal(equality_book, equality_event) is None
    equality_touch = proposal(equality_book, equality_event, touch=True)
    assert isinstance(equality_touch, FillProposal)
    assert equality_touch.reason == "limit-gap-through"
    assert equality_touch.base_price == dec("100.0")

    gap_book = OrderBook({})
    gap_book.accept("gap", order(Side.BUY), accepted_ts=TS)
    gap = proposal(gap_book, close_event(open_=98.0, low=95.0))
    assert isinstance(gap, FillProposal)
    assert gap.reason == "limit-gap-through"
    assert gap.base_price == dec("98.0")
    return touch, equality_touch, gap


def fixed_path_case() -> list[str]:
    book = OrderBook({})
    for order_id, side, price in (
        ("sell-102", Side.SELL, "102"),
        ("buy-98", Side.BUY, "98"),
        ("sell-101", Side.SELL, "101"),
        ("buy-99", Side.BUY, "99"),
    ):
        book.accept(order_id, order(side, price=price), accepted_ts=TS)
    sequence: list[str] = []
    handled: frozenset[str] = frozenset()
    for _ in range(4):
        candidate = proposal(book, close_event(), handled=handled)
        assert isinstance(candidate, FillProposal)
        sequence.append(candidate.order_id)
        handled |= {candidate.order_id}
    assert sequence == ["sell-101", "sell-102", "buy-99", "buy-98"]
    return sequence


def shared_volume_case() -> tuple[FillDecision, FillDecision]:
    event = close_event()
    simulator = FillSimulator(
        market=Market.FUTURES,
        rules={(Market.FUTURES, "BTCUSDT"): rules()},
        config=FillConfig(
            matching=MatchingPolicy(optimistic_market=True),
            volume_cap_pct=dec("0.1"),
        ),
    )
    simulator.accept("limit", order(Side.BUY, qty="0.6"), accepted_ts=TS)
    simulator.accept(
        "optimistic-market",
        order(
            Side.BUY,
            order_type=OrderType.MARKET,
            qty="0.6",
            created_at=TS + MINUTE,
        ),
        accepted_ts=TS + MINUTE,
    )
    first = simulator.propose_next(
        event,
        account(),
        consumed_volume=ZERO,
        handled_order_ids=frozenset(),
    )
    assert isinstance(first, FillDecision)
    second = simulator.propose_next(
        event,
        account(),
        consumed_volume=first.qty,
        handled_order_ids=frozenset({first.order_id}),
    )
    assert isinstance(second, FillDecision)
    assert (first.order_id, first.qty) == ("limit", dec("0.6"))
    assert (second.order_id, second.qty) == ("optimistic-market", dec("0.40"))
    assert first.qty + second.qty == dec("1.00")
    return first, second


def main() -> None:
    market = market_open_case()
    touch, equality_touch, gap = threshold_cases()
    path = fixed_path_case()
    first, second = shared_volume_case()
    assert dict(FILL_ASSUMPTION_METADATA) == {
        "market_open_volume_proxy": "previous_closed_bar",
        "marketable_limit_liquidity": "maker_after_local_book_acceptance",
    }
    metadata = ",".join(
        f"{key}={value}" for key, value in FILL_ASSUMPTION_METADATA.items()
    )
    print(
        "market=next-execution-open,"
        f"base={market.base_price},final={market.final_price},"
        f"slippage_bps=5,liquidity={market.liquidity.value},"
        f"fee_rate={market.fee_rate},fee={market.fee},"
        f"fee_asset={market.fee_asset},accounting_ts={market.accounting_ts}"
    )
    print(f"limit-threshold=strict:NO-FILL,touch:{touch.reason}@{touch.base_price}")
    print(
        "limit-open-equality=strict:NO-FILL,"
        f"touch:{equality_touch.reason}@{equality_touch.base_price}"
    )
    print(
        f"gap-through=BUY,limit=100,open=98.0,base={gap.base_price},"
        "accounting=bar-close"
    )
    print("flat-bar-path=O-H-L-C,sequence=" + ">".join(path))
    print(
        "volume-pool=bar-volume:10.0,cap:0.1,pool:1.00,"
        f"{first.order_id}:{first.qty},{second.order_id}:{second.qty}"
    )
    print("metadata=" + metadata)
    print("chapter-18-bar-matching-oracle=PASS")


if __name__ == "__main__":
    main()
