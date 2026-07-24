from pathlib import Path
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from portable_knowledge_qa.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["inspect", *sys.argv[1:]]))
