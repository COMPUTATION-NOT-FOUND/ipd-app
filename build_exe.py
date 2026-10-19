"""Build the double-click version of the app with PyInstaller.

    pip install -r requirements-build.txt
    python build_exe.py

Produces dist/ipd-app.exe on Windows (dist/ipd-app on macOS / Linux): one file that needs no
Python installed. Build on each OS you want to support (the GitHub Actions workflow in
.github/workflows/build-app.yml does all three). The .env file is NOT bundled: the class token
must not be baked into a file anyone can unpack; students put their .env next to the exe.
"""
import os
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))


def build():
    os.chdir(HERE)
    sep = os.pathsep   # PyInstaller's --add-data separator differs per OS (';' on Windows)
    PyInstaller.__main__.run([
        'launcher.py',
        '--name', 'ipd-app',
        '--onefile',
        '--console',             # the window shows the address and is how students stop the app
        '--noconfirm',
        '--clean',
        f'--add-data=templates{sep}templates',
        f'--add-data=static{sep}static',
        # Imported inside functions; listed so the bundle never misses them.
        '--hidden-import=app',
        '--hidden-import=isolation',
        '--hidden-import=hub_client',
        '--hidden-import=waitress',
    ])
    exe = os.path.join('dist', 'ipd-app.exe' if sys.platform == 'win32' else 'ipd-app')
    print(f"\nBuilt {exe}")


if __name__ == '__main__':
    build()
