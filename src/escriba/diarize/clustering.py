"""Agrupamento incremental de falantes.

O VAD já entrega falas fechadas, então não é preciso um pipeline de diarização
completo: basta um vetor por fala e um agrupamento online. Cada falante é um
centroide; uma fala nova entra no centroide mais parecido, se passar do limiar,
ou abre um falante novo.

O algoritmo é deliberadamente simples e sem estado escondido — é o que permite
testá-lo com vetores sintéticos e depurar uma reunião real olhando os escores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .base import normalizar

PREFIXO_PADRAO = "Falante"


@dataclass
class Speaker:
    """Um falante detectado na reunião."""

    id: str
    label: str
    centroid: np.ndarray
    utterances: int = 0
    total_s: float = 0.0
    # "cluster" (voz agrupada), "perfil" (bateu com uma voz cadastrada)
    # ou "manual" (a pessoa renomeou na interface).
    source: str = "cluster"
    profile_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "speaker_id": self.id,
            "label": self.label,
            "utterances": self.utterances,
            "total_s": round(self.total_s, 2),
            "source": self.source,
            "profile_id": self.profile_id,
        }


@dataclass
class Assignment:
    """Resultado de encaixar uma fala em um falante."""

    speaker: Speaker
    score: float
    is_new: bool
    matched_profile: bool = False


@dataclass
class RenameResult:
    speaker_id: str
    absorbed: list[str] = field(default_factory=list)


class OnlineSpeakerClusterer:
    def __init__(
        self,
        *,
        threshold: float = 0.55,
        max_speakers: int = 12,
        profiles=None,
        profile_threshold: float = 0.62,
        prefixo: str = PREFIXO_PADRAO,
    ) -> None:
        self.threshold = threshold
        self.max_speakers = max_speakers
        self.profiles = profiles
        self.profile_threshold = profile_threshold
        self.prefixo = prefixo
        self._speakers: dict[str, Speaker] = {}
        self._proximo = 1

    # ------------------------------------------------------------------ leitura

    @property
    def speakers(self) -> list[Speaker]:
        return list(self._speakers.values())

    def get(self, speaker_id: str) -> Speaker | None:
        return self._speakers.get(speaker_id)

    def centroids(self) -> dict[str, list[float]]:
        """Centroides por falante, para exportar e reaproveitar depois.

        É o que permite, mais tarde, cadastrar a voz de alguém a partir de uma
        reunião já gravada, sem guardar o áudio.
        """
        return {s.id: [float(v) for v in s.centroid] for s in self._speakers.values()}

    # -------------------------------------------------------------- atribuição

    def assign(self, embedding: np.ndarray, duration_s: float = 0.0) -> Assignment:
        vetor = normalizar(embedding)

        perfil = self._casar_perfil(vetor)
        if perfil is not None:
            profile_id, nome, escore = perfil
            speaker = self._por_perfil(profile_id, nome, vetor)
            self._atualizar(speaker, vetor, duration_s)
            return Assignment(speaker, escore, is_new=False, matched_profile=True)

        melhor, escore = self._mais_proximo(vetor)
        if melhor is not None and escore >= self.threshold:
            self._atualizar(melhor, vetor, duration_s)
            return Assignment(melhor, escore, is_new=False)

        if len(self._speakers) >= self.max_speakers:
            # Teto atingido: em vez de multiplicar falantes fantasma, a fala vai
            # para o mais parecido e o escore baixo denuncia a incerteza.
            if melhor is not None:
                self._atualizar(melhor, vetor, duration_s)
                return Assignment(melhor, escore, is_new=False)

        novo = self._criar(vetor)
        self._atualizar(novo, vetor, duration_s)
        return Assignment(novo, escore, is_new=True)

    # ------------------------------------------------------------------ edição

    def rename(self, speaker_id: str, nome: str) -> RenameResult:
        """Renomeia um falante; se o nome já existir, funde os dois.

        Renomear "Falante 3" para um nome que já está em uso é a forma natural de
        dizer "isto aqui é a mesma pessoa" — a fusão evita que a mesma voz
        continue caindo em dois grupos pelo resto da reunião.
        """
        alvo = self._speakers.get(speaker_id)
        if alvo is None:
            raise KeyError(f"falante desconhecido: {speaker_id}")

        nome = nome.strip()
        if not nome:
            raise ValueError("o nome não pode ser vazio.")

        gemeo = next(
            (s for s in self._speakers.values() if s.id != speaker_id and s.label == nome), None
        )
        alvo.label = nome
        alvo.source = "manual"
        if gemeo is None:
            return RenameResult(speaker_id=alvo.id)

        sobrevivente, absorvido = (
            (gemeo, alvo) if gemeo.utterances >= alvo.utterances else (alvo, gemeo)
        )
        self._fundir(sobrevivente, absorvido)
        return RenameResult(speaker_id=sobrevivente.id, absorbed=[absorvido.id])

    def merge(self, destino_id: str, origem_id: str) -> None:
        destino, origem = self._speakers.get(destino_id), self._speakers.get(origem_id)
        if destino is None or origem is None:
            raise KeyError("falante desconhecido na fusão.")
        self._fundir(destino, origem)

    # ---------------------------------------------------------------- internos

    def _casar_perfil(self, vetor: np.ndarray) -> tuple[str, str, float] | None:
        if self.profiles is None:
            return None
        achado = self.profiles.identify(vetor, threshold=self.profile_threshold)
        if achado is None:
            return None
        return achado.profile_id, achado.name, achado.score

    def _por_perfil(self, profile_id: str, nome: str, vetor: np.ndarray) -> Speaker:
        for speaker in self._speakers.values():
            if speaker.profile_id == profile_id:
                return speaker
        speaker = self._criar(vetor, label=nome)
        speaker.source = "perfil"
        speaker.profile_id = profile_id
        return speaker

    def _mais_proximo(self, vetor: np.ndarray) -> tuple[Speaker | None, float]:
        melhor, escore = None, -1.0
        for speaker in self._speakers.values():
            atual = float(np.dot(speaker.centroid, vetor))
            if atual > escore:
                melhor, escore = speaker, atual
        return melhor, escore

    def _criar(self, vetor: np.ndarray, *, label: str | None = None) -> Speaker:
        identificador = f"S{self._proximo}"
        speaker = Speaker(
            id=identificador,
            label=label or f"{self.prefixo} {self._proximo}",
            centroid=vetor.copy(),
        )
        self._speakers[identificador] = speaker
        self._proximo += 1
        return speaker

    def _atualizar(self, speaker: Speaker, vetor: np.ndarray, duration_s: float) -> None:
        # Média móvel ponderada pela duração: fala longa é evidência melhor do
        # timbre da pessoa do que um "sim" de meio segundo.
        peso = max(0.1, min(duration_s or 1.0, 10.0))
        acumulado = max(speaker.total_s, 0.1)
        centroide = speaker.centroid * acumulado + vetor * peso
        speaker.centroid = normalizar(centroide)
        speaker.utterances += 1
        speaker.total_s += duration_s

    def _fundir(self, destino: Speaker, origem: Speaker) -> None:
        total = max(destino.total_s + origem.total_s, 1e-6)
        destino.centroid = normalizar(
            destino.centroid * max(destino.total_s, 0.1)
            + origem.centroid * max(origem.total_s, 0.1)
        )
        destino.utterances += origem.utterances
        destino.total_s = total
        destino.label = origem.label if origem.source == "manual" else destino.label
        destino.profile_id = destino.profile_id or origem.profile_id
        self._speakers.pop(origem.id, None)
