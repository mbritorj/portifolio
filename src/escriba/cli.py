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

    acoes = {
        "dispositivos": _cmd_dispositivos,
        "gravar": _cmd_gravar,
        "servir": _cmd_servir,
        "arquivo": _cmd_arquivo,
        "ata": _cmd_ata,
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
