#!/usr/bin/env bash
#
# Instalação do Escriba no macOS.
#
# Faz tudo o que um script consegue fazer: dependências, ambiente virtual,
# pacote, modelo de voz e configuração inicial. Os dois passos que dependem da
# interface gráfica do macOS (dispositivo de multi-saída e permissão de
# microfone) ficam impressos no fim, porque nenhum script pode clicá-los.
#
# Uso:
#   ./instalar-macos.sh          # pergunta antes de instalar
#   ./instalar-macos.sh --sim    # não pergunta

set -euo pipefail

MODELO_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
MODELO_ARQUIVO="modelos/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

azul() { printf '\033[1;34m%s\033[0m\n' "$*"; }
verde() { printf '\033[0;32m%s\033[0m\n' "$*"; }
amarelo() { printf '\033[0;33m%s\033[0m\n' "$*"; }
erro() { printf '\033[0;31merro: %s\033[0m\n' "$*" >&2; }

morrer() { erro "$*"; exit 1; }

confirmar() {
  [[ "${SEM_PERGUNTA:-0}" == "1" ]] && return 0
  if [[ ! -t 0 ]]; then
    morrer "sem terminal interativo para confirmar. Rode com --sim."
  fi
  read -r -p "$1 [S/n] " resposta
  [[ -z "$resposta" || "$resposta" =~ ^[SsYy] ]]
}

SEM_PERGUNTA=0
[[ "${1:-}" == "--sim" ]] && SEM_PERGUNTA=1

cd "$RAIZ"

# ---------------------------------------------------------------- verificações

[[ "$(uname -s)" == "Darwin" ]] || morrer "este script é para macOS. No Linux, veja o README."
[[ -f pyproject.toml ]] || morrer "rode este script de dentro da pasta do Escriba (onde está o pyproject.toml)."

azul "== Escriba — instalação no macOS =="
echo
echo "O que vai acontecer:"
echo "  1. Homebrew instala o Python 3.12 e o BlackHole (driver de áudio virtual)"
echo "  2. Ambiente virtual em .venv, com o Escriba e suas dependências"
echo "  3. Download do modelo de reconhecimento de voz (~28 MB)"
echo "  4. escriba.toml inicial, se ainda não existir"
echo "  5. Autoteste"
echo
echo "A instalação do BlackHole é um driver de sistema: o Homebrew vai pedir a"
echo "sua senha de administrador."
echo
confirmar "Seguir?" || { echo "cancelado."; exit 0; }

# ------------------------------------------------------------------- homebrew

if ! command -v brew >/dev/null 2>&1; then
  erro "Homebrew não encontrado."
  echo "Instale primeiro (leva alguns minutos):" >&2
  # shellcheck disable=SC2016  # a linha é para o usuário copiar, não para expandir aqui
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
  echo "Depois rode este script de novo." >&2
  exit 1
fi

azul "[1/5] Dependências do sistema"

if brew list --formula python@3.12 >/dev/null 2>&1; then
  echo "  python@3.12 já instalado"
else
  echo "  instalando python@3.12…"
  brew install python@3.12
fi

if [[ -d "/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver" ]] ||
   brew list --cask blackhole-2ch >/dev/null 2>&1; then
  echo "  BlackHole já instalado"
else
  echo "  instalando blackhole-2ch (vai pedir a senha)…"
  brew install blackhole-2ch
fi

# ---------------------------------------------------------------- interpretador

PREFIXO_PY="$(brew --prefix python@3.12 2>/dev/null || true)"
PYTHON=""
if command -v python3.12 >/dev/null 2>&1; then
  PYTHON=python3.12
elif [[ -n "$PREFIXO_PY" && -x "$PREFIXO_PY/bin/python3.12" ]]; then
  PYTHON="$PREFIXO_PY/bin/python3.12"
# Qualquer 3.11+ serve; o projeto não usa nada específico do 3.12.
elif command -v python3 >/dev/null 2>&1 &&
     python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  PYTHON=python3
else
  morrer "não encontrei Python 3.11 ou superior. Rode: brew install python@3.12"
fi
echo "  usando $("$PYTHON" -V) em $(command -v "$PYTHON")"

# --------------------------------------------------------------------- ambiente

azul "[2/5] Ambiente virtual e pacote"
if [[ -d .venv ]]; then
  echo "  .venv já existe, reaproveitando"
else
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
echo "  instalando o Escriba e suas dependências (alguns minutos na primeira vez)…"
python -m pip install --quiet -e ".[local,web,diarizacao]"
command -v escriba >/dev/null 2>&1 || morrer "a instalação terminou mas o comando 'escriba' não apareceu."

# ----------------------------------------------------------------------- modelo

azul "[3/5] Modelo de reconhecimento de voz"
if [[ -f "$MODELO_ARQUIVO" ]]; then
  echo "  modelo já baixado"
else
  mkdir -p modelos
  echo "  baixando (~28 MB)…"
  curl -fsSL --retry 3 -o "$MODELO_ARQUIVO.parcial" "$MODELO_URL"
  mv "$MODELO_ARQUIVO.parcial" "$MODELO_ARQUIVO"
fi

# ------------------------------------------------------------------ configuração

azul "[4/5] Configuração"
if [[ -f escriba.toml ]]; then
  echo "  escriba.toml já existe, mantido como está"
else
  cat > escriba.toml <<TOML
# Gerado por instalar-macos.sh. Ajuste à vontade.

[audio]
# Nome do dispositivo que entrega o áudio dos outros participantes.
system_device = "BlackHole"

[asr]
# Em Apple Silicon a transcrição roda na CPU. Comece por "small" e suba para
# "medium" se o número de "x tempo real" que aparece ao encerrar sobrar folga.
model = "small"

[diarizacao]
# Separa as vozes dos participantes remotos.
enabled = true
model = "$MODELO_ARQUIVO"
TOML
  echo "  escriba.toml criado"
fi

# ------------------------------------------------------------------- autoteste

azul "[5/5] Autoteste"
if escriba autoteste >/dev/null 2>&1; then
  verde "  pipeline funcionando"
else
  morrer "o autoteste falhou. Rode 'escriba autoteste' para ver o erro."
fi

echo
if escriba dispositivos 2>/dev/null | grep -qi blackhole; then
  verde "BlackHole encontrado entre os dispositivos de entrada."
else
  amarelo "BlackHole ainda não aparece na lista de dispositivos."
  amarelo "Reinicie o Mac — o driver só carrega depois disso — e rode 'escriba dispositivos'."
fi

echo
azul "== Falta o que nenhum script pode fazer por você =="
cat <<FIM

1. Ouvir e gravar ao mesmo tempo

   Abra Configuração de Áudio e MIDI (Aplicativos > Utilitários):
     - clique no + (canto inferior esquerdo) > Criar dispositivo de multi-saída
     - marque BlackHole 2ch e a sua saída normal (fone ou alto-falantes)
     - em "correção de deriva", marque a SUA SAÍDA, não o BlackHole

   Depois, em Ajustes do Sistema > Som > Saída, escolha esse dispositivo.
   Sem isso, ou você não ouve a reunião, ou o Escriba não ouve os outros.

2. Permissão de microfone

   Na primeira gravação o macOS vai perguntar. Aceite — o BlackHole conta como
   entrada de áudio. Se recusar, o sistema entrega silêncio sem dar erro.

Para usar, sempre:

   cd "$RAIZ"
   source .venv/bin/activate
   escriba servir            # abre em http://127.0.0.1:8777

Detalhes e problemas comuns: docs/macos.md
FIM
