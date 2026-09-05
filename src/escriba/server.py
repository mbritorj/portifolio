"""Servidor local: interface web ao vivo em cima do pipeline.

Fica em 127.0.0.1 por padrão. A transcrição de uma reunião interna não tem por
que escutar em interface pública.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

try:
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, PlainTextResponse

    FASTAPI_DISPONIVEL = True
except ImportError:  # o servidor web é opcional
    FASTAPI_DISPONIVEL = False

from .audio.capture import build_source
from .audio.devices import DeviceError, list_input_devices
from .config import AppConfig
from .diarize import ConsentimentoAusente, VoiceProfileStore
from .pipeline import TranscriptionPipeline
from .session import Session
from .summarize import SummaryError, gerar_ata

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


class AppState:
    """Uma reunião por vez: pipeline, sessão e clientes conectados."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.pipeline: TranscriptionPipeline | None = None
        self.session = Session()
        self.ata: str | None = None
        self._clientes: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def registrar_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # --------------------------------------------------------------- transmissão

    async def conectar(self, websocket) -> None:
        self._clientes.add(websocket)
        await websocket.send_json({"type": "snapshot", **self.session.snapshot(),
                                   "gravando": self.gravando, "ata": self.ata})

    def desconectar(self, websocket) -> None:
        self._clientes.discard(websocket)

    def publicar(self, evento: dict) -> None:
        """Ponte das threads do pipeline para o event loop do servidor."""
        if self._loop is None or not self._clientes:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._transmitir(evento))
        )

    async def _transmitir(self, evento: dict) -> None:
        mortos = []
        for cliente in list(self._clientes):
            try:
                await cliente.send_json(evento)
            except Exception:
                mortos.append(cliente)
        for cliente in mortos:
            self._clientes.discard(cliente)

    # ------------------------------------------------------------------- controle

    @property
    def gravando(self) -> bool:
        return self.pipeline is not None and self.pipeline.rodando

    def iniciar(self, titulo: str | None = None) -> dict:
        if self.gravando:
            raise RuntimeError("já existe uma gravação em andamento.")

        self.session = Session(titulo)
        self.ata = None
        pipeline = TranscriptionPipeline(
            self.config, session=self.session, on_event=self.publicar
        )
        for nome, rotulo, spec, ativo in self._trilhas_configuradas():
            if not ativo:
                continue
            source = build_source(
                nome,
                spec=spec,
                backend=self.config.audio.backend,
                target_rate=self.config.audio.sample_rate,
                block_ms=self.config.audio.block_ms,
                name=nome,
            )
            pipeline.add_track(nome, rotulo, source)

        pipeline.start()
        self.pipeline = pipeline
        return {"titulo": self.session.titulo, "iniciada_em": self.session.iniciada_em.isoformat()}

    def _trilhas_configuradas(self):
        audio = self.config.audio
        return [
            ("sistema", self.config.system_label, audio.system_device, audio.capture_system),
            ("microfone", self.config.mic_label, audio.mic_device, audio.capture_mic),
        ]

    def renomear_falante(self, speaker_id: str, nome: str) -> dict:
        """Renomeia durante ou depois da gravação.

        Com o pipeline no ar, a troca também vale para o agrupamento, e as
        próximas falas daquela pessoa já saem com o nome certo.
        """
        if self.pipeline is not None and self.pipeline.rodando:
            return self.pipeline.renomear_falante(speaker_id, nome)
        resultado = self.session.renomear_falante(speaker_id, nome)
        self.publicar({"type": "speakers", "speakers": self.session.speakers})
        return resultado

    def cadastrar_voz(self, speaker_id: str, nome: str, *, consentimento: bool) -> dict:
        """Guarda a impressão vocal de um falante desta reunião.

        Usa o centroide já calculado, não o áudio — que nem é mantido. Só
        funciona depois de encerrar, quando o centroide está fechado.
        """
        centroide = self.session.centroids.get(speaker_id)
        if not centroide:
            raise ValueError(
                "ainda não há impressão vocal para este falante; encerre a gravação antes."
            )
        store = VoiceProfileStore(self.config.caminho_vozes())
        perfil = store.add(nome, centroide, consent=consentimento, note="cadastrado na interface")
        return perfil.resumo()

    def encerrar(self) -> list[str]:
        if self.pipeline is None:
            raise RuntimeError("nenhuma gravação em andamento.")
        self.pipeline.stop()
        self.pipeline = None
        caminhos = self.session.salvar(self.config.output_dir, formatos=("md", "json", "txt"))
        return [str(caminho) for caminho in caminhos]


def criar_app(config: AppConfig) -> Any:
    if not FASTAPI_DISPONIVEL:  # pragma: no cover - depende do ambiente
        raise RuntimeError("servidor web pede FastAPI. Use: pip install 'escriba[web]'")

    estado = AppState(config)

    @asynccontextmanager
    async def lifespan(_app):
        # O pipeline emite eventos de outras threads; guardar o event loop é o
        # que permite empurrá-los para os WebSockets com segurança.
        estado.registrar_loop(asyncio.get_running_loop())
        yield
        if estado.gravando:
            estado.pipeline.stop()

    app = FastAPI(title="Escriba", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/dispositivos")
    async def dispositivos() -> dict:
        try:
            encontrados = list_input_devices()
        except DeviceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "dispositivos": [
                {"indice": d.index, "nome": d.name, "canais": d.channels,
                 "api": d.hostapi, "loopback": d.is_loopback}
                for d in encontrados
            ]
        }

    @app.get("/api/estado")
    async def obter_estado() -> dict:
        stats = vars(estado.pipeline.stats) if estado.pipeline else {}
        return {"gravando": estado.gravando, "stats": stats, **estado.session.snapshot()}

    @app.post("/api/iniciar")
    async def iniciar(payload: dict | None = None) -> dict:
        titulo = (payload or {}).get("titulo")
        try:
            return await asyncio.to_thread(estado.iniciar, titulo)
        except (RuntimeError, DeviceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/encerrar")
    async def encerrar() -> dict:
        try:
            arquivos = await asyncio.to_thread(estado.encerrar)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"arquivos": arquivos}

    @app.post("/api/falantes")
    async def renomear_falante(payload: dict) -> dict:
        speaker_id, nome = payload.get("speaker_id"), (payload.get("nome") or "")
        if not speaker_id:
            raise HTTPException(status_code=400, detail="informe o speaker_id.")
        try:
            resultado = estado.renomear_falante(speaker_id, nome)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**resultado, "speakers": estado.session.speakers}

    @app.get("/api/vozes")
    async def listar_vozes() -> dict:
        caminho = config.caminho_vozes()
        if not caminho.exists():
            return {"vozes": []}
        return {"vozes": [p.resumo() for p in VoiceProfileStore(caminho).profiles]}

    @app.post("/api/vozes")
    async def cadastrar_voz(payload: dict) -> dict:
        try:
            return await asyncio.to_thread(
                estado.cadastrar_voz,
                payload.get("speaker_id", ""),
                (payload.get("nome") or "").strip(),
                consentimento=bool(payload.get("consentimento")),
            )
        except ConsentimentoAusente as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/exportar", response_class=PlainTextResponse)
    async def exportar(formato: str = "md") -> str:
        exportadores = {
            "md": estado.session.to_markdown, "txt": estado.session.to_text,
            "srt": estado.session.to_srt, "json": estado.session.to_json,
        }
        if formato not in exportadores:
            raise HTTPException(status_code=400, detail=f"formato inválido: {formato}")
        return exportadores[formato]()

    @app.post("/api/ata")
    async def ata(payload: dict | None = None) -> dict:
        instrucoes = (payload or {}).get("instrucoes")
        try:
            resumo = await asyncio.to_thread(
                gerar_ata, estado.session, config.summary, instrucoes=instrucoes
            )
        except SummaryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        estado.ata = resumo.markdown
        return {"ata": resumo.markdown, "modelo": resumo.modelo,
                "tokens_entrada": resumo.tokens_entrada, "tokens_saida": resumo.tokens_saida}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await estado.conectar(websocket)
        try:
            while True:
                await websocket.receive_text()  # mantém a conexão viva
        except WebSocketDisconnect:
            pass
        finally:
            estado.desconectar(websocket)

    app.state.escriba = estado
    return app


def servir(config: AppConfig) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("servidor web pede uvicorn. Use: pip install 'escriba[web]'") from exc

    if not _websocket_disponivel():
        print(
            "aviso: uvicorn sem suporte a WebSocket — a página vai cair no modo de\n"
            "consulta periódica. Para a transmissão ao vivo: pip install 'escriba[web]'"
        )

    uvicorn.run(
        criar_app(config), host=config.server.host, port=config.server.port, log_level="warning"
    )


def _websocket_disponivel() -> bool:
    """O uvicorn só fala WebSocket com um destes instalados."""
    from importlib.util import find_spec

    return any(find_spec(nome) is not None for nome in ("websockets", "wsproto"))
