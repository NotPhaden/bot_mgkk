import os
import sys
from pathlib import Path

# Ensure bot.py can be imported from the repository root in CI.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never require or expose a real Discord token in CI.
os.environ.setdefault("DISCORD_TOKEN", "pytest-dummy-token")
