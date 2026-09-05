"""Configuração da aplicação: valores padrão, arquivo TOML e variáveis de ambiente."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

# Vocabulário que costuma aparecer em reuniões corporativas brasileiras. O Whisper
# usa isso como contexto inicial e erra menos em siglas e nomes de produto.
PROMPT_INICIAL_PADRAO = (
    "Reunião corporativa em português do Brasil. Termos frequentes: SLA, TR, "
    "edital, licitação, pregão, backlog, sprint, deploy, firewall, switch, "
    "roteador, datacenter, nuvem, contrato, escopo, cronograma, entrega."
)


@dataclass
class AudioConfig:
    """Captura de áudio.

    ``system_device`` é a fonte de loopback (o que sai pelas caixas de som, ou
    seja, a voz dos outros participantes) e ``mic_device`` é o microfone.
    ``None`` significa detecção automática.
    """

    sample_rate: int = 16_000
    block_ms: int = 30
    mic_device: str | int | None = None
    system_device: str | int | None = None
    capture_mic: bool = True
    capture_system: bool = True
    # "auto" escolhe o backend pelo sistema operacional.
    backend: str = "auto"


@dataclass
class VadConfig:
    """Detecção de atividade de voz (VAD) por energia adaptativa."""

    # Quanto o quadro precisa estar acima do piso de ruído para contar como fala.
    speech_margin_db: float = 9.0
    # Piso absoluto: abaixo disso é silêncio, mesmo que o ruído de fundo caia muito.
    absolute_floor_dbfs: float = -55.0
    # Quadros consecutivos com voz para abrir uma fala.
    trigger_frames: int = 3
    # Silêncio contínuo que fecha a fala.
    hangover_ms: int = 700
    # Áudio mantido antes do gatilho, para não cortar a primeira sílaba.
    preroll_ms: int = 300
    # Descarta o trecho quando a voz efetiva (sem pré-roll nem hangover) for
    # mais curta que isso: tosse, clique de mouse, batida na mesa.
    min_utterance_ms: int = 400
    # Janela inicial só para medir o ruído do ambiente, sem abrir falas. Sem ela,
    # começar a gravar numa sala barulhenta trava o piso de ruído no valor errado.
    calibration_ms: int = 500
    # Corte forçado: transcreve e continua, para não segurar um monólogo inteiro.
    max_utterance_ms: int = 25_000
    # Constante de adaptação do piso de ruído (0-1; maior = adapta mais rápido).
    noise_adapt: float = 0.05


@dataclass
class AsrConfig:
    """Motor de reconhecimento de fala."""

    engine: str = "faster-whisper"
    model: str = "large-v3"
    device: str = "auto"          # "auto" | "cuda" | "cpu"
    compute_type: str = "auto"    # "auto" | "float16" | "int8_float16" | "int8"
    language: str = "pt"
    beam_size: int = 5
    # Beam menor nas hipóteses parciais: elas são refeitas a cada ciclo mesmo.
    partial_beam_size: int = 1
    # Intervalo entre hipóteses parciais (o "tempo real" que aparece na tela).
    partial_interval_s: float = 1.2
    # Parciais só começam depois que a fala tem pelo menos esta duração.
    partial_min_audio_s: float = 1.0
    initial_prompt: str = PROMPT_INICIAL_PADRAO
    # Segmentos com log-prob médio abaixo disso entram marcados como incertos.
    low_confidence_logprob: float = -1.0


@dataclass
class SummaryConfig:
    """Ata e itens de ação via Claude API (opcional)."""

    enabled: bool = True
    model: str = "claude-opus-5"
    max_tokens: int = 16_000


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8777


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    output_dir: Path = Path("transcricoes")
    # Rótulos exibidos na transcrição para cada trilha capturada.
    mic_label: str = "Eu"
    system_label: str = "Participantes"

    @classmethod
    def load(cls, path: str | Path | None = None) -> AppConfig:
        """Carrega o padrão, aplica o TOML (se houver) e depois o ambiente."""
        config = cls()
        toml_path = _resolve_config_path(path)
        if toml_path is not None:
            with open(toml_path, "rb") as handle:
                _apply_mapping(config, tomllib.load(handle))
        _apply_env(config)
        return config


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        resolved = Path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"arquivo de configuração não encontrado: {resolved}")
        return resolved
    for candidate in (Path("escriba.toml"), Path.home() / ".config" / "escriba" / "config.toml"):
        if candidate.exists():
            return candidate
    return None


def _apply_mapping(target: Any, mapping: dict[str, Any]) -> None:
    """Aplica um dicionário aninhado sobre uma árvore de dataclasses."""
    valid = {f.name: f for f in fields(target)}
    for key, value in mapping.items():
        if key not in valid:
            raise ValueError(f"opção de configuração desconhecida: {key}")
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _apply_mapping(current, value)
        else:
            setattr(target, key, _coerce(valid[key].type, value))


def _coerce(annotation: Any, value: Any) -> Any:
    text = str(annotation)
    if "Path" in text and not isinstance(value, Path):
        return Path(value)
    return value


# Variáveis de ambiente reconhecidas -> (seção, campo, conversor).
_ENV_MAP: dict[str, tuple[str | None, str, Any]] = {
    "ESCRIBA_MODEL": ("asr", "model", str),
    "ESCRIBA_DEVICE": ("asr", "device", str),
    "ESCRIBA_COMPUTE_TYPE": ("asr", "compute_type", str),
    "ESCRIBA_LANGUAGE": ("asr", "language", str),
    "ESCRIBA_ENGINE": ("asr", "engine", str),
    "ESCRIBA_MIC_DEVICE": ("audio", "mic_device", str),
    "ESCRIBA_SYSTEM_DEVICE": ("audio", "system_device", str),
    "ESCRIBA_BACKEND": ("audio", "backend", str),
    "ESCRIBA_HOST": ("server", "host", str),
    "ESCRIBA_PORT": ("server", "port", int),
    "ESCRIBA_SUMMARY_MODEL": ("summary", "model", str),
    "ESCRIBA_OUTPUT_DIR": (None, "output_dir", Path),
}


def _apply_env(config: AppConfig) -> None:
    for env_name, (section, field_name, converter) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        target = config if section is None else getattr(config, section)
        setattr(target, field_name, converter(raw))
