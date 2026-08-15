# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Kumiko Lamp desktop app.

    pyinstaller packaging/kumiko.spec --noconfirm

Built as a console binary deliberately.  A windowed build sets sys.stdout and
sys.stderr to None, and the export service depends on both: it re-runs itself as
a child process and parses the generator's stdout for the reasons a lamp was
refused.  `desktop/app.py` hides the window at startup when it owns it, so the
user still sees no console.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("../web/index.html", "web")]
binaries = []
hiddenimports = [
    "kumiko_lamp",
    "container", "container.server",
    "tools", "tools.dev_export_server",
    "manifold3d",
    # Imported inside split_bodies(), so the scanner cannot see them from the
    # module level -- and without them every part fails its body count.
    "scipy.sparse", "scipy.sparse.csgraph",
]

# trimesh reads data files (unit tables, template JSON) at import time and its
# submodules are resolved lazily, so a static scan misses most of it.
for pkg in ("trimesh",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("scipy.sparse.csgraph")

# Nothing in the geometry path plots, opens images, or drives a browser engine;
# they arrive as optional imports of trimesh and cost hundreds of megabytes.
excludes = [
    "matplotlib", "PIL", "IPython", "pytest", "playwright", "pydantic",
    "notebook", "jupyter", "pandas", "sympy", "networkx", "setuptools",
    "scipy.optimize", "scipy.interpolate", "scipy.signal", "scipy.stats",
    "scipy.integrate", "scipy.fft", "scipy.io", "scipy.ndimage",
]

a = Analysis(
    ["../desktop/app.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KumikoLamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # see the module docstring; the window is hidden at runtime
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="kumiko.ico",
)

# onedir, not onefile: onefile re-extracts every dependency to a temp directory
# on each launch, which at this size is tens of seconds of startup for no gain
# once an installer is placing the files anyway.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KumikoLamp",
)
