"""Put this test dir on sys.path so `import ragas_eval` works no matter where
pytest is invoked from (repo root, tool dir, or here)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
