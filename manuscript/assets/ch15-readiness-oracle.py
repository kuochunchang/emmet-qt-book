"""Chapter 15 fixed-offline dataset manifest and readiness oracle.

The helper creates deterministic teaching partitions, calls the released
``quant-data status`` entry point, and evaluates the returned evidence against
the committed dataset contract.  It does not download data or replace product
validation logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pyarrow as pa

from quant.common.models import Market
from quant.data.layout import DataPaths
from quant.data.schema import KLINES_SCHEMA
from quant.data.store import write_partition_file

CONTRACT_PATH = Path(__file__).with_name("ch15-dataset-contract.json")
EXPECTED_CONTRACT_SHA256 = (
    "8ca6240326c0dd9005e7bee21523ffc92db8e164198212c62a370436ad9bbb55"
)
COMPANION_BASELINE = {
    "repository": "emmet-qt-bt1",
    "tag": "v0.3.0",
    "commit": "c999965e5cc923281541409cda9502beb93b8a60",
    "tag_at_commit": (
        "v0.3.0@c999965e5cc923281541409cda9502beb93b8a60"
    ),
}


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def json_file_sha256(value: object) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def disk_parquet_files(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256_file(path)
        for path in sorted(directory.glob("*.parquet"))
        if path.is_file()
    }


def table(day_ms: int, generation: dict[str, Any]) -> pa.Table:
    count = generation["rows_per_day"]
    step = generation["step_ms"]
    values = generation["ohlcv"]
    return pa.table(
        {
            "open_time": pa.array(
                [day_ms + index * step for index in range(count)], pa.int64()
            ),
            "open": pa.array([values["open"]] * count, pa.float64()),
            "high": pa.array([values["high"]] * count, pa.float64()),
            "low": pa.array([values["low"]] * count, pa.float64()),
            "close": pa.array([values["close"]] * count, pa.float64()),
            "volume": pa.array([values["volume"]] * count, pa.float64()),
            "quote_volume": pa.array([values["quote_volume"]] * count, pa.float64()),
        },
        schema=KLINES_SCHEMA,
    )


def status(root: Path, partition: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [
            "quant-data",
            "status",
            "--root",
            str(root),
            "--market",
            partition["market"],
            "--symbol",
            partition["symbol"],
            "--kind",
            partition["kind"],
            "--interval",
            partition["interval"],
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stderr:
        raise AssertionError(completed.stderr)
    return completed.returncode, json.loads(completed.stdout)


def build_manifest(
    contract: dict[str, Any],
    observed: dict[str, Any],
    contract_sha256: str,
    product_status_sha256: str,
) -> dict[str, Any]:
    return {
        "manifest_schema_version": contract["manifest_schema_version"],
        "dataset_id": contract["dataset_id"],
        "source": contract["source"],
        "partition": contract["partition"],
        "coverage": observed["coverage"],
        "total_rows": observed["total_rows"],
        "last_refresh_ts": observed["last_refresh_ts"],
        "data_schema": contract["data_schema"],
        "rules_snapshot": {
            "version": contract["rules_snapshot"]["version"],
            "snapshot_ts": contract["rules_snapshot"]["snapshot_ts"],
            "sha256": contract["rules_snapshot"]["sha256"],
        },
        "files": observed["files"],
        "generation": contract["generation"],
        "companion_baseline": COMPANION_BASELINE,
        "contract": {
            "path": "dataset-contract.json",
            "sha256": contract_sha256,
        },
        "product_status": {
            "path": "product-status.json",
            "sha256": product_status_sha256,
        },
    }


def evidence_inputs(
    *,
    contract_path: str,
    contract_sha256: str,
    manifest: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    return {
        "companion_baseline": COMPANION_BASELINE,
        "contract": {"path": contract_path, "sha256": contract_sha256},
        "dataset_manifest": {
            "path": "dataset-manifest.json",
            "sha256": json_file_sha256(manifest),
        },
        "product_status": {
            "path": "product-status.json",
            "sha256": json_file_sha256(observed),
        },
    }


def assess(
    contract: dict[str, Any],
    observed: dict[str, Any],
    disk_files: dict[str, str],
    *,
    case: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    status_files = {item["filename"]: item["sha256"] for item in observed["files"]}
    checks = {
        "product_status_clean": observed["ok"] is True,
        "coverage_exact": observed["coverage"]
        == [[contract["coverage"]["start_ms"], contract["coverage"]["end_ms"]]],
        "required_rows_present": observed["total_rows"]
        == contract["coverage"]["expected_rows"],
        "file_set_exact": set(status_files)
        == set(disk_files)
        == set(contract["expected_files"]),
        "status_checksums_match_contract": status_files == contract["expected_files"],
        "disk_checksums_match_contract": disk_files == contract["expected_files"],
        "status_manifest_matches_disk": status_files == disk_files,
        "manifest_schema_supported": contract["manifest_schema_version"] == 1,
        "data_schema_supported": contract["data_schema"]
        == {"name": "quant.data.schema.KLINES_SCHEMA", "version": 1},
        "rules_snapshot_matches": canonical_sha256(
            contract["rules_snapshot"]["payload"]
        )
        == contract["rules_snapshot"]["sha256"],
        "fixed_offline_mode": contract["source"]["mode"] == "fixed-offline"
        and contract["source"]["network_used"] is False,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "readiness_schema_version": 2,
        "dataset_id": contract["dataset_id"],
        "case": case,
        "decision": "GO" if not failures else "NO-GO",
        "inputs": inputs,
        "evaluated_contract_canonical_sha256": canonical_sha256(contract),
        "checks": checks,
        "failures": failures,
    }


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json_bytes(value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    contract_path = args.contract.resolve()
    contract_bytes = contract_path.read_bytes()
    contract_sha256 = hashlib.sha256(contract_bytes).hexdigest()
    contract = json.loads(contract_bytes)
    committed_contract = contract_path == CONTRACT_PATH.resolve()
    if committed_contract:
        assert contract_sha256 == EXPECTED_CONTRACT_SHA256

    with tempfile.TemporaryDirectory(prefix="emmet-ch15-") as tmp:
        root = Path(tmp)
        partition = contract["partition"]
        directory = DataPaths(root).kline_dir(
            Market.FUTURES, partition["symbol"], partition["interval"]
        )
        directory.mkdir(parents=True)
        for day_ms in contract["generation"]["days_ms"]:
            day = str(day_ms)
            filename = {
                "1704067200000": "2024-01-01.parquet",
                "1704153600000": "2024-01-02.parquet",
            }[day]
            write_partition_file(
                directory,
                filename,
                table(day_ms, contract["generation"]),
                contract["generation"]["last_refresh_ts"],
            )

        status_code, observed = status(root, partition)
        assert status_code == 0 and observed["ok"] is True
        disk_files = disk_parquet_files(directory)
        manifest = build_manifest(
            contract,
            observed,
            contract_sha256,
            json_file_sha256(observed),
        )
        contract_inputs = evidence_inputs(
            contract_path="dataset-contract.json",
            contract_sha256=contract_sha256,
            manifest=manifest,
            observed=observed,
        )
        contract_report = assess(
            contract,
            observed,
            disk_files,
            case="provided-contract",
            inputs=contract_inputs,
        )

        checksum_contract = deepcopy(contract)
        checksum_contract["expected_files"]["2024-01-02.parquet"] = "0" * 64
        checksum_contract_sha256 = json_file_sha256(checksum_contract)
        checksum_failure = assess(
            checksum_contract,
            observed,
            disk_files,
            case="checksum-mismatch",
            inputs=evidence_inputs(
                contract_path="dataset-contract-checksum-mismatch.json",
                contract_sha256=checksum_contract_sha256,
                manifest=manifest,
                observed=observed,
            ),
        )

        coverage_contract = deepcopy(contract)
        coverage_contract["coverage"]["end_ms"] += 86_400_000
        coverage_contract["coverage"]["expected_rows"] += 24
        coverage_contract_sha256 = json_file_sha256(coverage_contract)
        coverage_failure = assess(
            coverage_contract,
            observed,
            disk_files,
            case="required-day-missing",
            inputs=evidence_inputs(
                contract_path="dataset-contract-required-day-missing.json",
                contract_sha256=coverage_contract_sha256,
                manifest=manifest,
                observed=observed,
            ),
        )

        unexpected_path = directory / "unexpected.parquet"
        shutil.copyfile(directory / "2024-01-01.parquet", unexpected_path)
        unexpected_files = disk_parquet_files(directory)
        unexpected_failure = assess(
            contract,
            observed,
            unexpected_files,
            case="unregistered-extra-file",
            inputs=contract_inputs,
        )

        if committed_contract:
            assert contract_report["decision"] == "GO"
            assert checksum_failure["failures"] == [
                "status_checksums_match_contract",
                "disk_checksums_match_contract",
            ]
            assert coverage_failure["failures"] == [
                "coverage_exact",
                "required_rows_present",
            ]
            assert unexpected_failure["failures"] == [
                "file_set_exact",
                "disk_checksums_match_contract",
                "status_manifest_matches_disk",
            ]

    output_dir = args.output_dir
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "dataset-contract.json").write_bytes(contract_bytes)
        write_json(output_dir / "product-status.json", observed)
        write_json(output_dir / "dataset-manifest.json", manifest)
        write_json(output_dir / "readiness-contract.json", contract_report)
        if committed_contract:
            write_json(
                output_dir / "dataset-contract-checksum-mismatch.json",
                checksum_contract,
            )
            write_json(
                output_dir / "dataset-contract-required-day-missing.json",
                coverage_contract,
            )
            write_json(
                output_dir / "readiness-checksum-mismatch.json", checksum_failure
            )
            write_json(
                output_dir / "readiness-required-day-missing.json", coverage_failure
            )
            write_json(
                output_dir / "readiness-unregistered-extra-file.json",
                unexpected_failure,
            )

    file_checksums = ",".join(
        f"{item['filename']}:{item['sha256']}" for item in manifest["files"]
    )
    print(f"dataset-id={contract['dataset_id']}")
    print(f"source-mode={contract['source']['mode']},network-false")
    print(
        "coverage="
        f"{contract['coverage']['start_ms']}..{contract['coverage']['end_ms']},"
        f"rows-{manifest['total_rows']}"
    )
    print(f"files={file_checksums}")
    print(f"companion-baseline={COMPANION_BASELINE['tag_at_commit']}")
    print(f"contract-sha256={contract_sha256}")
    print(f"product-status-sha256={json_file_sha256(observed)}")
    print(f"dataset-manifest-sha256={json_file_sha256(manifest)}")
    contract_failures = ",".join(contract_report["failures"])
    print(
        f"readiness-provided-contract={contract_report['decision']}"
        + (f",{contract_failures}" if contract_failures else "")
    )
    if committed_contract:
        print(
            "readiness-checksum-mismatch=NO-GO,"
            "status_checksums_match_contract,disk_checksums_match_contract"
        )
        print(
            "readiness-required-day-missing=NO-GO,"
            "coverage_exact,required_rows_present"
        )
        print(
            "readiness-unregistered-extra-file=NO-GO,file_set_exact,"
            "disk_checksums_match_contract,status_manifest_matches_disk"
        )
    if output_dir is not None:
        print(f"evidence-dir={output_dir}")
    print("chapter-15-readiness-oracle=PASS")


if __name__ == "__main__":
    main()
