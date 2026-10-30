"""Collecte Open-Meteo (archive ERA5) : météo horaire 2023-2025 et climatologie journalière.

Deux modes :
- `collect_open_meteo` appelle l'API et gèle les réponses brutes dans `data/raw/open_meteo/` ;
- `load_hourly_from_raw` / `load_daily_from_raw` reconstruisent les tables depuis ce snapshot,
  sans aucun appel réseau (exigence de reproductibilité de la consigne).

`quality_report` / `quality_report_from_raw` produisent le contrôle qualité par point.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Sans `models`, l'API sert `best_match`, qui mélange IFS 9 km (depuis 2017), ERA5 et ERA5-Land :
# la grille changerait en cours de série et la comparaison 1991-2025 serait inhomogène.
MODEL = "era5"

# Ordre imposé : vent, rayonnement, température (`_impossible_counts` s'appuie dessus).
# Attention : shortwave_radiation est une moyenne sur l'heure précédente (valeur à 12:00 =
# 11:00-12:00), alors que le vent et la température sont instantanés. Rien n'est décalé ici :
# le décalage sera traité à la jointure.
HOURLY_VARIABLES: tuple[str, ...] = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
DAILY_VARIABLES: tuple[str, ...] = (
    "wind_speed_10m_mean",
    "shortwave_radiation_sum",
    "temperature_2m_mean",
)

# Unités attendues : toute divergence lève une erreur plutôt que de fausser l'analyse en aval.
EXPECTED_HOURLY_UNITS: dict[str, str] = {
    "wind_speed_100m": "m/s",
    "shortwave_radiation": "W/m²",
    "temperature_2m": "°C",
}
EXPECTED_DAILY_UNITS: dict[str, str] = {
    "wind_speed_10m_mean": "m/s",
    "shortwave_radiation_sum": "MJ/m²",
    "temperature_2m_mean": "°C",
}
EXPECTED_TIME_UNIT = "iso8601"

HOURLY_START = "2023-01-01"
HOURLY_END = "2025-09-30"
# Normale climatique OMM 1991-2020, prolongée jusqu'à la fin de la période d'étude.
DAILY_START = "1991-01-01"
DAILY_END = "2025-09-30"

REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 5
# Attentes de 5, 10, 20 puis 40 s : le cumul (75 s) dépasse la fenêtre d'une minute de l'API.
RETRY_BASE_WAIT_S = 5.0
RETRY_MAX_WAIT_S = 120.0

# Limites de l'API gratuite : 600 appels/min, 5 000/h, 10 000/jour. Une requête longue compte
# pour plusieurs appels : poids = nLocations × (nDays / 14) × (nVariables / 10), soit environ
# 21,5 pour une requête horaire et 272 pour une requête journalière.
FREE_CALLS_PER_HOUR = 5000

# Écart maximal toléré entre le point demandé et la cellule renvoyée (cellule ERA5 : 0,25°).
MAX_GRID_DISTANCE_DEG = 0.5

ALLOWED_ROLES = frozenset({"wind", "solar", "demand"})
# Espagne péninsulaire : détecte notamment une inversion lat/lon dans la configuration.
MAINLAND_LAT_RANGE = (36.0, 44.0)
MAINLAND_LON_RANGE = (-10.0, 4.0)

# Le nom sert de nom de fichier : minuscules imposées pour éviter les collisions sur un système
# de fichiers insensible à la casse (macOS).
POINT_NAME_PATTERN = re.compile(r"[a-z0-9_]+")

TEMPERATURE_MIN_C = -30.0
TEMPERATURE_MAX_C = 50.0
# Heures (UTC, étiquette de fin d'heure moyennée) où le soleil reste sous -13° sur toute l'heure,
# pour les 7 points et toute l'année : la moindre valeur non nulle y est donc anormale.
NIGHT_HOURS_UTC = frozenset({23, 0, 1, 2, 3})


@dataclass(frozen=True)
class KindSpec:
    """Ce qui distingue une collecte horaire d'une collecte journalière."""

    variables: tuple[str, ...]
    units: dict[str, str]
    start: str
    end: str
    time_col: str


KINDS: dict[str, KindSpec] = {
    "hourly": KindSpec(
        HOURLY_VARIABLES, EXPECTED_HOURLY_UNITS, HOURLY_START, HOURLY_END, "time_utc"
    ),
    "daily": KindSpec(DAILY_VARIABLES, EXPECTED_DAILY_UNITS, DAILY_START, DAILY_END, "date"),
}


def _spec(kind: str) -> KindSpec:
    if kind not in KINDS:
        raise ValueError(f"kind inconnu : {kind!r}")
    return KINDS[kind]


@dataclass(frozen=True)
class WeatherPoint:
    """Point météo représentatif, lu depuis `config/weather_points.toml`."""

    name: str
    lat: float
    lon: float
    role: str
    region: str


def _validate_point(point: WeatherPoint, config_path: Path) -> None:
    if POINT_NAME_PATTERN.fullmatch(point.name) is None:
        raise ValueError(f"Nom de point invalide dans {config_path}: {point.name!r}")
    if point.role not in ALLOWED_ROLES:
        raise ValueError(f"{point.name}: rôle inconnu {point.role!r} dans {config_path}")
    lat_min, lat_max = MAINLAND_LAT_RANGE
    lon_min, lon_max = MAINLAND_LON_RANGE
    if not (lat_min <= point.lat <= lat_max and lon_min <= point.lon <= lon_max):
        raise ValueError(
            f"{point.name}: coordonnées hors de l'Espagne péninsulaire "
            f"({point.lat}, {point.lon}) dans {config_path}"
        )


def load_points(config_path: Path) -> list[WeatherPoint]:
    """Lit la liste des points météo depuis le fichier de configuration TOML."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)
    points = [
        WeatherPoint(
            name=str(p["name"]),
            lat=float(p["lat"]),
            lon=float(p["lon"]),
            role=str(p["role"]),
            region=str(p["region"]),
        )
        for p in config.get("points", [])
    ]
    if not points:
        raise ValueError(f"Aucun point défini dans {config_path}")
    names = [p.name for p in points]
    if len(names) != len(set(names)):
        raise ValueError(f"Noms de points en double dans {config_path}: {names}")
    for point in points:
        _validate_point(point, config_path)
    return points


def build_params(point: WeatherPoint, kind: str) -> dict[str, str | float]:
    """Paramètres de requête pour un point ; `kind` vaut 'hourly' ou 'daily'."""
    spec = _spec(kind)
    return {
        "latitude": point.lat,
        "longitude": point.lon,
        "start_date": spec.start,
        "end_date": spec.end,
        kind: ",".join(spec.variables),
        "models": MODEL,
        "timezone": "GMT",  # horodatages directement en UTC
        "wind_speed_unit": "ms",  # m/s au lieu des km/h par défaut
    }


def expected_times(kind: str) -> pd.DatetimeIndex:
    """Axe temporel attendu : 24 096 heures UTC en horaire, 12 692 jours en journalier."""
    spec = _spec(kind)
    if kind == "hourly":
        return pd.date_range(f"{spec.start}T00:00", f"{spec.end}T23:00", freq="h", tz="UTC")
    return pd.date_range(spec.start, spec.end, freq="D")


def api_call_weight(kind: str) -> float:
    """Poids d'une requête pour un point, selon la formule publiée par Open-Meteo."""
    spec = _spec(kind)
    n_days = (pd.Timestamp(spec.end) - pd.Timestamp(spec.start)).days + 1
    return n_days / 14 * len(spec.variables) / 10


def _retry_wait(attempt: int, response: requests.Response | None) -> float:
    """Attente croissante, ou celle demandée par le serveur via l'en-tête Retry-After."""
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    wait = float(retry_after) if retry_after.isdigit() else RETRY_BASE_WAIT_S * 2 ** (attempt - 1)
    return min(wait, RETRY_MAX_WAIT_S)


def fetch(params: dict[str, str | float], session: requests.Session) -> dict[str, Any]:
    """Appel GET, retenté sur 429, 5xx et erreurs réseau ; les autres 4xx échouent aussitôt."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: requests.Response | None = None
        try:
            response = session.get(ARCHIVE_URL, params=params, timeout=REQUEST_TIMEOUT_S)
            status = response.status_code
            if status == 429 or status >= 500:
                last_error = requests.HTTPError(f"HTTP {status}", response=response)
            elif status >= 400:
                # Le corps contient le champ `reason` d'Open-Meteo, utile au diagnostic.
                detail = f"HTTP {status} : {response.text[:200]}"
                raise requests.HTTPError(detail, response=response)
            else:
                payload: dict[str, Any] = response.json()
                return payload
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,  # réponse coupée en cours de transfert
            requests.exceptions.JSONDecodeError,  # corps tronqué malgré un code 200
        ) as err:
            last_error = err
        if attempt < MAX_ATTEMPTS:
            time.sleep(_retry_wait(attempt, response))
    raise RuntimeError(
        f"Échec après {MAX_ATTEMPTS} tentatives : relancer plus tard, "
        "les fichiers déjà gelés ne seront pas redemandés"
    ) from last_error


def raw_path(raw_dir: Path, kind: str, point: WeatherPoint) -> Path:
    return raw_dir / "open_meteo" / f"{kind}_{point.name}.json"


def build_record(
    payload: dict[str, Any],
    point: WeatherPoint,
    kind: str,
    params: dict[str, str | float],
) -> dict[str, Any]:
    """Associe la réponse brute, inchangée, à son bloc `_meta` de provenance."""
    times = expected_times(kind)
    return {
        "_meta": {
            "source": "Open-Meteo Historical Weather API (ERA5 reanalysis)",
            "owner": "OpenMeteo GmbH (Suisse) ; données ERA5 : ECMWF / Copernicus C3S",
            "url": ARCHIVE_URL,
            "access_method": "HTTP GET, API REST publique sans clé, réponse JSON",
            "params": params,
            "point": point.name,
            "role": point.role,
            "region": point.region,
            "requested_lat": point.lat,
            "requested_lon": point.lon,
            "extracted_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "licence": "CC BY 4.0 — attribution to Open-Meteo required",
            "underlying_data_attribution": (
                "Generated using Copernicus Climate Change Service information"
            ),
            "expected_coverage": {
                "start": times[0].isoformat(),
                "end": times[-1].isoformat(),
                "frequency": kind,
                "n_rows": len(times),
            },
        },
        "response": payload,
    }


def save_raw(
    payload: dict[str, Any],
    point: WeatherPoint,
    kind: str,
    params: dict[str, str | float],
    raw_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Gèle la réponse brute, après contrôle de son contrat (unités, UTC, couverture...).

    Un snapshot gelé n'est jamais réécrit sans `overwrite=True`. Une réponse invalide lève une
    erreur et n'est pas écrite. L'écriture passe par un fichier temporaire : une interruption
    ne peut pas laisser un JSON tronqué qui passerait pour un point déjà collecté.
    """
    path = raw_path(raw_dir, kind, point)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Snapshot déjà gelé, non réécrit : {path}")
    record = build_record(payload, point, kind, params)
    check_coverage(parse_response(record, kind), kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return path


def collect_open_meteo(config_path: Path, raw_dir: Path, *, overwrite: bool = False) -> list[Path]:
    """Collecte en ligne : un appel horaire et un appel journalier par point.

    Les fichiers déjà gelés sont sautés : après un refus de quota, il suffit de relancer. La
    pause entre deux requêtes étale la charge pondérée sur le budget horaire de l'API. Une ligne
    par fichier indique s'il est écrit ou sauté, et la pause prévue avant le prochain appel.
    """
    points = load_points(config_path)
    written: list[Path] = []
    pause_s = 0.0
    with requests.Session() as session:
        for point in points:
            for kind in KINDS:
                path = raw_path(raw_dir, kind, point)
                if path.exists() and not overwrite:
                    written.append(path)
                    print(f"sauté {path.name} (déjà gelé) — pause à venir : {pause_s:.1f} s")
                    continue
                time.sleep(pause_s)
                params = build_params(point, kind)
                payload = fetch(params, session)
                written.append(save_raw(payload, point, kind, params, raw_dir, overwrite=overwrite))
                pause_s = api_call_weight(kind) * 3600 / FREE_CALLS_PER_HOUR
                print(f"écrit {path.name} — pause à venir : {pause_s:.1f} s")
    return written


def parse_response(record: dict[str, Any], kind: str) -> pd.DataFrame:
    """Transforme un fichier brut en table longue : une ligne = un point × un pas de temps.

    Les `null` deviennent des NaN et sont conservés : `quality_report` les compte. La couverture
    temporelle relève de `check_coverage`.
    """
    spec = _spec(kind)
    meta = record["_meta"]
    response = record["response"]
    point = meta["point"]

    if response.get("error"):
        raise ValueError(f"{point}: erreur renvoyée par l'API : {response.get('reason')!r}")
    if response.get("utc_offset_seconds") != 0:
        raise ValueError(f"{point}: réponse non UTC ({response.get('utc_offset_seconds')})")
    units = response.get(f"{kind}_units", {})
    for var, unit in {"time": EXPECTED_TIME_UNIT, **spec.units}.items():
        if units.get(var) != unit:
            raise ValueError(f"{point}: unité inattendue pour {var}: {units.get(var)!r}")

    block = response.get(kind, {})
    missing = [var for var in ("time", *spec.variables) if var not in block]
    if missing:
        raise ValueError(f"{point}: variables absentes de la réponse : {missing}")
    lengths = {var: len(block[var]) for var in ("time", *spec.variables)}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"{point}: séries de longueurs différentes {lengths}")

    # Coordonnées réellement renvoyées : ERA5 ramène la requête à la cellule la plus proche.
    grid_lat = float(response["latitude"])
    grid_lon = float(response["longitude"])
    requested_lat = float(meta["requested_lat"])
    requested_lon = float(meta["requested_lon"])
    distance = max(abs(grid_lat - requested_lat), abs(grid_lon - requested_lon))
    if distance > MAX_GRID_DISTANCE_DEG:
        raise ValueError(
            f"{point}: cellule ({grid_lat}, {grid_lon}) trop éloignée du point demandé "
            f"({requested_lat}, {requested_lon})"
        )

    times = pd.to_datetime(block["time"])
    # dtype explicite : une colonne entièrement nulle resterait sinon de type object.
    df = pd.DataFrame({var: pd.Series(block[var], dtype="float64") for var in spec.variables})
    df.insert(0, spec.time_col, times.tz_localize("UTC") if kind == "hourly" else times)
    df.insert(1, "point", point)
    df.insert(2, "role", meta["role"])
    df.insert(3, "region", meta["region"])
    df["grid_lat"] = grid_lat
    df["grid_lon"] = grid_lon
    df["requested_lat"] = requested_lat
    df["requested_lon"] = requested_lon
    return df


def _coverage_counts(times: pd.Series[Any], kind: str) -> dict[str, int]:
    expected = expected_times(kind)
    actual = pd.DatetimeIndex(times)
    return {
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "duplicate_rows": int(actual.duplicated().sum()),
        "missing_timestamps": len(expected.difference(actual)),
        "unexpected_timestamps": len(actual.difference(expected)),
    }


def _coverage_ok(counts: dict[str, int]) -> bool:
    return counts["expected_rows"] == counts["actual_rows"] and not (
        counts["duplicate_rows"] or counts["missing_timestamps"] or counts["unexpected_timestamps"]
    )


def check_coverage(df: pd.DataFrame, kind: str) -> None:
    """Lève une erreur si la table d'un point n'a pas exactement l'axe temporel attendu."""
    counts = _coverage_counts(df[_spec(kind).time_col], kind)
    if not _coverage_ok(counts):
        points = sorted(df["point"].unique())
        raise ValueError(f"{points}: couverture {kind} incorrecte {counts}")


def _load_from_raw(raw_dir: Path, kind: str, *, strict: bool) -> pd.DataFrame:
    files = sorted((raw_dir / "open_meteo").glob(f"{kind}_*.json"))
    if not files:
        raise FileNotFoundError(f"Aucun fichier brut '{kind}_*.json' dans {raw_dir / 'open_meteo'}")
    frames = []
    for file in files:
        record = json.loads(file.read_text(encoding="utf-8"))
        if file.stem != f"{kind}_{record['_meta']['point']}":
            raise ValueError(f"{file.name}: contenu du point {record['_meta']['point']!r}")
        df = parse_response(record, kind)
        if kind == "daily":
            # Les fichiers gelés avant le passage à 1991 couvrent 1940-2025 : ils restent valides,
            # intacts sur le disque, et seule la table est restreinte avant le contrôle.
            df = df[df["date"] >= pd.Timestamp(DAILY_START)]
        if strict:
            check_coverage(df, kind)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def load_hourly_from_raw(raw_dir: Path, *, strict: bool = True) -> pd.DataFrame:
    """Météo horaire de tous les points, reconstruite depuis le snapshot brut (hors ligne).

    `strict=False` tolère une couverture incorrecte, pour pouvoir la décrire dans le rapport.
    """
    return _load_from_raw(raw_dir, "hourly", strict=strict)


def load_daily_from_raw(raw_dir: Path, *, strict: bool = True) -> pd.DataFrame:
    """Climatologie journalière de tous les points, reconstruite depuis le snapshot brut.

    Seules les dates >= `DAILY_START` sont gardées, y compris pour un fichier gelé plus long.
    `strict=False` tolère une couverture incorrecte, pour pouvoir la décrire dans le rapport.
    """
    return _load_from_raw(raw_dir, "daily", strict=strict)


def _impossible_counts(rows: pd.DataFrame, kind: str) -> dict[str, int]:
    """Compte les valeurs physiquement impossibles ; une valeur NaN n'est jamais comptée ici."""
    spec = _spec(kind)
    wind, radiation, temperature = (rows[var] for var in spec.variables)
    out_of_range = (temperature < TEMPERATURE_MIN_C) | (temperature > TEMPERATURE_MAX_C)
    counts = {
        "negative_wind": int((wind < 0).sum()),
        "negative_radiation": int((radiation < 0).sum()),
        "temperature_out_of_range": int(out_of_range.sum()),
    }
    if kind == "hourly":  # sans objet pour un cumul journalier
        at_night = rows[spec.time_col].dt.hour.isin(NIGHT_HOURS_UTC)
        counts["night_radiation"] = int((radiation[at_night].dropna() != 0).sum())
    return counts


def quality_report(
    df: pd.DataFrame, kind: str, expected_points: Sequence[str] | None = None
) -> pd.DataFrame:
    """Contrôle qualité : une ligne par point, sans modifier ni filtrer les données.

    Colonnes : lignes attendues et obtenues, doublons, horodatages manquants ou en trop, bornes
    temporelles, NaN par variable (`nan_<variable>`), valeurs impossibles et `coverage_ok`.
    `expected_points` fait apparaître un point absent (0 ligne) ; un point présent mais non
    attendu est ajouté à la fin, avec `is_expected_point=False`.
    """
    spec = _spec(kind)
    present = [str(name) for name in df["point"].unique()]
    expected = list(expected_points) if expected_points is not None else sorted(present)
    extras = sorted(set(present) - set(expected))

    rows: list[dict[str, Any]] = []
    for name in [*expected, *extras]:
        subset = df[df["point"] == name]
        times = subset[spec.time_col]
        counts = _coverage_counts(times, kind)
        rows.append(
            {
                "point": name,
                "kind": kind,
                **counts,
                "first_time": times.min(),
                "last_time": times.max(),
                **{f"nan_{var}": int(subset[var].isna().sum()) for var in spec.variables},
                **_impossible_counts(subset, kind),
                "is_expected_point": name in expected,
                "coverage_ok": _coverage_ok(counts) and name in expected,
            }
        )
    return pd.DataFrame(rows)


def quality_report_from_raw(raw_dir: Path, config_path: Path, kind: str) -> pd.DataFrame:
    """Rapport sur le snapshot brut, hors ligne, confronté aux points de la configuration."""
    df = _load_from_raw(raw_dir, kind, strict=False)
    return quality_report(df, kind, [point.name for point in load_points(config_path)])
