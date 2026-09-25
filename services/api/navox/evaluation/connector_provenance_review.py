"""Private operator review of historical source links; never deletes data.

Export includes personal data and action text for local owner review. Suggestions
must be checked against the owner's actual history, including previous accounts.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from navox.connectors.historical_provenance import (
    HistoricalReviewError,
    ReviewManifest,
    apply_review,
    digest,
    export_review,
)
from navox.db.models import Connection, ConnectorConnection
from navox.db.session import get_session_factory


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.digest or args.apply:
        manifest = ReviewManifest.model_validate_json(Path(args.file).read_text())
        sha = digest(manifest.model_dump(mode="json"))
        if args.digest:
            return {
                "review_sha256": sha,
                "unresolved": sum(a.connection_ids is None for a in manifest.assignments),
            }
        if not args.approve_sha256:
            raise HistoricalReviewError("explicit_owner_review_digest_required")
        async with get_session_factory()() as database:
            return await apply_review(database, manifest, approved_sha256=args.approve_sha256)
    async with get_session_factory()() as database:
        owner: Connection | ConnectorConnection | None = await database.get(
            Connection, args.connection_id
        )
        if owner is None or owner.provider != "google":
            owner = await database.get(ConnectorConnection, args.connection_id)
        if owner is None:
            raise HistoricalReviewError("connection_not_found")
        manifest = await export_review(
            database,
            connection_id=args.connection_id,
            workspace_id=owner.workspace_id,
            user_id=owner.user_id,
        )
    # Refuse overwrites/symlinks and restrict the review to its local owner.
    with os.fdopen(os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
        output.write(manifest.model_dump_json(indent=2) + "\n")
    return {
        "exported": True,
        "review_sha256": digest(manifest.model_dump(mode="json")),
        "records": len(manifest.assignments),
        "unresolved": sum(a.connection_ids is None for a in manifest.assignments),
        "notice": "Review every proposal locally. No attribution or deletion has been applied.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--connection-id", type=UUID)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--digest", action="store_true")
    parser.add_argument("--file", required=True)
    parser.add_argument("--approve-sha256")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), indent=2))
    except HistoricalReviewError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"error": "historical_review_unavailable"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
