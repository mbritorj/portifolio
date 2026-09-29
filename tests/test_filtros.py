"""Filtro de alucinações, com trechos reais de uma reunião de 52 minutos.

Os textos abaixo saíram da primeira gravação de verdade: 40% dos trechos do
microfone eram o próprio prompt devolvido como fala.
"""

from __future__ import annotations

import pytest

from escriba.asr.filtros import avaliar
from escriba.config import PROMPT_GENERICO_ANTIGO as PROMPT_INICIAL_PADRAO

ECO_PURO = [
    "Reunião corporativo em português do Brasil.",
    "Reunião corporativa em português do Brasil. Termos frequentes.",
    "SLA, TR, edital, licitação, pregão, backlog, sprint, deploy, firewall,",
    "Reunião corporativa em português do Brasil Reunião corporativa em português do Brasil.",
    "SLA, TR, edital, licitação, pregão, backlog, sprint, deploy, firewall, "
    "switch, roteador, datacenter, nuvem.",
]

FALA_REAL = [
    "Vamos lá. VPN, acredito que sim, porque eles têm comunicações das substações "
    "com datacenter deles, que é onde vai ficar o software de gerencia.",
    "Não, mas tá confirmado, não precisa de esse filtro, esse filtro PSX.",
    "Tá, vamos mandar aqui, dois minutos.",
    "Sim, pode ser, pode ser, pode ser, melhor.",
    "Eu vou atualizar o caderno de testes com essa nova apresentação que o Albino "
    "vai mandar para a gente.",
    # Contém palavras do prompt, mas em frase de verdade.
    "A gente precisa fechar o cronograma e a entrega com o cliente.",
    "Não, pode mandar powerpoint mesmo, tem problema não.",
]


@pytest.mark.parametrize("texto", ECO_PURO)
def test_eco_do_prompt_e_descartado(texto):
    veredito = avaliar(texto, initial_prompt=PROMPT_INICIAL_PADRAO)
    assert veredito.descartar is True
    assert veredito.motivo == "eco do prompt inicial"
    assert veredito.texto == ""


@pytest.mark.parametrize("texto", FALA_REAL)
def test_fala_real_passa_intacta(texto):
    veredito = avaliar(texto, initial_prompt=PROMPT_INICIAL_PADRAO)
    assert veredito.descartar is False
    assert veredito.texto == texto


def test_fala_colada_no_eco_preserva_a_fala():
    """O caso que mais dói: perder fala real por causa do eco grudado nela."""
    texto = (
        "Opa, Beno, boa tarde, tá me vendo? Tá bom, feliz. SLA, TR, edital, "
        "licitação, pregão, backlog, sprint, deploy, firewall, SLA, TR, edital, "
        "licitação, pregão, backlog, sprint, deployment, switch, roteador, "
        "datacenter, nuvem, contrato,"
    )
    veredito = avaliar(texto, initial_prompt=PROMPT_INICIAL_PADRAO)

    assert veredito.descartar is False
    assert veredito.recortado is True
    assert veredito.texto == "Opa, Beno, boa tarde, tá me vendo? Tá bom, feliz."


def test_eco_cortado_no_meio_leva_a_borda_junto():
    """O eco truncado deixa uma palavra flexionada do prompt para trás."""
    texto = (
        "Tá, no bilhete. Se a confirmou, está confirmado. Tranquilo. "
        "Reunião corporativo em português do Brasil"
    )
    veredito = avaliar(texto, initial_prompt=PROMPT_INICIAL_PADRAO)
    assert veredito.texto == "Tá, no bilhete. Se a confirmou, está confirmado. Tranquilo."


def test_frase_fantasma_sozinha_e_descartada():
    veredito = avaliar("Legendas por Paulo Montenegro")
    assert veredito.descartar is True
    assert veredito.motivo == "frase-fantasma do modelo"


def test_frase_fantasma_no_meio_da_fala_e_removida():
    veredito = avaliar("Legendas por Paulo Montenegro Desculpe. Oi, Mara. Tudo bem?")
    assert veredito.descartar is False
    assert veredito.texto == "Oi, Mara. Tudo bem?"


def test_repeticao_em_laco_e_descartada():
    veredito = avaliar("o mesmo o mesmo o mesmo o mesmo o mesmo o mesmo o mesmo o mesmo")
    assert veredito.descartar is True
    assert veredito.motivo == "repetição em laço"


def test_repeticao_curta_e_fala_normal():
    """'Sim, sim, sim' é resposta de gente, não laço do modelo."""
    assert avaliar("Sim, sim, sim.").descartar is False


def test_sem_prompt_nao_ha_eco_a_procurar():
    texto = "Reunião corporativa em português do Brasil."
    assert avaliar(texto, initial_prompt="").descartar is False


def test_texto_vazio_e_descartado():
    assert avaliar("   ").descartar is True


def test_glossario_de_nomes_nao_derruba_fala():
    """Com o prompt novo (nomes próprios), fala normal não vira eco."""
    glossario = "Albino, Patrícia Igreja, Yves, Q13, Nokia, Taesa, FiberSense, transponder."
    texto = "O Albino vai mandar a última versão para a Patrícia e para o Yves hoje."
    assert avaliar(texto, initial_prompt=glossario).descartar is False
