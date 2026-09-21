"""CLI entry point (SPEC §7).

This module defines the full command surface up front so later steps fill in
a handler function rather than restructure the argparse tree. Commands not
yet owned by an implemented step print "not implemented yet" and exit 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from tracker import EXIT_ERROR, EXIT_OK
from tracker.db import DEFAULT_DB_PATH, connect, migrate


def _not_implemented(command: str, step: int) -> Callable[[argparse.Namespace], int]:
    def handler(args: argparse.Namespace) -> int:
        print(f"{command}: not implemented yet (Step {step})", file=sys.stderr)
        return EXIT_ERROR

    return handler


def cmd_ingest(args: argparse.Namespace) -> int:
    """Dispatch `ingest <kind> <file>` / `ingest --all` to the owning step's handler.

    A later step wires its real ingest function in by replacing its entry in
    `ingest_handlers` (or `ingest_all_handler`) below — this dispatcher itself
    should not need to change.
    """
    if args.all:
        return args.ingest_all_handler(args)
    if not args.kind:
        print("ingest: specify a kind (linkedin, glassdoor, descriptions, salary) or --all", file=sys.stderr)
        return EXIT_ERROR
    if not args.file:
        print(f"ingest {args.kind}: missing file argument", file=sys.stderr)
        return EXIT_ERROR
    return args.ingest_handlers[args.kind](args)


def cmd_init(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        migrate(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps({"ok": True, "db": str(args.db)}))
    else:
        print(f"Initialized {args.db}")
    return EXIT_OK


def _global_flags_parser(*, suppress_defaults: bool) -> argparse.ArgumentParser:
    """Shared --db/--json/--dry-run definitions, added as `parents=[...]` to the
    top parser and to every subparser, so these flags work on either side of a
    subcommand (`tracker --json init` and `tracker init --json` both work).

    argparse's subparser mechanism re-applies a subparser's own defaults onto
    the shared namespace even for flags it was not given — which would silently
    stomp a value the top-level parser already collected (e.g. `--db X init`
    would lose X to the init subparser's own default). So only the top-level
    parser gets real defaults; every subparser copy uses argparse.SUPPRESS,
    which means "leave the namespace alone if this flag wasn't given here."
    """
    p = argparse.ArgumentParser(add_help=False)
    db_default = argparse.SUPPRESS if suppress_defaults else DEFAULT_DB_PATH
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    p.add_argument(
        "--db",
        default=db_default,
        help="path to the SQLite database (default: data/tracker.db)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=flag_default,
        help="machine-readable output where supported",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=flag_default,
        help="ingest only: validate and report, write nothing",
    )
    return p


def build_parser() -> argparse.ArgumentParser:
    top_flags = _global_flags_parser(suppress_defaults=False)
    global_flags = _global_flags_parser(suppress_defaults=True)
    parser = argparse.ArgumentParser(
        prog="tracker", description="Job application tracker", parents=[top_flags]
    )

    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser(
        "init", help="create/migrate data/tracker.db", parents=[global_flags]
    )
    init_parser.set_defaults(func=cmd_init)

    # ingest linkedin|glassdoor|descriptions|salary <file.json>, ingest --all
    ingest_parser = subparsers.add_parser(
        "ingest", help="load a captured JSON file", parents=[global_flags]
    )
    ingest_parser.add_argument(
        "kind",
        nargs="?",
        choices=["linkedin", "glassdoor", "descriptions", "salary"],
        help="the kind of file being ingested",
    )
    ingest_parser.add_argument("file", nargs="?", help="path to the JSON file")
    ingest_parser.add_argument(
        "--all", action="store_true", help="process every unprocessed file in data/inbox/"
    )
    ingest_parser.add_argument("--force", action="store_true", help="re-process an already-seen file")
    ingest_parser.set_defaults(
        func=cmd_ingest,
        ingest_handlers={
            "linkedin": _not_implemented("ingest linkedin", step=2),
            "glassdoor": _not_implemented("ingest glassdoor", step=3),
            "descriptions": _not_implemented("ingest descriptions", step=12),
            "salary": _not_implemented("ingest salary", step=10),
        },
        ingest_all_handler=_not_implemented("ingest --all", step=2),
    )

    # companies pending-glassdoor|list|merge
    companies_parser = subparsers.add_parser(
        "companies", help="company admin", parents=[global_flags]
    )
    companies_sub = companies_parser.add_subparsers(dest="companies_cmd")

    pending_parser = companies_sub.add_parser(
        "pending-glassdoor",
        help="emit the worklist of companies to fetch",
        parents=[global_flags],
    )
    pending_parser.add_argument("--stale-after-days", type=int, default=30)
    pending_parser.set_defaults(func=_not_implemented("companies pending-glassdoor", step=3))

    list_parser = companies_sub.add_parser(
        "list", help="list companies", parents=[global_flags]
    )
    list_parser.set_defaults(func=_not_implemented("companies list", step=3))

    merge_parser = companies_sub.add_parser(
        "merge", help="merge two company records", parents=[global_flags]
    )
    merge_parser.add_argument("keep_id", type=int)
    merge_parser.add_argument("drop_id", type=int)
    merge_parser.add_argument("--yes", action="store_true")
    merge_parser.set_defaults(func=_not_implemented("companies merge", step=3))

    # status <job_id> <status>
    status_parser = subparsers.add_parser(
        "status", help="set an application's status", parents=[global_flags]
    )
    status_parser.add_argument("job_id", type=int)
    status_parser.add_argument("status")
    status_parser.add_argument("--applied-on")
    status_parser.add_argument("--note")
    status_parser.set_defaults(func=_not_implemented("status", step=4))

    # event add
    event_parser = subparsers.add_parser(
        "event", help="manage application events", parents=[global_flags]
    )
    event_sub = event_parser.add_subparsers(dest="event_cmd")
    event_add_parser = event_sub.add_parser(
        "add", help="add an application event", parents=[global_flags]
    )
    event_add_parser.add_argument("job_id", type=int)
    event_add_parser.add_argument("--type", dest="event_type", required=True)
    event_add_parser.add_argument("--at", dest="occurs_at", required=True)
    event_add_parser.add_argument("--title")
    event_add_parser.add_argument("--notes")
    event_add_parser.set_defaults(func=_not_implemented("event add", step=4))

    # salary parse-postings|pending
    salary_parser = subparsers.add_parser(
        "salary", help="salary parsing and worklist", parents=[global_flags]
    )
    salary_sub = salary_parser.add_subparsers(dest="salary_cmd")
    salary_sub.add_parser(
        "parse-postings",
        help="parse jobs.salary_text into 'posting' rows",
        parents=[global_flags],
    ).set_defaults(func=_not_implemented("salary parse-postings", step=10))
    salary_sub.add_parser(
        "pending",
        help="jobs with no salary figure, for estimation",
        parents=[global_flags],
    ).set_defaults(func=_not_implemented("salary pending", step=10))

    # cv import-master|validate-master|brief|ingest|render|list
    cv_parser = subparsers.add_parser(
        "cv", help="master CV and tailored variants", parents=[global_flags]
    )
    cv_sub = cv_parser.add_subparsers(dest="cv_cmd")

    cv_import_parser = cv_sub.add_parser(
        "import-master", help="one-time .docx -> cv/master.yaml", parents=[global_flags]
    )
    cv_import_parser.add_argument("file")
    cv_import_parser.set_defaults(func=_not_implemented("cv import-master", step=11))

    cv_validate_parser = cv_sub.add_parser(
        "validate-master", help="check ids unique, schema valid", parents=[global_flags]
    )
    cv_validate_parser.set_defaults(func=_not_implemented("cv validate-master", step=11))

    cv_brief_parser = cv_sub.add_parser(
        "brief", help="emit the tailoring brief", parents=[global_flags]
    )
    cv_brief_parser.add_argument("job_id", type=int)
    cv_brief_parser.add_argument("--out")
    cv_brief_parser.set_defaults(func=_not_implemented("cv brief", step=12))

    cv_ingest_parser = cv_sub.add_parser(
        "ingest", help="store a tailored variant", parents=[global_flags]
    )
    cv_ingest_parser.add_argument("file")
    cv_ingest_parser.set_defaults(func=_not_implemented("cv ingest", step=12))

    cv_render_parser = cv_sub.add_parser(
        "render", help="render a variant to docx/pdf", parents=[global_flags]
    )
    cv_render_parser.add_argument("job_id", type=int)
    cv_render_parser.add_argument("--version", type=int)
    cv_render_parser.add_argument("--formats", default="docx,pdf")
    cv_render_parser.set_defaults(func=_not_implemented("cv render", step=11))

    cv_list_parser = cv_sub.add_parser(
        "list", help="list CV variants", parents=[global_flags]
    )
    cv_list_parser.add_argument("job_id", type=int, nargs="?")
    cv_list_parser.set_defaults(func=_not_implemented("cv list", step=12))

    # export csv|xlsx
    export_parser = subparsers.add_parser(
        "export", help="snapshot the dashboard to a file", parents=[global_flags]
    )
    export_sub = export_parser.add_subparsers(dest="export_cmd")
    export_csv_parser = export_sub.add_parser("csv", parents=[global_flags])
    export_csv_parser.add_argument("--out")
    export_csv_parser.set_defaults(func=_not_implemented("export csv", step=5))
    export_xlsx_parser = export_sub.add_parser("xlsx", parents=[global_flags])
    export_xlsx_parser.add_argument("--out")
    export_xlsx_parser.set_defaults(func=_not_implemented("export xlsx", step=5))

    # serve
    serve_parser = subparsers.add_parser(
        "serve", help="serve the web dashboard", parents=[global_flags]
    )
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--no-open", action="store_true")
    serve_parser.set_defaults(func=_not_implemented("serve", step=6))

    # stats
    stats_parser = subparsers.add_parser(
        "stats", help="summary counts", parents=[global_flags]
    )
    stats_parser.set_defaults(func=_not_implemented("stats", step=4))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_ERROR

    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_ERROR

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
