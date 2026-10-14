import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, precision_score, roc_auc_score


class XGBoostTrainer:
    def __init__(self, scale_pos_weight: float = 2.0):
        self.model = xgb.XGBClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.04,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="auc",
            scale_pos_weight=scale_pos_weight,  # Penalize false positives heavily
            early_stopping_rounds=25,
            n_jobs=-1,
            random_state=42
        )

    def train(self, X_train, y_train, w_train, X_val, y_val, w_val):
        self.model.fit(
            X_train, y_train,
            sample_weight=w_train,
            eval_set=[(X_val, y_val)],
            sample_weight_eval_set=[w_val],
            verbose=False
        )

    def evaluate(self, X_test, y_test) -> dict:
        preds = self.model.predict(X_test)
        probs = self.model.predict_proba(X_test)[:, 1]

        try:
            auc = float(roc_auc_score(y_test, probs)) if len(np.unique(y_test)) > 1 else 0.5
        except Exception:
            auc = 0.5

        return {
            "accuracy": float(accuracy_score(y_test, preds)),
            "precision": float(precision_score(y_test, preds, zero_division=0)),
            "auc": auc
        }

    def predict(self, X):
        return self.model.predict(X)

    def predict_proba(self, X):
        return self.model.predict_proba(X)

    def save_model(self, file_path: str) -> None:
        self.model.save_model(file_path)

    def load_model(self, file_path: str) -> None:
        self.model.load_model(file_path)
