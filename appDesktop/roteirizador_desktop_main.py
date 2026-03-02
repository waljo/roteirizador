from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from roteirizador_desktop.ui import run


if __name__ == "__main__":
    raise SystemExit(run())
