# Rodar no macOS

O macOS é o sistema em que o Escriba dá mais trabalho para instalar, por um motivo
específico: a Apple não deixa um processo comum ler o áudio de outros aplicativos. A
saída é um driver virtual que se comporta como uma placa de som — o áudio da reunião
passa por ele, e o Escriba grava dali.

Testado em Apple Silicon (M1 em diante). Em Mac Intel o caminho é o mesmo.

## Caminho curto: o script de instalação

O repositório traz um script que faz tudo o que dá para automatizar — dependências,
ambiente virtual, pacote, modelo de voz, configuração e autoteste:

```bash
git clone -b claude/meeting-transcription-assistant-yf2grq \
  https://github.com/mbritorj/portifolio.git escriba
cd escriba
./instalar-macos.sh
```

Ele pergunta antes de instalar qualquer coisa, pode ser rodado de novo sem estragar nada
(não sobrescreve o `escriba.toml` nem rebaixa o que já existe) e termina imprimindo os
dois passos que dependem da interface gráfica: o dispositivo de multi-saída e a
permissão de microfone.

O resto desta página explica cada passo, para quem prefere fazer à mão ou precisa
entender o que o script fez.

## 1. Dependências

```bash
# Homebrew, se ainda não tiver: https://brew.sh
brew install python@3.12 blackhole-2ch
```

O `blackhole-2ch` é o driver virtual. A instalação pede a senha de administrador e não
exige reiniciar, mas se o dispositivo não aparecer no passo 2, reinicie o Mac.

## 2. Ouvir e gravar ao mesmo tempo

Se você apenas mandar o som para o BlackHole, o Escriba grava e **você fica sem ouvir a
reunião**. Para os dois ao mesmo tempo, crie um dispositivo de **multi-saída** (não é o
"agregado", que serve para juntar entradas):

1. Abra **Configuração de Áudio e MIDI** (`/Aplicativos/Utilitários/Audio MIDI Setup`).
2. Clique no **+** — o primeiro dos três botões no canto inferior esquerdo, à esquerda
   do `−` e da engrenagem. A engrenagem **não** serve para isso.
3. Escolha **Criar dispositivo de multi-saída** (em inglês: *Create Multi-Output
   Device*).
4. No painel da direita, marque a coluna **Usar** (*Use*) em duas linhas: **BlackHole
   2ch** e a sua saída normal (*MacBook Air Speakers*, ou o seu fone).
5. Deixe o **BlackHole 2ch** como dispositivo principal (*Primary/Master Device*, no
   alto do painel) e marque **Correção de deriva** (*Drift Correction*) na **sua
   saída** — não no BlackHole. Se o som ficar picotado, inverta a marcação.
6. Renomeie: clique duas vezes no nome do novo dispositivo, na lista da esquerda, e
   escreva algo reconhecível, como "Reunião + gravação".

Depois, em **Ajustes do Sistema → Som → Saída**, escolha esse dispositivo de
multi-saída. Você continua ouvindo; o Escriba lê do BlackHole.

### Como saber se criou o dispositivo certo

As duas opções ficam no mesmo menu do `+`, e o agregado não serve aqui: ele distribui
canais (1-2 para o fone, 3-4 para o BlackHole) em vez de duplicar o áudio, então o
BlackHole não recebe nada e a transcrição sai vazia, sem erro nenhum.

**Não confira pela coluna `Type` dos Ajustes do Sistema**: ela mostra *Aggregate device*
para os dois, porque o macOS trata multi-saída como uma variação de agregado.

Confira no **Audio MIDI Setup**, selecionando o dispositivo. O de multi-saída:

* traz a frase *"Devices in this multi-output group will output audio simultaneously"*;
* tem **Primary Device** no alto e as colunas **Use**, **Volume** e **Drift Correction**;
* aparece na lista lateral com o ícone de alto-falantes empilhados e um `+`.

O agregado, em vez disso, mostra "Clock Source" e uma coluna "Resample", e não fala em
*simultaneously*. Se for esse o caso, apague com o `−` e crie de novo pela outra opção.

> Se você usa fone Bluetooth ou USB, ele precisa estar ligado e aparecer na lista para
> ser marcado. O mais prático é criar **dois** dispositivos de multi-saída — um com o
> fone + BlackHole, outro com os alto-falantes + BlackHole — e trocar em Ajustes do
> Sistema conforme a situação. Marcar os dois num só faz o som sair nos alto-falantes
> junto com o fone, o que o microfone capta de volta.
>
> Em fone Bluetooth, marque a *Correção de deriva* nele: o relógio do Bluetooth é o que
> mais desliza.

### Duas armadilhas com fone Bluetooth

**Não use o microfone do fone.** Em **Ajustes do Sistema → Som → Entrada**, escolha o
microfone do Mac. Quando o macOS usa o microfone de um fone Bluetooth, o aparelho troca
para o modo mãos-livres (HFP): a qualidade do áudio que sai dele despenca para algo
entre 8 e 16 kHz, o que estraga a transcrição do que os outros falam. Com o microfone do
Mac, o fone continua em A2DP e só recebe áudio.

**Aponte a saída do aplicativo da reunião para o dispositivo de multi-saída.** Teams,
Webex e Meet têm a própria escolha de alto-falante. Se ela estiver num dispositivo
específico em vez do padrão do sistema, o áudio passa por fora do multi-saída e o
BlackHole não recebe nada — a transcrição fica só com a sua voz. Escolha ali o mesmo
dispositivo de multi-saída (ou "igual ao sistema").

> O controle de volume das teclas não funciona em dispositivos de multi-saída. Ajuste o
> volume pelo próprio aplicativo da reunião, ou no painel do Audio MIDI Setup.

## 3. Instalar o Escriba

Rode os comandos **um de cada vez** e confira o resultado de cada um: se o `git clone`
falhar, os seguintes vão rodar na pasta errada.

```bash
git clone -b claude/meeting-transcription-assistant-yf2grq \
  https://github.com/mbritorj/portifolio.git escriba
cd escriba
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[local,web,diarizacao]"      # acrescente ,ata para a ata pela API
escriba autoteste                              # valida sem microfone e sem modelo
```

## 4. Permissão de microfone

Na primeira gravação, o macOS pergunta se o terminal pode usar o microfone. **Aceite** —
o BlackHole é visto pelo sistema como entrada de áudio, então a permissão vale para ele
também.

Se você recusou por engano, libere em **Ajustes do Sistema → Privacidade e Segurança →
Microfone**, marcando o Terminal (ou o iTerm, ou o VS Code, conforme onde você roda).
Sem isso o sistema entrega blocos zerados em vez de erro; o Escriba avisa depois de 10
segundos de silêncio absoluto, mas a causa é essa.

## 5. Conferir os dispositivos

```bash
escriba dispositivos
```

Deve aparecer algo como:

```
  [1] BlackHole 2ch (Core Audio, 2ch) [loopback]
  [2] MacBook Pro Microphone (Core Audio, 1ch)
```

Se o BlackHole não estiver na lista, o driver não carregou: reinicie o Mac.

## 6. Modelo de voz (para separar os participantes)

```bash
mkdir -p modelos && cd modelos
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx
cd ..
```

## 7. Configuração

Crie um `escriba.toml` na raiz do projeto:

```toml
[audio]
system_device = "BlackHole"          # o que os outros falam
# mic_device = "MacBook Pro Microphone"   # deixe comentado para usar o padrão

[asr]
# Em Apple Silicon a transcrição roda na CPU (veja a nota abaixo).
# Comece por "small"; suba para "medium" se o Mac aguentar.
model = "small"

[diarizacao]
enabled = true
model = "modelos/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
```

## 8. Usar

```bash
source .venv/bin/activate
escriba servir        # http://127.0.0.1:8777
```

Entre na reunião, clique em **Iniciar**, e ao final em **Encerrar** e **Para o Claude**.

## Desempenho em Apple Silicon

A transcrição usa faster-whisper, que roda em **CPU** no Mac: o CTranslate2, que está por
baixo, não usa a GPU da Apple (Metal/MPS). Ele aproveita bem o Accelerate, mas o
`large-v3` dificilmente acompanha uma reunião em tempo real num MacBook.

Recomendação prática: comece com `model = "small"`, olhe o número que o Escriba imprime
ao encerrar (`0,42× tempo real`, por exemplo) e suba para `medium` se sobrar folga.
Abaixo de `1,0×` a transcrição acompanha a reunião. Se ficar lento, teste também
`compute_type = "float32"` — em Apple Silicon o ganho da quantização `int8` varia com a
geração do chip, e vale medir em vez de supor.

Quem quiser o `large-v3` em tempo real no Mac precisa de um motor com Metal
(whisper.cpp ou mlx-whisper). O Escriba não traz esses motores hoje, mas a interface
`Transcriber` (`src/escriba/asr/base.py`) existe justamente para isso: são três métodos.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "a trilha 'sistema' está em silêncio absoluto há 10 s" | O terminal não tem permissão de microfone, ou a saída do sistema não está no dispositivo de multi-saída. |
| BlackHole não aparece em `escriba dispositivos` | O driver não carregou: reinicie o Mac. |
| Você criou o dispositivo e não sabe se é o certo | Confira no Audio MIDI Setup, não nos Ajustes do Sistema: lá os dois tipos aparecem como "Aggregate device". |
| Você para de ouvir a reunião | A saída do sistema está no BlackHole puro, não no dispositivo de multi-saída. |
| Só a sua voz aparece na transcrição | O aplicativo da reunião está mandando o som para um dispositivo específico, por fora do multi-saída. |
| O som do fone Bluetooth fica abafado | O fone entrou em modo mãos-livres porque o microfone dele está selecionado. Troque a entrada para o microfone do Mac. |
| Sua voz aparece duplicada nas duas trilhas | Você está em alto-falante: o microfone capta o que sai da caixa. Use fone. |
| Transcrição atrasa e some texto | O modelo é grande demais para a máquina: desça para `small` e confira o `× tempo real`. |
