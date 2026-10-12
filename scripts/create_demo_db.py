"""
scripts/create_demo_db.py
--------------------------
Tạo demo SQLite database với schema kiểu Spider.
Chạy 1 lần để khởi tạo:

    python scripts/create_demo_db.py

Output: demo_db/sales_demo.db
"""
import os
import sqlite3
import random

# Tự động xác định path chuẩn (chạy từ root project)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DB_DIR = os.path.join(PROJECT_ROOT, "demo_db")
DB_PATH = os.path.join(DB_DIR, "sales_demo.db")


def create_db():
    os.makedirs(DB_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Schema
    cur.executescript("""
    DROP TABLE IF EXISTS orders;
    DROP TABLE IF EXISTS products;
    DROP TABLE IF EXISTS customers;
    DROP TABLE IF EXISTS regions;

    CREATE TABLE regions (
        region_id   INTEGER PRIMARY KEY,
        region_name TEXT NOT NULL
    );

    CREATE TABLE customers (
        customer_id   INTEGER PRIMARY KEY,
        customer_name TEXT NOT NULL,
        email         TEXT,
        region_id     INTEGER REFERENCES regions(region_id)
    );

    CREATE TABLE products (
        product_id   INTEGER PRIMARY KEY,
        product_name TEXT NOT NULL,
        category     TEXT NOT NULL,
        unit_price   REAL NOT NULL
    );

    CREATE TABLE orders (
        order_id    INTEGER PRIMARY KEY,
        customer_id INTEGER REFERENCES customers(customer_id),
        product_id  INTEGER REFERENCES products(product_id),
        quantity    INTEGER NOT NULL,
        order_date  TEXT NOT NULL,
        revenue     REAL NOT NULL
    );
    """)

    # Regions
    cur.executemany("INSERT INTO regions VALUES (?,?)", [
        (1, "North"), (2, "South"), (3, "East"), (4, "West"),
    ])

    # Customers
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?)", [
        (1, "Alice Nguyen",   "alice@mail.com",   1),
        (2, "Bob Tran",       "bob@mail.com",     2),
        (3, "Carol Le",       "carol@mail.com",   3),
        (4, "David Pham",     "david@mail.com",   4),
        (5, "Emma Vo",        "emma@mail.com",    1),
        (6, "Frank Do",       "frank@mail.com",   2),
        (7, "Grace Hoang",    "grace@mail.com",   3),
        (8, "Henry Bui",      "henry@mail.com",   4),
    ])

    # Products
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", [
        (1, "Laptop Pro",    "Electronics", 1200.0),
        (2, "Wireless Mouse","Electronics",   35.0),
        (3, "Office Chair",  "Furniture",    350.0),
        (4, "Standing Desk", "Furniture",    650.0),
        (5, "Notebook A4",   "Stationery",     5.0),
        (6, "Pen Set",       "Stationery",    12.0),
        (7, "Monitor 27 inch", "Electronics", 450.0),
        (8, "Headphones",    "Electronics",  120.0),
    ])

    # Generate orders
    random.seed(42)
    orders = []
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    prices = [1200, 35, 350, 650, 5, 12, 450, 120]

    for oid in range(1, 201):
        cid = random.randint(1, 8)
        pid = random.randint(1, 8)
        qty = random.randint(1, 10)
        month = random.choice(months)
        day = random.randint(1, 28)
        date = f"{month}-{day:02d}"
        revenue = round(qty * prices[pid - 1], 2)
        orders.append((oid, cid, pid, qty, date, revenue))

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)

    conn.commit()
    conn.close()

    print(f"✅ Database created: {DB_PATH}")
    print(f"   Tables: regions (4), customers (8), products (8), orders (200)")


if __name__ == "__main__":
    create_db()
