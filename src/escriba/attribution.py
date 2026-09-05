"""Dar nome às vozes usando a transcrição oficial da plataforma.

Teams, Webex e Meet entregam, depois da reunião, um transcript **com os nomes**
dos participantes. Ele não serve para o tempo real, mas serve como gabarito: se
alinharmos os tempos, cada voz que o Escriba agrupou ganha o nome de quem
realmente falou — e o centroide daquela voz pode virar um cadastro, de modo que
na próxima reunião o nome apareça ao vivo.

Aceita WebVTT (Teams e Webex exportam nesse formato), SRT e texto simples no
padrão ``[00:00:04] Nome: fala``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .session import Session

# "00:01:02.500", "01:02.500" ou "00:01:02,500"
_TEMPO = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_SETA = re.compile(r"(.+?)\s*-->\s*(\S+)")
# Teams e Webex marcam o falante como <v Ana Souza>fala</v>
_VOZ_VTT = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>)?\s*$", re.DOTALL)
_LINHA_TEXTO = re.compile(
    r"^\[?(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\]?\s+([^:]{1,60}):\s*(.+)$"
)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Cue:
    """Um trecho do transcript oficial."""

    start_s: float
    end_s: float
    speaker: str | None
    text: str


@dataclass
class Proposta:
    """Sugestão de nome para uma voz agrupada pelo Escriba."""

    speaker_id: str
    label_atual: str
    nome: str
    confianca: float
    sobreposicao_s: float
    alternativas: dict[str, float] = field(default_factory=dict)


class TranscriptError(ValueError):
    pass


# --------------------------------------------------------------------- leitura


def ler_transcript(caminho: str | Path) -> list[Cue]:
    """Lê o transcript oficial, detectando o formato pelo conteúdo."""
    texto = Path(caminho).read_text(encoding="utf-8-sig", errors="replace")
    if texto.lstrip().upper().startswith("WEBVTT") or "-->" in texto:
        cues = _ler_cronometrado(texto)
    else:
        cues = _ler_texto(texto)
    if not cues:
        raise TranscriptError(
            f"não encontrei falas em {caminho}. Formatos aceitos: WebVTT (.vtt), "
            "SRT (.srt) ou texto no padrão '[00:00:04] Nome: fala'."
        )
    return cues


def _ler_cronometrado(texto: str) -> list[Cue]:
    """WebVTT e SRT: blocos separados por linha em branco, com '-->'."""
    cues: list[Cue] = []
    for bloco in re.split(r"\n\s*\n", texto.replace("\r\n", "\n")):
        linhas = [linha for linha in bloco.strip().splitlines() if linha.strip()]
        if not linhas:
            continue
        indice = next((i for i, linha in enumerate(linhas) if "-->" in linha), None)
        if indice is None:
            continue
        casamento = _SETA.search(linhas[indice])
        if casamento is None:
            continue
        inicio, fim = _para_segundos(casamento.group(1)), _para_segundos(casamento.group(2))
        if inicio is None or fim is None:
            continue

        corpo = "\n".join(linhas[indice + 1 :]).strip()
        falante, conteudo = _extrair_falante(corpo)
        if conteudo:
            cues.append(Cue(start_s=inicio, end_s=fim, speaker=falante, text=conteudo))
    return cues


def _ler_texto(texto: str) -> list[Cue]:
    """Texto simples: uma fala por linha, com hora e nome."""
    cues: list[Cue] = []
    for linha in texto.splitlines():
        casamento = _LINHA_TEXTO.match(linha.strip())
        if casamento is None:
            continue
        horas, minutos, segundos, milis, falante, conteudo = casamento.groups()
        inicio = (
            int(horas or 0) * 3600 + int(minutos) * 60 + int(segundos) + int(milis or 0) / 1000
        )
        # Sem fim declarado, estima pelo tamanho da fala (~14 caracteres por segundo).
        cues.append(
            Cue(
                start_s=inicio,
                end_s=inicio + max(1.5, len(conteudo) / 14),
                speaker=falante.strip(),
                text=conteudo.strip(),
            )
        )
    for anterior, seguinte in zip(cues, cues[1:], strict=False):
        anterior.end_s = min(anterior.end_s, seguinte.start_s)
    return cues


def _extrair_falante(corpo: str) -> tuple[str | None, str]:
    casamento = _VOZ_VTT.match(corpo.strip())
    if casamento is not None:
        return casamento.group(1).strip(), _TAGS.sub("", casamento.group(2)).strip()
    limpo = _TAGS.sub("", corpo).strip()
    # Alguns exportadores escrevem "Ana Souza: fala" em vez da marcação <v>.
    if ":" in limpo:
        possivel, resto = limpo.split(":", 1)
        if 0 < len(possivel) <= 60 and "\n" not in possivel and not possivel.isdigit():
            return possivel.strip(), resto.strip()
    return None, limpo


def _para_segundos(marca: str) -> float | None:
    casamento = _TEMPO.search(marca)
    if casamento is None:
        return None
    horas, minutos, segundos, milis = casamento.groups()
    return (
        int(horas or 0) * 3600 + int(minutos) * 60 + int(segundos) + int(milis.ljust(3, "0")) / 1000
    )


# ------------------------------------------------------------------ alinhamento


def estimar_offset(
    session: Session,
    cues: list[Cue],
    *,
    max_offset_s: float = 1800.0,
    resolucao_s: float = 0.1,
) -> float:
    """Descobre a defasagem entre os dois relógios.

    A plataforma conta o tempo do início da reunião; o Escriba, do momento em que
    a captura começou. Em vez de pedir esse número, procuramos o deslocamento que
    faz os períodos de fala dos dois lados coincidirem — é uma correlação cruzada
    de duas linhas do tempo binárias.
    """
    if not session.segments or not cues:
        return 0.0

    duracao = max(
        max(s.end_s for s in session.segments), max(c.end_s for c in cues)
    ) + max_offset_s
    tamanho = int(duracao / resolucao_s) + 1
    if tamanho > 4_000_000:  # reunião absurdamente longa: não vale a memória
        return 0.0

    nossa = np.zeros(tamanho, dtype=np.float32)
    deles = np.zeros(tamanho, dtype=np.float32)
    for segmento in session.segments:
        nossa[_faixa(segmento.start_s, segmento.end_s, resolucao_s, tamanho)] = 1.0
    for cue in cues:
        deles[_faixa(cue.start_s, cue.end_s, resolucao_s, tamanho)] = 1.0

    correlacao = np.fft.irfft(np.fft.rfft(nossa) * np.conj(np.fft.rfft(deles)), n=tamanho)
    limite = int(max_offset_s / resolucao_s)
    # Índices baixos = transcript atrasado; índices altos (do fim) = adiantado.
    candidatos = np.concatenate([correlacao[: limite + 1], correlacao[-limite:]])
    melhor = int(np.argmax(candidatos))
    passos = melhor if melhor <= limite else melhor - (limite + 1) - limite
    return round(passos * resolucao_s, 2)


def _faixa(inicio: float, fim: float, resolucao: float, tamanho: int) -> slice:
    return slice(
        max(0, min(tamanho - 1, int(inicio / resolucao))),
        max(0, min(tamanho, int(fim / resolucao))),
    )


def mapear(
    session: Session,
    cues: list[Cue],
    *,
    offset_s: float | None = None,
    min_confianca: float = 0.5,
) -> tuple[list[Proposta], float]:
    """Casa cada voz agrupada com o nome que mais fala junto dela.

    Devolve as propostas que passaram do limiar e o deslocamento usado.
    """
    offset = estimar_offset(session, cues) if offset_s is None else offset_s

    # (speaker_id -> nome -> segundos de sobreposição)
    votos: dict[str, dict[str, float]] = {}
    for segmento in session.segments:
        if segmento.speaker_id is None:
            continue
        alvo = votos.setdefault(segmento.speaker_id, {})
        for cue in cues:
            if not cue.speaker:
                continue
            sobreposicao = min(segmento.end_s, cue.end_s + offset) - max(
                segmento.start_s, cue.start_s + offset
            )
            if sobreposicao > 0:
                alvo[cue.speaker] = alvo.get(cue.speaker, 0.0) + sobreposicao

    rotulos = {f["speaker_id"]: f["label"] for f in session.speakers}
    propostas: list[Proposta] = []
    for speaker_id, contagem in votos.items():
        if not contagem:
            continue
        total = sum(contagem.values())
        nome, peso = max(contagem.items(), key=lambda item: item[1])
        confianca = peso / total if total else 0.0
        if confianca < min_confianca:
            continue
        propostas.append(
            Proposta(
                speaker_id=speaker_id,
                label_atual=rotulos.get(speaker_id, speaker_id),
                nome=nome,
                confianca=round(confianca, 3),
                sobreposicao_s=round(peso, 2),
                alternativas={k: round(v, 2) for k, v in sorted(
                    contagem.items(), key=lambda item: -item[1]
                )},
            )
        )
    propostas.sort(key=lambda p: -p.sobreposicao_s)
    return propostas, offset


def aplicar(session: Session, propostas: list[Proposta]) -> list[dict]:
    """Renomeia as vozes na sessão, da proposta mais forte para a mais fraca."""
    aplicadas = []
    for proposta in propostas:
        try:
            resultado = session.renomear_falante(proposta.speaker_id, proposta.nome)
        except KeyError:
            # Já foi absorvido por uma fusão anterior (duas vozes, uma pessoa).
            continue
        aplicadas.append({**resultado, "nome": proposta.nome, "de": proposta.label_atual})
    return aplicadas
