"""SQLite database layer — schema sesuai blueprint terbaru."""

import os
import logging
import aiosqlite
from config import DB_PATH

logger = logging.getLogger(__name__)

# ─── DDL ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    period               TEXT    UNIQUE NOT NULL,
    draw_time            TEXT    NOT NULL,
    full_number          TEXT    NOT NULL,       -- 4 digit: "1295"
    target_position      TEXT    NOT NULL,       -- depan | tengah | belakang
    target_number_2d     TEXT    NOT NULL,       -- "12"/"29"/"95"
    target_bk            TEXT    NOT NULL,       -- "BE" | "KE"
    target_gj            TEXT    NOT NULL,       -- "GE" | "GA"
    created_at           TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bets (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    period                 TEXT    NOT NULL,
    target_position        TEXT    NOT NULL,
    bet_dimension          TEXT    NOT NULL,   -- "besar_kecil" | "genap_ganjil"
    bet_choice             TEXT    NOT NULL,   -- "BE" | "KE" | "GE" | "GA"
    bet_amount_per_angka   REAL    NOT NULL,   -- Rp per angka (integer IDR)
    total_amount           REAL    NOT NULL,   -- bet_amount_per_angka × 50 × 1000 (Rupiah)
    martingale_level       INTEGER NOT NULL,
    confidence             REAL    DEFAULT 0,
    status                 TEXT    DEFAULT 'placed',  -- placed | won | lost
    win_amount             REAL    DEFAULT 0,
    result_2d              TEXT,              -- 2D belakang yang keluar
    result_match           TEXT,              -- "BE"/"KE"/"GE"/"GA" yang keluar
    api_response           TEXT,
    created_at             TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date             TEXT PRIMARY KEY,
    total_bets       INTEGER DEFAULT 0,
    total_wins       INTEGER DEFAULT 0,
    total_bet_amount REAL    DEFAULT 0,
    total_win_amount REAL    DEFAULT 0,
    profit           REAL    DEFAULT 0,
    ending_balance   REAL    DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Keys untuk bot_state:
#   consecutive_losses_bk  — streak kalah untuk dimensi BK
#   consecutive_losses_gj  — streak kalah untuk dimensi GJ
#   martingale_level_bk    — level martingale BK saat ini
#   martingale_level_gj    — level martingale GJ saat ini
#   last_period            — periode terakhir yang diproses
#   daily_loss             — total kerugian hari ini (Rupiah)


# ─── Init ─────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in _SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s)
        await db.commit()
    logger.info("Database siap: %s", DB_PATH)


# ─── Results ──────────────────────────────────────────────────────────────────

async def save_result(
    period: str,
    draw_time: str,
    full_number: str,
    target_position: str,
    target_number_2d: str,
    target_bk: str,
    target_gj: str,
) -> bool:
    """Simpan hasil draw baru. Return True jika berhasil insert, False jika duplikat."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO results
               (period, draw_time, full_number,
                target_position, target_number_2d, target_bk, target_gj)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (period, draw_time, full_number, target_position, target_number_2d, target_bk, target_gj),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_recent_results(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_last_result() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM results ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def result_exists(period: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM results WHERE period = ?", (period,)
        ) as cur:
            return await cur.fetchone() is not None


# ─── Bets ─────────────────────────────────────────────────────────────────────

async def save_bet(
    period: str,
    target_position: str,
    dimension: str,          # "besar_kecil" | "genap_ganjil"
    choice: str,             # "BE" | "KE" | "GE" | "GA"
    bet_amount_per_angka: int,
    total_amount: int,
    martingale_level: int,
    confidence: float = 0.0,
    api_response: str | None = None,
) -> int:
    """Simpan satu bet (satu dimensi). Return ID row."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO bets
               (period, target_position, bet_dimension, bet_choice,
                bet_amount_per_angka, total_amount,
                martingale_level, confidence, api_response)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (period, target_position, dimension, choice,
             bet_amount_per_angka, total_amount,
             martingale_level, confidence, api_response),
        )
        await db.commit()
        return cur.lastrowid


async def settle_bet(
    bet_id: int,
    status: str,           # "won" | "lost"
    win_amount: int,
    result_2d: str,
    result_match: str,     # kategori yang keluar: "BE"/"KE"/"GE"/"GA"
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE bets
               SET status = ?, win_amount = ?,
                   result_2d = ?, result_match = ?
               WHERE id = ?""",
            (status, win_amount, result_2d, result_match, bet_id),
        )
        await db.commit()


async def get_placed_bets(period: str) -> list[dict]:
    """Ambil semua bet berstatus 'placed' untuk periode tertentu."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bets WHERE period = ? AND status = 'placed'", (period,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_placed_bets() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bets WHERE status = 'placed' ORDER BY id"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Bot state ────────────────────────────────────────────────────────────────

async def get_state(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_state(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (key, str(value)),
        )
        await db.commit()


# ─── Daily stats ──────────────────────────────────────────────────────────────

async def update_daily_stats(
    date: str,
    bet_amount: int,
    win_amount: int,
    is_win: bool,
) -> None:
    profit = win_amount - bet_amount
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_stats
               (date, total_bets, total_wins, total_bet_amount, total_win_amount, profit)
               VALUES (?, 1, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   total_bets       = total_bets + 1,
                   total_wins       = total_wins + excluded.total_wins,
                   total_bet_amount = total_bet_amount + excluded.total_bet_amount,
                   total_win_amount = total_win_amount + excluded.total_win_amount,
                   profit           = profit + excluded.profit""",
            (date, 1 if is_win else 0, bet_amount, win_amount, profit),
        )
        await db.commit()


async def set_daily_ending_balance(date: str, balance: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_stats (date, ending_balance)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET ending_balance = excluded.ending_balance""",
            (date, balance),
        )
        await db.commit()


async def get_daily_stats(date: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM daily_stats WHERE date = ?", (date,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
