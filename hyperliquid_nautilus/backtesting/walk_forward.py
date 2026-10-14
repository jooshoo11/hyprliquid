import os
import json
from datetime import timedelta
import pandas as pd


class WalkForwardValidator:
    def __init__(self, train_days: int = 21, test_days: int = 7):
        self.train_delta = timedelta(days=train_days)
        self.test_delta = timedelta(days=test_days)

    def run(self, df: pd.DataFrame, features: list, trainer) -> list[dict]:
        df = df.sort_index()
        start_date = df.index.min()
        end_date = df.index.max()

        current_start = start_date
        metrics = []

        while current_start + self.train_delta + self.test_delta <= end_date:
            train_end = current_start + self.train_delta
            test_end = train_end + self.test_delta

            train_mask = (df.index >= current_start) & (df.index < train_end)
            test_mask = (df.index >= train_end) & (df.index < test_end)

            train_df = df[train_mask]
            test_df = df[test_mask]

            if len(train_df) < 100 or len(test_df) < 10:
                current_start += self.test_delta
                continue

            trainer.train(
                train_df[features], train_df['target'], train_df['sample_weight'],
                test_df[features], test_df['target'], test_df['sample_weight']
            )

            fold_metrics = trainer.evaluate(test_df[features], test_df['target'])
            fold_metrics['fold_start'] = str(train_end)
            metrics.append(fold_metrics)

            current_start += self.test_delta

        # Export final model logic here...
        os.makedirs('config', exist_ok=True)
        with open('config/optimized_params.json', 'w') as f:
            json.dump({"metrics": metrics, "top_features": features}, f, indent=4)

        return metrics
