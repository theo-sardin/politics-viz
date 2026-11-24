"""Command line: ``pv sync`` builds the database, ``pv serve`` runs the site,
``pv export`` writes it out as static files."""

from __future__ import annotations

import argparse
import logging
import sys

from pv import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pv", description="politics-viz — radar des intérêts déclarés")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="télécharger les sources et reconstruire la base")
    p_sync.add_argument("--db", default=None, help=f"fichier SQLite (défaut : {config.DB_PATH})")
    p_sync.add_argument("--offline", action="store_true", help="n'utiliser que les fichiers déjà en cache")
    p_sync.add_argument("--force", action="store_true", help="retélécharger même si le cache est à jour")
    p_sync.add_argument("-v", "--verbose", action="store_true")

    p_serve = sub.add_parser("serve", help="lancer le site")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    p_export = sub.add_parser("export", help="écrire le site en fichiers statiques")
    p_export.add_argument("out", nargs="?", default="site", help="dossier de sortie (défaut : site)")
    p_export.add_argument("--base", default="", help="chemin sous lequel le site sera servi, ex. /politics-viz")

    sub.add_parser("stats", help="résumé du contenu de la base")

    args = parser.parse_args(argv)
    if args.cmd == "sync":
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        from pv.pipeline.run import sync

        report = sync(args.db, offline=args.offline, force=args.force)
        for stage, out in report.items():
            print(f"{stage}: {out}")
        return 0
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("pv.web.app:app", host=args.host, port=args.port, reload=args.reload)
        return 0
    if args.cmd == "export":
        from pathlib import Path

        from pv.web.export import export

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        stats = export(Path(args.out), base=args.base)
        print(f"{stats['pages']} pages, {stats['files']} fichiers, {stats['megabytes']} Mo → {args.out}")
        return 0
    if args.cmd == "stats":
        from pv import db

        conn = db.connect(config.DB_PATH, readonly=True)
        for table in (
            "person",
            "declaration",
            "holding",
            "interest",
            "company",
            "lobby_org",
            "lobby_action",
            "bill",
            "scrutin",
            "vote",
            "position_change",
            "signal",
        ):
            print(f"{table:16} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>10,}")
        print(f"{'synced_at':16} {db.get_meta(conn, 'synced_at')}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
