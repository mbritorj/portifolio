"""Cadastro de vozes: associa um nome a uma impressão vocal.

O que fica em disco é o vetor, não o áudio — e ainda assim isso é **dado
biométrico**, portanto dado pessoal sensível (LGPD, art. 5º, II e art. 11).
Por isso o cadastro exige consentimento explícito, guarda a data em que ele foi
dado e nunca acontece como efeito colateral de uma gravação: alguém precisa
pedir, com o nome da pessoa na mão.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .base import cosseno, normalizar

AVISO_LGPD = (
    "A impressão vocal identifica uma pessoa e é dado pessoal sensível (LGPD, "
    "art. 11). Cadastre apenas com consentimento específico de quem está sendo "
    "cadastrado, e apague quando não precisar mais."
)


@dataclass
class VoiceProfile:
    profile_id: str
    name: str
    embedding: list[float]
    created_at: str
    updated_at: str
    samples: int = 1
    consent: bool = False
    consent_at: str | None = None
    note: str = ""

    def vector(self) -> np.ndarray:
        return normalizar(np.array(self.embedding, dtype=np.float32))

    def resumo(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "samples": self.samples,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "dim": len(self.embedding),
            "note": self.note,
        }


@dataclass
class Match:
    profile_id: str
    name: str
    score: float


class ConsentimentoAusente(RuntimeError):
    """Tentativa de cadastrar uma voz sem consentimento registrado."""


class VoiceProfileStore:
    """Coleção de vozes cadastradas, persistida em um JSON local."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._profiles: dict[str, VoiceProfile] = {}
        self.load()

    # ------------------------------------------------------------------ disco

    def load(self) -> None:
        self._profiles.clear()
        if not self.path.exists():
            return
        dados = json.loads(self.path.read_text(encoding="utf-8"))
        for item in dados.get("profiles", []):
            perfil = VoiceProfile(**item)
            self._profiles[perfil.profile_id] = perfil

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {
            "aviso": AVISO_LGPD,
            "profiles": [asdict(p) for p in self._profiles.values()],
        }
        self.path.write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ leitura

    def __len__(self) -> int:
        return len(self._profiles)

    @property
    def profiles(self) -> list[VoiceProfile]:
        return list(self._profiles.values())

    def get(self, chave: str) -> VoiceProfile | None:
        """Busca por identificador ou por nome (sem diferenciar acento e caixa)."""
        if chave in self._profiles:
            return self._profiles[chave]
        alvo = _slug(chave)
        return next((p for p in self._profiles.values() if _slug(p.name) == alvo), None)

    def identify(self, embedding: np.ndarray, *, threshold: float = 0.62) -> Match | None:
        """Devolve o perfil mais parecido, se passar do limiar."""
        vetor = normalizar(embedding)
        melhor, escore = None, -1.0
        for perfil in self._profiles.values():
            if len(perfil.embedding) != vetor.size:
                continue  # cadastrado com outro modelo; ignorar em vez de errar
            atual = cosseno(perfil.vector(), vetor)
            if atual > escore:
                melhor, escore = perfil, atual
        if melhor is None or escore < threshold:
            return None
        return Match(profile_id=melhor.profile_id, name=melhor.name, score=escore)

    # ------------------------------------------------------------------ escrita

    def add(
        self,
        name: str,
        embedding: np.ndarray,
        *,
        consent: bool = False,
        note: str = "",
        samples: int = 1,
    ) -> VoiceProfile:
        """Cadastra ou reforça uma voz. Sem consentimento, recusa."""
        if not consent:
            raise ConsentimentoAusente(AVISO_LGPD)
        nome = name.strip()
        if not nome:
            raise ValueError("informe o nome da pessoa.")

        vetor = normalizar(embedding)
        agora = datetime.now().isoformat(timespec="seconds")
        existente = self.get(nome)
        if existente is not None and len(existente.embedding) == vetor.size:
            # Reforço: média dos vetores, ponderada pelo número de amostras já
            # cadastradas. Cadastrar de novo melhora o perfil em vez de trocá-lo.
            atual = existente.vector() * existente.samples
            combinado = normalizar(atual + vetor * samples)
            existente.embedding = [float(v) for v in combinado]
            existente.samples += samples
            existente.updated_at = agora
            existente.consent = True
            existente.consent_at = existente.consent_at or agora
            if note:
                existente.note = note
            self.save()
            return existente

        perfil = VoiceProfile(
            profile_id=_novo_id(nome, self._profiles),
            name=nome,
            embedding=[float(v) for v in vetor],
            created_at=agora,
            updated_at=agora,
            samples=samples,
            consent=True,
            consent_at=agora,
            note=note,
        )
        self._profiles[perfil.profile_id] = perfil
        self.save()
        return perfil

    def remove(self, chave: str) -> bool:
        perfil = self.get(chave)
        if perfil is None:
            return False
        self._profiles.pop(perfil.profile_id, None)
        self.save()
        return True


def _slug(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalizado.lower()).strip("-")


def _novo_id(nome: str, existentes: dict[str, VoiceProfile]) -> str:
    base = _slug(nome) or "voz"
    if base not in existentes:
        return base
    indice = 2
    while f"{base}-{indice}" in existentes:
        indice += 1
    return f"{base}-{indice}"
