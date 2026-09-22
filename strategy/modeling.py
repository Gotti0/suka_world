"""P24 고급 퀀트 모델링 및 앙상블 모듈 (T9)

구글 노트북LM(계량 투자 전략과 백테스팅 분석의 기초)의 3대 핵심 이론을 구현:
1. Grinold & Kahn: 알파 스케일링, Connor(1997) 베이지안 수축, 경기순환 역발상 캘리브레이션
2. Marcos López de Prado: EWM 일별 변동성 연동 동적 트리플 배리어, 메타 레이블링(XGBoost)
3. Ernie Chan: 3-State 시장 국면(Regime) 게이팅, Prado Z-Score 확률 매핑 및 하프 켈리(Half-Kelly) 사이징
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.mixture import GaussianMixture
import xgboost as xgb

logger = logging.getLogger(__name__)


class P24GrinoldKahnOptimizer:
    """Grinold & Kahn의 액티브 포트폴리오 운용 이론에 기반한 알파 스케일링 및 수축기"""

    @staticmethod
    def bayesian_shrink_ic(raw_ic: float, t_periods: int = 280) -> float:
        """Connor (1997) 폐쇄형 베이지안 수축 공식
        IC_shrunk = IC_sample * (T * IC^2 / (T * IC^2 + 1))
        """
        t_ic2 = t_periods * (raw_ic ** 2)
        shrinkage_factor = t_ic2 / (t_ic2 + 1.0)
        return float(raw_ic * shrinkage_factor)

    @classmethod
    def calculate_refined_alphas(
        cls,
        scores: Dict[str, float],
        ic_map: Dict[str, float],
        t_periods: int = 280,
        residual_vols: Optional[Dict[str, float]] = None,
        apply_contrarian_inversion: bool = True
    ) -> Dict[str, float]:
        """정제된 알파 산출:
        alpha_n = omega_n * IC_shrunk_n * z_n
        음(-)의 IC를 갖는 경기순환 자산군은 부호가 자연스럽게 역발상 숏으로 반전됩니다.
        """
        refined_alphas = {}
        for asset, score in scores.items():
            sample_ic = ic_map.get(asset, 0.0)
            shrunk_ic = cls.bayesian_shrink_ic(sample_ic, t_periods)
            
            # 음수 IC 역발상 처리
            effective_ic = shrunk_ic if apply_contrarian_inversion else max(0.0, shrunk_ic)
            
            vol = residual_vols.get(asset, 1.0) if residual_vols else 1.0
            refined_alphas[asset] = float(vol * effective_ic * score)
            
        return refined_alphas

    @staticmethod
    def calculate_active_weights(alphas: Dict[str, float], max_gross_leverage: float = 1.0) -> Dict[str, float]:
        """알파 벡터를 기반으로 총 레버리지 제한 하의 동일/비례 가중치 산출"""
        abs_sum = sum(abs(a) for a in alphas.values())
        if abs_sum == 0:
            return {a: 0.0 for a in alphas}
            
        return {a: (alpha / abs_sum) * max_gross_leverage for a, alpha in alphas.items()}


class DynamicTripleBarrierLabeler:
    """Marcos López de Prado (AFML)의 동적 트리플 배리어 레이블링"""

    @staticmethod
    def compute_daily_volatility(close_series: pd.Series, span: int = 100) -> pd.Series:
        """지수 가중 이동 표준편차(EWM Std)를 활용한 동적 일별 변동성 (sigma_t)"""
        returns = close_series.pct_change(fill_method=None)
        vol = returns.ewm(span=span).std().fillna(returns.std()).fillna(0.01)
        return vol

    @classmethod
    def label_meta_events(
        cls,
        daily_prices: pd.DataFrame,
        weekly_signals: pd.DataFrame,
        pt_mult: float = 2.0,
        sl_mult: float = 1.0,
        holding_days: int = 5
    ) -> pd.DataFrame:
        """금요일 진입 시점의 주간 신호에 대해 5영업일 동안의 트리플 배리어 도달 여부를 레이블링합니다.
        최초 도달 배리어가 익절선이면 y_meta = 1, 손절선 또는 만기 마이너스면 y_meta = 0
        """
        labeled_records = []
        trading_dates = daily_prices["date"].sort_values().tolist()
        date_to_idx = {d: i for i, d in enumerate(trading_dates)}
        
        daily_prices_indexed = daily_prices.set_index("date")
        
        for _, sig_row in weekly_signals.iterrows():
            entry_date = sig_row["date"]
            asset = sig_row["asset_id"]
            side = int(sig_row["pos_base_L4"]) if "pos_base_L4" in sig_row else int(sig_row.get("sign_L4", 0))
            
            if side == 0 or entry_date not in date_to_idx or asset not in daily_prices_indexed.columns:
                continue
                
            entry_idx = date_to_idx[entry_date]
            if entry_idx + 1 >= len(trading_dates):
                continue
                
            p0 = daily_prices_indexed.loc[entry_date, asset]
            if pd.isna(p0) or p0 <= 0:
                continue
                
            # 국소 변동성 추정
            hist_prices = daily_prices_indexed[asset].iloc[max(0, entry_idx - 60) : entry_idx + 1]
            sigma = hist_prices.pct_change().std()
            if pd.isna(sigma) or sigma <= 0:
                sigma = 0.015
                
            pt_barrier = p0 * (1.0 + pt_mult * sigma * side) if side > 0 else p0 * (1.0 - pt_mult * sigma * abs(side))
            sl_barrier = p0 * (1.0 - sl_mult * sigma * side) if side > 0 else p0 * (1.0 + sl_mult * sigma * abs(side))
            
            end_idx = min(entry_idx + holding_days, len(trading_dates) - 1)
            future_window = trading_dates[entry_idx + 1 : end_idx + 1]
            
            touched = None
            final_p = p0
            
            for t_day in future_window:
                curr_p = daily_prices_indexed.loc[t_day, asset]
                if pd.isna(curr_p):
                    continue
                final_p = curr_p
                
                if side > 0:
                    if curr_p >= pt_barrier:
                        touched = "pt"
                        break
                    elif curr_p <= sl_barrier:
                        touched = "sl"
                        break
                else:
                    if curr_p <= pt_barrier:
                        touched = "pt"
                        break
                    elif curr_p >= sl_barrier:
                        touched = "sl"
                        break
                        
            # 만기 도달 시
            realized_return = (final_p / p0 - 1.0) * side
            if touched == "pt":
                y_meta = 1
            elif touched == "sl":
                y_meta = 0
            else:
                y_meta = 1 if realized_return > 0 else 0
                
            labeled_records.append({
                "date": entry_date,
                "asset_id": asset,
                "side": side,
                "p0": p0,
                "sigma": sigma,
                "y_meta": y_meta,
                "realized_ret": realized_return
            })
            
        return pd.DataFrame(labeled_records)


class XGBMetaClassifier:
    """메타 레이블링(Meta-Labeling) 학습 및 승률 예측을 위한 XGBoost 모델"""

    def __init__(self):
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss"
        )
        self.is_fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series):
        self.model.fit(X, y)
        self.is_fitted = True

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self.is_fitted:
            return np.full(len(X), 0.5)
        return self.model.predict_proba(X)[:, 1]


class P24RegimeDetector:
    """3-State Gaussian HMM / GMM 기반 시장 국면 감지기
    State 2: Low-Vol Bull (정상 모멘텀 가동, w = 1.0)
    State 1: Correction / Bear (노이즈 증가, 방어적 가중치 w = 0.25)
    State 0: High-Vol Crash / Panic (유동성 런, 서킷 브레이커 w = 0.0)
    """

    def __init__(self):
        self.gmm = GaussianMixture(n_components=3, covariance_type="full", random_state=42)
        self.state_map = {}

    def fit(self, returns: pd.Series, ranges: Optional[pd.Series] = None):
        valid = pd.DataFrame({"ret": returns})
        if ranges is not None:
            valid["range"] = ranges
        else:
            valid["range"] = returns.abs()
            
        valid = valid.dropna()
        self.gmm.fit(valid)
        
        # State 특성 분류:
        # State 2 = 가장 높은 양의 평균 수익 (Bull)
        # State 0 = 가장 높은 변동성/레인지 (Panic Crash)
        # State 1 = 중간 (Bear/Correction)
        means = self.gmm.means_[:, 0]
        vols = self.gmm.covariances_[:, 0, 0]
        
        bull_state = int(np.argmax(means))
        crash_state = int(np.argmax(vols))
        remaining = [s for s in [0, 1, 2] if s != bull_state and s != crash_state]
        bear_state = remaining[0] if remaining else (0 if bull_state != 0 else 1)
        
        self.state_map = {
            bull_state: 2,   # Bull
            bear_state: 1,   # Bear
            crash_state: 0   # Crash
        }

    def predict_regime(self, current_return: float, current_range: float) -> int:
        """현재 상태의 표준 국면 (0, 1, 2) 반환"""
        feat = pd.DataFrame([[current_return, current_range]], columns=["ret", "range"])
        raw_state = int(self.gmm.predict(feat)[0])
        return self.state_map.get(raw_state, 1)

    def get_gating_multiplier(self, regime_state: int) -> float:
        """국면별 신호 게이팅 승수"""
        if regime_state == 2:
            return 1.0   # Bull: 풀가동
        elif regime_state == 1:
            return 0.25  # Bear/Correction: 감축
        else:
            return 0.0   # Crash: 전액 현금화 (서킷 브레이커)


class P24DynamicSizer:
    """Marcos López de Prado Z-Score 매핑 + Ernie Chan Half-Kelly 동적 사이징"""

    @staticmethod
    def probability_to_bet_size(prob: float, side: int) -> float:
        """Prado (AFML 10장) Z-통계량 기반 연속 베팅 크기 매핑:
        z = (p - 0.5) / sqrt(p * (1 - p))
        m = side * (2 * Phi(z) - 1)
        """
        p_clipped = np.clip(prob, 0.01, 0.99)
        z = (p_clipped - 0.5) / np.sqrt(p_clipped * (1.0 - p_clipped))
        m = float(2.0 * norm.cdf(z) - 1.0)
        return float(side * m)

    @classmethod
    def calculate_half_kelly_weight(
        cls,
        prob: float,
        side: int,
        fraction: float = 0.5,
        max_asset_cap: float = 0.15
    ) -> float:
        """Prado 베팅 강도에 Half-Kelly(0.5배) 안전 계수를 결합한 포지션 가중치"""
        raw_size = cls.probability_to_bet_size(prob, side)
        kelly_size = raw_size * fraction
        # 상하한 캡
        return float(np.clip(kelly_size, -max_asset_cap, max_asset_cap))
