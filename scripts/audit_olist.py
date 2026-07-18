"""Run deterministic data-quality and BI metric checks on the Olist warehouse."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_active_dataset_manifest  # noqa: E402
from src.tools.db_tools import get_db_path, readonly_connection  # noqa: E402


def scalar(conn: Any, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def audit() -> dict[str, Any]:
    manifest, _ = get_active_dataset_manifest()
    database = get_db_path()
    failures: list[str] = []
    checks: dict[str, Any] = {}

    with readonly_connection(database) as conn:
        checks["integrity_check"] = scalar(conn, "PRAGMA integrity_check")
        if checks["integrity_check"] != "ok":
            failures.append("SQLite integrity_check did not return ok")

        checks["foreign_key_violations"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        if checks["foreign_key_violations"]:
            failures.append("foreign-key violations found")

        table_counts: dict[str, int] = {}
        for table, expected in manifest.get("row_counts", {}).items():
            if table == "geolocation_source":
                continue
            actual = int(scalar(conn, f'SELECT COUNT(*) FROM "{table}"'))
            table_counts[table] = actual
            if actual != expected:
                failures.append(f"{table}: expected {expected:,} rows, found {actual:,}")
        checks["table_counts"] = table_counts

        checks["order_date_range"] = list(
            conn.execute(
                "SELECT date(MIN(purchase_timestamp)), date(MAX(purchase_timestamp)) FROM orders"
            ).fetchone()
        )
        if checks["order_date_range"][1] != manifest.get("as_of_date"):
            failures.append("maximum order date does not match manifest as_of_date")

        checks["invalid_nonnegative_amounts"] = int(
            scalar(
                conn,
                "SELECT "
                "(SELECT COUNT(*) FROM order_items WHERE price < 0 OR freight_value < 0) + "
                "(SELECT COUNT(*) FROM payments WHERE payment_value < 0)",
            )
        )
        if checks["invalid_nonnegative_amounts"]:
            failures.append("negative price, freight, or payment values found")

        checks["invalid_review_scores"] = int(
            scalar(conn, "SELECT COUNT(*) FROM reviews WHERE review_score NOT BETWEEN 1 AND 5")
        )
        if checks["invalid_review_scores"]:
            failures.append("review scores outside 1..5 found")

        expected_view_counts = {
            "order_financials": table_counts.get("orders"),
            "order_delivery_metrics": int(
                scalar(conn, "SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
            ),
            "product_sales": table_counts.get("order_items"),
            "category_sales_summary": int(
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM ("
                    " SELECT category_name FROM product_sales"
                    " WHERE order_status = 'delivered' GROUP BY category_name)",
                )
            ),
            "delivery_kpis": 1,
            "payment_type_summary": int(
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM ("
                    " SELECT COALESCE(payment_type, 'unknown') FROM payments"
                    " GROUP BY COALESCE(payment_type, 'unknown'))",
                )
            ),
            "customer_order_summary": int(
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM ("
                    " SELECT customer_unique_id FROM order_financials"
                    " GROUP BY customer_unique_id)",
                )
            ),
        }
        view_counts = {
            view: int(scalar(conn, f'SELECT COUNT(*) FROM "{view}"'))
            for view in expected_view_counts
        }
        checks["view_counts"] = view_counts
        for view, expected in expected_view_counts.items():
            if view_counts[view] != expected:
                failures.append(
                    f"{view} row count mismatch: expected {expected}, found {view_counts[view]}"
                )

        raw_item_value = float(scalar(conn, "SELECT ROUND(SUM(price), 2) FROM order_items"))
        view_item_value = float(scalar(conn, "SELECT ROUND(SUM(item_value), 2) FROM order_financials"))
        checks["item_value_reconciliation"] = {
            "raw": raw_item_value,
            "order_view": view_item_value,
            "difference": round(view_item_value - raw_item_value, 2),
        }
        if abs(view_item_value - raw_item_value) > 0.01:
            failures.append("order_financials item totals do not reconcile")

        raw_payment_value = float(scalar(conn, "SELECT ROUND(SUM(payment_value), 2) FROM payments"))
        view_payment_value = float(
            scalar(conn, "SELECT ROUND(SUM(payment_value), 2) FROM order_financials")
        )
        checks["payment_value_reconciliation"] = {
            "raw": raw_payment_value,
            "order_view": view_payment_value,
            "difference": round(view_payment_value - raw_payment_value, 2),
        }
        if abs(view_payment_value - raw_payment_value) > 0.01:
            failures.append("order_financials payment totals do not reconcile")

        semantic_reconciliation = {
            "category_delivered_gmv": float(
                scalar(conn, "SELECT ROUND(SUM(delivered_gmv), 2) FROM category_sales_summary")
            ),
            "payment_type_value": float(
                scalar(conn, "SELECT ROUND(SUM(payment_value), 2) FROM payment_type_summary")
            ),
            "on_time_delivery_pct": float(
                scalar(conn, "SELECT on_time_delivery_pct FROM delivery_kpis")
            ),
            "repeat_customers": int(
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM customer_order_summary WHERE order_count >= 2",
                )
            ),
        }
        checks["semantic_view_reconciliation"] = semantic_reconciliation
        raw_delivered_gmv = float(
            scalar(
                conn,
                "SELECT ROUND(SUM(oi.price), 2) FROM order_items oi "
                "JOIN orders o ON o.order_id = oi.order_id WHERE o.status = 'delivered'",
            )
        )
        if abs(semantic_reconciliation["category_delivered_gmv"] - raw_delivered_gmv) > 0.01:
            failures.append("category_sales_summary delivered GMV does not reconcile")
        if abs(semantic_reconciliation["payment_type_value"] - raw_payment_value) > 0.01:
            failures.append("payment_type_summary totals do not reconcile")

        metrics = dict(
            conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM orders) AS orders, "
                "(SELECT COUNT(*) FROM orders WHERE status = 'delivered') AS delivered_orders, "
                "(SELECT ROUND(SUM(oi.price), 2) FROM order_items oi "
                " JOIN orders o ON o.order_id = oi.order_id WHERE o.status = 'delivered') "
                " AS delivered_gmv, "
                "(SELECT ROUND(AVG(delivered_on_time) * 100, 4) "
                " FROM order_delivery_metrics) AS on_time_delivery_pct, "
                "(SELECT COUNT(*) FROM ("
                " SELECT c.customer_unique_id FROM orders o "
                " JOIN customers c ON c.customer_id = o.customer_id "
                " GROUP BY c.customer_unique_id HAVING COUNT(DISTINCT o.order_id) > 1"
                ")) AS repeat_customers"
            ).fetchone()
        )
        if abs(
            semantic_reconciliation["on_time_delivery_pct"]
            - float(metrics["on_time_delivery_pct"])
        ) > 0.0001:
            failures.append("delivery_kpis on-time percentage does not reconcile")
        if semantic_reconciliation["repeat_customers"] != int(metrics["repeat_customers"]):
            failures.append("customer_order_summary repeat-customer count does not reconcile")

    return {
        "success": not failures,
        "database": str(database),
        "database_bytes": database.stat().st_size,
        "checks": checks,
        "metrics": metrics,
        "failures": failures,
    }


def main() -> int:
    try:
        report = audit()
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
