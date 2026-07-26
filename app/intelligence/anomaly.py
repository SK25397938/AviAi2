from sklearn.ensemble import IsolationForest
import numpy as np

class FlightAnomalyDetector:
    def __init__(self):
        # Isolation Forest is perfect for unknown anomalies
        self.model = IsolationForest(
            n_estimators=200,
            contamination=0.03,  # ~3% flights considered abnormal
            random_state=42
        )
        self.trained = False

    def train(self, feature_vectors: list):
        """
        Train on normal flight behavior
        feature_vectors = list of [altitude, speed, heading, vertical]
        """
        if not feature_vectors:
            return

        X = np.array(feature_vectors)
        self.model.fit(X)
        self.trained = True

    def score(self, feature_vector: list):
        """
        Returns anomaly score & flag
        """
        if not self.trained:
            return {
                "anomaly": False,
                "score": 0.0,
                "confidence": 0.0
            }

        X = np.array([feature_vector])

        # IsolationForest score: lower = more abnormal
        raw_score = self.model.decision_function(X)[0]
        anomaly_flag = self.model.predict(X)[0] == -1

        confidence = min(abs(raw_score), 1.0)

        return {
            "anomaly": anomaly_flag,
            "score": round(raw_score, 4),
            "confidence": round(confidence, 2)
        }
