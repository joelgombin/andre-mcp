# André MCP — Analyse électorale française

Serveur MCP (Model Context Protocol) donnant accès aux résultats de **55 élections
françaises (1999-2026) au niveau du bureau de vote**, croisés avec les données
socio-démographiques de l'INSEE, la base des élus de la République et les
découpages administratifs officiels.

**Serveur hébergé, prêt à l'emploi :** `https://mcp.andre.vote/mcp`
Aucune installation, aucun téléchargement de données, aucune authentification.

> Ce dépôt est la fiche publique du serveur (documentation et `server.json`).
> Le code source vit dans le monorepo [André](https://andre.vote), dont
> l'extraction en paquet autonome est en cours — voir
> [Installation locale](#installation-locale).

## Connexion

### Claude Code

```bash
claude mcp add --transport http andre https://mcp.andre.vote/mcp
```

### Claude Desktop, Cursor, VS Code…

```json
{
  "mcpServers": {
    "andre": {
      "type": "http",
      "url": "https://mcp.andre.vote/mcp"
    }
  }
}
```

Emplacement du fichier pour Claude Desktop :
- macOS : `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows : `%APPDATA%\Claude\claude_desktop_config.json`

Pour les clients qui ne gèrent pas encore le transport HTTP :

```json
{
  "mcpServers": {
    "andre": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.andre.vote/mcp"]
    }
  }
}
```

> **Limite d'appels d'outils sur Claude.ai / Claude Desktop**
>
> Claude impose une limite d'environ 15-20 appels d'outils par tour de
> conversation. Les analyses approfondies (résultats T1+T2, bureaux de vote,
> croisement socio-démographique…) peuvent l'atteindre. Cliquez **Continuer**
> pour reprendre. Pour des analyses complètes sans interruption,
> [andre.vote](https://andre.vote) n'a pas cette contrainte.

## Outils

| Outil | Description |
|-------|-------------|
| `query_elections` | Résultats électoraux (55 élections, 1999-2026) |
| `query_admin` | Divisions administratives (communes, EPCI, cantons, départements) |
| `query_elus` | Élus de la République (base BRÉF, Zenodo) |
| `query_sociodem` | Données socio-démographiques INSEE au niveau bureau de vote |
| `query_sql` | Requêtes SQL directes sur la base DuckDB (lecture seule) |
| `query_dlp` | Composition sociale des candidats municipaux 2020 |
| `generate_map` | Cartes électorales multi-couches |
| `visualize` | Visualisations Plotly (bar, line, scatter…) |
| `simulate_fusions` | Simulation de fusions de listes municipales |
| `export_data` | Export CSV/JSON/TSV |
| `create_data_table` | Tableaux de données formatés |

Tous les outils portent un `title` et une annotation `readOnlyHint` /
`destructiveHint`. **Aucun outil n'est destructif** : le serveur est en lecture
seule sur des données publiques.

`query_elections` propose des **actions « panel »** qui répondent en un seul
appel, ce qui limite le risque d'atteindre le plafond d'appels d'outils :

- `compare_elections` — compare deux scrutins pour un territoire
- `query_all_circonscriptions` — vainqueur de chaque circonscription (législatives)
- `vote_reserve` — score d'une famille + abstention par bureau (réserves de voix)
- `select_communes` — sélectionne/classe des communes par critères géographiques, démographiques et électoraux
- `cross_election_panel` — évolution d'une famille ou d'une nuance par commune sur N scrutins
- `bureau_breakdown` — pivot par bureau (top nuances + participation, socio-démo en option)

## Resources documentaires

Références consultables automatiquement par le client :

- `andre://references/methodologies` — méthodologies d'analyse (réserves, typologies, ACP)
- `andre://references/data_schema` — schéma des données électorales
- `andre://references/political_colors` — couleurs des familles politiques
- `andre://references/election_dates` — dates des 55 élections
- `andre://references/victory_rules` — règles de victoire par type d'élection
- `andre://references/nuances` — codes de nuance politique
- `andre://references/insee_variables` — variables socio-démographiques INSEE
- `andre://skills/electoral-analysis` — skill principal (workflows, débogage)

## Prompts pré-construits

- **analyser_commune** — analyse électorale complète d'une commune
- **reserves_de_voix** — identification des réserves pour une force politique
- **carte_electorale** — carte thématique d'un département
- **comparer_elections** — comparaison temporelle entre deux scrutins
- **resultats_rapides** — résultats synthétiques d'une élection

## Exemples

```
Analyse les résultats des législatives 2024 à Marseille
→ query_admin (code 13055) puis query_elections puis query_sociodem
```

```
Quelles sont les communes les plus abstentionnistes des Bouches-du-Rhône
aux européennes 2024 ?
→ query_sql avec une requête agrégée
```

## Données

Exclusivement des sources publiques officielles :

| Source | Licence | Contenu |
|--------|---------|---------|
| [data.gouv.fr](https://data.gouv.fr) | Licence Ouverte 2.0 | Résultats électoraux, candidatures |
| [IGN / Etalab](https://github.com/etalab) | Licence Ouverte 2.0 | Contours administratifs |
| [INSEE](https://www.insee.fr) | Licence Ouverte 2.0 | Données socio-démographiques |
| [BRÉF (Zenodo)](https://zenodo.org) | CC BY 4.0 | Élus de la République |
| [FERD](https://ferd.fr) | CC BY 4.0 | Codage politique à trois niveaux |

Les données socio-démographiques sont interpolées de l'IRIS vers le bureau de
vote via les pondérations de l'Observatoire national des bureaux de vote.

## Limites & robustesse

Le serveur expose des données publiques en lecture seule, sans authentification.
Garde-fous en place :

- **`query_sql`** — connexion DuckDB en lecture seule, requêtes d'écriture
  bloquées, `LIMIT` de sécurité injecté sur les `SELECT`/`WITH` sans limite
  explicite.
- **Garde de concurrence** — nombre d'exécutions lourdes simultanées borné,
  avec une réponse gracieuse « serveur occupé » au-delà.
- **Métriques** — séries Prometheus sur `/metrics` (outil, latence, taux
  d'erreur, concurrence). Ni les arguments, ni les résultats, ni les
  identifiants de session ne sont exposés.

## Installation locale

Pas encore disponible. Le serveur délègue ses requêtes à des scripts qui vivent
encore dans le monorepo André ; un paquet PyPI autonome est en préparation.
En attendant, utilisez le serveur distant.

## Registre

Publié au registre officiel MCP sous le nom **`vote.andre/elections`**
(namespace vérifié par DNS sur `andre.vote`). Voir [`server.json`](server.json).

## Licence

MIT — voir [LICENSE](LICENSE).

Les données restent soumises aux licences de leurs producteurs respectifs
(Licence Ouverte 2.0, CC BY 4.0).
