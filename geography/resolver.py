from __future__ import annotations

from dataclasses import dataclass
import difflib
import re

from core.normalization import normalize_text
from .database import GeographyDatabase, GeographicRecord, _UF_SIGLA_NOME

_UF_SIGLAS_NORMALIZADAS = {normalize_text(s) for s in _UF_SIGLA_NOME}


@dataclass(frozen=True)
class GeographicResolution:
    input_text: str
    target_type: str
    value: str | None
    method: str
    score: float
    status: str
    matched_type: str | None = None
    matched_name: str | None = None
    uf: str = ""
    municipality: str = ""
    district: str = ""
    candidates: tuple[str, ...] = ()


def _target_value(record: GeographicRecord, target_type: str) -> str:
    if target_type == "municipio":
        return record.municipality or (record.name if record.entity_type == "municipio" else "")
    if target_type == "distrito":
        return record.name if record.entity_type == "distrito" else record.district
    if target_type == "bairro":
        return record.name if record.entity_type == "bairro" else ""
    return record.name


def _collapse(records: list[GeographicRecord], text: str, target_type: str, method: str, score: float) -> GeographicResolution:
    usable = [(r, _target_value(r, target_type)) for r in records]
    usable = [(r, value) for r, value in usable if value]
    if not usable:
        return GeographicResolution(text, target_type, None, method, score, "NÃO IDENTIFICADO")

    targets: dict[str, list[tuple[GeographicRecord, str]]] = {}
    for record, value in usable:
        targets.setdefault(normalize_text(value), []).append((record, value))

    if len(targets) != 1:
        labels = []
        for group in targets.values():
            r, value = group[0]
            detail = f"{value} — {r.uf}" if r.uf else value
            if r.municipality and target_type != "municipio":
                detail += f" / {r.municipality}"
            labels.append(detail)
        return GeographicResolution(text, target_type, None, method, score, "AMBÍGUO", candidates=tuple(labels))

    group = next(iter(targets.values()))
    record, value = group[0]
    # Vários registros podem compartilhar exatamente o mesmo nome de destino (ex.:
    # "Flamengo" existe como bairro em várias cidades do Brasil) mas pertencerem a
    # municípios diferentes. Nesse caso sabemos o nome com segurança, mas não a
    # qual município ele pertence — não reportamos um município arbitrário, para
    # não gerar falsas inconsistências na checagem de coerência Cidade × Bairro.
    distinct_municipalities = {normalize_text(r.municipality) for r, _ in group if r.municipality}
    municipality_confident = record.municipality if len(distinct_municipalities) <= 1 else ""
    uf_confident = record.uf if len(distinct_municipalities) <= 1 else ""
    return GeographicResolution(
        text, target_type, value, method, score,
        "CONFIRMADO" if method in {"geografica_exata", "geografica_exata_sufixo_uf"} else "SUGESTÃO",
        matched_type=record.entity_type,
        matched_name=record.name,
        uf=uf_confident,
        municipality=municipality_confident,
        district=record.district,
    )


def resolve_to_target(text: object, target_type: str, database: GeographyDatabase, fuzzy_cutoff: float = 0.88) -> GeographicResolution:
    raw = "" if text is None else str(text).strip()
    key = normalize_text(raw)
    if not key:
        return GeographicResolution(raw, target_type, None, "vazio", 0.0, "VAZIO")

    exact = database.by_name.get(key, [])
    if exact:
        return _collapse(exact, raw, target_type, "geografica_exata", 1.0)

    # Padrão muito comum: nome do lugar + sigla do estado ("Valença RJ",
    # "São Fidélis/RJ", "Cachoeira de Macacu, RJ"...). Remove a sigla do fim
    # e tenta EXATO de novo — seguro porque não é um recorte livre, é uma
    # sigla de UF reconhecida sendo descartada.
    words = key.split()
    if len(words) >= 2 and words[-1] in _UF_SIGLAS_NORMALIZADAS:
        stripped_key = " ".join(words[:-1])
        stripped_exact = database.by_name.get(stripped_key, [])
        if stripped_exact:
            return _collapse(stripped_exact, raw, target_type, "geografica_exata_sufixo_uf", 0.99)

    # A resposta também pode trazer o nome do lugar embutido em texto maior
    # sem seguir esse padrão simples. Procuramos candidatos contidos, mas só
    # como SUGESTÃO (não aplicamos sozinho): nomes de bairro/distrito curtos e
    # genéricos existem repetidos pelo Brasil todo (ex.: um bairro chamado só
    # "Cachoeira" ou só "Caxias" em outra cidade), e aplicar automaticamente
    # arrisca trocar a cidade certa por uma errada por coincidência de nome.
    prefiltered = [k for k in database.by_name if len(k) >= 3 and k in key]
    contained = [
        k for k in prefiltered
        if len(k) >= 0.6 * len(key) and re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", key)
    ]
    if contained:
        contained.sort(key=len, reverse=True)
        best_len = len(contained[0])
        top = [k for k in contained if len(k) == best_len]
        records = [r for k in top for r in database.by_name[k]]
        return _collapse(records, raw, target_type, "geografica_substring", 0.90)

    close_keys = difflib.get_close_matches(key, list(database.by_name), n=5, cutoff=fuzzy_cutoff)
    if not close_keys:
        return GeographicResolution(raw, target_type, None, "sem_correspondencia_geografica", 0.0, "NÃO IDENTIFICADO")

    best_score = difflib.SequenceMatcher(None, key, close_keys[0]).ratio()
    # Keep only candidates nearly tied with the best to avoid false certainty.
    selected = [k for k in close_keys if best_score - difflib.SequenceMatcher(None, key, k).ratio() <= 0.025]
    records = [r for k in selected for r in database.by_name[k]]
    return _collapse(records, raw, target_type, "geografica_fuzzy", best_score)
