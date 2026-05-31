import sqlite3

from app.config import DB_PATH


def main() -> None:
    if not DB_PATH.exists():
        print(f"Banco de dados não encontrado: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, bank, value_date, description, amount, category
            FROM transactions
            ORDER BY id
            LIMIT 200
            """
        ).fetchall()

    if not rows:
        print("Nenhuma transação encontrada")
        return

    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
