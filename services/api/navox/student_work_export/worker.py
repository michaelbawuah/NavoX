"""One resource-limited process per export; input and PDF stay in anonymous pipes."""

import json
import sys

from navox.student_work_export.render import RenderError, render
from navox.student_work_export.schemas import MAX_REQUEST_BYTES, ExportInput


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if not 0 < len(raw) <= MAX_REQUEST_BYTES:
            raise RenderError("input_limit")
        payload = ExportInput.model_validate_json(raw)
        output = render(payload)
        sys.stdout.buffer.write(output)
    except RenderError as error:
        code = str(error)
        if code not in {
            "size_limit",
            "unsupported_text",
            "page_limit",
            "metadata_limit",
            "renderer_unavailable",
            "input_limit",
        }:
            code = "renderer_unavailable"
        sys.stdout.buffer.write(json.dumps({"error": code}).encode())
    except Exception:
        sys.stdout.buffer.write(b'{"error":"renderer_unavailable"}')


if __name__ == "__main__":
    main()
