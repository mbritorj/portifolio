"""Agrupamento de falantes e cadastro de vozes, com vetores sintéticos."""

from __future__ import annotations

import numpy as np
import pytest

from escriba.diarize import (
    ConsentimentoAusente,
    OnlineSpeakerClusterer,
    VoiceProfileStore,
    cosseno,
    normalizar,
)
from escriba.diarize.mock import MockEmbedder


def voz(indice: int, ruido: float = 0.0, dim: int = 32, seed: int = 0) -> np.ndarray:
    """Vetor estável por pessoa, com uma perturbação controlada por fala."""
    base = np.zeros(dim, dtype=np.float32)
    base[indice % dim] = 1.0
    base[(indice * 7 + 3) % dim] = 0.6
    if ruido:
        base = base + np.random.default_rng(seed).normal(0, ruido, dim).astype(np.float32)
    return normalizar(base)


# ------------------------------------------------------------------- clustering


def test_falas_da_mesma_voz_caem_no_mesmo_falante():
    clusterer = OnlineSpeakerClusterer(threshold=0.55)
    atribuicoes = [clusterer.assign(voz(1, 0.05, seed=i), 3.0) for i in range(5)]

    assert len({a.speaker.id for a in atribuicoes}) == 1
    assert atribuicoes[0].is_new is True
    assert all(a.is_new is False for a in atribuicoes[1:])
    assert clusterer.speakers[0].utterances == 5


def test_vozes_diferentes_viram_falantes_diferentes():
    clusterer = OnlineSpeakerClusterer(threshold=0.55)
    a = clusterer.assign(voz(1), 3.0)
    b = clusterer.assign(voz(9), 3.0)
    c = clusterer.assign(voz(20), 3.0)

    assert len({a.speaker.id, b.speaker.id, c.speaker.id}) == 3
    assert [s.label for s in clusterer.speakers] == ["Falante 1", "Falante 2", "Falante 3"]


def test_conversa_alternada_mantem_dois_falantes():
    clusterer = OnlineSpeakerClusterer(threshold=0.55)
    ids = []
    for volta in range(6):
        ids.append(clusterer.assign(voz(1, 0.05, seed=volta), 4.0).speaker.id)
        ids.append(clusterer.assign(voz(15, 0.05, seed=volta + 50), 4.0).speaker.id)

    assert len(set(ids)) == 2
    assert ids[0::2] == [ids[0]] * 6  # o primeiro falante volta sempre ao mesmo grupo
    assert ids[1::2] == [ids[1]] * 6


def test_teto_de_falantes_e_respeitado():
    clusterer = OnlineSpeakerClusterer(threshold=0.9, max_speakers=3)
    for indice in range(10):
        clusterer.assign(voz(indice * 3), 2.0)
    assert len(clusterer.speakers) == 3


def test_centroide_pesa_mais_a_fala_longa():
    clusterer = OnlineSpeakerClusterer(threshold=0.3)
    clusterer.assign(voz(1), 20.0)     # fala longa define o timbre
    clusterer.assign(voz(2), 0.2)      # interjeição curta quase não desloca
    centroide = clusterer.speakers[0].centroid
    assert cosseno(centroide, voz(1)) > cosseno(centroide, voz(2))


def test_renomear_marca_a_origem():
    clusterer = OnlineSpeakerClusterer()
    atribuicao = clusterer.assign(voz(1), 3.0)
    resultado = clusterer.rename(atribuicao.speaker.id, "Ana Souza")

    assert resultado.absorbed == []
    assert clusterer.get(atribuicao.speaker.id).label == "Ana Souza"
    assert clusterer.get(atribuicao.speaker.id).source == "manual"


def test_renomear_para_um_nome_existente_funde_os_falantes():
    clusterer = OnlineSpeakerClusterer(threshold=0.95)
    primeiro = clusterer.assign(voz(1), 10.0).speaker.id
    segundo = clusterer.assign(voz(9), 3.0).speaker.id
    clusterer.rename(primeiro, "Ana")

    resultado = clusterer.rename(segundo, "Ana")

    assert len(clusterer.speakers) == 1
    assert resultado.speaker_id == primeiro
    assert resultado.absorbed == [segundo]
    assert clusterer.get(primeiro).utterances == 2


def test_renomear_falante_inexistente_falha():
    with pytest.raises(KeyError):
        OnlineSpeakerClusterer().rename("S9", "Ana")


def test_nome_vazio_e_recusado():
    clusterer = OnlineSpeakerClusterer()
    identificador = clusterer.assign(voz(1), 2.0).speaker.id
    with pytest.raises(ValueError, match="vazio"):
        clusterer.rename(identificador, "   ")


def test_centroides_saem_serializaveis():
    clusterer = OnlineSpeakerClusterer()
    clusterer.assign(voz(1), 2.0)
    centroides = clusterer.centroids()
    assert list(centroides) == ["S1"]
    assert all(isinstance(v, float) for v in centroides["S1"])


# ---------------------------------------------------------------------- perfis


def test_cadastro_exige_consentimento(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    with pytest.raises(ConsentimentoAusente):
        store.add("Ana", voz(1))
    assert len(store) == 0


def test_cadastro_persiste_e_recarrega(tmp_path):
    caminho = tmp_path / "vozes.json"
    VoiceProfileStore(caminho).add("Ana Souza", voz(1), consent=True)

    recarregado = VoiceProfileStore(caminho)
    assert len(recarregado) == 1
    perfil = recarregado.get("ana souza")   # busca sem acento e sem caixa
    assert perfil is not None and perfil.consent is True and perfil.consent_at


def test_cadastrar_de_novo_reforca_o_perfil(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana", voz(1, 0.05, seed=1), consent=True)
    perfil = store.add("Ana", voz(1, 0.05, seed=2), consent=True)

    assert len(store) == 1
    assert perfil.samples == 2


def test_identificacao_respeita_o_limiar(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana", voz(1), consent=True)

    achado = store.identify(voz(1, 0.05, seed=3), threshold=0.62)
    assert achado is not None and achado.name == "Ana" and achado.score > 0.62
    assert store.identify(voz(25), threshold=0.62) is None


def test_perfil_de_outro_modelo_e_ignorado(tmp_path):
    """Trocar o modelo muda a dimensão do vetor; melhor ignorar do que errar."""
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana", voz(1, dim=64), consent=True)
    assert store.identify(voz(1, dim=32)) is None


def test_remocao(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana", voz(1), consent=True)
    assert store.remove("Ana") is True
    assert store.remove("Ana") is False
    assert len(VoiceProfileStore(tmp_path / "vozes.json")) == 0


def test_clusterer_usa_o_nome_cadastrado(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana Souza", voz(1), consent=True)
    clusterer = OnlineSpeakerClusterer(profiles=store, profile_threshold=0.62)

    atribuicao = clusterer.assign(voz(1, 0.05, seed=4), 3.0)
    assert atribuicao.matched_profile is True
    assert atribuicao.speaker.label == "Ana Souza"
    assert atribuicao.speaker.source == "perfil"

    # A segunda fala da mesma pessoa cai no mesmo falante, não em um novo.
    outra = clusterer.assign(voz(1, 0.05, seed=5), 3.0)
    assert outra.speaker.id == atribuicao.speaker.id
    assert len(clusterer.speakers) == 1


def test_voz_desconhecida_continua_anonima(tmp_path):
    store = VoiceProfileStore(tmp_path / "vozes.json")
    store.add("Ana", voz(1), consent=True)
    clusterer = OnlineSpeakerClusterer(profiles=store)

    atribuicao = clusterer.assign(voz(20), 3.0)
    assert atribuicao.matched_profile is False
    assert atribuicao.speaker.label == "Falante 1"


# -------------------------------------------------------------------- embedder


def test_mock_embedder_separa_timbres():
    embedder = MockEmbedder()
    sr = 16_000
    t = np.arange(sr * 2) / sr

    grave_a = embedder.embed((0.3 * np.sin(2 * np.pi * 120 * t)).astype(np.float32))
    grave_b = embedder.embed((0.2 * np.sin(2 * np.pi * 122 * t)).astype(np.float32))
    agudo = embedder.embed((0.3 * np.sin(2 * np.pi * 260 * t)).astype(np.float32))

    assert cosseno(grave_a, grave_b) > cosseno(grave_a, agudo)
    assert embedder.chamadas == 3


@pytest.mark.skipif(
    not __import__("os").environ.get("ESCRIBA_TEST_MODEL"),
    reason="defina ESCRIBA_TEST_MODEL com o caminho de um .onnx de embedding de falante",
)
def test_extrator_sherpa_com_modelo_real():
    """Roda o extrator de verdade quando há um modelo à mão.

    Fica fora da suíte padrão de propósito: baixar 28 MB não pode ser requisito
    para rodar os testes.
    """
    import os

    from escriba.diarize.sherpa_embedder import SherpaEmbedder

    embedder = SherpaEmbedder(os.environ["ESCRIBA_TEST_MODEL"], num_threads=2)
    sr = 16_000
    t = np.arange(int(sr * 3)) / sr

    def timbre(f0: float, seed: int) -> np.ndarray:
        harmonicos = sum((0.5 / k) * np.sin(2 * np.pi * f0 * k * t) for k in range(1, 6))
        silabas = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)
        ruido = np.random.default_rng(seed).normal(0, 0.001, t.size)
        return (0.2 * harmonicos * silabas + ruido).astype(np.float32)

    grave_a = embedder.embed(timbre(120, 1))
    grave_b = embedder.embed(timbre(120, 2))
    agudo = embedder.embed(timbre(210, 3))

    assert grave_a.shape == (embedder.dim,)
    assert np.isclose(np.linalg.norm(grave_a), 1.0, atol=1e-5)
    assert cosseno(grave_a, grave_b) > cosseno(grave_a, agudo)
