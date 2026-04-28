from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_PACKAGES = PROJECT_ROOT / ".python_packages"


def ensure_local_packages() -> None:
    if LOCAL_PACKAGES.exists():
        local_packages_path = str(LOCAL_PACKAGES)
        if local_packages_path not in sys.path:
            sys.path.insert(0, local_packages_path)
