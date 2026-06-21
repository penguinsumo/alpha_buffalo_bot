import os
from pathlib import Path
from dotenv import load_dotenv

def load_env_safely():
    # บังคับให้หาจาก Root ของโปรเจกต์เสมอ
    root_dir = Path(__file__).resolve().parent.parent.parent
    load_dotenv(dotenv_path=root_dir / ".env")
