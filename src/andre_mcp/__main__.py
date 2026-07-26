"""Point d'entrée CLI pour andre-mcp."""

import click

from andre_mcp.server import run


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """André MCP — Serveur MCP pour l'analyse électorale française."""
    if ctx.invoked_subcommand is None:
        # Par défaut, lance le serveur stdio
        run()


@cli.command()
@click.option("--http", is_flag=True, help="Mode Streamable HTTP (au lieu de stdio)")
@click.option("--host", default="0.0.0.0", help="Adresse d'écoute HTTP (défaut: 0.0.0.0)")
@click.option("--port", default=8080, type=int, help="Port d'écoute HTTP (défaut: 8080)")
def serve(http, host, port):
    """Lance le serveur MCP."""
    if http:
        from andre_mcp.server import run_http
        click.echo(f"Serveur MCP HTTP sur http://{host}:{port}/mcp")
        click.echo("Les utilisateurs ajoutent dans leur config Claude :")
        click.echo(f'  {{"url": "http://{host}:{port}/mcp"}}')
        run_http(host=host, port=port)
    else:
        run()


@cli.command()
def status():
    """Affiche l'état des données installées."""
    from andre_mcp.config import get_data_dir
    from andre_mcp.setup_cli import check_dataset_status

    data_dir = get_data_dir()
    click.echo(f"Répertoire de données : {data_dir}")

    db_path = data_dir / "elections.duckdb"
    click.echo(f"Base DuckDB : {'✓' if db_path.exists() else '✗ (lancer andre-mcp setup)'}")

    categories = check_dataset_status(data_dir)
    for cat, info in categories.items():
        click.echo(f"\n{cat.upper()} ({info['downloaded']}/{info['total']} fichiers, {info['size_mb']:.1f} Mo) :")
        for f in info["files"]:
            icon = "✓" if f["exists"] else "✗"
            size = f" ({f['size_mb']} Mo)" if f["exists"] else ""
            click.echo(f"  {icon} {f['name']}{size}")


@cli.command()
@click.option("--with-maps", is_flag=True, help="Inclure les contours géographiques (~60 Mo)")
@click.option("--with-sociodem", is_flag=True, help="Inclure les données socio-démo")
@click.option("--all", "download_all", is_flag=True, help="Télécharger tout")
@click.option("--force", is_flag=True, help="Retélécharger même si existant")
@click.option("--skip-db", is_flag=True, help="Ne pas construire la base DuckDB")
def setup(with_maps, with_sociodem, download_all, force, skip_db):
    """Télécharge les données électorales et initialise la base DuckDB."""
    from andre_mcp.config import get_data_dir
    from andre_mcp.setup_cli import build_duckdb, download_datasets

    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Répertoire de données : {data_dir}\n")

    # Déterminer les catégories à télécharger
    categories = ["essential"]
    if with_maps or download_all:
        categories.append("maps")
    if with_sociodem or download_all:
        categories.append("sociodem")
    if download_all:
        categories.append("ferd")

    # Télécharger
    click.echo(f"Téléchargement des catégories : {', '.join(categories)}\n")
    stats = download_datasets(data_dir, categories=categories, force=force)

    click.echo(f"\n{stats['downloaded']} téléchargés, {stats['skipped']} déjà présents, {stats['failed']} échoués")

    # Construire DuckDB
    if not skip_db:
        click.echo("\nConstruction de la base DuckDB...")
        success = build_duckdb(data_dir)
        if success:
            click.echo("\nSetup terminé. Vous pouvez maintenant utiliser André dans Claude.")
        else:
            click.echo("\nErreur lors de la construction de la base. Vérifiez les données.", err=True)
    else:
        click.echo("\nTéléchargement terminé (base DuckDB non reconstruite).")


@cli.command(name="rebuild-db")
def rebuild_db():
    """Reconstruit la base DuckDB à partir des données existantes."""
    from andre_mcp.config import get_data_dir
    from andre_mcp.setup_cli import build_duckdb

    data_dir = get_data_dir()
    click.echo(f"Reconstruction de la base DuckDB dans : {data_dir}")
    success = build_duckdb(data_dir)
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
