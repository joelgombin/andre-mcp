"""SHA du commit embarqué dans l'image au déploiement.

Pendant : `api/build_info.py`. Le fichier `GIT_SHA` est écrit à la racine du
dépôt par `scripts/ci/stamp_version.sh` avant la création du tarball CapRover ;
il est copié dans l'image par le `COPY . .` de `andre-mcp/Dockerfile`.
"""

import os
from functools import lru_cache
from pathlib import Path

# src/andre_mcp/build_info.py → src/andre_mcp → src → andre-mcp → racine
_SHA_FILE = Path(__file__).resolve().parents[3] / "GIT_SHA"

#: Valeur renvoyée hors déploiement (dev local, tests).
UNKNOWN_SHA = "dev"


@lru_cache(maxsize=1)
def git_sha() -> str:
    """Renvoie le SHA du commit déployé, ou "dev" en local."""
    from_env = os.environ.get("ANDRE_GIT_SHA", "").strip()
    if from_env:
        return from_env
    try:
        return _SHA_FILE.read_text(encoding="utf-8").strip() or UNKNOWN_SHA
    except OSError:
        return UNKNOWN_SHA
