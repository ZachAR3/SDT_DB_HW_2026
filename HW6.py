import csv
import io
import json
import os
import re
import sys


class SimpleDBMS:
    def __init__(self, path="hw6.db"):
        self.path = path
        self.data = {"tables": {}}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def create_table(self, name, columns):
        if name in self.data["tables"]:
            raise ValueError(f"Table '{name}' already exists")
        self.data["tables"][name] = {"columns": columns, "rows": []}
        self.save()

    def insert(self, name, values):
        table = self._table(name)
        if len(values) != len(table["columns"]):
            raise ValueError("Column count does not match value count")
        row = dict(zip(table["columns"], values))
        table["rows"].append(row)
        self.save()

    def select(self, name, columns=None, where=None):
        table = self._table(name)
        columns = table["columns"] if columns is None else columns
        self._check_columns(table, columns)
        if where:
            self._check_columns(table, [where[0]])
        rows = []
        for row in table["rows"]:
            if where and row.get(where[0]) != where[1]:
                continue
            rows.append({column: row[column] for column in columns})
        return columns, rows

    def update(self, name, updates, where=None):
        table = self._table(name)
        self._check_columns(table, updates.keys())
        if where:
            self._check_columns(table, [where[0]])
        count = 0
        for row in table["rows"]:
            if where and row.get(where[0]) != where[1]:
                continue
            row.update(updates)
            count += 1
        self.save()
        return count

    def delete(self, name, where=None):
        table = self._table(name)
        if where:
            self._check_columns(table, [where[0]])
        kept = []
        count = 0
        for row in table["rows"]:
            if where and row.get(where[0]) == where[1]:
                count += 1
                continue
            if where is None:
                count += 1
                continue
            kept.append(row)
        table["rows"] = [] if where is None else kept
        self.save()
        return count

    def _table(self, name):
        if name not in self.data["tables"]:
            raise ValueError(f"Table '{name}' does not exist")
        return self.data["tables"][name]

    def _check_columns(self, table, columns):
        missing = [column for column in columns if column not in table["columns"]]
        if missing:
            raise ValueError(f"Unknown columns: {', '.join(missing)}")


def parse_csv_list(text):
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    values = next(reader, [])
    return [strip_quotes(value.strip()) for value in values]


def strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_where(text):
    match = re.fullmatch(r"\s*(\w+)\s*=\s*(.+)\s*", text)
    if not match:
        raise ValueError("Invalid WHERE clause")
    return match.group(1), strip_quotes(match.group(2).strip())


def parse_set(text):
    updates = {}
    for part in parse_csv_list(text):
        if "=" not in part:
            raise ValueError("Invalid SET clause")
        column, value = part.split("=", 1)
        updates[column.strip()] = strip_quotes(value.strip())
    return updates


def format_rows(columns, rows):
    widths = [len(column) for column in columns]
    for row in rows:
        for i, column in enumerate(columns):
            widths[i] = max(widths[i], len(str(row[column])))
    header = " | ".join(column.ljust(widths[i]) for i, column in enumerate(columns))
    line = "-+-".join("-" * widths[i] for i in range(len(columns)))
    body = [" | ".join(str(row[column]).ljust(widths[i]) for i, column in enumerate(columns)) for row in rows]
    return "\n".join([header, line] + body) if rows else "\n".join([header, line])


def execute(db, command):
    command = command.strip().rstrip(";")
    if not command:
        return None

    create_match = re.fullmatch(r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)", command, re.IGNORECASE)
    if create_match:
        name = create_match.group(1)
        columns = [column.strip() for column in create_match.group(2).split(",") if column.strip()]
        db.create_table(name, columns)
        return "OK"

    insert_match = re.fullmatch(r"INSERT\s+INTO\s+(\w+)\s+VALUES\s*\((.*)\)", command, re.IGNORECASE)
    if insert_match:
        name = insert_match.group(1)
        values = parse_csv_list(insert_match.group(2))
        db.insert(name, values)
        return "OK"

    select_match = re.fullmatch(
        r"SELECT\s+(\*|[\w\s,]+)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?",
        command,
        re.IGNORECASE,
    )
    if select_match:
        selected = select_match.group(1).strip()
        name = select_match.group(2)
        where = parse_where(select_match.group(3)) if select_match.group(3) else None
        columns = None if selected == "*" else [column.strip() for column in selected.split(",")]
        result_columns, rows = db.select(name, columns, where)
        return format_rows(result_columns, rows)

    update_match = re.fullmatch(
        r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?",
        command,
        re.IGNORECASE,
    )
    if update_match:
        name = update_match.group(1)
        updates = parse_set(update_match.group(2))
        where = parse_where(update_match.group(3)) if update_match.group(3) else None
        count = db.update(name, updates, where)
        return f"{count} row(s) updated"

    delete_match = re.fullmatch(r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?", command, re.IGNORECASE)
    if delete_match:
        name = delete_match.group(1)
        where = parse_where(delete_match.group(2)) if delete_match.group(2) else None
        count = db.delete(name, where)
        return f"{count} row(s) deleted"

    raise ValueError("Unsupported command")


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "hw6.db"
    db = SimpleDBMS(db_path)
    if not sys.stdin.isatty():
        script = sys.stdin.read().split(";")
        for piece in script:
            command = piece.strip()
            if not command:
                continue
            result = execute(db, command)
            if result is not None:
                print(result)
        return

    print("Simple DBMS. Type EXIT to quit.")
    while True:
        try:
            command = input("db> ").strip()
        except EOFError:
            break
        if command.upper() in {"EXIT", "QUIT"}:
            break
        try:
            result = execute(db, command)
            if result is not None:
                print(result)
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
