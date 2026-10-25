# PyInstaller spec: build independently on Windows, Linux, and macOS.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['native_app.py'],
    pathex=[],
    binaries=[],
    datas=[('native_dashboard/static', 'native_dashboard/static')] + collect_data_files('trajectory_dashboard'),
    hiddenimports=['waitress', 'zmq.backend.cython'],
    hookspath=[],
    excludes=['dash', 'plotly', 'matplotlib', 'tkinter', 'IPython', 'pytest', 'notebook'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DaariDeepa', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DaariDeepa')
