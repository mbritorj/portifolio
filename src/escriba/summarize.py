"""Ata, decisões e itens de ação a partir da transcrição, via Claude API.

Etapa opcional e explicitamente remota: tudo o mais no Escriba roda na máquina
local. Só o texto já transcrito sai daqui, e apenas quando alguém pede o resumo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import SummaryConfig
from .session import Session

# Pedido em primeira pessoa, para colar no Claude junto com o arquivo exportado.
# É o mesmo conteúdo que o comando `escriba ata` manda pela API, para que os dois
# caminhos produzam o mesmo documento.
PEDIDO_ATA = """\
Anexei a transcrição automática de uma reunião em português do Brasil. O começo \
do arquivo lista quem falou e como cada nome foi obtido.

Com base apenas nessa transcrição, produza em markdown:

## Resumo
Três a seis frases sobre o que a reunião tratou e onde chegou.

## Tópicos discutidos
Um item por assunto, com o que foi dito e onde ficou. Cite o horário do trecho \
entre parênteses.

## Tarefas
Tabela com as colunas: Tarefa | Responsável | Prazo | Onde foi dito.
Uma linha por tarefa acordada. Em "Onde foi dito", ponha o horário do trecho \
que originou a tarefa.

## Pontos em aberto
Questões levantadas e não resolvidas, e decisões que ficaram para depois.

Regras:

- Não invente nada. Se a reunião não tratou de algo, não preencha a seção — \
escreva que não houve.
- Responsável: use o nome só quando a pessoa assumiu a tarefa ou foi designada \
na transcrição. Use "não identificado" quando quem assumiu for uma voz sem \
nome, e "não definido" quando ninguém assumiu.
- Prazo: a data ou o marco dito na reunião, ou "não definido".
- A transcrição é automática e tem erros. Interprete pelo contexto e não \
transforme trecho ambíguo em afirmação. Se uma tarefa depender de um trecho \
marcado com (?), assinale a dúvida na linha da tabela.
- Escreva direto. Sem preâmbulo, sem elogiar a reunião e sem considerações \
finais.
"""

SYSTEM_PROMPT = """\
Você redige atas de reunião em português do Brasil a partir de transcrições \
automáticas.

A transcrição vem de reconhecimento de fala e tem erros: palavras trocadas, \
frases cortadas e nomes grafados de forma inconsistente. Interprete pelo \
contexto, sem inventar conteúdo. Quando um trecho for ambíguo demais para \
afirmar algo, registre a dúvida em vez de escolher uma versão.

O início do arquivo diz quem falou e de onde veio cada nome. Nomes de pessoas \
são confiáveis. Rótulos como "Falante 2" são vozes distintas sem identificação: \
não deduza quem são. "Eu" é quem gravou a reunião. Falas simultâneas não são \
separadas, então um trecho pode misturar duas pessoas.

Escreva direto. Não elogie a reunião, não abra com preâmbulo e não feche com \
considerações finais.
"""


@dataclass
class Summary:
    markdown: str
    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0


class SummaryError(RuntimeError):
    pass


def gerar_ata(
    session: Session,
    config: SummaryConfig | None = None,
    *,
    instrucoes: str | None = None,
) -> Summary:
    """Gera a ata da sessão. Levanta ``SummaryError`` se faltar a dependência ou a chave."""
    config = config or SummaryConfig()
    if not config.enabled:
        raise SummaryError("geração de ata desativada na configuração.")

    # O arquivo exportado tem cabeçalho mesmo sem falas, então o que decide é a
    # existência de trechos transcritos, não o tamanho do texto.
    if not session.segments:
        raise SummaryError("a transcrição está vazia; nada a resumir.")
    transcricao = session.to_claude().strip()

    try:
        import anthropic
    except ImportError as exc:
        raise SummaryError(
            "SDK da Anthropic não instalado. Use: pip install 'escriba[ata]'"
        ) from exc

    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # credencial ausente ou inválida
        raise SummaryError(
            "não foi possível autenticar na Claude API. Defina ANTHROPIC_API_KEY "
            "ou rode 'ant auth login'."
        ) from exc

    pedido = [PEDIDO_ATA, "", "---", "", transcricao]
    if instrucoes:
        pedido += ["", f"Instruções adicionais: {instrucoes}"]

    try:
        # Streaming porque a ata de uma reunião longa é uma requisição longa, e o
        # tempo de resposta passa fácil do limite de uma chamada sem streaming.
        with client.messages.stream(
            model=config.model,
            max_tokens=config.max_tokens,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": "\n".join(pedido)}],
        ) as stream:
            mensagem = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise SummaryError(f"a Claude API respondeu {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError(f"falha de conexão com a Claude API: {exc}") from exc

    if mensagem.stop_reason == "refusal":
        raise SummaryError("o modelo recusou a solicitação de ata.")

    texto = "\n".join(
        bloco.text for bloco in mensagem.content if bloco.type == "text"
    ).strip()
    if not texto:
        raise SummaryError("a Claude API devolveu uma resposta vazia.")

    return Summary(
        markdown=texto,
        modelo=config.model,
        tokens_entrada=mensagem.usage.input_tokens,
        tokens_saida=mensagem.usage.output_tokens,
    )
