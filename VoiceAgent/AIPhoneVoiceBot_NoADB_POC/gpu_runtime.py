"""Expose only this venv's CUDA runtime DLLs to CTranslate2 on Windows."""
import os
import sys
from pathlib import Path

_handles = []

def configure():
    if os.name != 'nt':
        return
    root = Path(sys.prefix) / 'Lib' / 'site-packages'
    paths = list((root / 'nvidia').glob('*/bin')) + [root / 'ctranslate2']
    for path in paths:
        if path.is_dir():
            _handles.append(os.add_dll_directory(str(path)))
            os.environ['PATH'] = str(path) + os.pathsep + os.environ.get('PATH', '')
