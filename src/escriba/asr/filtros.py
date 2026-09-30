"""Filtro de alucinações do reconhecimento de fala.

O Whisper, quando recebe silêncio ou ruído sem fala, não devolve vazio: ele
inventa. As duas invenções mais comuns têm assinatura reconhecível:

* **eco do contexto** — devolve o ``initial_prompt`` como se fosse fala. Uma
  lista de termos é a forma que mais vira eco, e o resultado é uma transcrição
  salpicada do próprio prompt;
* **frases-fantasma** — restos do material de treino ("Legendas por…",
  "Inscreva-se no canal"), que aparecem sobre trechos mudos.

Nenhuma das duas some ajustando o VAD: elas ocorrem justamente nos trechos que
o VAD deixa passar por engano, e um VAD mais rígido cortaria fala de verdade.
Filtrar a saída é mais seguro do que tentar não produzi-la.

O filtro **recorta em vez de descartar** sempre que pode: alucinação costuma vir
colada em fala verdadeira ("Opa, boa tarde, tá me vendo?" seguido do prompt
inteiro), e jogar o bloco fora levaria junto o que foi realmente dito. Só
descarta quando não sobra frase.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Frases que o Whisper herdou de legendas e narrações do material de treino.
FRASES_FANTASMA = (
    r"legendas? (?:por|pela|feitas? por)[^.!?]*",
    r"legendado por[^.!?]*",
    r"transcri[çc][ãa]o (?:por|feita por)[^.!?]*",
    r"subtitles? by[^.!?]*",
    r"amara\.org[^.!?]*",
    r"inscreva-se no canal[^.!?]*",
    r"obrigado por assistir[^.!?]*",
    r"at[ée] (?:a )?pr[óo]xima edi[çc][ãa]o[^.!?]*",
)

# Fórmulas de despedida de vídeo que o Whisper solta sobre silêncio. Ao
# contrário das de cima, estas aparecem em conversa de verdade ("obrigado,
# gente, até a próxima"), então só são alucinação quando são o trecho inteiro.
FRASES_ISOLADAS = (
    "ate a proxima",
    "ate a proxima edicao",
    "ate mais",
    "muito obrigado pela atencao",
    "obrigado por assistir",
)

_FANTASMAS = re.compile("|".join(FRASES_FANTASMA), re.IGNORECASE)
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")
_PALAVRA = re.compile(r"\S+")

#: Tamanho da janela usada para reconhecer eco. Menor que isto vira coincidência
#: de vocabulário — "contrato" e "escopo" aparecem em reunião de verdade.
JANELA = 3
#: Cobertura a partir da qual, se não sobrar frase, o trecho é descartado.
COBERTURA_MINIMA = 0.5
#: Trecho sobrevivente menor que isto, depois de um recorte, é resto de eco.
PALAVRAS_POR_TRECHO = 3
#: Abaixo disto, o que sobrou depois do recorte não é frase: descarta.
PALAVRAS_MINIMAS = 3
#: Variedade de palavras abaixo da qual um trecho longo é repetição em laço.
VARIEDADE_MINIMA = 0.35
PALAVRAS_PARA_AVALIAR_REPETICAO = 12
#: Trecho curto precisa ser mais monótono para ser condenado: "sim, sim, sim"
#: e "pode ser, pode ser" são fala real; "uf, uf, uf, uf, uf, uf, uf" não é.
VARIEDADE_MINIMA_CURTA = 0.25
PALAVRAS_PARA_AVALIAR_REPETICAO_CURTA = 5


@dataclass
class Veredito:
    """O que fazer com um trecho transcrito."""

    texto: str
    descartar: bool = False
    motivo: str | None = None
    #: True quando sobrou fala depois de remover a parte alucinada.
    recortado: bool = False


def normalizar(texto: str) -> list[str]:
    """Reduz a texto comparável: sem acento, sem pontuação, em minúsculas."""
    return [p for p in (_normalizar_palavra(p) for p in texto.split()) if p]


def avaliar(texto: str, *, initial_prompt: str = "") -> Veredito:
    """Decide se o trecho é fala, é invenção, ou é fala com invenção colada."""
    limpo, tirou_fantasma = _remover_fantasmas(texto)
    if not normalizar(limpo):
        return Veredito(texto="", descartar=True, motivo="frase-fantasma do modelo")

    if _e_despedida_isolada(normalizar(limpo)):
        return Veredito(texto="", descartar=True, motivo="frase-fantasma do modelo")

    recortado, cobertura = _remover_eco(limpo, initial_prompt)
    if cobertura > 0:
        restante = normalizar(recortado)
        if len(restante) < PALAVRAS_MINIMAS or cobertura >= COBERTURA_MINIMA and not restante:
            return Veredito(texto="", descartar=True, motivo="eco do prompt inicial")
        return Veredito(texto=recortado, motivo="eco do prompt inicial", recortado=True)

    if _e_repeticao(normalizar(limpo)):
        return Veredito(texto=limpo, descartar=True, motivo="repetição em laço")

    return Veredito(texto=limpo, recortado=tirou_fantasma)


# --------------------------------------------------------------------- internos


def _normalizar_palavra(palavra: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", palavra).encode("ascii", "ignore").decode()
    return _NAO_ALFANUM.sub("", sem_acento.lower())


def _remover_fantasmas(texto: str) -> tuple[str, bool]:
    limpo = _FANTASMAS.sub(" ", texto)
    if limpo == texto:
        return texto.strip(), False
    return _arrumar_pontuacao(limpo), True


def _remover_eco(texto: str, prompt: str) -> tuple[str, float]:
    """Tira do texto as janelas de palavras que vieram do prompt.

    Varre por janelas em vez de alinhar as duas sequências porque o eco quase
    sempre vem repetido — o mesmo pedaço do prompt três ou quatro vezes
    seguidas —, e um alinhamento casaria apenas a primeira cópia.
    """
    palavras_prompt = normalizar(prompt)
    if len(palavras_prompt) < JANELA:
        return texto, 0.0

    janelas = {
        tuple(palavras_prompt[i : i + JANELA])
        for i in range(len(palavras_prompt) - JANELA + 1)
    }

    posicoes = [
        (m.start(), m.end(), _normalizar_palavra(m.group())) for m in _PALAVRA.finditer(texto)
    ]
    indices = [i for i, (_, _, p) in enumerate(posicoes) if p]
    if len(indices) < JANELA:
        return texto, 0.0

    coberto: set[int] = set()
    for pos in range(len(indices) - JANELA + 1):
        grupo = indices[pos : pos + JANELA]
        if tuple(posicoes[i][2] for i in grupo) in janelas:
            coberto.update(grupo)

    if not coberto:
        return texto, 0.0

    _expandir_vizinhos(coberto, indices, posicoes, set(palavras_prompt))

    # Sobras de uma ou duas palavras entre pedaços removidos são restos do
    # próprio eco ("...deployment,"), não fala. Só o primeiro trecho é mantido
    # curto, porque é onde costuma estar a frase de verdade.
    sobreviventes = [i for i in indices if i not in coberto]
    guardar: set[int] = set()
    for trecho in _agrupar_consecutivos(sobreviventes):
        primeiro = trecho[0] == indices[0]
        if primeiro or len(trecho) >= PALAVRAS_POR_TRECHO:
            guardar.update(trecho)

    mantidos = [
        texto[inicio:fim]
        for i, (inicio, fim, palavra) in enumerate(posicoes)
        if i in guardar or (not palavra and i in guardar)
    ]
    return _arrumar_pontuacao(" ".join(mantidos)), len(coberto) / len(indices)


def _expandir_vizinhos(
    coberto: set[int], indices: list[int], posicoes: list, vocabulario: set[str]
) -> None:
    """Absorve as bordas do eco que a janela não pegou.

    O eco costuma ser cortado no meio ("…Tranquilo. Reunião corporativo" com o
    resto removido): a palavra que sobrou é do prompt, só que flexionada, e por
    isso não fechou janela. Vizinha de trecho já identificado como eco e com a
    mesma raiz de uma palavra do prompt, ela vai junto.
    """
    posicao_por_indice = {indice: ordem for ordem, indice in enumerate(indices)}
    mudou = True
    while mudou:
        mudou = False
        for indice in list(coberto):
            ordem = posicao_por_indice[indice]
            for vizinho in (ordem - 1, ordem + 1):
                if not 0 <= vizinho < len(indices):
                    continue
                alvo = indices[vizinho]
                if alvo in coberto:
                    continue
                if _mesma_raiz(posicoes[alvo][2], vocabulario):
                    coberto.add(alvo)
                    mudou = True


def _mesma_raiz(palavra: str, vocabulario: set[str]) -> bool:
    """Palavra do prompt, aceitando flexão ("corporativo" por "corporativa")."""
    if palavra in vocabulario:
        return True
    if len(palavra) < 5:
        return False
    return any(len(v) >= 5 and (v[:5] == palavra[:5]) for v in vocabulario)


def _agrupar_consecutivos(indices: list[int]) -> list[list[int]]:
    """Quebra a lista em trechos de índices vizinhos na sequência original."""
    grupos: list[list[int]] = []
    for indice in indices:
        if grupos and indice - grupos[-1][-1] == 1:
            grupos[-1].append(indice)
        else:
            grupos.append([indice])
    return grupos


def _arrumar_pontuacao(texto: str) -> str:
    """Junta os pedaços que sobraram sem deixar pontuação órfã."""
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    texto = re.sub(r"^[\s.,;:!?…–—-]+", "", texto)
    return re.sub(r"\s+([.,;:!?])", r"\1", texto).strip()


def _e_despedida_isolada(palavras: list[str]) -> bool:
    """True quando o trecho inteiro é só uma fórmula de despedida de vídeo."""
    texto = " ".join(palavras)
    return any(texto == frase for frase in FRASES_ISOLADAS)


def _e_repeticao(palavras: list[str]) -> bool:
    """True para o laço de repetição clássico do Whisper sobre ruído."""
    variedade = len(set(palavras)) / len(palavras) if palavras else 1.0
    if len(palavras) >= PALAVRAS_PARA_AVALIAR_REPETICAO:
        return variedade < VARIEDADE_MINIMA
    if len(palavras) >= PALAVRAS_PARA_AVALIAR_REPETICAO_CURTA:
        return variedade < VARIEDADE_MINIMA_CURTA
    return False
