"""Put the experiment_loop package dir on sys.path so the flat referee modules
(decision, sampling, ...) import the same way they do at runtime (evaluate.py
inserts HERE on sys.path). Keeps tests aligned with the real import surface."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
