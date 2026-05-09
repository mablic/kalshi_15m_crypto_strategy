import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategy.models import LOAD_PARAMETERS
from scipy.stats import norm
import numpy as np

class CRYPTO_BAYESIAN_STRATEGY():
    def __init__(self, model_name: str):
        self.model_parameters = {}
        for key, value in LOAD_PARAMETERS(model_name).items():
            self.model_parameters[key] = value


    def get_model_parameters(self) -> dict:
        return self.model_parameters


    def generate_probability(self, input_values: dict) -> bool:
        try:
            log_odds = np.log(self.model_parameters['period']['good'][0] / self.model_parameters['period']['bad'][0])
            for feature, values in input_values.items():
                if feature in self.model_parameters.keys():
                    g_m, g_s = self.model_parameters[feature]['good']
                    b_m, b_s = self.model_parameters[feature]['bad']

                    # Likelihood ratio
                    p_g = norm.pdf(values, g_m, g_s + 1e-10)
                    p_b = norm.pdf(values, b_m, b_s + 1e-10)

                    if p_b > 0:
                        log_odds += np.log(p_g / p_b)

            prob = 1 / (1 + np.exp(-log_odds))
            return prob
        except Exception as e:
            print(f"Error generating probability: {e}")
            return 0.0


if __name__ == "__main__":
    model_name = "yes_bayesian"
    strategy = CRYPTO_BAYESIAN_STRATEGY(model_name)
    print(strategy.generate_probability(
        input_values={'1m_yes_dist_momentum': -26.09,    
                        '3m_yes_dist_momentum': -88.07,     
                        'momentum_5m': 0,    
                        'yes_dist': -39.74}))