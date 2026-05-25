import json
import os
import shlex
import sys


class KeyValueStore:
    def __init__(self, log_path="hw7.log"):
        self.log_path = log_path
        self.state = {}
        if os.path.exists(log_path):
            self._replay()

    def set(self, key, value):
        self._append({"op": "SET", "key": key, "value": value})
        self.state[key] = value

    def delete(self, key):
        self._append({"op": "DELETE", "key": key})
        self.state.pop(key, None)

    def get(self, key):
        return self.state.get(key)

    def items(self):
        return dict(sorted(self.state.items()))

    def _append(self, record):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _replay(self):
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record["op"] == "SET":
                    self.state[record["key"]] = record["value"]
                elif record["op"] == "DELETE":
                    self.state.pop(record["key"], None)


def execute(store, command):
    parts = shlex.split(command)
    if not parts:
        return None
    op = parts[0].upper()

    if op == "SET" and len(parts) >= 3:
        store.set(parts[1], " ".join(parts[2:]))
        return "OK"
    if op == "GET" and len(parts) == 2:
        value = store.get(parts[1])
        return "NULL" if value is None else value
    if op == "DELETE" and len(parts) == 2:
        store.delete(parts[1])
        return "OK"
    if op == "SHOW":
        items = store.items()
        if not items:
            return "(empty)"
        return "\n".join(f"{key} = {value}" for key, value in items.items())

    raise ValueError("Supported commands: SET, GET, DELETE, SHOW")


def main():
    log_path = sys.argv[1] if len(sys.argv) > 1 else "hw7.log"
    store = KeyValueStore(log_path)
    if not sys.stdin.isatty():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            result = execute(store, line)
            if result is not None:
                print(result)
        return

    print("Append-only key-value store. Type EXIT to quit.")
    while True:
        try:
            command = input("kv> ").strip()
        except EOFError:
            break
        if command.upper() in {"EXIT", "QUIT"}:
            break
        try:
            result = execute(store, command)
            if result is not None:
                print(result)
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
