"""
Runtime dependency checker and auto-installer.

Usage at the top of each module (before any 3rd-party imports):

    from _dep_checker import ensure_deps

    ensure_deps({
        "aiohttp": "aiohttp",
        "fastmcp": "fastmcp",
    })

    import aiohttp          # safe now
    from fastmcp import ... # safe now

Suppresses pip output for clean startup.
"""
import importlib
import os
import subprocess
import sys
from typing import Dict


def ensure_deps(package_map: Dict[str, str]) -> bool:
    """Ensure all given dependencies are available; auto-install missing ones.

    Args:
        package_map: {import_name: pip_package_name} mapping.
                     e.g. {"aiohttp": "aiohttp", "fastmcp": "fastmcp"}

    Returns:
        True if all dependencies are ready, False otherwise.
    """
    missing = []
    for import_name, pip_name in package_map.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return True

    auto_install = os.environ.get("AUTO_INSTALL_DEPS", "1").lower() in ("1", "true", "yes")

    if not auto_install:
        print(f"[WARNING] Missing dependencies: {', '.join(missing)}. "
              f"Install them manually or set AUTO_INSTALL_DEPS=1", file=sys.stderr)
        return False

    print(f"[INFO] Installing missing dependencies: {', '.join(missing)} ...")

    index_url = os.environ.get("PIP_INDEX_URL", "")

    for pkg in missing:
        try:
            cmd = [sys.executable, "-m", "pip", "install", pkg, "--quiet"]
            if index_url:
                cmd.extend(["-i", index_url])
            subprocess.check_call(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[INFO]  ✓ {pkg} installed")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed to install {pkg}: {e}", file=sys.stderr)
            return False

    return True
