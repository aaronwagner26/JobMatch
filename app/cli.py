from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.engine import JobMatchEngine
from app.core.types import FilterCriteria, JobSourceConfig
from app.utils.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local job matching tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resume_parser = subparsers.add_parser("resume-import", help="Parse and store a resume")
    resume_parser.add_argument("path", type=Path)

    source_add = subparsers.add_parser("source-add", help="Add or update a job source")
    source_add.add_argument("--id", type=int)
    source_add.add_argument("--name", required=True)
    source_add.add_argument("--url", required=True)
    source_add.add_argument("--type", default="auto")
    source_add.add_argument("--identifier")
    source_add.add_argument("--disabled", action="store_true")
    source_add.add_argument("--playwright", action="store_true")
    source_add.add_argument("--refresh-minutes", type=int, default=180)
    source_add.add_argument("--notes", default="")

    subparsers.add_parser("sources", help="List configured sources")
    scan_parser = subparsers.add_parser("scan", help="Scan enabled sources")
    scan_parser.add_argument("--source-id", action="append", type=int, default=[])

    matches_parser = subparsers.add_parser("matches", help="Print ranked matches")
    matches_parser.add_argument("--location", default="")
    matches_parser.add_argument("--remote", default="any")
    matches_parser.add_argument("--job-type", default="any")
    matches_parser.add_argument("--clearance", action="append", default=[])
    matches_parser.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("settings", help="Print stored settings")
    return parser


def main() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    engine = JobMatchEngine()

    if args.command == "resume-import":
        resume = engine.save_resume(args.path)
        print(json.dumps({"resume_id": resume.id, "filename": resume.filename, "skills": resume.skills[:16]}, indent=2))
        return

    if args.command == "source-add":
        source = engine.save_source(
            JobSourceConfig(
                id=args.id,
                name=args.name,
                source_type=args.type,
                url=args.url,
                identifier=args.identifier,
                enabled=not args.disabled,
                use_playwright=args.playwright,
                refresh_minutes=args.refresh_minutes,
                notes=args.notes,
            )
        )
        print(json.dumps({"source_id": source.id, "name": source.name, "type": source.source_type, "url": source.url}, indent=2))
        return

    if args.command == "sources":
        sources = engine.list_sources()
        print(json.dumps([{"id": source.id, "name": source.name, "type": source.source_type, "url": source.url} for source in sources], indent=2))
        return

    if args.command == "scan":
        summary = asyncio.run(engine.scan_sources(args.source_id or None))
        print(
            json.dumps(
                {
                    "started_at": summary.started_at.isoformat(),
                    "finished_at": summary.finished_at.isoformat(),
                    "total_jobs": summary.total_jobs,
                    "results": [
                        {
                            "source": result.source.name,
                            "status": result.status,
                            "jobs": len(result.jobs),
                            "created": result.jobs_created,
                            "updated": result.jobs_updated,
                            "unchanged": result.jobs_unchanged,
                            "deactivated": result.jobs_deactivated,
                            "error": result.error,
                        }
                        for result in summary.results
                    ],
                },
                indent=2,
            )
        )
        return

    if args.command == "matches":
        matches = engine.get_ranked_matches(
            FilterCriteria(
                location_query=args.location,
                remote_mode=args.remote,
                job_type=args.job_type,
                clearance_terms=args.clearance,
            )
        )
        payload = [
            {
                "score": round(match.score, 4),
                "title": match.job.title,
                "company": match.job.company,
                "location": match.job.location,
                "matched_skills": match.matched_skills,
                "missing_skills": match.missing_skills,
                "url": match.job.url,
            }
            for match in matches[: args.limit]
        ]
        print(json.dumps(payload, indent=2))
        return

    if args.command == "settings":
        print(json.dumps(engine.get_settings(), indent=2))
        return


if __name__ == "__main__":
    main()
