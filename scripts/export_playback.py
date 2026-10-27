"""Turn recorded sessions into playback files for the web player (web/sessions/*.json).

  python scripts/export_playback.py            # all of demo/*.json
  python scripts/export_playback.py demo/x.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onboardiq.playback import export_all  # noqa: E402

if __name__ == "__main__":
    for s in export_all(sys.argv[1:] or None):
        print(f"{s['id']}: {s['highlight']}")
