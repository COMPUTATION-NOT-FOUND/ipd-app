"""Render the student guide to PDF.

    pip install playwright        # once (a developer tool, not needed to run the app)
    python docs/guide/build_pdf.py

Reads docs/guide/student-guide.html and writes docs/student-guide.pdf, using the Microsoft Edge
or Chrome already installed on the machine (no browser download needed).
"""
import os
import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / 'student-guide.html'
TARGET = HERE.parent / 'student-guide.pdf'


def build():
    with sync_playwright() as p:
        browser = None
        for channel in ('msedge', 'chrome'):
            try:
                browser = p.chromium.launch(channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SOURCE.as_uri())
        page.pdf(path=str(TARGET), format='A4', print_background=True, prefer_css_page_size=True)
        browser.close()
    print(f"Wrote {os.path.relpath(TARGET)}")


if __name__ == '__main__':
    build()
