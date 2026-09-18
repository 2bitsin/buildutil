import sys
from pathlib import Path

# Put tools/ on the path so `import buildutil...` resolves when pytest
# runs from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
