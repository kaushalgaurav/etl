"""Delete downloaded source files whose rows are already in the database.

New ingests clean up after themselves (PLATFORM_DELETE_SOURCE_FILES_AFTER_INGEST).
This is the one-off catch-up for files downloaded before that was switched on.

    python purge_source_files.py --dry-run
    python purge_source_files.py

Deletes `.xls`, `.xlsx`, `.csv` and `.tsv` under data/raw. The `.meta.json`
sidecars are kept: a few hundred bytes each, and they record the server's
modified-date, size and sha256 of the file that produced each row.

Be aware of what this costs: `rebuild_hmis_raw.py` rebuilds hmis_raw from
these reports, and cannot run until they are downloaded again. Ordinary
re-crawls are unaffected — an already-ingested branch is skipped on the
jobs/datasets tables, not on the file cache.
"""
import argparse

from app.core.config import RAW_DIR
from app.modules.ingestion import cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey-id", type=int, default=None,
                        help="Clear only this survey's own cache. Omit for every survey.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted and exit.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt.")
    args = parser.parse_args()

    scope = f"survey {args.survey_id}" if args.survey_id is not None else "all surveys"
    print(f"source directory : {RAW_DIR}")
    print(f"scope            : {scope}")

    preview = cleanup.purge_all(dry_run=True, survey_id=args.survey_id)
    count, size_gb = preview["would_remove"], preview["would_free"] / 1_073_741_824
    print(f"files to remove  : {count:,}")
    print(f"space to free    : {size_gb:.2f} GB")

    if not count:
        print("Nothing to do.")
        return
    if args.dry_run:
        print("\nDry run — nothing was deleted.")
        return

    if not args.yes:
        print("\nThis cannot be undone. rebuild_hmis_raw.py will need the "
              "reports downloaded again.")
        if input("Type 'delete' to continue: ").strip().lower() != "delete":
            print("Cancelled.")
            return

    result = cleanup.purge_all(survey_id=args.survey_id)
    print("\n" + cleanup.describe(result))
    for error in result["errors"][:10]:
        print("  could not delete:", error)
    if len(result["errors"]) > 10:
        print(f"  ... and {len(result['errors']) - 10} more")


if __name__ == "__main__":
    main()
