"""
Entry point: python run.py [topic_hint]
"""
import sys
from app.engine import run_pipeline


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else ""
    run_pipeline(topic)
