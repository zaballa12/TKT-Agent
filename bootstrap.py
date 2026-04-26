from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_PACKAGES = PROJECT_ROOT / ".python_packages"

if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))

load_dotenv(PROJECT_ROOT / ".env")
