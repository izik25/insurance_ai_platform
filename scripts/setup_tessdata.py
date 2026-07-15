"""Fetch Hebrew + English Tesseract language data into the project's
tessdata/ directory.

The Tesseract binary must already be installed and on PATH (its bundled
tessdata usually only ships 'eng'; we keep a project-local copy instead of
writing into the (often admin-only) system install directory).

Usage: python scripts/setup_tessdata.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from urllib.request import urlretrieve

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESSDATA_DIR = PROJECT_ROOT / "tessdata"
HEB_URL = "https://github.com/tesseract-ocr/tessdata/raw/main/heb.traineddata"


def main() -> None:
    TESSDATA_DIR.mkdir(exist_ok=True)

    heb_path = TESSDATA_DIR / "heb.traineddata"
    if not heb_path.exists():
        print(f"Downloading {HEB_URL} ...")
        urlretrieve(HEB_URL, heb_path)  # noqa: S310 - fixed, trusted URL
    else:
        print("heb.traineddata already present, skipping download.")

    eng_path = TESSDATA_DIR / "eng.traineddata"
    if not eng_path.exists():
        system_eng = shutil.which("tesseract")
        if system_eng is None:
            print(
                "WARNING: tesseract not found on PATH; cannot locate the "
                "bundled eng.traineddata to copy. Install Tesseract OCR first.",
                file=sys.stderr,
            )
        else:
            bundled_tessdata = Path(system_eng).parent / "tessdata" / "eng.traineddata"
            if bundled_tessdata.exists():
                shutil.copy(bundled_tessdata, eng_path)
            else:
                print(f"WARNING: {bundled_tessdata} not found.", file=sys.stderr)
    else:
        print("eng.traineddata already present, skipping copy.")

    print(f"tessdata ready at {TESSDATA_DIR}")


if __name__ == "__main__":
    main()
