"""Salin data energy-collector antar dua PostgreSQL (skema sama).

Hanya INSERT ke database tujuan. Data di sumber dan data lama di tujuan
tidak dihapus.

Isi SUMBER_DB_URL dan TUJUAN_DB_URL di bawah, lalu:
  python migrasi.py --dari 2026-08-01 --sampai 2026-08-26
  python migrasi.py --dari 2026-08-01 --sampai 2026-08-26 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, time, timezone
from typing import Any, Iterable, Optional, Sequence

import asyncpg

# WIB = UTC+7 (tanpa tzdata; Windows Python 3.10 tidak punya IANA zone).
TZ = timezone(timedelta(hours=7))
TZ_LABEL = "WIB (UTC+7)"
BATCH = 1000

# URL PostgreSQL — edit di sini.
SUMBER_DB_URL = "postgresql://test:sundayaTEST*2023@192.168.100.248:5438/postgres"
TUJUAN_DB_URL = "postgresql://test:sundayaTEST*2023@192.168.100.248:5438/energy_meter"

# Kolom lowercase — PostgreSQL menyimpan identifier tanpa quote sebagai lowercase.
READINGS_COLS = (
    "time",
    "session_id",
    "cycle_id",
    "meter_id",
    "device_type",
    "uab", "ubc", "uca",
    "ua", "ub", "uc",
    "ia", "ib", "ic",
    "pt", "pa", "pb", "pc",
    "qt", "qa", "qb", "qc",
    "pft", "pfa", "pfb", "pfc",
    "frequency", "active_power_demand",
    "impep", "expep",
    "q1eq", "q2eq", "q3eq", "q4eq",
)
SESSION_COLS = ("session_id", "meter_id", "start_time", "end_time")
CYCLE_COLS = (
    "cycle_id", "session_id", "meter_id",
    "start_time", "end_time", "impep", "expep",
)


def parse_date(value: str) -> datetime:
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"tanggal harus YYYY-MM-DD, dapat: {value!r}"
        ) from exc
    return datetime.combine(day, time.min, tzinfo=TZ)


def _records(rows: Sequence[asyncpg.Record], cols: Sequence[str]) -> list[tuple]:
    return [tuple(row[c] for c in cols) for row in rows]


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _meter_sql(meter_id: Optional[str], param_index: int) -> str:
    if not meter_id:
        return ""
    return f" AND meter_id = ${param_index}"


def _insert_sql(table: str, cols: Sequence[str], conflict: Optional[str] = None) -> str:
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    if conflict:
        sql += f" ON CONFLICT ({conflict}) DO NOTHING"
    return sql


async def _require_tables(conn: asyncpg.Connection, label: str) -> None:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        ["meter_readings", "production_sessions", "production_cycles"],
    )
    found = {r["table_name"] for r in rows}
    missing = {"meter_readings", "production_sessions", "production_cycles"} - found
    if missing:
        raise SystemExit(
            f"{label}: tabel tidak lengkap (skema.sql belum diterapkan?): {sorted(missing)}"
        )


async def _insert_rows(
    dest: asyncpg.Connection,
    table: str,
    cols: Sequence[str],
    rows: Sequence[asyncpg.Record],
    conflict: Optional[str] = None,
) -> int:
    if not rows:
        return 0
    sql = _insert_sql(table, cols, conflict)
    n = 0
    for chunk in _chunks(rows, BATCH):
        await dest.executemany(sql, _records(chunk, cols))
        n += len(chunk)
    return n


async def migrate(
    sumber_dsn: str,
    tujuan_dsn: str,
    mulai: datetime,
    selesai_eksklusif: datetime,
    meter_id: Optional[str],
    dry_run: bool,
) -> None:
    if sumber_dsn.strip() == tujuan_dsn.strip():
        raise SystemExit("sumber dan tujuan tidak boleh URL yang sama")

    src = await asyncpg.connect(sumber_dsn)
    dst = await asyncpg.connect(tujuan_dsn)
    try:
        await _require_tables(src, "sumber")
        await _require_tables(dst, "tujuan")

        args: list[Any] = [mulai, selesai_eksklusif]
        meter_sql = _meter_sql(meter_id, 3)
        if meter_id:
            args.append(meter_id)

        readings = await src.fetch(
            f"""
            SELECT {", ".join(READINGS_COLS)}
            FROM meter_readings
            WHERE time >= $1 AND time < $2{meter_sql}
            ORDER BY time ASC
            """,
            *args,
        )

        session_ids = {r["session_id"] for r in readings if r["session_id"] is not None}
        cycle_ids = {r["cycle_id"] for r in readings if r["cycle_id"] is not None}

        sessions_by_time = await src.fetch(
            f"""
            SELECT {", ".join(SESSION_COLS)}
            FROM production_sessions
            WHERE start_time >= $1 AND start_time < $2{meter_sql}
            """,
            *args,
        )
        session_ids.update(r["session_id"] for r in sessions_by_time)

        cycles_by_time = await src.fetch(
            f"""
            SELECT {", ".join(CYCLE_COLS)}
            FROM production_cycles
            WHERE start_time >= $1 AND start_time < $2{meter_sql}
            """,
            *args,
        )
        cycle_ids.update(r["cycle_id"] for r in cycles_by_time)
        session_ids.update(r["session_id"] for r in cycles_by_time)

        sessions: list[asyncpg.Record] = []
        if session_ids:
            sessions = await src.fetch(
                f"""
                SELECT {", ".join(SESSION_COLS)}
                FROM production_sessions
                WHERE session_id = ANY($1::uuid[])
                """,
                list(session_ids),
            )

        cycles: list[asyncpg.Record] = []
        if cycle_ids:
            cycles = await src.fetch(
                f"""
                SELECT {", ".join(CYCLE_COLS)}
                FROM production_cycles
                WHERE cycle_id = ANY($1::uuid[])
                """,
                list(cycle_ids),
            )
            missing_parents = {
                c["session_id"] for c in cycles
            } - {s["session_id"] for s in sessions}
            if missing_parents:
                extra = await src.fetch(
                    f"""
                    SELECT {", ".join(SESSION_COLS)}
                    FROM production_sessions
                    WHERE session_id = ANY($1::uuid[])
                    """,
                    list(missing_parents),
                )
                sessions.extend(extra)

        dest_reading_keys = {
            (r["time"], r["meter_id"])
            for r in await dst.fetch(
                f"""
                SELECT time, meter_id
                FROM meter_readings
                WHERE time >= $1 AND time < $2{meter_sql}
                """,
                *args,
            )
        }
        new_readings = [
            r for r in readings if (r["time"], r["meter_id"]) not in dest_reading_keys
        ]

        print(
            f"Rentang  : {mulai.isoformat()} s/d {selesai_eksklusif.isoformat()} "
            f"(eksklusif, {TZ_LABEL})"
        )
        if meter_id:
            print(f"Meter    : {meter_id}")
        print(
            f"Sumber   : sessions={len(sessions)}  cycles={len(cycles)}  "
            f"readings={len(readings)}"
        )
        print(
            f"INSERT   : sessions={len(sessions)}  cycles={len(cycles)}  "
            f"readings={len(new_readings)} "
            f"(readings skip sudah ada={len(readings) - len(new_readings)})"
        )

        if dry_run:
            print("Dry-run: tidak ada INSERT.")
            return

        async with dst.transaction():
            n_s = await _insert_rows(
                dst, "production_sessions", SESSION_COLS, sessions, conflict="session_id"
            )
            n_c = await _insert_rows(
                dst, "production_cycles", CYCLE_COLS, cycles, conflict="cycle_id"
            )
            n_r = await _insert_rows(dst, "meter_readings", READINGS_COLS, new_readings)
        print(f"Selesai INSERT: sessions={n_s}  cycles={n_c}  readings={n_r}")
        print("Data di sumber dan data lama di tujuan tidak diubah/dihapus.")
    finally:
        await src.close()
        await dst.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "INSERT data schema.sql ke PostgreSQL tujuan berdasarkan tanggal. "
            "URL database diisi di dalam file (SUMBER_DB_URL / TUJUAN_DB_URL)."
        )
    )
    p.add_argument(
        "--dari",
        "--from",
        dest="dari",
        type=parse_date,
        help="Tanggal mulai YYYY-MM-DD (inklusif)",
    )
    p.add_argument(
        "--sampai",
        "--to",
        dest="sampai",
        type=parse_date,
        help="Tanggal akhir YYYY-MM-DD (inklusif)",
    )
    p.add_argument("--meter", dest="meter", help="Opsional: hanya meter_id ini")
    p.add_argument("--dry-run", action="store_true", help="Hitung saja, tidak INSERT")
    return p


def _prompt(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        raise SystemExit(f"{label} wajib diisi")
    return value


def main() -> None:
    if "user:pass@host" in TUJUAN_DB_URL:
        raise SystemExit("Isi TUJUAN_DB_URL di migrasi.py dulu.")

    args = build_parser().parse_args()
    dari = args.dari or parse_date(_prompt("Tanggal dari (YYYY-MM-DD)"))
    sampai = args.sampai or parse_date(_prompt("Tanggal sampai (YYYY-MM-DD)"))
    if sampai < dari:
        raise SystemExit("--sampai tidak boleh lebih awal dari --dari")

    selesai = sampai + timedelta(days=1)
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'INSERT'}")

    asyncio.run(
        migrate(
            sumber_dsn=SUMBER_DB_URL,
            tujuan_dsn=TUJUAN_DB_URL,
            mulai=dari,
            selesai_eksklusif=selesai,
            meter_id=args.meter,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except asyncpg.PostgresError as exc:
        print(f"Error PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)
