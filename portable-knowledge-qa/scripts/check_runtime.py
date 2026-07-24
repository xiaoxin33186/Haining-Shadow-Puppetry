from __future__ import annotations

import importlib.util
import json
import platform
import sys


def main() -> int:
    optional_dependencies = {
        "openpyxl": importlib.util.find_spec("openpyxl") is not None,
        "xlrd": importlib.util.find_spec("xlrd") is not None,
    }
    payload = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_supported": sys.version_info >= (3, 10),
        "optional_dependencies": optional_dependencies,
        "format_support": {
            "md_txt_docx_json": True,
            "xlsx_xlsm": optional_dependencies["openpyxl"],
            "xls": optional_dependencies["xlrd"],
            "pdf_ocr": False,
        },
        "automatic_install": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["python_supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
