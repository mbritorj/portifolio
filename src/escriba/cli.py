"""Linha de comando do Escriba."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from .config import AppConfig

DESCRICAO = """\
Escriba — transcrição de reuniões em tempo real, em português do Brasil,
sem entrar na sala como bot. O áudio é capturado da própria máquina.
"""


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="escriba", description=DESCRICAO,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="arquivo TOML de configuração")
    parser.add_argument("--motor", help="motor de transcrição (faster-whisper, mock)")
    parser.add_argument("--modelo", help="modelo Whisper (tiny, base, small, medium, large-v3)")
    parser.add_argument(
        "--diarizar", action="store_true",
        help="separa as vozes dos participantes remotos (precisa de --modelo-voz)",
    )
    parser.add_argument("--modelo-voz", help="caminho do .onnx de embedding de falante")
    parser.add_argument("-v", "--verboso", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("dispositivos", help="lista as entradas de áudio e marca as de loopback")

    gravar = sub.add_parser("gravar", help="transcreve uma reunião ao vivo no terminal")
    gravar.add_argument("--titulo", help="título da reunião")
    gravar.add_argument("--so-sistema", action="store_true",
                        help="captura apenas o áudio dos outros participantes")
    gravar.add_argument("--so-microfone", action="store_true",
                        help="captura apenas o seu microfone")

    servir = sub.add_parser("servir", help="abre a interface web ao vivo")
    servir.add_argument("--porta", type=int, help="porta HTTP (padrão 8777)")
    servir.add_argument("--host", help="endereço de escuta (padrão 127.0.0.1)")

    arquivo = sub.add_parser("arquivo", help="transcreve um WAV já gravado")
    arquivo.add_argument("caminho", help="arquivo WAV PCM 16 bits")
    arquivo.add_argument("--falante", default="Participantes", help="rótulo do falante")
    arquivo.add_argument("--formatos", default="md,json",
                         help="formatos de saída separados por vírgula (md, txt, srt, json)")

    ata = sub.add_parser("ata", help="gera ata e itens de ação a partir de uma transcrição .json")
    ata.add_argument("caminho", help="arquivo .json exportado pelo Escriba")
    ata.add_argument("--instrucoes", help="orientação extra para a ata")
    ata.add_argument("--saida", help="arquivo .md de destino (padrão: ao lado do .json)")

    vozes = sub.add_parser("vozes", help="cadastro de vozes (nome automático dos falantes)")
    acao = vozes.add_subparsers(dest="acao", required=True)
    acao.add_parser("listar", help="mostra as vozes cadastradas")
    cadastrar = acao.add_parser("cadastrar", help="cadastra a voz de uma pessoa")
    cadastrar.add_argument("nome", help="nome da pessoa")
    cadastrar.add_argument("--audio", required=True, help="WAV com a voz dela (20 a 30 s bastam)")
    cadastrar.add_argument(
        "--sim", action="store_true", help="confirma o consentimento sem perguntar"
    )
    remover = acao.add_parser("remover", help="apaga uma voz cadastrada")
    remover.add_argument("nome", help="nome ou identificador do cadastro")

    nomear = sub.add_parser(
        "nomear", help="dá nome às vozes usando o transcript oficial da plataforma"
    )
    nomear.add_argument("caminho", help="arquivo .json exportado pelo Escriba")
    nomear.add_argument(
        "--transcript", required=True,
        help="transcript da plataforma (.vtt, .srt ou texto com hora e nome)",
    )
    nomear.add_argument(
        "--offset", type=float,
        help="defasagem em segundos entre os dois relógios (o padrão é descobrir sozinho)",
    )
    nomear.add_argument(
        "--min-confianca", type=float, default=0.5,
        help="fração mínima de sobreposição para aceitar um nome (padrão 0.5)",
    )
    nomear.add_argument(
        "--simular", action="store_true", help="mostra as propostas sem alterar nada"
    )
    nomear.add_argument(
        "--cadastrar", action="store_true",
        help="cadastra as vozes identificadas, para a próxima reunião já sair com nome",
    )
    nomear.add_argument(
        "--sim", action="store_true", help="confirma o consentimento sem perguntar"
    )

    sub.add_parser(
        "autoteste",
        help="valida o pipeline com áudio sintético, sem modelo nem microfone",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verboso else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = AppConfig.load(args.config)
    if args.motor:
        config.asr.engine = args.motor
    if args.modelo:
        config.asr.model = args.modelo
    if args.modelo_voz:
        config.diarizacao.model = args.modelo_voz
    if args.diarizar:
        config.diarizacao.enabled = True

    acoes = {
        "dispositivos": _cmd_dispositivos,
        "gravar": _cmd_gravar,
        "servir": _cmd_servir,
        "arquivo": _cmd_arquivo,
        "ata": _cmd_ata,
        "vozes": _cmd_vozes,
        "nomear": _cmd_nomear,
        "autoteste": _cmd_autoteste,
    }
    try:
        return acoes[args.comando](config, args)
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------------ comandos


def _cmd_dispositivos(config: AppConfig, _args) -> int:
    from .audio.devices import DeviceError, list_input_devices

    try:
        dispositivos = list_input_devices()
    except DeviceError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    if not dispositivos:
        print("nenhuma entrada de áudio encontrada.")
        return 1
    print("Entradas de áudio disponíveis:\n")
    for dispositivo in dispositivos:
        print(f"  {dispositivo}")
    if not any(d.is_loopback for d in dispositivos):
        print(
            "\nNenhuma fonte de loopback detectada — sem ela o Escriba não ouve os\n"
            "outros participantes. Veja a seção 'Captura por sistema operacional'\n"
            "no README."
        )
    return 0


def _cmd_gravar(config: AppConfig, args) -> int:
    from .audio.capture import build_source
    from .audio.devices import DeviceError
    from .pipeline import TranscriptionPipeline
    from .session import Session

    if args.so_sistema:
        config.audio.capture_mic = False
    if args.so_microfone:
        config.audio.capture_system = False
    if not (config.audio.capture_mic or config.audio.capture_system):
        print("erro: nada a capturar.", file=sys.stderr)
        return 1

    session = Session(args.titulo)
    pipeline = TranscriptionPipeline(config, session=session, on_event=_imprimir_evento)

    trilhas = [
        ("sistema", config.system_label, config.audio.system_device, config.audio.capture_system),
        ("microfone", config.mic_label, config.audio.mic_device, config.audio.capture_mic),
    ]
    for nome, rotulo, spec, ativo in trilhas:
        if not ativo:
            continue
        try:
            source = build_source(
                nome, spec=spec, backend=config.audio.backend,
                target_rate=config.audio.sample_rate, block_ms=config.audio.block_ms, name=nome,
            )
        except DeviceError as exc:
            print(f"erro na trilha {nome}: {exc}", file=sys.stderr)
            return 1
        pipeline.add_track(nome, rotulo, source)

    parada = threading.Event()

    def encerrar(_sig, _frame):
        if not parada.is_set():
            print("\n\nencerrando (transcrevendo o que ficou na fila)…")
        parada.set()

    signal.signal(signal.SIGINT, encerrar)
    print(f"Gravando «{session.titulo}». Ctrl+C encerra e salva.\n")
    pipeline.run_until_complete(parada=parada)

    caminhos = session.salvar(config.output_dir, formatos=("md", "json", "txt"))
    print("\nSalvo em:")
    for caminho in caminhos:
        print(f"  {caminho}")
    return 0


def _cmd_servir(config: AppConfig, args) -> int:
    from .server import servir

    if args.porta:
        config.server.port = args.porta
    if args.host:
        config.server.host = args.host
    print(f"Escriba em http://{config.server.host}:{config.server.port}")
    servir(config)
    return 0


def _cmd_arquivo(config: AppConfig, args) -> int:
    from .audio.capture import WavFileSource
    from .pipeline import TranscriptionPipeline
    from .session import Session

    caminho = Path(args.caminho)
    if not caminho.exists():
        print(f"erro: {caminho} não existe.", file=sys.stderr)
        return 1

    session = Session(caminho.stem)
    pipeline = TranscriptionPipeline(config, session=session, on_event=_imprimir_evento)
    pipeline.add_track(
        "arquivo", args.falante,
        WavFileSource(
            caminho,
            target_rate=config.audio.sample_rate,
            block_ms=config.audio.block_ms,
        ),
    )
    pipeline.run_until_complete()

    formatos = tuple(f.strip() for f in args.formatos.split(",") if f.strip())
    caminhos = session.salvar(config.output_dir, formatos=formatos)
    print("\nSalvo em:")
    for destino in caminhos:
        print(f"  {destino}")
    return 0


def _cmd_ata(config: AppConfig, args) -> int:
    from .session import Session
    from .summarize import SummaryError, gerar_ata

    origem = Path(args.caminho)
    if not origem.exists():
        print(f"erro: {origem} não existe.", file=sys.stderr)
        return 1

    session = Session.from_json(origem.read_text(encoding="utf-8"))
    try:
        resumo = gerar_ata(session, config.summary, instrucoes=args.instrucoes)
    except SummaryError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    destino = Path(args.saida) if args.saida else origem.with_name(f"{origem.stem}_ata.md")
    destino.write_text(resumo.markdown + "\n", encoding="utf-8")
    print(resumo.markdown)
    print(
        f"\n[{resumo.modelo}: {resumo.tokens_entrada} tokens de entrada, "
        f"{resumo.tokens_saida} de saída]\nSalvo em: {destino}"
    )
    return 0


def _cmd_vozes(config: AppConfig, args) -> int:
    from .diarize import VoiceProfileStore
    from .diarize.profiles import AVISO_LGPD

    caminho = config.caminho_vozes()
    store = VoiceProfileStore(caminho)

    if args.acao == "listar":
        if not len(store):
            print(f"nenhuma voz cadastrada em {caminho}.")
            return 0
        print(f"Vozes cadastradas em {caminho}:\n")
        for perfil in store.profiles:
            print(
                f"  {perfil.name}  [{perfil.profile_id}]  "
                f"{perfil.samples} amostra(s), cadastrada em {perfil.created_at[:10]}"
            )
        return 0

    if args.acao == "remover":
        if store.remove(args.nome):
            print(f"voz de {args.nome} removida.")
            return 0
        print(f"erro: {args.nome!r} não está cadastrado.", file=sys.stderr)
        return 1

    # cadastrar
    origem = Path(args.audio)
    if not origem.exists():
        print(f"erro: {origem} não existe.", file=sys.stderr)
        return 1
    if not _confirmar(AVISO_LGPD, args.sim):
        print("cadastro cancelado.")
        return 1

    try:
        vetor = _impressao_vocal(config, origem)
    except (RuntimeError, ValueError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    perfil = store.add(args.nome, vetor, consent=True, note=f"cadastrado de {origem.name}")
    print(f"voz de {perfil.name} cadastrada em {caminho} ({perfil.samples} amostra(s)).")
    return 0


def _impressao_vocal(config: AppConfig, caminho: Path):
    """Extrai o vetor da voz de um WAV, usando só os trechos com fala."""
    import numpy as np

    from .audio.capture import WavFileSource
    from .audio.vad import EnergyVad
    from .diarize import create_embedder

    vad = EnergyVad(config.vad, config.audio.sample_rate, config.audio.block_ms)
    falas = []
    fonte = WavFileSource(
        caminho, target_rate=config.audio.sample_rate, block_ms=config.audio.block_ms
    )
    for bloco in fonte.blocks():
        falas += [u.audio for u in vad.process(bloco)]
    restante = vad.flush()
    if restante is not None:
        falas.append(restante.audio)

    if not falas:
        raise ValueError(
            f"não encontrei fala em {caminho.name}. Grave 20 a 30 segundos falando normalmente."
        )
    audio = np.concatenate(falas)
    duracao = audio.size / config.audio.sample_rate
    if duracao < 5:
        print(f"aviso: só {duracao:.1f}s de fala; o cadastro fica mais firme com 20 a 30 s.")

    embedder = create_embedder(config.diarizacao)
    try:
        return embedder.embed(audio, config.audio.sample_rate)
    finally:
        embedder.close()


def _cmd_nomear(config: AppConfig, args) -> int:
    from .attribution import TranscriptError, aplicar, ler_transcript, mapear
    from .session import Session

    origem, transcript = Path(args.caminho), Path(args.transcript)
    for arquivo in (origem, transcript):
        if not arquivo.exists():
            print(f"erro: {arquivo} não existe.", file=sys.stderr)
            return 1

    session = Session.from_json(origem.read_text(encoding="utf-8"))
    if not any(s.speaker_id for s in session.segments):
        print(
            "erro: esta transcrição não tem vozes separadas. Grave com --diarizar "
            "para que haja o que nomear.",
            file=sys.stderr,
        )
        return 1

    try:
        cues = ler_transcript(transcript)
    except TranscriptError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    propostas, offset = mapear(
        session, cues, offset_s=args.offset, min_confianca=args.min_confianca
    )
    print(f"Defasagem entre os relógios: {offset:+.1f}s\n")
    if not propostas:
        print("nenhuma voz atingiu a confiança mínima; nada foi alterado.")
        return 1

    for proposta in propostas:
        print(
            f"  {proposta.label_atual} → {proposta.nome}  "
            f"(confiança {proposta.confianca:.0%}, {proposta.sobreposicao_s:.0f}s em comum)"
        )
        outras = [n for n in proposta.alternativas if n != proposta.nome][:2]
        if outras:
            print(f"      também apareceu junto de: {', '.join(outras)}")

    if args.simular:
        print("\n(simulação: nada foi gravado)")
        return 0

    aplicar(session, propostas)
    caminhos = session.salvar(origem.parent, formatos=("md", "json", "txt"))
    print("\nAtualizado:")
    for caminho in caminhos:
        print(f"  {caminho}")

    if args.cadastrar:
        return _cadastrar_das_propostas(config, session, args.sim)
    return 0


def _cadastrar_das_propostas(config: AppConfig, session, sem_perguntar: bool) -> int:
    """Transforma os centroides desta reunião em cadastro de voz.

    É o atalho que o transcript oficial abre: quem foi identificado aqui passa a
    ser reconhecido ao vivo na próxima reunião, sem ninguém gravar amostra.
    """
    from .diarize import VoiceProfileStore
    from .diarize.profiles import AVISO_LGPD

    # Depois de aplicar as propostas, a sessão é a fonte da verdade: nomes já
    # renomeados, vozes já fundidas, centroides das que sobreviveram.
    disponiveis = {
        falante["speaker_id"]: falante["label"]
        for falante in session.speakers
        if falante.get("has_centroid") and falante.get("source") == "manual"
    }
    if not disponiveis:
        print(
            "\naviso: esta transcrição não guardou impressões vocais "
            "(gravada antes da diarização?), então não há o que cadastrar."
        )
        return 0

    pergunta = f"{AVISO_LGPD}\n\nCadastrar: {', '.join(sorted(disponiveis.values()))}"
    if not _confirmar(pergunta, sem_perguntar):
        print("cadastro cancelado.")
        return 0

    store = VoiceProfileStore(config.caminho_vozes())
    for speaker_id, nome in disponiveis.items():
        store.add(
            nome, session.centroids[speaker_id], consent=True, note="identificado pelo transcript"
        )
    print(f"\n{len(disponiveis)} voz(es) cadastrada(s) em {config.caminho_vozes()}.")
    return 0


def _confirmar(mensagem: str, sem_perguntar: bool) -> bool:
    print(f"\n{mensagem}\n")
    if sem_perguntar:
        return True
    if not sys.stdin.isatty():
        print("erro: sem terminal interativo; use --sim para confirmar.", file=sys.stderr)
        return False
    return input("Confirma? [s/N] ").strip().lower() in {"s", "sim", "y", "yes"}


def _cmd_autoteste(config: AppConfig, _args) -> int:
    """Roda captura, VAD, transcrição e exportação com áudio sintético."""
    import tempfile
    import wave

    import numpy as np

    from .asr.mock import MockTranscriber
    from .audio.capture import WavFileSource
    from .pipeline import TranscriptionPipeline
    from .session import Session

    taxa = config.audio.sample_rate
    gerador = np.random.default_rng(7)

    def fala(duracao: float, frequencia: float) -> np.ndarray:
        t = np.arange(int(taxa * duracao)) / taxa
        envelope = np.clip(np.sin(np.pi * t / duracao), 0, 1)
        return (0.3 * envelope * np.sin(2 * np.pi * frequencia * t)).astype(np.float32)

    def silencio(duracao: float) -> np.ndarray:
        return gerador.normal(0, 0.0004, int(taxa * duracao)).astype(np.float32)

    sinal = np.concatenate(
        [silencio(0.5), fala(2.0, 190), silencio(1.2), fala(2.5, 240), silencio(0.6)]
    )
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "autoteste.wav"
        with wave.open(str(caminho), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(taxa)
            handle.writeframes((np.clip(sinal, -1, 1) * 32767).astype("<i2").tobytes())

        session = Session("Autoteste")
        pipeline = TranscriptionPipeline(
            config, session=session, transcriber=MockTranscriber(config.asr)
        )
        pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho, target_rate=taxa))
        pipeline.run_until_complete()

    trechos = len(session.segments)
    print(f"trechos detectados: {trechos} (esperado: 2)")
    print(session.to_text())
    if trechos != 2:
        print("autoteste FALHOU: a segmentação não separou as duas falas.", file=sys.stderr)
        return 1
    print("autoteste OK — captura, VAD, fila de inferência e exportação funcionando.")
    return 0


# -------------------------------------------------------------------------- saída


def _imprimir_evento(evento: dict) -> None:
    tipo = evento.get("type")
    if tipo == "segment":
        marca = " (?)" if evento.get("low_confidence") else ""
        _limpar_linha()
        print(f"[{evento['start_label']}] {evento['speaker']}: {evento['text']}{marca}")
    elif tipo == "partial" and evento.get("text"):
        _limpar_linha()
        print(f"  … {evento['speaker']}: {evento['text']}", end="\r", flush=True)
    elif tipo == "status" and evento.get("state") == "loading_model":
        print("carregando modelo…", flush=True)
    elif tipo == "error":
        _limpar_linha()
        print(f"erro [{evento.get('track')}]: {evento['message']}", file=sys.stderr)


def _limpar_linha() -> None:
    if sys.stdout.isatty():
        print("\033[2K", end="\r")


if __name__ == "__main__":
    raise SystemExit(main())
