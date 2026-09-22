"""
dart_financials.py - DART 재무제표 API 수집 모듈
OpenDartReader를 사용하여 상장사의 재무제표(CFS/OFS)를 수집하여 SQLite에 다일(Multi-day) 배치 방식으로 증분 적재합니다.
"""

import os
import time
import io
import logging
from contextlib import redirect_stdout
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from typing import Dict, Iterable, List, Optional

# import sys
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "OpenDartReader-master")))
# Local OpenDartReader package compatibility can be tricky, assuming OpenDartReader is in site-packages or sys_path
try:
    import OpenDartReader
except ImportError:
    logging.warning("OpenDartReader 패키지가 없습니다. 'pip install OpenDartReader'를 실행하세요.")

from collector.db_writer import DBWriter
from backtester.utils.valuation_calculator import calculate_valuation_record, normalize_stock_code
from backtester.progress import progress_bar

logger = logging.getLogger(__name__)

class DartFinancialsCollector:
    def __init__(self, db_path: Optional[str] = None, chunk_size: int = 20):
        load_dotenv()
        self.api_key = os.getenv("DART_API_KEY")
        if not self.api_key:
            raise ValueError("환경 변수 DART_API_KEY가 설정되지 않았습니다.")
            
        self.dart = OpenDartReader(self.api_key)
        self.db_writer = DBWriter(db_path) if db_path else DBWriter()
        self.reprt_codes = ['11011', '11012', '11013', '11014']
        self.fs_div_cache = {}  # 기업별 제출 기준(CFS/OFS) 캐시 메모리
        self.chunk_size = max(1, int(chunk_size))

    def close(self):
        self.db_writer.close()

    def update_corp_codes(self):
        """DART 제공 법인 고유번호 목록을 최신화하여 dart_corp_code 테이블에 적재합니다."""
        logger.info("DART 고유번호 마스터 데이터 다운로드 시작...")
        df_corp = self.dart.corp_codes
        
        # 스키마에 정의된 컬럼만 추출
        df_corp = df_corp[['corp_code', 'corp_name', 'stock_code', 'modify_date']]
        
        # DataFrame is expected to have columns: corp_code, corp_name, stock_code, modify_date
        records = df_corp.to_dict('records')
        for r in records:
            # 상장되지 않은 기업의 stock_code는 공백 문자열이거나 NaN일 수 있으므로 정제
            sc = str(r.get('stock_code', '')).strip()
            r['stock_code'] = sc if sc else None

        self.db_writer.upsert_many('dart_corp_code', records)
        logger.info("DART 고유번호 마스터 데이터 %d건 적재 완료", len(records))

    def _clean_amount(self, val):
        if pd.isna(val) or val == "" or val == "-":
            return None
        try:
            return int(str(val).replace(",", ""))
        except ValueError:
            return None

    def _stock_exists(self, stock_code: str) -> bool:
        row = self.db_writer.conn.execute(
            "SELECT 1 FROM stock_master WHERE stock_code = ? LIMIT 1",
            (stock_code,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
        for idx in range(0, len(items), size):
            yield items[idx:idx + size]

    def _parse_financial_row(self, row, corp_code: str, year: int, reprt_code: str) -> dict:
        account_id = str(row.get('account_id', ''))
        if account_id == 'nan' or not account_id:
            account_id = None

        return {
            'corp_code': str(row.get('corp_code', corp_code)),
            'bsns_year': int(row.get('bsns_year', year)),
            'reprt_code': str(row.get('reprt_code', reprt_code)),
            'fs_div': str(row.get('fs_div', self.fs_div_cache.get(corp_code, 'CFS'))),
            'sj_div': str(row.get('sj_div', '')),
            'sj_nm': str(row.get('sj_nm', '')),
            'account_id': account_id,
            'account_nm': str(row.get('account_nm', '')),
            'account_detail': str(row.get('account_detail', '')) if not pd.isna(row.get('account_detail')) else None,
            'thstrm_nm': str(row.get('thstrm_nm', '')) if not pd.isna(row.get('thstrm_nm')) else None,
            'thstrm_amount': self._clean_amount(row.get('thstrm_amount')),
            'frmtrm_nm': str(row.get('frmtrm_nm', '')) if not pd.isna(row.get('frmtrm_nm')) else None,
            'frmtrm_amount': self._clean_amount(row.get('frmtrm_amount')),
            'bfefrmtrm_nm': str(row.get('bfefrmtrm_nm', '')) if not pd.isna(row.get('bfefrmtrm_nm')) else None,
            'bfefrmtrm_amount': self._clean_amount(row.get('bfefrmtrm_amount')),
            'ord': int(row.get('ord', 0)) if not pd.isna(row.get('ord')) else None,
            'currency': str(row.get('currency', 'KRW')),
            'updated_at': datetime.now().isoformat(),
        }

    def _fetch_financial_statements_multi(self, corp_codes: List[str], year: int, reprt_code: str) -> Dict[str, List[dict]]:
        result_map: Dict[str, List[dict]] = {code: [] for code in corp_codes}
        if not corp_codes:
            return result_map

        f = io.StringIO()
        with redirect_stdout(f):
            # 쉼표 구분 corp_code를 전달하면 OpenDartReader가 fnlttMultiAcnt.json을 사용한다.
            df = self.dart.finstate(','.join(corp_codes), year, reprt_code=reprt_code)

        output = f.getvalue()
        if output:
            if '020' in output or '010' in output or '일일 허용량' in output or 'rate limit' in output.lower():
                raise RuntimeError(f"OpenDartReader API 한도 초과: {output.strip()}")

        if df is None or df.empty:
            return result_map

        for _, row in df.iterrows():
            corp_code = str(row.get('corp_code', '')).strip()
            if not corp_code:
                continue
            if corp_code not in result_map:
                result_map[corp_code] = []
            result_map[corp_code].append(self._parse_financial_row(row, corp_code, year, reprt_code))

        return result_map

    def _fetch_financial_statements(self, corp_code: str, year: int, reprt_code: str):
        """특정 기업/연도/보고서코드에 대한 재무제표 데이터를 조회 및 정제하여 반환합니다."""
        cached_div = self.fs_div_cache.get(corp_code)
        
        df = None
        used_div = None
        f = io.StringIO()
        
        # OpenDartReader 내부에서 나는 에러/경고 프린트 문을 가로채기 위함
        with redirect_stdout(f):
            # 1. 캐시가 없거나 CFS로 검증된 기업이라면 CFS 시도
            if cached_div in (None, 'CFS'):
                try:
                    df = self.dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div='CFS')
                    used_div = 'CFS'
                except Exception as e:
                    if 'rate limit' in str(e).lower() or '한도 초과' in str(e):
                        raise
                    df = None

            # 2. 결과가 없으면서, 캐시가 없거나 OFS로 검증된 기업이라면 OFS 폴백 시도
            if (df is None or df.empty) and cached_div in (None, 'OFS'):
                try:
                    df = self.dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div='OFS')
                    used_div = 'OFS'
                except Exception as e:
                    if 'rate limit' in str(e).lower() or '한도 초과' in str(e):
                        raise
                    df = None

        # 가로챈 로그 분석 (일일 한도 초과 등 예외 파악 및 013 무시)
        output = f.getvalue()
        if output:
            if '020' in output or '010' in output or '일일 허용량' in output or 'rate limit' in output.lower():
                raise RuntimeError(f"OpenDartReader API 한도 초과: {output.strip()}")
            elif '013' not in output and '조회된 데이타가 없습니다' not in output:
                logger.debug(f"{corp_code} ({year}, {reprt_code}) 로그: {output.strip()}")

        if df is None or df.empty:
            return []

        # 데이터 조회에 성공했다면, 해당 기업의 재무제표 구분을 기록하여 이후 API 낭비를 방지
        if corp_code not in self.fs_div_cache:
            self.fs_div_cache[corp_code] = used_div

        # 열 이름 정리 및 반환
        # df columns depend on the API response, OpenDartReader typically uses standard names:
        #  rcept_no, reprt_code, bsns_year, corp_code, sj_div, sj_nm, account_id, account_nm, account_detail,
        #  thstrm_nm, thstrm_amount, frmtrm_nm, frmtrm_amount, bfefrmtrm_nm, bfefrmtrm_amount, ord, currency
        
        records = [self._parse_financial_row(row, corp_code, year, reprt_code) for _, row in df.iterrows()]
        return records

    def run_batch_for_year(self, year: int, limit: Optional[int] = None):
        """지정된 연도의 전체 상장사에 대해 4개 분기 재무제표 배치를 수행합니다."""
        # 상장사만 조회
        cur = self.db_writer.conn.cursor()
        cur.execute("SELECT corp_code, stock_code FROM dart_corp_code WHERE stock_code IS NOT NULL")
        listed_corps = cur.fetchall()

        if limit is not None:
            listed_corps = listed_corps[:limit]

        # 각 보고서 유형별로 배치 정의
        for reprt in self.reprt_codes:
            batch_id = f"dart_fs_{year}_{reprt}"
            codes = [row[0] for row in listed_corps]
            stock_code_map = {row[0]: normalize_stock_code(row[1]) for row in listed_corps}
            
            # DB 진행률 트래킹 초기화
            self.db_writer.init_batch(batch_id, codes)
            pending_codes = self.db_writer.get_pending_codes(batch_id)
            
            if not pending_codes:
                logger.info(f"배치 {batch_id} 는 이미 모두 완료되었습니다. 건너뜁니다.")
                continue

            logger.info(f"배치 {batch_id} 시작: 총 {len(pending_codes)}개 기업 남음")

            for codes_chunk in progress_bar(
                self._chunked(pending_codes, self.chunk_size),
                desc=batch_id,
                leave=False,
            ):
                chunk_started_at = time.perf_counter()
                chunk_financial_rows: List[dict] = []
                chunk_valuation_rows: List[dict] = []
                chunk_done_codes: List[str] = []
                chunk_error_rows: List[tuple[str, str]] = []
                fatal_error: Optional[Exception] = None

                try:
                    chunk_records_map = self._fetch_financial_statements_multi(codes_chunk, year, reprt)
                except Exception as chunk_err:
                    err_str = str(chunk_err)
                    if '초과' in err_str or 'rate' in err_str.lower() or '한도' in err_str:
                        logger.critical("Open DART 일일 호출 한도 초과(추정)로 전체 배치를 완전히 중단합니다.")
                        raise RuntimeError("Open DART API 일일 한도 초과") from chunk_err
                    logger.warning("청크 조회 실패, 단건 폴백 시도: %s", err_str)
                    chunk_records_map = {code: [] for code in codes_chunk}

                for corp_code in codes_chunk:
                    try:
                        records = chunk_records_map.get(corp_code, [])
                        if not records:
                            records = self._fetch_financial_statements(corp_code, year, reprt)

                        if records:
                            sc = stock_code_map.get(corp_code)
                            for r in records:
                                r['stock_code'] = sc
                            chunk_financial_rows.extend(records)

                            # 재무제표 적재 직후 밸류에이션 지표를 계산해 함께 적재한다.
                            if sc and self._stock_exists(sc):
                                valuation_row = calculate_valuation_record(
                                    conn=self.db_writer.conn,
                                    records=records,
                                    stock_code=sc,
                                    corp_code=corp_code,
                                    bsns_year=year,
                                    reprt_code=reprt,
                                    dart_client=self.dart,
                                )
                                chunk_valuation_rows.append(valuation_row)

                        chunk_done_codes.append(corp_code)
                        logger.debug(f"{corp_code} 적재 성공 (데이터 {len(records)}건)")

                    except Exception as e:
                        err_str = str(e)
                        chunk_error_rows.append((corp_code, err_str))
                        logger.error(f"{corp_code} 수집 중 에러: {err_str}")

                        if '초과' in err_str or 'rate' in err_str.lower() or '한도' in err_str:
                            logger.critical("Open DART 일일 호출 한도 초과(추정)로 전체 배치를 완전히 중단합니다.")
                            fatal_error = RuntimeError("Open DART API 일일 한도 초과")
                            break

                if chunk_financial_rows:
                    self.db_writer.upsert_financial_statements(chunk_financial_rows, commit=False, show_progress=False)
                if chunk_valuation_rows:
                    self.db_writer.upsert_valuation_metrics(chunk_valuation_rows, commit=False, show_progress=False)
                for corp_code in chunk_done_codes:
                    self.db_writer.mark_done(batch_id, corp_code, commit=False)
                for corp_code, err_str in chunk_error_rows:
                    self.db_writer.mark_error(batch_id, corp_code, err_str, commit=False)

                self.db_writer.conn.commit()
                logger.debug(
                    "%s 청크 처리 완료: corp=%d, fs=%d, valuation=%d, elapsed=%.3fs",
                    batch_id,
                    len(codes_chunk),
                    len(chunk_financial_rows),
                    len(chunk_valuation_rows),
                    time.perf_counter() - chunk_started_at,
                )

                if fatal_error is not None:
                    raise fatal_error

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    collector = DartFinancialsCollector()
    try:
        collector.update_corp_codes()
        collector.run_batch_for_year(2025)
    finally:
        collector.close()
