# ND
Nous développons une application permettant de **naviguer, comprendre et explorer l’activité législative** (Assemblée nationale & Sénat).
Les données brutes proviennent de l’open data de l’État via **Tricoteuses**, qui reste la **source de vérité**.


## Tooling
- **uv** for everything Python: deps, venvs, running scripts (`uv run ...`, `uv add ...`, `uv sync`).
- **ruff** for Python formatting and linting:
  - Format: `uv run ruff format .`
  - Lint/fix: `uv run ruff check --fix .`
- **typer** for CLIs.

## Coding
- Clean code, but not verbose. No ceremony for its own sake.
- Dont use too much private method/attribute (_function)
- Rule of thumb: if removing _ improves readability, drop it. Use leading underscore only for small, local helpers tightly scoped to a single module.
- No from __future__ import annotations — Python 3.12+, native syntax suffices
- pathlib.Path over os.path.


## Data from tricoteuses
In this project we use data from tricoteuses-parlement-api. This API expose a cleaned and structured version of the french open data for Assemblee National and Senat.
Their API is build with express, using prisma as framework to model the data.
- API Repo: https://git.tricoteuses.fr/logiciels/tricoteuses-api-parlement
- API Docs https://parlement.tricoteuses.fr/docs
ex: GET https://parlement.tricoteuses.fr/debats/json
