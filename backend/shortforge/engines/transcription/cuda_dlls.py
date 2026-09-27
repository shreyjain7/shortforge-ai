"""Make pip-installed NVIDIA runtime DLLs (cuBLAS, cuDNN) discoverable on Windows.

CTranslate2 wheels do not bundle cuBLAS/cuDNN. The ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``
wheels place DLLs in ``site-packages/nvidia/*/bin`` which is not on the default DLL search path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False
_handles: list[object] = []


def ensure_cuda_dlls() -> list[str]:
    global _done
    added: list[str] = []
    if _done or os.name != "nt":
        return added
    _done = True
    for base in {Path(p) for p in sys.path if p and "site-packages" in p}:
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            if bin_dir.is_dir():
                try:
                    _handles.append(os.add_dll_directory(str(bin_dir)))
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                    added.append(str(bin_dir))
                except OSError:
                    pass
    return added
