"""Private stdin/stdout protocol for a bounded calendar parser; never log input."""

import base64
import json
import resource
import sys
from datetime import datetime

from navox.connectors.builtin.lms_feed import parse_feed
from navox.lms_feeds.transport import FeedError


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    request = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024))
    result = parse_feed(
        base64.b64decode(request["content"], validate=True),
        timezone=request["timezone"],
        all_day_due_time=request["all_day_due_time"],
        now=datetime.fromisoformat(request["now"]),
        private_url=request["private_url"],
        platform=request["platform"],
    )
    sys.stdout.write(result.model_dump_json())


if __name__ == "__main__":
    try:
        main()
    except FeedError as error:
        sys.stdout.write(json.dumps({"error": error.code}))
        sys.exit(2)
    except Exception:
        sys.exit(2)
