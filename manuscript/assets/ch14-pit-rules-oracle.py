"""Chapter 14 fixed-offline PIT-universe and rules-era oracle.

The helper imports frozen teaching lifecycle evidence, calls the released PIT and
rules interfaces, and applies the chapter's causality gate. It does not download
market data or reconstruct historical exchange rules.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from quant.common.models import Market
from quant.common.rules import RulesRepository
from quant.data.lifecycle import (
    LifecycleRow,
    LifecycleStore,
    SnapshotSymbol,
    SymbolStatus,
)
from quant.data.universe import point_in_time_universe

FIXTURE = Path(__file__).with_name("ch14-pit-rules-cases.json")
EXPECTED_FIXTURE_SHA256 = (
    "271c20be5d4f4455dfbf6ab8b783d28d3aec17eef8ae4edd1ad4848f1b270401"
)


def lifecycle_row(market: Market, raw: dict[str, object]) -> LifecycleRow:
    return LifecycleRow(
        market=market.value,
        symbol=str(raw["symbol"]),
        onboard_ts=int(raw["onboard_ts"]),
        settle_ts=int(raw["settle_ts"]) if raw["settle_ts"] is not None else None,
        delist_announce_ts=(
            int(raw["delist_announce_ts"])
            if raw["delist_announce_ts"] is not None
            else None
        ),
        delist_ts=int(raw["delist_ts"]) if raw["delist_ts"] is not None else None,
        status=SymbolStatus(str(raw["status"])),
        raw_status=str(raw["raw_status"]),
        first_seen_ts=int(raw["first_seen_ts"]),
        last_seen_ts=int(raw["last_seen_ts"]),
        source=str(raw["source"]),
    )


def write_rules(root: Path, rows: list[dict[str, object]]) -> RulesRepository:
    directory = root / Market.FUTURES.value
    directory.mkdir(parents=True)
    for row in rows:
        path = directory / f"{row['symbol']}.json"
        path.write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
    return RulesRepository(root)


def rule_snapshot_times(
    repository: RulesRepository, symbols: frozenset[str]
) -> set[int]:
    return {
        repository.load_symbol_rules(Market.FUTURES, symbol).snapshot_ts
        for symbol in symbols
    }


def main() -> None:
    fixture_bytes = FIXTURE.read_bytes()
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    assert fixture_sha256 == EXPECTED_FIXTURE_SHA256
    fixture = json.loads(fixture_bytes)
    market = Market(str(fixture["market"]))
    research_as_of = int(fixture["research_as_of"])
    current_as_of = int(fixture["current_as_of"])
    expected_rules_snapshot_ts = int(fixture["expected_rules_snapshot_ts"])

    with tempfile.TemporaryDirectory(prefix="emmet-ch14-") as tmp:
        base = Path(tmp)
        store = LifecycleStore(base / "lifecycle.sqlite")
        store.import_rows(
            [lifecycle_row(market, raw) for raw in fixture["lifecycle_rows"]]
        )

        historical = point_in_time_universe(store, market, research_as_of)
        current = point_in_time_universe(store, market, current_as_of)
        assert historical.symbols == frozenset({"BTCUSDT", "OLDUSDT"})
        assert historical.earliest_evidence_ts == 1546300800000
        assert historical.survivorship_warning is False
        assert current.symbols == frozenset({"BTCUSDT", "NEWUSDT"})
        print("pit-2021=BTCUSDT,OLDUSDT,warning-false,PASS")
        print("current-2024=BTCUSDT,NEWUSDT")
        print("current-survivors-for-2021=MISMATCH,FAIL-CLOSED")

        insufficient = fixture["insufficient_evidence"]
        warning_store = LifecycleStore(base / "warning.sqlite")
        warning_store.upsert_from_snapshot(
            market,
            int(insufficient["first_snapshot_ts"]),
            [
                SnapshotSymbol(
                    symbol=str(insufficient["symbol"]),
                    status="TRADING",
                    onboard_date=int(insufficient["onboard_ts"]),
                    quote_asset="USDT",
                )
            ],
        )
        warning = point_in_time_universe(
            warning_store, market, int(insufficient["query_as_of"])
        )
        assert warning.symbols == frozenset({"BTCUSDT"})
        assert warning.earliest_evidence_ts == 1767225600000
        assert warning.survivorship_warning is True
        print("pre-evidence-universe=warning-true,FAIL-CLOSED")

        historical_repository = write_rules(
            base / "rules-historical", fixture["rule_snapshots"]["historical"]
        )
        historical_times = rule_snapshot_times(
            historical_repository, historical.symbols
        )
        assert historical_times == {expected_rules_snapshot_ts}
        assert expected_rules_snapshot_ts <= research_as_of
        print(f"rules-2021=snapshot-{expected_rules_snapshot_ts},PASS")

        future_repository = write_rules(
            base / "rules-future", fixture["rule_snapshots"]["future"]
        )
        future_times = rule_snapshot_times(future_repository, historical.symbols)
        assert future_times == {1704067200000}
        assert min(future_times) > research_as_of
        print("future-rules-for-2021=snapshot-1704067200000,FAIL-CLOSED")

    print(f"fixture-sha256={fixture_sha256}")
    print("chapter-14-pit-rules-oracle=PASS")


if __name__ == "__main__":
    main()
