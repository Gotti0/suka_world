import xgboost as xgb
import pandas as pd
import numpy as np
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class XGBMetaModel:
    """
    메타 레이블링(Meta-Labeling)을 위한 XGBoost 모델 클래스입니다.
    기본 전략의 신호가 '참(Success)'일 확률을 예측합니다.
    """
    def __init__(self, model_path: str = None):
        self.model = xgb.XGBClassifier()
        if model_path:
            try:
                self.model.load_model(model_path)
                logger.info(f"모델을 로드했습니다: {model_path}")
            except:
                logger.warning("기존 모델을 찾을 수 없어 초기 상태로 시작합니다.")

    def prepare_features(self, sentiment: int, market_data: Dict[str, float]) -> pd.DataFrame:
        """분석 결과와 시장 데이터를 특징량(Feature)으로 변환합니다."""
        features = {
            "sentiment": sentiment,
            "vix": market_data.get("vix", 20.0),
            "kospi_mom": market_data.get("kospi_mom", 0.0),
            "volatility": market_data.get("volatility", 0.01)
        }
        return pd.DataFrame([features])

    def predict_success_prob(self, features: pd.DataFrame) -> float:
        """해당 신호가 성공할 확률(0~1)을 반환합니다."""
        try:
            # 모델이 학습되지 않은 경우 기본값 0.5 반환
            if not hasattr(self.model, "n_features_in_"):
                return 0.5
            probs = self.model.predict_proba(features)
            return float(probs[0][1])
        except Exception as e:
            logger.error(f"예측 중 오류 발생: {e}")
            return 0.5

class FractionalKellySizer:
    """
    켈리 공식을 활용하여 적정 투자 비중을 산출합니다.
    자산의 변동성을 고려하여 Fractional Kelly(분수 켈리)를 적용합니다.
    """
    def __init__(self, fraction: float = 0.5):
        self.fraction = fraction

    def calculate_size(self, win_prob: float, win_loss_ratio: float = 1.5) -> float:
        """
        켈리 공식: f* = (p * (b + 1) - 1) / b
        p: 승률 (win_prob)
        b: 손익비 (win_loss_ratio)
        """
        if win_prob <= 0 or win_loss_ratio <= 0:
            return 0.0
            
        kelly_f = (win_prob * (win_loss_ratio + 1) - 1) / win_loss_ratio
        
        # 음수면 투자 안 함, 양수면 fraction 적용 및 최대 20% 제한
        size = max(0, kelly_f) * self.fraction
        return min(size, 0.2) # 종목당 최대 비중 20% 제한

if __name__ == "__main__":
    # 테스트
    sizer = FractionalKellySizer(fraction=0.3)
    prob = 0.65 # 승률 65% 가정
    bet_size = sizer.calculate_size(prob)
    print(f"승률 {prob*100}%일 때 권장 비중: {bet_size*100:.2f}%")
