"""第 20 章：逐筆核對 Phase 3 funding／liquidation 會計 golden。"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

EXPECTED_CANONICAL_SHA256 = (
    "f9e65a281e5fe242a75475e5c99832247b00f8d97a380a735cb99ffb908ef7c7"
)


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def canonical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    companion = Path(os.environ["EMMET_QT_BT1_DIR"])
    golden_path = companion / "tests/fixtures/accounting/it3_golden.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    digest = canonical_sha256(golden)
    assert digest == EXPECTED_CANONICAL_SHA256
    assert len(golden["external_causes"]) == 40
    assert len(golden["effects"]) == 19
    assert len(golden["fills"]) == 6
    assert len(golden["funding_posted"]) == 2
    assert len(golden["liquidations"]) == 1

    funding = {row["symbol"]: row for row in golden["funding_posted"]}
    btc = funding["BTCUSDT"]
    eth = funding["ETHUSDT"]
    for row in (btc, eth):
        assert dec(row["cash_flow"]) == (
            -dec(row["signed_qty"]) * dec(row["mark_price"]) * dec(row["rate"])
        )
    assert dec(btc["cash_flow"]) == dec("-51.4800")
    assert dec(eth["cash_flow"]) == dec("-0.2030")

    before_funding = dec(golden["risk_trace"][2]["margin_balance_close"])
    after_funding = dec(golden["risk_trace"][3]["margin_balance_close"])
    assert before_funding == dec("49.08720025000")
    assert after_funding == dec("-2.39279975000")
    assert after_funding - before_funding == dec(btc["cash_flow"])

    btc_funding_effects = [
        effect
        for effect in golden["effects"]
        if effect["cause_key"]["root_identity"] == ["futures", "BTCUSDT", "funding", ""]
    ]
    assert [effect["effect_seq"] for effect in btc_funding_effects] == [0, 1, 2, 3]
    assert [effect["payload_type"] for effect in btc_funding_effects] == [
        "FundingPostedEvent",
        "OrderEvent",
        "OrderEvent",
        "LiquidationEvent",
    ]
    canceled = tuple(
        effect["payload"]["update"]["order_id"] for effect in btc_funding_effects[1:3]
    )
    assert canceled == ("BT-F-00000004", "BT-F-00000006")
    assert all(
        effect["payload"]["update"]["status"] == "CANCELED"
        for effect in btc_funding_effects[1:3]
    )

    liquidation = golden["liquidations"][0]
    leg = liquidation["legs"][0]
    assert liquidation["scope"] == "cross_account"
    assert liquidation["trigger_reason"] == "margin_balance"
    assert tuple(liquidation["canceled_order_ids"]) == canceled
    assert dec(liquidation["margin_balance"]) == after_funding
    assert dec(leg["fee"]) == (
        dec(leg["qty"]) * dec(leg["execution_price"]) * dec(leg["fee_rate"])
    )
    assert leg["symbol"] == "BTCUSDT"
    assert leg["close_side"] == "SELL"

    final = golden["final_account"]
    assert golden["final_reservations"] == []
    assert final["futures"]["positions"] == []
    assert dec(final["futures"]["wallet_balance"]) == dec(
        "24.69955390988955823293172694"
    )
    assert dec(final["spot"]["balances"]["BTC"]["free"]) == dec("0.98")
    assert dec(final["spot"]["balances"]["USDT"]["free"]) == dec("1199.7001000000")

    print(f"golden-sha256={digest}")
    print("golden-counts=external:40,effects:19,fills:6,funding:2,liquidations:1")
    print(
        "funding=BTCUSDT,qty:0.02,mark:9900,rate:0.26,"
        "cash-flow:-51.4800,margin-balance:49.08720025000->-2.39279975000"
    )
    print(
        "liquidation=scope:cross_account,reason:margin_balance,"
        "cancel:BT-F-00000004+BT-F-00000006,close:BTCUSDT-0.02,"
        f"fee:{leg['fee']}"
    )
    print("isolated=ETHUSDT,funding:-0.2030,position-final:closed")
    print(
        "final=futures-wallet:24.69955390988955823293172694,"
        "positions:0,reservations:0,spot-BTC:0.98,spot-USDT:1199.7001000000"
    )
    print("chapter-20-funding-liquidation-golden-oracle=PASS")


if __name__ == "__main__":
    main()
