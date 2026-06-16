# PyInstaller build spec for OPEN-IPTV.
# Build a single-file Windows executable with:
#     pip install pyinstaller
#     pyinstaller OPEN-IPTV.spec
# The result is dist/OPEN-IPTV.exe. User data (config.json, caches) is created
# next to the exe, not inside it.

block_cipher = None

a = Analysis(
    ['iptv_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[
        'views.groups', 'views.channels', 'views.search',
        'views.favourites', 'views.filters', 'views.settings',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OPEN-IPTV',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
