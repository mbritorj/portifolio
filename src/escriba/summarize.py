"""Ata, decisões e itens de ação a partir da transcrição, via Claude API.

Etapa opcional e explicitamente remota: tudo o mais no Escriba roda na máquina
local. Só o texto já transcrito sai daqui, e apenas quando alguém pede o resumo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import SummaryConfig
from .session import Session

SYSTEM_PROMPT = """\
Você redige atas de reunião em português do Brasil a partir de transcrições \
automáticas.

A transcrição vem de reconhecimento de fala e tem erros: palavras trocadas, \
frases cortadas e nomes grafados de forma inconsistente. Interprete pelo \
contexto, sem inventar conteúdo. Quando um trecho for ambíguo demais para \
afirmar algo, registre a dúvida em vez de escolher uma versão.

Os falantes aparecem separados apenas por origem do áudio ("Eu" é quem gravou; \
"Participantes" reúne todos os demais, sem distinção entre eles). Não atribua \
falas a pessoas específicas além dessa divisão, a menos que alguém se \
identifique pelo nome na própria transcrição.

Estrutura da resposta, em markdown:

## Resumo
Três a seis frases sobre o que a reunião tratou e onde chegou.

## Decisões
Lista do que foi decidido. Se nada foi decidido, escreva "Nenhuma decisão \
registrada."

## Itens de ação
Tabela com as colunas Ação | Responsável | Prazo. Use "não definido" onde a \
transcrição não disser.

## Pontos em aberto
Questões levantadas e não resolvidas.

Escreva de forma direta. Não elogie a reunião, não abra com preâmbulo e não \
feche com considerações finais.\
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

    transcricao = session.to_text().strip()
    if not transcricao:
        raise SummaryError("a transcrição está vazia; nada a resumir.")

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

    pedido = [
        f"Reunião: {session.titulo}",
        f"Data: {session.iniciada_em.strftime('%d/%m/%Y %H:%M')}",
        "",
        "Transcrição:",
        "",
        transcricao,
    ]
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
