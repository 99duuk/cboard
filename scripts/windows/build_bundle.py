"""Build the account-free Windows runtime installer."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_release import build


if __name__ == '__main__':
    build('windows')
