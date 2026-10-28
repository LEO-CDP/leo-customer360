import sys
from pathlib import Path

# The service's importable modules live under src/ (run as `uvicorn app:app
# --app-dir src`); put it on sys.path so tests import them the same way.
sys.path.insert(0, str(Path(__file__).parent / "src"))
