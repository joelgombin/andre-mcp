# Image du serveur MCP André — transport stdio.
#
# Elle démarre et répond à l'introspection (tools/list, resources/list,
# prompts/list) sans aucune donnée : `create_server()` ne fait qu'enregistrer
# les outils, et l'accès aux données est paresseux. Les appels d'outils
# nécessitent en revanche le jeu de données complet, absent de cette image —
# pour un usage réel, connectez-vous au serveur hébergé
# https://mcp.andre.vote/mcp (voir le README).

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "-c", "from andre_mcp.server import run; run()"]
