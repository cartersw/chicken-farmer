"""Desktop entry point that also gives spawned validation workers the source path."""
from pathlib import Path
import sys

project = Path(__file__).resolve().parent
sys.path.insert(0, str(project / "src"))

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from cs2_data.desktop import main
    raise SystemExit(main())
