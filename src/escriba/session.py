"""Estado da transcrição e exportação para arquivo."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _timestamp(segundos: float) -> str:
    delta = timedelta(seconds=max(0.0, segundos))
    horas, resto = divmod(int(delta.total_seconds()), 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segs:02d}"


def _timestamp_srt(segundos: float) -> str:
    total = max(0.0, segundos)
    horas, resto = divmod(int(total), 3600)
    minutos, segs = divmod(resto, 60)
    milis = int((total - int(total)) * 1000)
    return f"{horas:02d}:{minutos:02d}:{segs:02d},{milis:03d}"


@dataclass
class Segment:
    """Um trecho já transcrito e fechado."""

    id: int
    track: str
    speaker: str
    start_s: float
    end_s: float
    text: str
    avg_logprob: float = 0.0
    low_confidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_label"] = _timestamp(self.start_s)
        return data


class Session:
    """Coleção de segmentos de uma reunião, com exportação.

    Segura para uso entre threads: o pipeline escreve de uma thread de captura e
    o servidor lê da thread do event loop.
    """

    def __init__(self, titulo: str | None = None) -> None:
        self.titulo = titulo or "Reunião"
        self.iniciada_em = datetime.now()
        self.encerrada_em: datetime | None = None
        self._segments: list[Segment] = []
        self._partials: dict[str, dict[str, Any]] = {}
        self._proximo_id = 1
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ escrita

    def add_segment(
        self,
        *,
        track: str,
        speaker: str,
        start_s: float,
        end_s: float,
        text: str,
        avg_logprob: float = 0.0,
        low_confidence_limite: float = -1.0,
    ) -> Segment | None:
        """Registra um trecho fechado. Texto vazio é ignorado."""
        if not text.strip():
            return None
        with self._lock:
            segment = Segment(
                id=self._proximo_id,
                track=track,
                speaker=speaker,
                start_s=start_s,
                end_s=end_s,
                text=text.strip(),
                avg_logprob=avg_logprob,
                low_confidence=avg_logprob < low_confidence_limite,
            )
            self._proximo_id += 1
            self._segments.append(segment)
            self._partials.pop(track, None)
            return segment

    def set_partial(self, *, track: str, speaker: str, start_s: float, text: str) -> None:
        with self._lock:
            if text.strip():
                self._partials[track] = {
                    "track": track, "speaker": speaker,
                    "start_s": start_s, "text": text.strip(),
                }
            else:
                self._partials.pop(track, None)

    def clear_partial(self, track: str) -> None:
        with self._lock:
            self._partials.pop(track, None)

    def encerrar(self) -> None:
        with self._lock:
            self.encerrada_em = datetime.now()
            self._partials.clear()

    # ------------------------------------------------------------------- leitura

    @property
    def segments(self) -> list[Segment]:
        with self._lock:
            return list(self._segments)

    @property
    def partials(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._partials.values())

    @property
    def duracao_s(self) -> float:
        fim = self.encerrada_em or datetime.now()
        return (fim - self.iniciada_em).total_seconds()

    def snapshot(self) -> dict[str, Any]:
        """Estado completo, para um cliente que acabou de se conectar."""
        return {
            "titulo": self.titulo,
            "iniciada_em": self.iniciada_em.isoformat(),
            "duracao_s": self.duracao_s,
            "segments": [s.to_dict() for s in self.segments],
            "partials": self.partials,
        }

    @classmethod
    def from_json(cls, dados: str | dict[str, Any]) -> Session:
        """Reconstrói uma sessão a partir do JSON exportado.

        Serve para gerar a ata depois, sem precisar regravar a reunião.
        """
        bruto = json.loads(dados) if isinstance(dados, str) else dados
        session = cls(bruto.get("titulo"))
        iniciada = bruto.get("iniciada_em")
        if iniciada:
            session.iniciada_em = datetime.fromisoformat(iniciada)
        duracao = float(bruto.get("duracao_s") or 0.0)
        session.encerrada_em = session.iniciada_em + timedelta(seconds=duracao)
        for item in bruto.get("segments", []):
            session.add_segment(
                track=item.get("track", "desconhecida"),
                speaker=item.get("speaker", "Desconhecido"),
                start_s=float(item.get("start_s", 0.0)),
                end_s=float(item.get("end_s", 0.0)),
                text=item.get("text", ""),
                avg_logprob=float(item.get("avg_logprob", 0.0)),
            )
        return session

    # --------------------------------------------------------------- exportação

    def to_markdown(self, *, agrupar: bool = True) -> str:
        linhas = [
            f"# {self.titulo}",
            "",
            f"- **Início:** {self.iniciada_em.strftime('%d/%m/%Y %H:%M')}",
            f"- **Duração:** {_timestamp(self.duracao_s)}",
            f"- **Trechos:** {len(self._segments)}",
            "",
            "## Transcrição",
            "",
        ]
        blocos = self._agrupar(self.segments) if agrupar else [
            (s.speaker, s.start_s, s.text, s.low_confidence) for s in self.segments
        ]
        for speaker, inicio, texto, incerto in blocos:
            marca = " _(baixa confiança)_" if incerto else ""
            linhas.append(f"**[{_timestamp(inicio)}] {speaker}:** {texto}{marca}")
            linhas.append("")
        return "\n".join(linhas).rstrip() + "\n"

    def to_text(self) -> str:
        return "\n".join(
            f"[{_timestamp(inicio)}] {speaker}: {texto}"
            for speaker, inicio, texto, _ in self._agrupar(self.segments)
        ) + "\n"

    def to_srt(self) -> str:
        blocos = []
        for indice, segment in enumerate(self.segments, start=1):
            blocos.append(
                f"{indice}\n"
                f"{_timestamp_srt(segment.start_s)} --> {_timestamp_srt(segment.end_s)}\n"
                f"{segment.speaker}: {segment.text}\n"
            )
        return "\n".join(blocos)

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, indent=2)

    def salvar(
        self, diretorio: str | Path, *, formatos: Iterable[str] = ("md", "json")
    ) -> list[Path]:
        """Grava a transcrição e devolve os caminhos gerados."""
        destino = Path(diretorio)
        destino.mkdir(parents=True, exist_ok=True)
        base = f"{self.iniciada_em.strftime('%Y-%m-%d_%H%M')}_{_slug(self.titulo)}"
        escritores = {
            "md": self.to_markdown, "txt": self.to_text,
            "srt": self.to_srt, "json": self.to_json,
        }
        caminhos = []
        for formato in formatos:
            if formato not in escritores:
                raise ValueError(f"formato desconhecido: {formato}")
            caminho = destino / f"{base}.{formato}"
            caminho.write_text(escritores[formato](), encoding="utf-8")
            caminhos.append(caminho)
        return caminhos

    # ---------------------------------------------------------------- internos

    @staticmethod
    def _agrupar(
        segments: list[Segment], *, intervalo_max_s: float = 8.0
    ) -> list[tuple[str, float, str, bool]]:
        """Junta trechos seguidos do mesmo falante em um parágrafo só.

        A transcrição ao vivo sai picada por natureza (o VAD corta a cada pausa);
        na leitura posterior, um parágrafo por turno de fala vale mais.
        """
        blocos: list[tuple[str, float, str, bool]] = []
        anterior_fim: float | None = None
        for segment in segments:
            mesmo_turno = (
                blocos
                and blocos[-1][0] == segment.speaker
                and anterior_fim is not None
                and segment.start_s - anterior_fim <= intervalo_max_s
            )
            if mesmo_turno:
                speaker, inicio, texto, incerto = blocos[-1]
                blocos[-1] = (
                    speaker, inicio, f"{texto} {segment.text}".strip(),
                    incerto or segment.low_confidence,
                )
            else:
                blocos.append(
                    (segment.speaker, segment.start_s, segment.text, segment.low_confidence)
                )
            anterior_fim = segment.end_s
        return blocos


def _slug(texto: str) -> str:
    import re
    import unicodedata

    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^a-zA-Z0-9]+", "-", normalizado).strip("-").lower()
    return limpo or "reuniao"
