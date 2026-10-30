"""Agrégation nationale de la météo horaire : une ligne par heure UTC.

Entrée : la table longue produite par ``load_hourly_from_raw`` (une ligne = un point x une heure).
Sortie : une table large indexée par ``time_utc``, prête pour la jointure avec les prix REE.

Deux versions coexistent :
- la moyenne simple (version par défaut, utilisée dans la jointure principale) ;
- la moyenne pondérée par la capacité installée de la région de chaque point (sensibilité),
  calculée seulement si la table de capacité REE est fournie.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from spain_electricity_weather.collect.open_meteo import load_hourly_from_raw

# Colonne nationale -> (variable Open-Meteo source, rôle des points utilisés ; None = tous)
AGGREGATES: dict[str, tuple[str, str | None]] = {
    "wind_100m_ms": ("wind_speed_100m", "wind"),
    "solar_wm2": ("shortwave_radiation", "solar"),
    "temp_c": ("temperature_2m", None),
}

# Colonnes pondérables -> technologie REE dont la capacité sert de poids
WEIGHTED: dict[str, str] = {
    "wind_100m_ms": "wind",
    "solar_wm2": "solar",
}

CAPACITY_COLUMNS = ("region", "technology", "year", "capacity_mw")


def check_complete_grid(hourly: pd.DataFrame) -> None:
    """Refuse toute table incomplète : doublon, trou horaire, point manquant ou valeur NaN."""
    if hourly.duplicated(["time_utc", "point"]).any():
        raise ValueError("doublons (time_utc, point) dans la météo horaire")

    variables = [var for var, _ in AGGREGATES.values()]
    n_missing = int(hourly[variables].isna().sum().sum())
    if n_missing:
        raise ValueError(f"{n_missing} valeurs manquantes dans la météo horaire")

    n_points = hourly["point"].nunique()
    points_per_hour = hourly.groupby("time_utc")["point"].nunique()
    incomplete = points_per_hour[points_per_hour != n_points]
    if not incomplete.empty:
        first = incomplete.index[0]
        raise ValueError(f"{len(incomplete)} heures sans les {n_points} points (1re : {first})")

    times = points_per_hour.index
    expected = pd.date_range(times.min(), times.max(), freq="h", tz="UTC")
    if len(times) != len(expected):
        raise ValueError(f"{len(expected) - len(times)} heures manquantes dans la grille horaire")


def simple_national(hourly: pd.DataFrame) -> pd.DataFrame:
    """Moyenne simple des points de chaque rôle, heure par heure."""
    out: dict[str, pd.Series] = {}
    for name, (var, role) in AGGREGATES.items():
        subset = hourly if role is None else hourly[hourly["role"] == role]
        if subset.empty:
            raise ValueError(f"aucun point de rôle {role!r} pour {name}")
        out[name] = subset.groupby("time_utc")[var].mean()
    return pd.DataFrame(out)


def weighted_national(hourly: pd.DataFrame, capacity: pd.DataFrame) -> pd.DataFrame:
    """Moyenne pondérée par la capacité installée (MW) de la région de chaque point, par année.

    ``capacity`` : une ligne par région x technologie x année, colonnes ``CAPACITY_COLUMNS``.
    """
    missing_cols = set(CAPACITY_COLUMNS) - set(capacity.columns)
    if missing_cols:
        raise ValueError(f"colonnes manquantes dans la capacité : {sorted(missing_cols)}")
    if capacity.duplicated(["region", "technology", "year"]).any():
        raise ValueError("doublons (region, technology, year) dans la capacité")

    out: dict[str, pd.Series] = {}
    for name, tech in WEIGHTED.items():
        var, role = AGGREGATES[name]
        subset = hourly.loc[hourly["role"] == role, ["time_utc", "region", var]].copy()
        subset["year"] = subset["time_utc"].dt.year
        caps = capacity.loc[capacity["technology"] == tech, ["region", "year", "capacity_mw"]]
        merged = subset.merge(caps, on=["region", "year"], how="left", validate="many_to_one")

        if len(merged) != len(subset):
            raise ValueError(f"{name} : la jointure avec la capacité a changé le nombre de lignes")
        no_cap = merged.loc[merged["capacity_mw"].isna(), ["region", "year"]].drop_duplicates()
        if not no_cap.empty:
            pairs = list(no_cap.itertuples(index=False, name=None))
            raise ValueError(f"{name} : capacité {tech} absente pour {pairs}")
        if (merged["capacity_mw"] <= 0).any():
            raise ValueError(f"{name} : capacité {tech} nulle ou négative")

        merged["weighted"] = merged[var] * merged["capacity_mw"]
        sums = merged.groupby("time_utc")[["weighted", "capacity_mw"]].sum()
        out[f"{name}_w"] = sums["weighted"] / sums["capacity_mw"]
    return pd.DataFrame(out)


def build_national_weather(
    hourly: pd.DataFrame, capacity: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Table nationale : moyenne simple, plus la version pondérée si la capacité est fournie."""
    check_complete_grid(hourly)
    national = simple_national(hourly)
    if capacity is not None:
        national = national.join(weighted_national(hourly, capacity), validate="one_to_one")
    national.index.name = "time_utc"
    return national.reset_index()


def build_from_raw(
    raw_dir: Path, out_path: Path, capacity: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Reconstruit la table nationale depuis le snapshot brut et l'écrit en Parquet."""
    national = build_national_weather(load_hourly_from_raw(raw_dir), capacity)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    national.to_parquet(out_path, index=False)
    return national
