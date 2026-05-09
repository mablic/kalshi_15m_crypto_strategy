from pathlib import Path
import json
import warnings

warnings.filterwarnings("ignore")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MODELS_DIR = _REPO_ROOT / "models"


def LOAD_PARAMETERS(model_name: str, filepath: Path | None = None):
    if filepath is None:
        filepath = _MODELS_DIR / f"{model_name}.json"
    
    with open(filepath, 'r') as f:
        params_serializable = json.load(f)
    
    # Convert lists back to tuples
    parameters = {}
    for feature, values in params_serializable.items():
        parameters[feature] = {
            'good': tuple(values['good']),
            'bad': tuple(values['bad'])
        }
    
    return parameters