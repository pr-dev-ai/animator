"""
Launcher for ComfyUI that prevents its types.py from shadowing stdlib.

Problem: ComfyUI ships a types.py in its root directory. When the interpreter
runs `python3 main.py` from inside the ComfyUI directory, that directory lands
at sys.path[0], so early stdlib bootstrapping (functools -> types, enum -> types)
picks up ComfyUI's types.py instead of the real one, causing:
  ImportError: cannot import name 'MappingProxyType' from partially initialized
  module 'types' (/app/workspace/ComfyUI/types.py)

Fix: this file lives at /app/ (not inside the ComfyUI directory). Python adds
/app to sys.path[0]; stdlib modules load cleanly and are cached in sys.modules.
We then append the ComfyUI directory and delegate to main.py via runpy. By the
time runpy inserts ComfyUI's dir at sys.path[0], all conflicting stdlib modules
are already cached — nothing re-imports them.
"""
import sys
import os
import runpy

COMFYUI_DIR = "/app/workspace/ComfyUI"

# Append AFTER stdlib paths so the stdlib types module is found first.
sys.path.append(COMFYUI_DIR)

# ComfyUI uses relative file access (models/, output/, etc.) so chdir here.
os.chdir(COMFYUI_DIR)

# Argparse inside main.py reads sys.argv[0] as the program name.
sys.argv[0] = "main.py"

runpy.run_path(os.path.join(COMFYUI_DIR, "main.py"), run_name="__main__")
