"""Fixed imported entrypoint. No model-generated code and no host execution."""
import os
from pathlib import Path
import sys

sys.path.insert(0, '/work/package')
from app.allocate import allocate, BusinessInputError

try:
    allocate(Path(os.environ['TM_SKILL_INPUT_DIR']), Path(os.environ['TM_SKILL_OUTPUT_DIR']))
except BusinessInputError as error:
    print(str(error), file=sys.stderr)
    sys.exit(2)
