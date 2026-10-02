"""Glossário da reunião: os nomes próprios que o modelo precisa conhecer.

É o `initial_prompt` do Whisper, só que editável sem abrir arquivo de
configuração — porque ele muda a cada reunião. Nomes de pessoas, empresas e
produtos são justamente o que o reconhecimento mais erra, e o que mais
atrapalha quem vai ler a ata depois: "VMware" virou *Vaymer*, *Weimar*, *VMer*
e *Vêmero* na mesma transcrição.

Fica num arquivo próprio, fora da pasta de transcrições, para valer tanto na
interface quanto na linha de comando.
"""

from __future__ import annotations

from pathlib import Path

#: O Whisper só considera as últimas ~224 unidades do prompt; o resto é
#: ignorado em silêncio. Cortar aqui, com aviso, é melhor do que deixar o
#: usuário achar que cadastrou um glossário que o modelo nunca viu.
LIMITE_CARACTERES = 600

AVISO_LIMITE = (
    f"O glossário foi cortado em {LIMITE_CARACTERES} caracteres: o modelo "
    "ignora o que passa disso. Deixe só os nomes que vão aparecer nesta reunião."
)


def ler(caminho: str | Path) -> str:
    """Devolve o glossário salvo, ou vazio se ainda não houver um."""
    arquivo = Path(caminho)
    if not arquivo.exists():
        return ""
    try:
        return normalizar(arquivo.read_text(encoding="utf-8"))
    except OSError:
        return ""


def salvar(caminho: str | Path, texto: str) -> tuple[str, str | None]:
    """Grava o glossário. Devolve o que ficou salvo e um aviso, se houver."""
    limpo = normalizar(texto)
    aviso = None
    if len(limpo) > LIMITE_CARACTERES:
        limpo = limpo[:LIMITE_CARACTERES].rsplit(" ", 1)[0].rstrip(" ,;")
        aviso = AVISO_LIMITE

    arquivo = Path(caminho)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    if limpo:
        arquivo.write_text(limpo + "\n", encoding="utf-8")
    elif arquivo.exists():
        arquivo.unlink()
    return limpo, aviso


def normalizar(texto: str) -> str:
    """Uma linha só, sem espaços sobrando: é assim que o prompt é usado."""
    return " ".join(texto.split())


def contar_termos(texto: str) -> int:
    """Quantos termos o glossário tem, para a interface mostrar."""
    return len([t for t in (p.strip() for p in texto.replace(";", ",").split(",")) if t])
