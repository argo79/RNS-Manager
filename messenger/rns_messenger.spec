# -*- mode: python ; coding: utf-8 -*-
import glob
import os

from PyInstaller.utils.hooks import collect_all

datas = [('templates', 'templates'), ('static', 'static'), ('version.py', '.')]
binaries = []

hiddenimports = [
    'flask_socketio',
    'socketio',
    'engineio',
    'engineio.async_drivers.threading',
    'core.reticulum_config',
    'lxst_telephony',
    'lxst_telephony.bridge',
    'lxst_telephony.core',
    'lxst_telephony.discovery',
    'lxst_telephony.errors',
    'lxst_telephony.models',
    'lxst_telephony.native',
]

TELEPHONY_DIR = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..', 'lxst-telephony'))
telephony_license = os.path.join(TELEPHONY_DIR, 'THIRD_PARTY_LICENSES')
if os.path.isdir(telephony_license):
    datas.append((telephony_license, 'THIRD_PARTY_LICENSES'))

for package in ('RNS', 'LXMF', 'LXST', 'pycodec2'):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

# pycodec2 carica una libreria condivisa esterna al package:
#   RPATH = $ORIGIN/../pycodec2.libs
# Va copiata mantenendo la stessa struttura anche nell'archivio onefile.
try:
    import pycodec2
    pkg_dir = os.path.dirname(pycodec2.__file__)
    libs_dir = os.path.join(os.path.dirname(pkg_dir), 'pycodec2.libs')
    for lib in glob.glob(os.path.join(libs_dir, '*.so*')):
        binaries.append((lib, 'pycodec2.libs'))
except Exception:
    pass

# Evita che l'ambiente di build trascini dipendenze non usate.
# numpy resta incluso: serve a LXST e alla gestione audio.
EXCLUDES = [
    'scipy', 'pandas', 'matplotlib', 'PIL', 'tkinter',
    'astroid', 'pylint', 'IPython', 'jupyter', 'pytest', 'sphinx',
    'cv2', 'PyQt5', 'PySide6', 'gi',
    'test', 'unittest', 'pydoc', 'pydoc_data', 'distutils',
    'setuptools', 'pip', 'wheel',
]


a = Analysis(
    ['lxmf_chat.py'],
    pathex=[TELEPHONY_DIR],
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
    name='rns_messenger',
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
