# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('templates', 'templates'), ('static', 'static'), ('modules', 'modules'), ('version.py', '.')]
binaries = []
hiddenimports = ['RNS.Interfaces.LocalInterface', 'RNS.Interfaces.TCPInterface']

# La libreria RNS viene inclusa nel binario, ma i comandi CLI
# (rnid, rnstatus, rnpath, rnprobe) restano quelli installati sul
# sistema dell'utente, insieme alla configurazione ~/.reticulum.
tmp_ret = collect_all('RNS')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# Evita che l'ambiente di build trascini dipendenze non usate.
# numpy/astroid/pylint erano la causa del .run da 107 MB.
EXCLUDES = [
    'numpy', 'scipy', 'pandas', 'matplotlib', 'PIL', 'tkinter',
    'astroid', 'pylint', 'IPython', 'jupyter', 'pytest', 'sphinx',
    'cv2', 'PyQt5', 'PySide6', 'gi',
    'test', 'unittest', 'pydoc', 'pydoc_data', 'distutils',
    'setuptools', 'pip', 'wheel',
]


a = Analysis(
    ['rns_manager.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='rns_manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
