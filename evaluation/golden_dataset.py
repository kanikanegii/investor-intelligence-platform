from pathlib import Path

import yaml
from pydantic import BaseModel

_DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "golden_dataset.yaml"


class GoldenExample(BaseModel):
    """A single hand-curated evaluation example."""
    question: str
    company: str
    year: int
    ground_truth: str
    expected_kpis: dict[str, str | int | None] | None = None


def load_golden_dataset(path: str | Path = _DEFAULT_PATH) -> list[GoldenExample]:
    """
    Load the golden Q&A dataset used for RAGAS evaluation.

    Args:
        path: Path to a YAML file of examples (see data/golden_dataset.yaml
            for the expected format).

    Returns:
        Parsed, validated examples.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [GoldenExample(**item) for item in raw]
