"""P24 가격 패널을 만든다 (T3).

크로스체크 DB 는 다른 프로젝트(brent_benfold)의 것이므로 읽기 전용 URI 로만 연다.

산출물
- data/p24_daily_close.csv   : 일별 종가(자산별 열, 거래가 없는 날은 빈칸)
- data/p24_weekly_close.csv  : 주간 종가(금요일 기준, 그 주 마지막 거래일 종가)
- data/p24_checks.json       : 점검 결과(월물 교체 비교, ICB 대응 상관, 결측)

주간 시점 규약
- 주 t 의 신호에는 한국시간 목요일 23:59 까지 게시된 영상만 쓴다.
- 주 t 의 포지션은 금요일 종가에 잡고 다음 주 금요일 종가까지 보유한다.
- DRAM 지수·LME 현물·런던 금은 종가만 있어 시가 체결을 쓸 수 없고, 가장 이른 금요일 종가
  (DRAM, 한국시간 오후)도 목요일 마감 이후이므로 미래 참조가 생기지 않는다.

    python -m strategy.price_panel
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CROSSCHECK_DB = BASE_DIR.parent / "brent_benfold" / "data" / "cybos_crosscheck.db"
P24_PATH = BASE_DIR / "strategy" / "p24.yaml"
OUT_DIR = BASE_DIR / "data"
START = "20200101"

# ICB 대응이 이름만으로 불확실한 지수와 비교할 미국 업종 지수
ICB_PROBES = {
    "DJTFVS": ["DJUSSB", "BANK", "DJTBAK", "DJTINN"],
    "DJTNCG": ["DJUSFB", "DJTFOB", "DJTRET", "DJTHCA"],
    "DJTIGS": ["DJUSHR", "DJUSEC", ".DJT", "DJTBAS"],
    "DJTBAS": ["DJUSST", "DJUSNF", "DJTCHE", "CM@MCD"],
}


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{CROSSCHECK_DB.as_posix()}?mode=ro", uri=True)


def load_overseas(con, codes) -> pd.DataFrame:
    q = (f"select us_code as code, date, close from overseas_daily_raw "
         f"where date >= ? and us_code in ({','.join('?' * len(codes))})")
    return pd.read_sql(q, con, params=[START, *codes])


def load_domestic(con, codes) -> pd.DataFrame:
    q = (f"select stock_code as code, date, close from daily_ohlcv "
         f"where date >= ? and stock_code in ({','.join('?' * len(codes))})")
    return pd.read_sql(q, con, params=[START, *codes])


def to_wide(long: pd.DataFrame) -> pd.DataFrame:
    long = long.assign(date=pd.to_datetime(long["date"], format="%Y%m%d"))
    return long.pivot(index="date", columns="code", values="close").sort_index()


def equal_weight_index(closes: pd.DataFrame) -> pd.Series:
    """구성 자산의 일간 수익률을 동일가중 평균해 1에서 시작하는 지수로 만든다."""
    rets = closes.pct_change(fill_method=None)
    return (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()


def weekly(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("W-FRI").last()


def main() -> None:
    p24 = yaml.safe_load(P24_PATH.read_text(encoding="utf-8"))
    assets = p24["assets"]
    raw_of = {a["id"]: ([a["raw_code"]] if isinstance(a["raw_code"], str) else a["raw_code"])
              for a in assets if "raw_code" in a}
    kr_codes = sorted({c for a in assets if a.get("source") == "kr_etf" for c in a["codes"]})
    ov_codes = sorted({c for a in assets if a.get("source") != "kr_etf" for c in a["codes"]}
                      | {c for v in raw_of.values() for c in v}
                      | {c for v in ICB_PROBES.values() for c in v}
                      | {"SOX", "DJGT"})
    con = connect()
    ov = to_wide(load_overseas(con, ov_codes))
    kr = to_wide(load_domestic(con, kr_codes))
    con.close()

    def series(codes, table):
        cols = [table[c] for c in codes]
        return cols[0] if len(cols) == 1 else equal_weight_index(pd.concat(cols, axis=1))

    daily = pd.DataFrame(index=ov.index.union(kr.index))
    for a in assets:
        daily[a["id"]] = series(a["codes"], kr if a.get("source") == "kr_etf" else ov)
    daily["SOX"] = ov["SOX"]
    daily["DJGT"] = ov["DJGT"]
    for aid, codes in raw_of.items():
        daily[f"{aid}_RAW"] = series(codes, ov)
    daily = daily.dropna(how="all")

    wk = weekly(daily.ffill(limit=5))
    wret = wk.pct_change(fill_method=None)

    checks = {"roll_compare": {}, "icb_probe": {}, "coverage": {}}
    since = wret.loc["2021-05-01":]
    for aid in raw_of:
        pair = since[[aid, f"{aid}_RAW"]].dropna()
        cum = (1 + pair).prod() - 1
        checks["roll_compare"][aid] = {
            "weekly_corr": round(float(pair.corr().iloc[0, 1]), 3),
            "cum_return_used": round(float(cum[aid]), 3),
            "cum_return_raw": round(float(cum[f"{aid}_RAW"]), 3),
            "largest_weekly_gaps": {
                d.strftime("%Y-%m-%d"): round(float(v), 3)
                for d, v in (pair[aid] - pair[f"{aid}_RAW"]).abs().nlargest(3).items()
            },
        }
    wk_ov = weekly(ov.ffill(limit=5)).pct_change(fill_method=None).loc["2021-05-01":]
    for target, probes in ICB_PROBES.items():
        corr = wk_ov[[target, *probes]].corr()[target].drop(target)
        checks["icb_probe"][target] = {k: round(float(v), 3) for k, v in corr.sort_values(ascending=False).items()}
    for col in daily.columns:
        s = daily[col].loc["2021-05-01":]
        checks["coverage"][col] = {"first": str(s.first_valid_index().date()) if s.notna().any() else None,
                                   "last": str(s.last_valid_index().date()) if s.notna().any() else None,
                                   "days": int(s.notna().sum())}

    OUT_DIR.mkdir(exist_ok=True)
    daily.to_csv(OUT_DIR / "p24_daily_close.csv", float_format="%.6f")
    wk.to_csv(OUT_DIR / "p24_weekly_close.csv", float_format="%.6f")
    (OUT_DIR / "p24_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: checks[k] for k in ("roll_compare", "icb_probe")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
