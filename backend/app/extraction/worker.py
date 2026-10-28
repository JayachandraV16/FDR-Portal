"""Child process entry point: python -E -s -m app.extraction.worker <json args>, document on stdin.

Runs in a separate process so a malformed or hostile file can crash or hang only this process, which
the parent kills on timeout. It is started WITHOUT the application's environment (no database URL, no
secret key) and imports nothing but the extraction package and pypdf. The result is one JSON object
on stdout; the document is never written anywhere.
"""

import dataclasses
import json
import sys

from app.extraction.ocr import NoOcr, TesseractEngine
from app.extraction.text import LocalTextExtractor
from app.extraction.types import ErrorCode, Limits, TextResult

MAX_INPUT = 64 * 1024 * 1024


def main() -> int:
    try:
        args = json.loads(sys.argv[1])
        limits = Limits(**args["limits"])
        command = args.get("ocr_command")
        ocr = TesseractEngine(command, args.get("ocr_language", "eng")) if command else NoOcr()
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            result = TextResult(error=ErrorCode.WORKER_FAILED)
        else:
            result = LocalTextExtractor(ocr).extract(data, args["content_type"], limits)
    except Exception:
        result = TextResult(error=ErrorCode.WORKER_FAILED)
    sys.stdout.write(json.dumps(dataclasses.asdict(result)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
