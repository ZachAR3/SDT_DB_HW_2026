import socket
import subprocess
import tempfile
import time
import urllib.request
import os
from pathlib import Path

import duckdb


BASE_DIR = Path(__file__).resolve().parent
RESULTS_FILE = BASE_DIR / "results.txt"
PARQUET_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet"

POSTGRES_QUERY = """
WITH hourly AS (
  SELECT
    EXTRACT(HOUR FROM tpep_pickup_datetime) AS pickup_hour,
    pulocationid AS pickup_zone,
    COUNT(*) AS trip_count,
    ROUND(AVG(trip_distance)::numeric, 2) AS avg_distance,
    ROUND(AVG(EXTRACT(EPOCH FROM (tpep_dropoff_datetime - tpep_pickup_datetime)) / 60.0)::numeric, 2) AS avg_minutes,
    ROUND(SUM(total_amount)::numeric, 2) AS total_revenue
  FROM yellow_trips
  WHERE trip_distance > 0
    AND fare_amount > 0
    AND tpep_dropoff_datetime >= tpep_pickup_datetime
  GROUP BY EXTRACT(HOUR FROM tpep_pickup_datetime), pulocationid
), ranked AS (
  SELECT
    *,
    RANK() OVER (PARTITION BY pickup_hour ORDER BY total_revenue DESC) AS revenue_rank
  FROM hourly
)
SELECT *
FROM ranked
WHERE revenue_rank <= 3
ORDER BY pickup_hour, revenue_rank, pickup_zone;
"""

DUCKDB_QUERY = """
WITH hourly AS (
  SELECT
    EXTRACT(HOUR FROM tpep_pickup_datetime) AS pickup_hour,
    pulocationid AS pickup_zone,
    COUNT(*) AS trip_count,
    ROUND(AVG(trip_distance), 2) AS avg_distance,
    ROUND(AVG(date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime)) / 60.0, 2) AS avg_minutes,
    ROUND(SUM(total_amount), 2) AS total_revenue
  FROM yellow_trips
  WHERE trip_distance > 0
    AND fare_amount > 0
    AND tpep_dropoff_datetime >= tpep_pickup_datetime
  GROUP BY EXTRACT(HOUR FROM tpep_pickup_datetime), pulocationid
), ranked AS (
  SELECT
    *,
    RANK() OVER (PARTITION BY pickup_hour ORDER BY total_revenue DESC) AS revenue_rank
  FROM hourly
)
SELECT *
FROM ranked
WHERE revenue_rank <= 3
ORDER BY pickup_hour, revenue_rank, pickup_zone;
"""


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True, **kwargs)


def sql_path(path):
    return str(path).replace("\\", "/")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare_csv(work_dir):
    parquet_file = work_dir / "yellow_tripdata_2024-01.parquet"
    csv_file = work_dir / "yellow_tripdata_2024-01_small.csv"

    urllib.request.urlretrieve(PARQUET_URL, parquet_file)

    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT
            tpep_pickup_datetime,
            tpep_dropoff_datetime,
            passenger_count,
            trip_distance,
            fare_amount,
            total_amount,
            PULocationID AS pulocationid,
            DOLocationID AS dolocationid
          FROM read_parquet('{sql_path(parquet_file)}')
        ) TO '{sql_path(csv_file)}' (HEADER, DELIMITER ',');
        """
    )
    con.close()
    return csv_file


def init_postgres(work_dir):
    pgdata = work_dir / "pgdata"
    socket_dir = work_dir / "socket"
    log_file = work_dir / "postgres.log"
    socket_dir.mkdir()
    port = free_port()

    run(["initdb", "-D", str(pgdata), "-A", "trust", "-U", "postgres"])
    run(
        [
            "pg_ctl",
            "-D",
            str(pgdata),
            "-l",
            str(log_file),
            "-o",
            f"-k {socket_dir} -p {port}",
            "start",
        ]
    )
    return pgdata, socket_dir, port


def stop_postgres(pgdata):
    run(["pg_ctl", "-D", str(pgdata), "stop"])


def load_postgres(csv_file, socket_dir, port):
    env = os.environ.copy()
    env.update({"PGHOST": str(socket_dir), "PGPORT": str(port), "PGUSER": "postgres"})
    subprocess.run(["createdb", "taxi"], check=True, text=True, env=env)

    sql = f"""
    CREATE TABLE yellow_trips (
      tpep_pickup_datetime timestamp,
      tpep_dropoff_datetime timestamp,
      passenger_count integer,
      trip_distance double precision,
      fare_amount double precision,
      total_amount double precision,
      pulocationid integer,
      dolocationid integer
    );

    \\copy yellow_trips FROM '{sql_path(csv_file)}' CSV HEADER;

    CREATE INDEX idx_pickup_time ON yellow_trips (tpep_pickup_datetime);
    CREATE INDEX idx_pickup_zone ON yellow_trips (pulocationid);
    ANALYZE yellow_trips;
    """

    subprocess.run(
        ["psql", "-d", "taxi"],
        input=sql,
        check=True,
        text=True,
        env=env,
        capture_output=True,
    )
    return env


def build_duckdb(csv_file, work_dir):
    db_file = work_dir / "yellow_trips_duckdb.db"
    con = duckdb.connect(str(db_file))
    con.execute(
        f"""
        CREATE TABLE yellow_trips AS
        SELECT *
        FROM read_csv_auto('{sql_path(csv_file)}', header=True)
        ORDER BY tpep_pickup_datetime, pulocationid;
        """
    )
    con.close()
    return db_file


def benchmark_postgres(env, runs=3):
    times = []
    sample = []
    for i in range(runs):
        start = time.perf_counter()
        result = subprocess.run(
            ["psql", "-d", "taxi", "-t", "-A", "-F", ","],
            input=POSTGRES_QUERY,
            check=True,
            text=True,
            env=env,
            capture_output=True,
        )
        times.append(time.perf_counter() - start)
        if i == 0:
            sample = [line for line in result.stdout.strip().splitlines() if line][:5]

    count_result = subprocess.run(
        ["psql", "-d", "taxi", "-t", "-A"],
        input="SELECT COUNT(*) FROM yellow_trips;",
        check=True,
        text=True,
        env=env,
        capture_output=True,
    )
    row_count = int(count_result.stdout.strip())
    return row_count, sample, times


def benchmark_duckdb(db_file, runs=3):
    con = duckdb.connect(str(db_file), read_only=True)
    times = []
    sample = []
    for i in range(runs):
        start = time.perf_counter()
        rows = con.execute(DUCKDB_QUERY).fetchall()
        times.append(time.perf_counter() - start)
        if i == 0:
            sample = rows[:5]
    row_count = con.execute("SELECT COUNT(*) FROM yellow_trips").fetchone()[0]
    con.close()
    return row_count, sample, times


def write_results(pg_rows, duck_rows, pg_times, duck_times):
    pg_avg = sum(pg_times) / len(pg_times)
    duck_avg = sum(duck_times) / len(duck_times)
    speedup = pg_avg / duck_avg if duck_avg else 0

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        f.write("Dataset: yellow_tripdata_2024-01.parquet\n")
        f.write("Projected CSV: yellow_tripdata_2024-01_small.csv\n")
        f.write(f"Row count in PostgreSQL: {pg_rows}\n")
        f.write(f"Row count in DuckDB: {duck_rows}\n")
        f.write(f"PostgreSQL times (s): {', '.join(f'{value:.4f}' for value in pg_times)}\n")
        f.write(f"DuckDB times (s): {', '.join(f'{value:.4f}' for value in duck_times)}\n")
        f.write(f"PostgreSQL average (s): {pg_avg:.4f}\n")
        f.write(f"DuckDB average (s): {duck_avg:.4f}\n")
        f.write(f"DuckDB speedup over PostgreSQL: {speedup:.2f}x\n")


def main():
    with tempfile.TemporaryDirectory(prefix="hw8_") as temp_dir:
        work_dir = Path(temp_dir)
        csv_file = prepare_csv(work_dir)
        pgdata, socket_dir, port = init_postgres(work_dir)
        try:
            env = load_postgres(csv_file, socket_dir, port)
            duckdb_file = build_duckdb(csv_file, work_dir)

            pg_rows, pg_sample, pg_times = benchmark_postgres(env)
            duck_rows, duck_sample, duck_times = benchmark_duckdb(duckdb_file)
            write_results(pg_rows, duck_rows, pg_times, duck_times)

            print("PostgreSQL sample:")
            for row in pg_sample:
                print(row)
            print()
            print("DuckDB sample:")
            for row in duck_sample:
                print(row)
            print()
            print(f"Results written to {RESULTS_FILE}")
        finally:
            stop_postgres(pgdata)


if __name__ == "__main__":
    main()
