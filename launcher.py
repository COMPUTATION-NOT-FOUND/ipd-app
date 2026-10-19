"""Entry point for the bundled, double-click version of the app (see build_exe.py).

`python app.py` stays the way to run from source; this file is what PyInstaller turns into
ipd-app.exe. It differs from app.py only in two ways:

* multiprocessing.freeze_support() runs FIRST. Sandbox mode (isolation.py) starts worker
  processes, and in a bundled exe on Windows each worker is the exe itself started again. Without
  this call every worker would start another copy of the app instead of doing its job.
* the browser opens on the app automatically.
"""
import multiprocessing
import sys


def run():
    multiprocessing.freeze_support()
    import app
    # `ipd-app.exe --no-browser` starts the server without opening a browser tab.
    app.main(open_browser='--no-browser' not in sys.argv)


if __name__ == '__main__':
    run()
