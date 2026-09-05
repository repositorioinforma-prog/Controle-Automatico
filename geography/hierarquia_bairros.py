"""
Resolve nomes populares de loteamentos/parcelamentos para o bairro oficial
real a que pertencem, usando uma hierarquia municipal conhecida (ex.:
CAMPO_GRANDE_MS). Isso permite recodificar com segurança quando alguém
escreve um loteamento que fica DE VERDADE dentro de um bairro aprovado pelo
projeto — sem nunca "empurrar" a resposta para outro bairro aprovado só por
proximidade/vizinhança, que é uma decisão de negócio e não deve ser tomada
implicitamente pelo motor de matching.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.normalization import normalize_text


@dataclass
class BairroHierarchyMatch:
    bairro_oficial: str
    regiao: str
    metodo: str  # 'bairro_oficial_exato' ou 'loteamento_contido'


class BairroHierarchy:
    def __init__(self, data: dict[str, dict]):
        self.data = data
        self._index: dict[str, str] = {}  # texto normalizado -> bairro oficial
        for bairro_oficial, info in data.items():
            self._index.setdefault(normalize_text(bairro_oficial), bairro_oficial)
            for loteamento in info.get("parcelamentos", []):
                key = normalize_text(loteamento)
                # em caso de nome duplicado entre bairros oficiais diferentes
                # (acontece: um loteamento pode aparecer em dois polígonos por
                # sobreposição histórica), mantém o primeiro e não sobrescreve —
                # evita afirmar uma relação de contenção que não é inequívoca.
                self._index.setdefault(key, bairro_oficial)

    def find(self, text: str) -> BairroHierarchyMatch | None:
        key = normalize_text(text)
        if not key:
            return None
        bairro_oficial = self._index.get(key)
        if not bairro_oficial:
            return None
        metodo = "bairro_oficial_exato" if normalize_text(bairro_oficial) == key else "loteamento_contido"
        return BairroHierarchyMatch(bairro_oficial, self.data[bairro_oficial]["regiao"], metodo)

    def approved_representative(self, bairro_oficial: str, project_labels: dict) -> tuple[object, str] | None:
        """Entre os VALUE LABELS aprovados do projeto, encontra qual (se algum)
        representa esse mesmo bairro oficial — comparando cada label aprovado
        contra a hierarquia (o próprio nome do bairro oficial ou um de seus
        loteamentos)."""
        info = self.data.get(bairro_oficial)
        if not info:
            return None
        valid_keys = {normalize_text(bairro_oficial)} | {normalize_text(p) for p in info.get("parcelamentos", [])}
        matches = [
            (code, label) for code, label in project_labels.items()
            if normalize_text(label) in valid_keys
        ]
        if len(matches) == 1:
            return matches[0]
        return None  # nenhum representante aprovado, ou mais de um (ambíguo) — não decide sozinho


KNOWN_HIERARCHIES: dict[str, dict] = {}


def register_hierarchy(municipio: str, data: dict[str, dict]) -> None:
    KNOWN_HIERARCHIES[normalize_text(municipio)] = data


def get_hierarchy(municipio: str) -> BairroHierarchy | None:
    data = KNOWN_HIERARCHIES.get(normalize_text(municipio))
    return BairroHierarchy(data) if data else None


from .campo_grande_bairros import CAMPO_GRANDE_MS  # noqa: E402

register_hierarchy("Campo Grande", CAMPO_GRANDE_MS)
