"""
初始化模拟电商数据库
跑一次生成 data/mock_db.sqlite,后续 Agent 都查这个库
"""
import sqlite3
from pathlib import Path
import random
from datetime import datetime, timedelta

# 项目根目录 / data / mock_db.sqlite
DB_PATH = Path(__file__).parent.parent.parent / "data" / "mock_db.sqlite"


def init_database(seed: int = 42, as_of_date: datetime | None = None):
    random.seed(seed)
    as_of_date = as_of_date or datetime.now()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 删旧库,保证每次跑结果一致
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # 建 4 张表
    cursor.executescript("""
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        region TEXT NOT NULL,
        register_date DATE NOT NULL
    );

    CREATE TABLE products (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL
    );

    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL,
        order_date DATE NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    CREATE TABLE order_items (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    CREATE INDEX idx_orders_customer_date ON orders(customer_id, order_date);
    CREATE INDEX idx_orders_date_status ON orders(order_date, status);
    CREATE INDEX idx_order_items_order ON order_items(order_id);
    CREATE INDEX idx_order_items_product ON order_items(product_id);
    """)

    # 100 个客户
    regions = ["华东", "华南", "华北", "西南", "西北"]
    customers = []
    for i in range(1, 101):
        customers.append((
            i,
            f"客户_{i:03d}",
            random.choice(regions),
            (as_of_date - timedelta(days=random.randint(30, 1095))).strftime("%Y-%m-%d")
        ))
    cursor.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)

    # 50 个产品
    categories = ["电子产品", "服装", "食品", "家居", "图书"]
    products = []
    for i in range(1, 51):
        products.append((
            i,
            f"产品_{i:03d}",
            random.choice(categories),
            round(random.uniform(10, 5000), 2)
        ))
    cursor.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    # 500 个订单 + 订单明细
    statuses = ["completed", "pending", "cancelled"]
    orders = []
    order_items = []
    item_id = 1
    for order_id in range(1, 501):
        customer_id = random.randint(1, 100)
        order_date = (as_of_date - timedelta(days=random.randint(1, 730))).strftime("%Y-%m-%d")
        status = random.choices(statuses, weights=[0.8, 0.15, 0.05])[0]
        orders.append((order_id, customer_id, order_date, status))

        # 每个订单 1-5 件商品
        for _ in range(random.randint(1, 5)):
            order_items.append((
                item_id,
                order_id,
                random.randint(1, 50),
                random.randint(1, 5)
            ))
            item_id += 1

    cursor.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    cursor.executemany("INSERT INTO order_items VALUES (?,?,?,?)", order_items)

    conn.commit()

    print(f"数据库已创建: {DB_PATH}")
    print(f"  customers:   {cursor.execute('SELECT COUNT(*) FROM customers').fetchone()[0]} 条")
    print(f"  products:    {cursor.execute('SELECT COUNT(*) FROM products').fetchone()[0]} 条")
    print(f"  orders:      {cursor.execute('SELECT COUNT(*) FROM orders').fetchone()[0]} 条")
    print(f"  order_items: {cursor.execute('SELECT COUNT(*) FROM order_items').fetchone()[0]} 条")

    conn.close()


if __name__ == "__main__":
    init_database()
