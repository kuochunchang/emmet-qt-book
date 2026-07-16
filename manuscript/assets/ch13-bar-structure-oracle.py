"""Chapter 13 fixed-offline structural-quality oracle.

This helper only constructs frozen teaching partitions and calls released product
interfaces. It does not repair, sort, or otherwise normalize the source rows.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import pyarrow as pa

from quant.common.datasource import KlineFeed
from quant.common.errors import DataIntegrityError
from quant.common.models import Market
from quant.data.layout import DataPaths
from quant.data.schema import KLINES_SCHEMA
from quant.data.store import write_partition_file

FIXTURE = Path(__file__).with_name("ch13-bar-structure-cases.json")
EXPECTED_FIXTURE_SHA256 = (
    "0a03e8e8c989e280c3db0f7c55893d063801634bb8509e8f3a285f445ada41da"
)


def table(rows: list[dict[str, float | int]]) -> pa.Table:
    return pa.table(
        {
            "open_time": pa.array([row["open_time"] for row in rows], pa.int64()),
            "open": pa.array([row["open"] for row in rows], pa.float64()),
            "high": pa.array([row["high"] for row in rows], pa.float64()),
            "low": pa.array([row["low"] for row in rows], pa.float64()),
            "close": pa.array([row["close"] for row in rows], pa.float64()),
            "volume": pa.array([row["volume"] for row in rows], pa.float64()),
            "quote_volume": pa.array(
                [row["quote_volume"] for row in rows], pa.float64()
            ),
        },
        schema=KLINES_SCHEMA,
    )


def status(root: Path, symbol: str, interval: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [
            "quant-data",
            "status",
            "--root",
            str(root),
            "--market",
            "um",
            "--symbol",
            symbol,
            "--kind",
            "klines",
            "--interval",
            interval,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stderr:
        raise AssertionError(completed.stderr)
    return completed.returncode, json.loads(completed.stdout)


def main() -> None:
    fixture_bytes = FIXTURE.read_bytes()
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    assert fixture_sha256 == EXPECTED_FIXTURE_SHA256
    fixture = json.loads(fixture_bytes)
    symbol = fixture["symbol"]
    interval = fixture["interval"]
    cases = fixture["cases"]

    with tempfile.TemporaryDirectory(prefix="emmet-ch13-") as tmp:
        base = Path(tmp)
        results: dict[str, tuple[int, dict[str, object], pa.Table]] = {}
        for name, rows in cases.items():
            case_root = base / name
            directory = DataPaths(case_root).kline_dir(
                Market.FUTURES, symbol, interval
            )
            directory.mkdir(parents=True)
            case_table = table(rows)
            write_partition_file(
                directory, "fixed.parquet", case_table, 1704153600000
            )
            code, report = status(case_root, symbol, interval)
            results[name] = (code, report, case_table)

        clean_code, clean, _ = results["clean"]
        assert clean_code == 0 and clean["ok"] is True
        assert clean["total_rows"] == 3
        print("clean=status-0,rows-3,PASS")

        gap_code, gap, _ = results["gap"]
        assert gap_code == 2 and gap["ok"] is False
        assert gap["gaps"] == [
            {
                "start_ms": 1704070800000,
                "end_ms": 1704074400000,
                "missing": 1,
            }
        ]
        print("gap=status-2,missing-1,FAIL-CLOSED")

        duplicate_code, duplicate, _ = results["duplicate"]
        assert duplicate_code == 2 and duplicate["ok"] is False
        assert duplicate["duplicates"] == [1704070800000]
        print("duplicate=status-2,ts-1704070800000,FAIL-CLOSED")

        invalid_code, invalid, _ = results["invalid_ohlc"]
        assert invalid_code == 2 and invalid["ok"] is False
        assert invalid["ohlc_violations"] == [1704070800000]
        print("invalid-ohlc=status-2,ts-1704070800000,FAIL-CLOSED")

        order_code, order, order_table = results["out_of_order"]
        assert order_code == 0 and order["order_violations"] == []
        print("out-of-order=status-0,INSUFFICIENT")
        try:
            list(
                KlineFeed(
                    [order_table],
                    market=Market.FUTURES,
                    symbol=symbol,
                    interval=interval,
                )
            )
        except DataIntegrityError as error:
            assert "open_time 必須嚴格遞增" in str(error)
        else:
            raise AssertionError("out-of-order rows were accepted by KlineFeed")
        print("out-of-order-feed=FAIL-CLOSED")

    print(f"fixture-sha256={fixture_sha256}")
    print("chapter-13-structure-oracle=PASS")


if __name__ == "__main__":
    main()
