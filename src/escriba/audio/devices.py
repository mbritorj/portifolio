"""Descoberta dos dispositivos de captura.

A parte interessante é achar o *loopback*: a fonte que entrega o áudio que o
computador está tocando, ou seja, a voz dos outros participantes da reunião.
Cada sistema operacional resolve isso de um jeito:

* Linux (PulseAudio/PipeWire): cada saída tem uma fonte ``.monitor`` associada,
  que aparece como entrada normal. Não precisa instalar nada.
* Windows: WASAPI tem modo loopback nativo, exposto em Python pelo PyAudioWPatch.
  Alternativa sem código nativo: um cabo virtual (VB-CABLE) como saída.
* macOS: o sistema não expõe loopback para processos comuns sem API privilegiada;
  o caminho prático é um dispositivo virtual (BlackHole, Loopback) em um
  dispositivo agregado, para você continuar ouvindo a reunião.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass


@dataclass
class DeviceInfo:
    index: int
    name: str
    channels: int
    sample_rate: int
    hostapi: str
    is_loopback: bool = False

    def __str__(self) -> str:
        marca = " [loopback]" if self.is_loopback else ""
        return f"[{self.index}] {self.name} ({self.hostapi}, {self.channels}ch){marca}"


class DeviceError(RuntimeError):
    """Nenhum dispositivo utilizável foi encontrado."""


def _sounddevice():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # OSError: PortAudio ausente no sistema
        raise DeviceError(
            "sounddevice/PortAudio não disponível. Instale com "
            "'pip install sounddevice' (Linux: 'apt install libportaudio2')."
        ) from exc
    return sd


def list_input_devices() -> list[DeviceInfo]:
    """Lista todas as entradas de áudio visíveis, marcando as de loopback."""
    sd = _sounddevice()
    hostapis = sd.query_hostapis()
    devices: list[DeviceInfo] = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        name = device["name"]
        devices.append(
            DeviceInfo(
                index=index,
                name=name,
                channels=device["max_input_channels"],
                sample_rate=int(device["default_samplerate"]),
                hostapi=hostapis[device["hostapi"]]["name"],
                is_loopback=looks_like_loopback(name),
            )
        )
    return devices


_LOOPBACK_HINTS = (
    "monitor",        # PulseAudio / PipeWire
    "loopback",       # WASAPI loopback, dispositivos virtuais diversos
    "blackhole",      # macOS
    "soundflower",    # macOS (legado)
    "vb-audio",       # VB-CABLE
    "cable output",   # VB-CABLE
    "stereo mix",     # placas Windows antigas
    "mixagem estéreo",
)


def looks_like_loopback(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _LOOPBACK_HINTS)


def find_loopback_device() -> DeviceInfo:
    """Escolhe a melhor fonte de loopback disponível na máquina."""
    candidates = [device for device in list_input_devices() if device.is_loopback]
    if candidates:
        # No Linux, prefere o monitor da saída padrão em vez do primeiro da lista.
        preferido = _default_monitor_name()
        if preferido:
            for device in candidates:
                if preferido.lower() in device.name.lower():
                    return device
        return candidates[0]
    raise DeviceError(_mensagem_sem_loopback())


def find_microphone_device() -> DeviceInfo:
    """Escolhe o microfone padrão do sistema."""
    sd = _sounddevice()
    default_index = sd.default.device[0]
    for device in list_input_devices():
        if device.index == default_index and not device.is_loopback:
            return device
    for device in list_input_devices():
        if not device.is_loopback:
            return device
    raise DeviceError("nenhum microfone encontrado.")


def resolve_device(spec: str | int | None, *, loopback: bool) -> DeviceInfo:
    """Resolve o que veio da configuração: índice, trecho do nome ou automático."""
    if spec is None:
        return find_loopback_device() if loopback else find_microphone_device()
    devices = list_input_devices()
    if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
        index = int(spec)
        for device in devices:
            if device.index == index:
                return device
        raise DeviceError(f"dispositivo de índice {index} não existe ou não tem entrada.")
    lowered = str(spec).lower()
    for device in devices:
        if lowered in device.name.lower():
            return device
    disponiveis = "\n  ".join(str(d) for d in devices)
    raise DeviceError(f"nenhum dispositivo casa com {spec!r}. Disponíveis:\n  {disponiveis}")


def _default_monitor_name() -> str | None:
    """Nome do monitor da saída padrão do PulseAudio/PipeWire, se houver."""
    if platform.system() != "Linux":
        return None
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{sink}.monitor" if sink else None


def _mensagem_sem_loopback() -> str:
    sistema = platform.system()
    if sistema == "Windows":
        return (
            "Nenhum loopback encontrado. No Windows, instale o PyAudioWPatch "
            "('pip install PyAudioWPatch') e use backend='wasapi', ou instale o "
            "VB-CABLE e mande o áudio da reunião para ele."
        )
    if sistema == "Darwin":
        return (
            "Nenhum loopback encontrado. No macOS, instale o BlackHole "
            "('brew install blackhole-2ch') e crie um dispositivo de multi-saída "
            "com BlackHole + sua saída, para gravar e continuar ouvindo a reunião. "
            "Passo a passo em docs/macos.md."
        )
    return (
        "Nenhum monitor encontrado. No Linux, confira se o PulseAudio/PipeWire "
        "está ativo ('pactl list sources short') e se existe uma fonte terminada "
        "em '.monitor'."
    )
