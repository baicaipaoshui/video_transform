from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = SKILLS.parents[1]
EXTRACTOR = SCENARIO_ROOT / "video_extractor"
