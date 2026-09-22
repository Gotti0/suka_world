"""
db_writer.py — SQLite 적재 공통 모듈

PRAGMA 설정, UPSERT, 증분 기준 날짜 조회, 배치 진행률 추적을 제공한다.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from backtester.progress import progress_bar

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "deshin_backtester.db"


class DBWriter:
    """SQLite DB 적재 관리자."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self._apply_pragmas()
        self._ensure_stock_master_state_schema()
        self._ensure_elw_phase1_schema()
        self._ensure_elw_factors_schema()
        self._ensure_batch_progress_schema()
        self._ensure_virtual_elw_schema()
        self._ensure_daily_recommendations_schema()
        logger.info("DB 연결: %s", self.db_path)

    def _apply_pragmas(self):
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA cache_size = -64000")

    def _ensure_daily_recommendations_schema(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_recommendations ("
            "elw_code TEXT NOT NULL,"
            "elw_name TEXT,"
            "underlying_code TEXT,"
            "predicted_return REAL,"      # 로컬 모델 예측치
            "gcp_predicted_return REAL,"  # GCP Vertex AI 예측치
            "latest_date TEXT,"
            "updated_at TEXT,"
            "PRIMARY KEY (elw_code, latest_date)"
            ")"
        )

    def _ensure_elw_factors_schema(self):
        """elw_snapshot_factors 테이블 및 Greeks 컬럼 존재 확인."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS elw_snapshot_factors ("
            "elw_code TEXT NOT NULL,"
            "snapshot_ts TEXT NOT NULL,"
            "date TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "PRIMARY KEY (elw_code, snapshot_ts)"
            ")"
        )
        
        # 신규 Greeks 컬럼 추가
        greeks_cols = {
            "iv": "REAL",
            "theoretical_price": "REAL",
            "delta": "REAL",
            "gamma": "REAL",
            "theta": "REAL",
            "vega": "REAL",
            "close": "REAL",
            "bid1": "REAL",
            "ask1": "REAL",
            "volume": "REAL",
            "ask_vol1": "REAL",
            "bid_vol1": "REAL",
            "time": "TEXT",
            "open": "REAL",
            "high": "REAL",
            "low": "REAL",
            "parity": "REAL",
            "gearing": "REAL",
            "diff_sign": "TEXT",
            "diff": "REAL",
            "market_segment": "TEXT",
            "e_gearing": "REAL",
            "break_even_rate": "REAL",
            "premium": "REAL"
        }
        
        for col, dtype in greeks_cols.items():
            self._ensure_column("elw_snapshot_factors", col, dtype)
            
        self.conn.commit()

    def _table_exists(self, table: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
        return cur.fetchone() is not None

    def _get_table_columns(self, table: str) -> list[str]:
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]

    def _ensure_column(self, table: str, column: str, definition: str):
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_stock_master_state_schema(self):
        if not self._table_exists("stock_master"):
            return

        self._ensure_column("stock_master", "is_active", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("stock_master", "last_seen_at", "TEXT")
        self._ensure_column("stock_master", "last_fetch_at", "TEXT")
        self._ensure_column("stock_master", "miss_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("stock_master", "inactive_at", "TEXT")
        self._ensure_column("daily_recommendations", "gcp_predicted_return", "REAL")
        self.conn.commit()

    def _ensure_elw_phase1_schema(self):
        if self._table_exists("elw_master"):
            self._ensure_column("elw_master", "option_type", "TEXT")
            self._ensure_column("elw_master", "prev_volume", "INTEGER DEFAULT 0")
            self._ensure_column("elw_master", "is_active", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("elw_master", "last_seen_at", "TEXT")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_elw_rollover_window "
                "ON elw_master (maturity_date DESC, last_trading_date DESC, remain_days ASC)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_elw_active ON elw_master (is_active)"
            )

        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS calibration_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "effective_from TEXT NOT NULL,"
            "effective_to TEXT,"
            "spread_factor REAL NOT NULL,"
            "iv_factor REAL NOT NULL,"
            "source_window TEXT NOT NULL,"
            "updated_at TEXT NOT NULL"
            ")"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calibration_effective_from "
            "ON calibration_history (effective_from DESC)"
        )
        self.conn.commit()

    def _ensure_batch_progress_schema(self):
        """배치 진행률 추적을 위한 테이블 정의"""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS batch_progress ("
            "batch_id TEXT NOT NULL,"
            "target_code TEXT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'PENDING',"
            "error_msg TEXT,"
            "started_at TEXT,"
            "finished_at TEXT,"
            "PRIMARY KEY (batch_id, target_code)"
            ")"
        )
        self.conn.commit()

    def _ensure_virtual_elw_schema(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS virtual_elw_daily ("
            "elw_code TEXT NOT NULL,"
            "date TEXT NOT NULL,"
            "underlying_close REAL,"
            "hv REAL,"
            "remain_days INTEGER,"
            "theoretical_price REAL NOT NULL,"
            "delta REAL,"
            "gamma REAL,"
            "theta REAL,"
            "vega REAL,"
            "created_at TEXT NOT NULL,"
            "PRIMARY KEY (elw_code, date)"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS backtest_results ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "elw_code TEXT NOT NULL,"
            "model_type TEXT NOT NULL,"
            "hyperparams TEXT NOT NULL,"
            "cagr REAL,"
            "mdd REAL,"
            "sharpe REAL,"
            "sortino REAL,"
            "created_at TEXT NOT NULL"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS backtest_series ("
            "elw_code TEXT NOT NULL,"
            "date TEXT NOT NULL,"
            "strategy_return REAL,"
            "cumulative_return REAL,"
            "PRIMARY KEY (elw_code, date)"
            ")"
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("DB 연결 종료")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ── 테이블 전량 교체 (마스터 데이터용) ──────────────────────────

    def replace_all(self, table: str, rows: list[dict], chunk_size: int = 50000, show_progress: bool = True):
        """테이블을 TRUNCATE 후 전량 INSERT 한다."""
        if not rows:
            logger.warning("replace_all: 빈 데이터 — %s 건너뜀", table)
            return 0

        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM {table}")
        
        values = [tuple(r[c] for c in cols) for r in rows]
        iterator = progress_bar(range(0, len(values), chunk_size), desc=f"Replace {table}", leave=False) if show_progress else range(0, len(values), chunk_size)
        for i in iterator:
            chunk = values[i:i+chunk_size]
            cur.executemany(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
                chunk,
            )
        self.conn.commit()
        logger.info("replace_all: %s — %d행 적재", table, len(rows))
        return len(rows)

    def _get_primary_key_columns(self, table: str) -> list[str]:
        """테이블의 PRIMARY KEY 컬럼 목록을 반환한다."""
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        pk_columns = [(row[5], row[1]) for row in cur.fetchall() if row[5] > 0]
        pk_columns.sort(key=lambda item: item[0])
        return [name for _, name in pk_columns]

    # ── 증분 적재 (UPSERT) ──────────────────────────────────────

    def upsert_many(self, table: str, rows: list[dict], chunk_size: int = 50000, commit: bool = True, show_progress: bool = True):
        """PRIMARY KEY 기준으로 안전하게 UPSERT 한다."""
        if not rows:
            return 0

        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        pk_cols = self._get_primary_key_columns(table)

        cur = self.conn.cursor()
        values = [tuple(r[c] for c in cols) for r in rows]
        total_inserted = 0

        if pk_cols:
            update_cols = [c for c in cols if c not in pk_cols]
            if update_cols:
                update_set = ", ".join([f"{c} = excluded.{c}" for c in update_cols])
                sql = (
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                    f"ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {update_set}"
                )
            else:
                sql = (
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                    f"ON CONFLICT({', '.join(pk_cols)}) DO NOTHING"
                )
        else:
            sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
        
        iterator = progress_bar(range(0, len(values), chunk_size), desc=f"Upsert {table}", leave=False) if show_progress else range(0, len(values), chunk_size)
        for i in iterator:
            chunk = values[i:i+chunk_size]
            cur.executemany(
                sql,
                chunk,
            )
            total_inserted += cur.rowcount
            
        if commit:
            self.conn.commit()
        return total_inserted

    def mark_stock_master_seen(self, stock_codes: list[str], seen_at: str | None = None):
        """마스터 동기화 시점에 확인된 종목을 활성 상태로 갱신한다."""
        if not stock_codes or not self._table_exists("stock_master"):
            return 0

        now = seen_at or datetime.now().isoformat()
        cur = self.conn.cursor()
        cur.executemany(
            "UPDATE stock_master "
            "SET is_active = 1, last_seen_at = ?, miss_count = 0, inactive_at = NULL "
            "WHERE stock_code = ?",
            [(now, code) for code in stock_codes],
        )
        self.conn.commit()
        return cur.rowcount

    def record_stock_chart_success(self, stock_code: str, fetched_at: str | None = None):
        """차트 수집 성공 시 활성 상태와 성공 시각을 갱신한다."""
        if not self._table_exists("stock_master"):
            return 0

        now = fetched_at or datetime.now().isoformat()
        cur = self.conn.execute(
            "UPDATE stock_master "
            "SET is_active = 1, last_fetch_at = ?, miss_count = 0, inactive_at = NULL "
            "WHERE stock_code = ?",
            (now, stock_code),
        )
        self.conn.commit()
        return cur.rowcount

    def record_stock_chart_miss(self, stock_code: str, threshold: int = 3):
        """차트 수집 실패/무응답 시 miss_count 를 누적하고 임계값에서 비활성화한다."""
        if not self._table_exists("stock_master"):
            return 0

        now = datetime.now().isoformat()
        cur = self.conn.execute(
            "UPDATE stock_master "
            "SET miss_count = COALESCE(miss_count, 0) + 1, "
            "    is_active = CASE WHEN COALESCE(miss_count, 0) + 1 >= ? THEN 0 ELSE is_active END, "
            "    inactive_at = CASE WHEN COALESCE(miss_count, 0) + 1 >= ? THEN ? ELSE inactive_at END "
            "WHERE stock_code = ?",
            (threshold, threshold, now, stock_code),
        )
        self.conn.commit()
        return cur.rowcount

    def get_stock_codes(self, include_inactive: bool = False) -> list[str]:
        """국내 종목코드 목록을 반환한다."""
        if not self._table_exists("stock_master"):
            return []

        where_clause = "" if include_inactive else " WHERE COALESCE(is_active, 1) = 1"
        cur = self.conn.execute(
            f"SELECT stock_code FROM stock_master{where_clause} ORDER BY stock_code"
        )
        return [row[0] for row in cur.fetchall()]

    def upsert_financial_statements(self, rows: list[dict], chunk_size: int = 50000, commit: bool = True, show_progress: bool = True):
        """financial_statements 전용 증분 적재 (UPSERT).
        자연키 기반으로 충돌 시 UPDATE를 수행합니다.
        """
        if not rows:
            return 0

        table_cols = set(self._get_table_columns("financial_statements"))
        cols = [c for c in rows[0].keys() if c in table_cols]
        if not cols:
            logger.warning("upsert_financial_statements: 적재 가능한 컬럼이 없습니다")
            return 0

        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        index_list = self.conn.execute("PRAGMA index_list(financial_statements)").fetchall()
        has_uq_fs_natural_key = any(row[1] == "uq_fs_natural_key" for row in index_list)

        if has_uq_fs_natural_key:
            conflict_cols = [
                c for c in ['corp_code', 'bsns_year', 'reprt_code', 'fs_div', 'sj_div', 'account_id'] if c in cols
            ]
        else:
            conflict_cols = [c for c in self._get_primary_key_columns("financial_statements") if c in cols]

        if not conflict_cols:
            conflict_cols = [c for c in ['corp_code', 'bsns_year', 'reprt_code', 'sj_div', 'account_id'] if c in cols]

        update_set = ", ".join([f"{c} = excluded.{c}" for c in cols if c not in set(conflict_cols)])

        sql = f"""
            INSERT INTO financial_statements ({col_names})
            VALUES ({placeholders})
            ON CONFLICT ({', '.join(conflict_cols)})
            DO UPDATE SET
                {update_set}
        """
        cur = self.conn.cursor()
        values = [tuple(r.get(c) for c in cols) for r in rows]
        total_inserted = 0
        
        iterator = progress_bar(range(0, len(values), chunk_size), desc="Upsert FS", leave=False) if show_progress else range(0, len(values), chunk_size)
        for i in iterator:
            chunk = values[i:i+chunk_size]
            cur.executemany(sql, chunk)
            total_inserted += cur.rowcount

        if commit:
            self.conn.commit()
        return total_inserted

    def upsert_valuation_metrics(self, rows: list[dict], chunk_size: int = 50000, commit: bool = True, show_progress: bool = True):
        """valuation_metrics 전용 증분 적재 (UPSERT).
        (stock_code, statement_date, bsns_year, reprt_code) 충돌 시 UPDATE를 수행합니다.
        """
        if not rows:
            return 0

        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        natural_keys = {'stock_code', 'statement_date', 'bsns_year', 'reprt_code'}
        update_set = ", ".join([f"{c} = excluded.{c}" for c in cols if c not in natural_keys])

        sql = f"""
            INSERT INTO valuation_metrics ({col_names})
            VALUES ({placeholders})
            ON CONFLICT (stock_code, statement_date, bsns_year, reprt_code)
            DO UPDATE SET
                {update_set}
        """

        cur = self.conn.cursor()
        values = [tuple(r[c] for c in cols) for r in rows]
        total_inserted = 0

        iterator = progress_bar(range(0, len(values), chunk_size), desc="Upsert Valuation", leave=False) if show_progress else range(0, len(values), chunk_size)
        for i in iterator:
            chunk = values[i:i+chunk_size]
            cur.executemany(sql, chunk)
            total_inserted += cur.rowcount

        if commit:
            self.conn.commit()
        return total_inserted

    # ── 증분 기준 날짜 조회 ─────────────────────────────────────

    def get_latest_date(self, table: str, code_col: str, code_val: str) -> str | None:
        """특정 종목의 DB 내 최신 거래일을 반환한다. 없으면 None."""
        cur = self.conn.execute(
            f"SELECT MAX(date) FROM {table} WHERE {code_col} = ?",
            (code_val,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    # ── 배치 진행률 관리 ────────────────────────────────────────

    def init_batch(self, batch_id: str, codes: list[str]):
        """배치 대상 종목들의 진행률을 PENDING 으로 초기화한다. 이미 존재하면 건너뜀."""
        cur = self.conn.cursor()
        now = datetime.now().isoformat()
        cur.executemany(
            "INSERT OR IGNORE INTO batch_progress (batch_id, target_code, status, started_at) "
            "VALUES (?, ?, 'PENDING', ?)",
            [(batch_id, code, now) for code in codes],
        )
        self.conn.commit()

    def get_pending_codes(self, batch_id: str) -> list[str]:
        """PENDING 또는 ERROR 상태의 종목 코드를 반환한다."""
        cur = self.conn.execute(
            "SELECT target_code FROM batch_progress "
            "WHERE batch_id = ? AND status IN ('PENDING', 'ERROR') "
            "ORDER BY target_code",
            (batch_id,),
        )
        return [r[0] for r in cur.fetchall()]

    def reset_batch(self, batch_id: str):
        """특정 배치의 모든 진행률 데이터를 삭제하여 처음부터 다시 시작하게 함."""
        self.conn.execute("DELETE FROM batch_progress WHERE batch_id = ?", (batch_id,))
        self.conn.commit()
        logger.info(f"배치 초기화 완료: {batch_id}")

    def mark_done(self, batch_id: str, code: str, commit: bool = True):
        self.conn.execute(
            "UPDATE batch_progress SET status = 'DONE', finished_at = ? "
            "WHERE batch_id = ? AND target_code = ?",
            (datetime.now().isoformat(), batch_id, code),
        )
        if commit:
            self.conn.commit()

    def mark_error(self, batch_id: str, code: str, error_msg: str, commit: bool = True):
        self.conn.execute(
            "UPDATE batch_progress SET status = 'ERROR', error_msg = ?, finished_at = ? "
            "WHERE batch_id = ? AND target_code = ?",
            (error_msg, datetime.now().isoformat(), batch_id, code),
        )
        if commit:
            self.conn.commit()

    # ── 보정계수 관리 (Calibration) ──────────────────────────

    def write_calibration_factors(
        self,
        effective_from: str,
        spread_factor: float,
        iv_factor: float,
        source_window: str,
        effective_to: str | None = None,
        commit: bool = True,
    ) -> int:
        """보정계수 기록. 기존 유효한 계수의 effective_to를 업데이트한다."""
        if not self._table_exists("calibration_history"):
            logger.warning("calibration_history 테이블이 없습니다")
            return 0

        cur = self.conn.cursor()
        
        # 기존 활성 계수의 effective_to를 설정
        cur.execute(
            "UPDATE calibration_history SET effective_to = ? "
            "WHERE effective_to IS NULL AND effective_from < ?",
            (effective_from, effective_from),
        )

        # 새 계수 기록
        cur.execute(
            "INSERT INTO calibration_history "
            "(effective_from, effective_to, spread_factor, iv_factor, source_window, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                effective_from,
                effective_to,
                float(spread_factor),
                float(iv_factor),
                str(source_window),
                datetime.now().isoformat(),
            ),
        )
        
        if commit:
            self.conn.commit()
        return cur.rowcount

    def get_calibration_factors(self, date: str) -> dict[str, float]:
        """특정 날짜의 유효한 보정계수를 조회한다.
        Returns: {'spread_factor': float, 'iv_factor': float}
        """
        if not self._table_exists("calibration_history"):
            return {"spread_factor": 1.0, "iv_factor": 1.0}

        cur = self.conn.execute(
            "SELECT spread_factor, iv_factor FROM calibration_history "
            "WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to > ?) "
            "ORDER BY effective_from DESC LIMIT 1",
            (date, date),
        )
        row = cur.fetchone()
        if row:
            return {
                "spread_factor": float(row[0]),
                "iv_factor": float(row[1]),
            }
        return {"spread_factor": 1.0, "iv_factor": 1.0}

    # ── 실행 로그 관리 (Execution) ──────────────────────────

    def _ensure_execution_log_schema(self):
        """execution_log 테이블 정의 확인"""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS execution_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "date TEXT NOT NULL,"
            "ticker TEXT NOT NULL,"
            "side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),"
            "requested_price REAL NOT NULL,"
            "executed_price REAL NOT NULL,"
            "quantity INTEGER NOT NULL,"
            "fill_status TEXT NOT NULL CHECK (fill_status IN ('filled', 'rejected', 'partial')),"
            "rejection_reason TEXT,"
            "commission REAL NOT NULL DEFAULT 0,"
            "slippage_pct REAL NOT NULL DEFAULT 0,"
            "created_at TEXT NOT NULL"
            ")"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_date_ticker "
            "ON execution_log (date DESC, ticker)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_ticker_side "
            "ON execution_log (ticker, side)"
        )
        self.conn.commit()

    def write_execution_logs(
        self,
        execution_logs: list[dict],
        chunk_size: int = 10000,
        commit: bool = True,
        show_progress: bool = False,
    ) -> int:
        """실행 로그를 배치로 저장한다.
        각 로그는 다음 필드를 포함해야 함:
        - date, ticker, side, requested_price, executed_price, quantity
        - fill_status, rejection_reason, commission, slippage_pct
        """
        if not execution_logs:
            return 0

        self._ensure_execution_log_schema()

        cols = [
            "date",
            "ticker",
            "side",
            "requested_price",
            "executed_price",
            "quantity",
            "fill_status",
            "rejection_reason",
            "commission",
            "slippage_pct",
            "created_at",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        cur = self.conn.cursor()
        now = datetime.now().isoformat()
        
        values = []
        for log in execution_logs:
            row = tuple(
                log.get(col) if col != "created_at" else now
                for col in cols
            )
            values.append(row)

        total_inserted = 0
        iterator = (
            progress_bar(range(0, len(values), chunk_size), desc="Write Execution Log", leave=False)
            if show_progress
            else range(0, len(values), chunk_size)
        )
        for i in iterator:
            chunk = values[i : i + chunk_size]
            cur.executemany(f"INSERT INTO execution_log ({col_names}) VALUES ({placeholders})", chunk)
            total_inserted += cur.rowcount

        if commit:
            self.conn.commit()
        logger.info("write_execution_logs: %d행 적재", total_inserted)
        return total_inserted
