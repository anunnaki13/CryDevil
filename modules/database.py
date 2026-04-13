"""SQLite database layer for single-bot multi-position mode."""

import logging
import os

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    period             TEXT UNIQUE NOT NULL,
    draw_time          TEXT NOT NULL,
    full_number        TEXT NOT NULL,
    depan_number_2d    TEXT NOT NULL,
    depan_bk           TEXT NOT NULL,
    depan_gj           TEXT NOT NULL,
    tengah_number_2d   TEXT NOT NULL,
    tengah_bk          TEXT NOT NULL,
    tengah_gj          TEXT NOT NULL,
    belakang_number_2d TEXT NOT NULL,
    belakang_bk        TEXT NOT NULL,
    belakang_gj        TEXT NOT NULL,
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bets (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    period                TEXT NOT NULL,
    target_position       TEXT NOT NULL,
    bet_dimension         TEXT NOT NULL,
    bet_slot              TEXT NOT NULL,
    bet_choice            TEXT NOT NULL,
    bet_amount_per_angka  REAL NOT NULL,
    total_amount          REAL NOT NULL,
    martingale_level      INTEGER NOT NULL,
    confidence            REAL DEFAULT 0,
    status                TEXT DEFAULT 'placed',
    win_amount            REAL DEFAULT 0,
    result_2d             TEXT,
    result_match          TEXT,
    api_response          TEXT,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date             TEXT PRIMARY KEY,
    total_bets       INTEGER DEFAULT 0,
    total_wins       INTEGER DEFAULT 0,
    total_bet_amount REAL DEFAULT 0,
    total_win_amount REAL DEFAULT 0,
    profit           REAL DEFAULT 0,
    ending_balance   REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    period            TEXT NOT NULL,
    slot              TEXT NOT NULL,
    target_position   TEXT NOT NULL,
    bet_dimension     TEXT NOT NULL,
    predicted_choice  TEXT NOT NULL,
    confidence        REAL NOT NULL,
    source            TEXT NOT NULL,
    selected_for_bet  INTEGER DEFAULT 0,
    actual_choice     TEXT,
    is_correct        INTEGER,
    reason            TEXT DEFAULT '',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    settled_at        TEXT,
    UNIQUE(period, slot, source)
);

CREATE TABLE IF NOT EXISTS knowledge_base_snapshots (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_count       INTEGER NOT NULL,
    period_from        TEXT NOT NULL,
    period_to          TEXT NOT NULL,
    summary_text       TEXT NOT NULL,
    knowledge_json     TEXT NOT NULL,
    model              TEXT NOT NULL,
    source             TEXT NOT NULL,
    is_active          INTEGER DEFAULT 1,
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in _SCHEMA.strip().split(";"):
            sql = stmt.strip()
            if sql:
                await db.execute(sql)
        await db.commit()
    logger.info("Database siap: %s", DB_PATH)


async def save_result(period: str, draw_time: str, parsed: dict) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO results
               (period, draw_time, full_number,
                depan_number_2d, depan_bk, depan_gj,
                tengah_number_2d, tengah_bk, tengah_gj,
                belakang_number_2d, belakang_bk, belakang_gj)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                period,
                draw_time,
                parsed["full"],
                parsed["depan"], parsed["depan_bk"], parsed["depan_gj"],
                parsed["tengah"], parsed["tengah_bk"], parsed["tengah_gj"],
                parsed["belakang"], parsed["belakang_bk"], parsed["belakang_gj"],
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_recent_results(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_last_result() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM results ORDER BY id DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def result_exists(period: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM results WHERE period = ?", (period,)) as cur:
            return await cur.fetchone() is not None


async def save_bet(
    period: str,
    target_position: str,
    dimension: str,
    bet_slot: str,
    choice: str,
    bet_amount_per_angka: int,
    total_amount: int,
    martingale_level: int,
    confidence: float = 0.0,
    api_response: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO bets
               (period, target_position, bet_dimension, bet_slot, bet_choice,
                bet_amount_per_angka, total_amount, martingale_level, confidence, api_response)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                period,
                target_position,
                dimension,
                bet_slot,
                choice,
                bet_amount_per_angka,
                total_amount,
                martingale_level,
                confidence,
                api_response,
            ),
        )
        await db.commit()
        return cur.lastrowid


async def settle_bet(
    bet_id: int,
    status: str,
    win_amount: int,
    result_2d: str,
    result_match: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE bets
               SET status = ?, win_amount = ?, result_2d = ?, result_match = ?
               WHERE id = ?""",
            (status, win_amount, result_2d, result_match, bet_id),
        )
        await db.commit()


async def get_placed_bets(period: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bets WHERE period = ? AND status = 'placed' ORDER BY id",
            (period,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_state(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_state WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_state(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = CURRENT_TIMESTAMP""",
            (key, str(value)),
        )
        await db.commit()


async def update_daily_stats(date: str, bet_amount: int, win_amount: int, is_win: bool) -> None:
    profit = win_amount - bet_amount
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_stats
               (date, total_bets, total_wins, total_bet_amount, total_win_amount, profit)
               VALUES (?, 1, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   total_bets = total_bets + 1,
                   total_wins = total_wins + excluded.total_wins,
                   total_bet_amount = total_bet_amount + excluded.total_bet_amount,
                   total_win_amount = total_win_amount + excluded.total_win_amount,
                   profit = profit + excluded.profit""",
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
        async with db.execute("SELECT * FROM daily_stats WHERE date = ?", (date,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_aggregate_daily_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                   COUNT(*) AS total_days,
                   COALESCE(SUM(total_bets), 0) AS total_bets,
                   COALESCE(SUM(total_wins), 0) AS total_wins,
                   COALESCE(SUM(total_bet_amount), 0) AS total_bet_amount,
                   COALESCE(SUM(total_win_amount), 0) AS total_win_amount,
                   COALESCE(SUM(profit), 0) AS profit
               FROM daily_stats"""
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {
                "total_days": 0,
                "total_bets": 0,
                "total_wins": 0,
                "total_bet_amount": 0,
                "total_win_amount": 0,
                "profit": 0,
            }


async def count_distinct_bet_periods() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(DISTINCT period) FROM bets") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0


async def save_prediction_run(
    period: str,
    slot: str,
    target_position: str,
    dimension: str,
    predicted_choice: str,
    confidence: float,
    source: str,
    selected_for_bet: bool,
    reason: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO prediction_runs
               (period, slot, target_position, bet_dimension, predicted_choice,
                confidence, source, selected_for_bet, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(period, slot, source) DO UPDATE SET
                   target_position = excluded.target_position,
                   bet_dimension = excluded.bet_dimension,
                   predicted_choice = excluded.predicted_choice,
                   confidence = excluded.confidence,
                   selected_for_bet = excluded.selected_for_bet,
                   reason = excluded.reason""",
            (
                period,
                slot,
                target_position,
                dimension,
                predicted_choice,
                confidence,
                source,
                1 if selected_for_bet else 0,
                reason,
            ),
        )
        await db.commit()


async def settle_prediction_runs(period: str, parsed: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, target_position, bet_dimension, predicted_choice FROM prediction_runs "
            "WHERE period = ? AND is_correct IS NULL",
            (period,),
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            target = row["target_position"]
            dimension = row["bet_dimension"]
            actual_choice = parsed[f"{target}_{'bk' if dimension == 'besar_kecil' else 'gj'}"]
            is_correct = 1 if row["predicted_choice"] == actual_choice else 0
            await db.execute(
                """UPDATE prediction_runs
                   SET actual_choice = ?, is_correct = ?, settled_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (actual_choice, is_correct, row["id"]),
            )

        await db.commit()


async def get_prediction_feedback(limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT slot,
                      COUNT(*) AS total,
                      COALESCE(SUM(is_correct), 0) AS wins,
                      AVG(confidence) AS avg_confidence
               FROM prediction_runs
               WHERE is_correct IS NOT NULL
               GROUP BY slot
               ORDER BY total DESC, slot ASC
               LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_knowledge_base_snapshot(
    *,
    source_count: int,
    period_from: str,
    period_to: str,
    summary_text: str,
    knowledge_json: str,
    model: str,
    source: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE knowledge_base_snapshots SET is_active = 0 WHERE is_active = 1")
        cur = await db.execute(
            """INSERT INTO knowledge_base_snapshots
               (source_count, period_from, period_to, summary_text, knowledge_json, model, source, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (source_count, period_from, period_to, summary_text, knowledge_json, model, source),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_knowledge_base() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM knowledge_base_snapshots WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_knowledge_base_history(limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM knowledge_base_snapshots ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]
