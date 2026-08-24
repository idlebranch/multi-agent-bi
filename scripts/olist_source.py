"""Shared parsing helpers for loading the Olist source archive into PostgreSQL."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "raw" / "olist.zip"
SEMANTIC_MODEL = PROJECT_ROOT / "data" / "olist_semantic_model.json"

FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "translations": "product_category_name_translation.csv",
}


def text_value(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def int_value(value: str | None) -> int | None:
    value = text_value(value)
    return int(value) if value is not None else None


def float_value(value: str | None) -> float | None:
    value = text_value(value)
    return float(value) if value is not None else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_members(bundle: zipfile.ZipFile) -> dict[str, str]:
    by_basename: dict[str, list[str]] = defaultdict(list)
    for member in bundle.namelist():
        if not member.endswith("/"):
            by_basename[Path(member).name].append(member)

    resolved: dict[str, str] = {}
    for key, expected in FILES.items():
        matches = by_basename.get(expected, [])
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one {expected!r} in archive, found {len(matches)}"
            )
        resolved[key] = matches[0]
    return resolved


def csv_rows(bundle: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    with bundle.open(member, "r") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            yield from csv.DictReader(text)


def write_manifest(manifest: Path, payload: dict[str, Any]) -> None:
    manifest = manifest.resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(manifest.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, manifest)
