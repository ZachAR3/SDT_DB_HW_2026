import csv
import io
import json
import os
import re
import sys


class TypedPageDBMS:
    def __init__(self, root="hw6_star_data", page_size=4):
        self.root = root
        self.page_size = page_size
        self.schema_path = os.path.join(root, "schema.json")
        os.makedirs(root, exist_ok=True)
        if os.path.exists(self.schema_path):
            with open(self.schema_path, "r", encoding="utf-8") as f:
                self.schema = json.load(f)
        else:
            self.schema = {"tables": {}, "page_size": page_size}
            self._save_schema()

    def create_table(self, name, columns):
        if name in self.schema["tables"]:
            raise ValueError(f"Table '{name}' already exists")
        self.schema["tables"][name] = {"columns": columns, "pages": []}
        os.makedirs(self._table_dir(name), exist_ok=True)
        self._save_schema()

    def insert(self, name, values):
        table = self._table(name)
        columns = table["columns"]
        if len(values) != len(columns):
            raise ValueError("Column count does not match value count")
        row = {}
        for column, raw_value in zip(columns, values):
            row[column["name"]] = cast_value(raw_value, column["type"])
        pages = table["pages"]
        if not pages:
            pages.append("page_0.json")
            self._write_page(name, pages[0], [])
        page_name = pages[-1]
        page = self._read_page(name, page_name)
        if len(page) >= self.schema["page_size"]:
            page_name = f"page_{len(pages)}.json"
            pages.append(page_name)
            page = []
        page.append(row)
        self._write_page(name, page_name, page)
        self._save_schema()

    def select(self, name, columns=None, where=None):
        table = self._table(name)
        selected = [column["name"] for column in table["columns"]] if columns is None else columns
        self._check_columns(table, selected)
        if where:
            self._check_columns(table, [where[0]])
            where = (where[0], cast_value(where[1], self._column_type(table, where[0])))
        rows = []
        for row in self._rows(name):
            if where and row.get(where[0]) != where[1]:
                continue
            rows.append({column: row[column] for column in selected})
        return selected, rows

    def update(self, name, updates, where=None):
        table = self._table(name)
        self._check_columns(table, updates.keys())
        if where:
            self._check_columns(table, [where[0]])
            where = (where[0], cast_value(where[1], self._column_type(table, where[0])))
        typed_updates = {
            column: cast_value(value, self._column_type(table, column))
            for column, value in updates.items()
        }
        count = 0
        for page_name in table["pages"]:
            page = self._read_page(name, page_name)
            changed = False
            for row in page:
                if where and row.get(where[0]) != where[1]:
                    continue
                row.update(typed_updates)
                count += 1
                changed = True
            if changed:
                self._write_page(name, page_name, page)
        return count

    def delete(self, name, where=None):
        table = self._table(name)
        if where:
            self._check_columns(table, [where[0]])
            where = (where[0], cast_value(where[1], self._column_type(table, where[0])))
        count = 0
        for page_name in table["pages"]:
            page = self._read_page(name, page_name)
            kept = []
            for row in page:
                if where is None or row.get(where[0]) == where[1]:
                    count += 1
                else:
                    kept.append(row)
            self._write_page(name, page_name, kept)
        return count

    def _table(self, name):
        if name not in self.schema["tables"]:
            raise ValueError(f"Table '{name}' does not exist")
        return self.schema["tables"][name]

    def _table_dir(self, name):
        return os.path.join(self.root, name)

    def _page_path(self, table_name, page_name):
        return os.path.join(self._table_dir(table_name), page_name)

    def _read_page(self, table_name, page_name):
        path = self._page_path(table_name, page_name)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_page(self, table_name, page_name, rows):
        path = self._page_path(table_name, page_name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)

    def _save_schema(self):
        with open(self.schema_path, "w", encoding="utf-8") as f:
            json.dump(self.schema, f, indent=2)

    def _rows(self, name):
        table = self._table(name)
        for page_name in table["pages"]:
            for row in self._read_page(name, page_name):
                yield row

    def _check_columns(self, table, names):
        columns = {column["name"] for column in table["columns"]}
        missing = [name for name in names if name not in columns]
        if missing:
            raise ValueError(f"Unknown columns: {', '.join(missing)}")

    def _column_type(self, table, name):
        for column in table["columns"]:
            if column["name"] == name:
                return column["type"]
        raise ValueError(f"Unknown column '{name}'")


def cast_value(value, value_type):
    value_type = value_type.lower()
    if value_type in {"str", "string", "text"}:
        return strip_quotes(value)
    if value_type in {"int", "integer"}:
        return int(strip_quotes(value))
    if value_type in {"float", "double", "real"}:
        return float(strip_quotes(value))
    if value_type in {"bool", "boolean"}:
        raw = strip_quotes(value).lower()
        if raw in {"true", "1"}:
            return True
        if raw in {"false", "0"}:
            return False
        raise ValueError(f"Invalid boolean value '{value}'")
    raise ValueError(f"Unsupported type '{value_type}'")


def strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_csv_list(text):
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    return next(reader, [])


def parse_where(text):
    match = re.fullmatch(r"\s*(\w+)\s*=\s*(.+)\s*", text)
    if not match:
        raise ValueError("Invalid WHERE clause")
    return match.group(1), match.group(2).strip()


def parse_set(text):
    updates = {}
    for part in parse_csv_list(text):
        if "=" not in part:
            raise ValueError("Invalid SET clause")
        column, value = part.split("=", 1)
        updates[column.strip()] = value.strip()
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
        columns = []
        for definition in create_match.group(2).split(","):
            parts = definition.strip().split()
            if len(parts) != 2:
                raise ValueError("Each column must have a name and a type")
            columns.append({"name": parts[0], "type": parts[1]})
        db.create_table(name, columns)
        return "OK"

    insert_match = re.fullmatch(r"INSERT\s+INTO\s+(\w+)\s+VALUES\s*\((.*)\)", command, re.IGNORECASE)
    if insert_match:
        db.insert(insert_match.group(1), parse_csv_list(insert_match.group(2)))
        return "OK"

    select_match = re.fullmatch(
        r"SELECT\s+(\*|[\w\s,]+)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?",
        command,
        re.IGNORECASE,
    )
    if select_match:
        selected = select_match.group(1).strip()
        where = parse_where(select_match.group(3)) if select_match.group(3) else None
        columns = None if selected == "*" else [column.strip() for column in selected.split(",")]
        result_columns, rows = db.select(select_match.group(2), columns, where)
        return format_rows(result_columns, rows)

    update_match = re.fullmatch(
        r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?",
        command,
        re.IGNORECASE,
    )
    if update_match:
        updates = parse_set(update_match.group(2))
        where = parse_where(update_match.group(3)) if update_match.group(3) else None
        count = db.update(update_match.group(1), updates, where)
        return f"{count} row(s) updated"

    delete_match = re.fullmatch(r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?", command, re.IGNORECASE)
    if delete_match:
        where = parse_where(delete_match.group(2)) if delete_match.group(2) else None
        count = db.delete(delete_match.group(1), where)
        return f"{count} row(s) deleted"

    raise ValueError("Unsupported command")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "hw6_star_data"
    db = TypedPageDBMS(root)
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

    print("Typed page DBMS. Type EXIT to quit.")
    while True:
        try:
            command = input("db*> ").strip()
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
