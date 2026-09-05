# Arquitetura

```
src/escriba/
├── config.py          padrões, escriba.toml e variáveis de ambiente
├── audio/
│   ├── devices.py     descoberta de dispositivos e do loopback por sistema
│   ├── capture.py     fontes: PortAudio, WASAPI loopback, arquivo WAV
│   ├── resample.py    mono + 16 kHz + float32 (o formato que o Whisper espera)
│   └── vad.py         recorte das falas por energia adaptativa
├── asr/
│   ├── base.py        interface Transcriber
│   ├── faster_whisper_engine.py
│   └── mock.py        motor falso, para teste sem modelo
├── diarize/
│   ├── base.py        interface SpeakerEmbedder
│   ├── sherpa_embedder.py  impressão vocal em ONNX
│   ├── mock.py        extrator falso, para teste sem modelo
│   ├── clustering.py  agrupamento incremental por cosseno
│   └── profiles.py    cadastro de vozes (nome ↔ impressão vocal)
├── attribution.py     nomes a partir do transcript oficial da plataforma
├── pipeline.py        threads de captura, fila de inferência, eventos
├── session.py         segmentos, hipóteses parciais, exportação
├── server.py          FastAPI + WebSocket
├── summarize.py       ata via Claude API (opcional)
├── cli.py             linha de comando
└── web/index.html     interface ao vivo
```

## Threads e fila

```
thread captura(sistema) ─┐
                         ├─► PriorityQueue ─► thread inferência ─► eventos ─► WebSocket
thread captura(microfone)┘                                                └─► Session
```

Uma thread por trilha de áudio, **uma só** de inferência. A fila é por prioridade
`(prioridade, seq)`: `0` para trecho fechado, `1` para hipótese parcial. Duas
consequências desenhadas de propósito:

* quando uma fala fecha enquanto três parciais esperam, o texto definitivo passa na
  frente — é ele que o usuário vai guardar;
* antes de transcrever uma parcial, o worker confere se já chegou outra mais nova para a
  mesma trilha. Se chegou, a antiga é descartada sem inferência. Sob carga, o sistema
  degrada perdendo hipóteses provisórias (que seriam substituídas de qualquer jeito) em
  vez de acumular atraso.

O contador `stats.parciais_descartadas` mostra quanto disso está acontecendo; junto com
`fator_tempo_real`, é o diagnóstico de "a máquina não está acompanhando".

## O VAD

Detector por energia com piso de ruído adaptativo, sem modelo e sem biblioteca nativa:

1. **Calibração** — os primeiros 500 ms só medem o ambiente. Sem essa janela, começar a
   gravar numa sala barulhenta trava o piso de ruído no valor errado e o detector passa
   a reunião inteira achando que há fala.
2. **Limiar** — `max(piso_absoluto, piso_de_ruído + margem)`. O piso sobe devagar
   (`noise_adapt`) e desce rápido, para que uma tosse não eleve o limiar e mate as falas
   seguintes.
3. **Gatilho e hangover** — 3 blocos acima do limiar abrem a fala; 700 ms abaixo dele a
   fecham. O hangover é o que impede que uma respiração no meio da frase vire dois
   trechos.
4. **Pré-roll** — os 300 ms anteriores ao gatilho entram no áudio enviado ao modelo,
   senão a primeira sílaba se perde.
5. **Duração mínima medida na voz efetiva**, não no tamanho do buffer. Pré-roll e
   hangover somam mais de um segundo; se o corte olhasse o buffer, qualquer estalo
   passaria pelo filtro de 400 ms.
6. **Corte por duração máxima** — aos 25 s a fala é fechada e reaberta colada na linha
   do tempo, para que um monólogo não segure a transcrição até o fim.

Tudo isso é `numpy` puro, o que torna o comportamento testável com áudio sintético: veja
`tests/test_vad.py`.

## Diarização: das vozes aos nomes

O problema se divide em dois, e só o primeiro é acústico.

**Separar as vozes.** Como o VAD já entrega falas fechadas, não é preciso um
pipeline de diarização completo: um vetor por fala e um agrupamento online dão
conta. Cada falante é um centroide; a fala entra no centroide mais próximo se
passar do limiar de cosseno, senão abre um falante novo. O centroide é uma média
ponderada pela duração — uma fala de 20 s descreve melhor o timbre da pessoa do
que um "sim" de meio segundo.

Três decisões que o código materializa:

* **Fala curta herda o falante anterior.** Abaixo de `min_audio_s` o vetor é
  instável; inventar um "Falante 7" a partir de um "uhum" polui a transcrição
  mais do que atribuir ao último que falou.
* **Hipótese parcial não gera vetor.** Ela é refeita a cada ciclo; extrair
  embedding de um trecho que ainda cresce custa caro e muda de resposta.
* **Teto de falantes.** Atingido o limite, a fala vai para o mais parecido e o
  escore baixo denuncia a incerteza — melhor do que multiplicar grupos fantasma.

**Dar nome.** Nenhum modelo acústico produz "Ana Souza": nome vem de fora. São
três fontes, e as três terminam no mesmo lugar (`Session.renomear_falante`):

1. a pessoa renomeia na interface;
2. a voz bate com um cadastro (`diarize/profiles.py`), e o nome sai já na
   primeira fala;
3. o transcript oficial da plataforma (`attribution.py`) nomeia tudo depois.

A rota 3 alimenta a 2: os centroides ficam salvos no `.json` da transcrição, e
`escriba nomear --cadastrar` os transforma em cadastro de voz. Assim a reunião
seguinte já sai com nome ao vivo, sem ninguém gravar amostra.

### Alinhamento de relógios

O transcript da plataforma conta o tempo do início da reunião; o Escriba, do
momento em que a captura começou. Em vez de pedir esse número, `estimar_offset`
rasteriza as duas linhas do tempo em bins de 100 ms e acha o deslocamento de
maior correlação cruzada (via FFT). É barato, não depende do texto e funciona
mesmo quando a transcrição automática erra as palavras.

### Renomear é fundir

Renomear um falante para um nome que já existe funde os dois grupos: centroides
combinados por duração, segmentos remapeados. Isso resolve o erro mais comum do
agrupamento — a mesma pessoa partida em dois grupos — com a ação que a pessoa já
ia fazer de qualquer jeito.

## Linha do tempo

Cada trilha tem seu próprio relógio de áudio (blocos consumidos × duração do bloco).
No primeiro bloco de cada trilha, o pipeline guarda a diferença para o instante em que a
gravação começou e soma esse deslocamento em todos os tempos. Sem isso, uma trilha que
abre 200 ms depois da outra ficaria permanentemente adiantada na transcrição.

## A entrega para o Claude

O arquivo `.claude.md` (`Session.to_claude`) é o produto final do sistema, e não é o
markdown de leitura com outro nome. Ele acrescenta três coisas que mudam a qualidade da
ata:

1. **Quem falou e quanto**, com a procedência de cada nome — cadastro de voz, informado
   por quem gravou, ou voz sem identificação. É o que permite atribuir tarefa a pessoa.
2. **Um aviso explícito sobre as vozes anônimas**, para que o modelo registre
   "não identificado" em vez de deduzir de quem é a tarefa.
3. **A declaração de que o texto vem de reconhecimento automático**, com os trechos de
   baixa confiança marcados com `(?)`, para que trecho estranho seja tratado como erro
   de transcrição e não como algo que alguém disse.

O pedido que acompanha o arquivo (`summarize.PEDIDO_ATA`) é o mesmo que o comando
`escriba ata` envia pela API. Manter um texto só evita que os dois caminhos divirjam:
quem anexa no aplicativo e quem automatiza pela API recebem o mesmo documento.

## Formato do áudio

Tudo é normalizado na entrada do pipeline para `float32` mono a 16 kHz. A reamostragem
usa `scipy.signal.resample_poly` quando disponível e cai para interpolação linear quando
não — o que mantém o pacote instalável sem scipy, ao custo de um filtro pior.

## Extensões previstas

* **Outro motor de transcrição:** implemente `Transcriber` (`transcribe`, `warmup`,
  `close`) e registre em `asr/__init__.py`. O resto do pipeline não muda.
* **Outra fonte de áudio:** implemente `AudioSource.blocks()` devolvendo blocos mono
  float32 na taxa alvo. É assim que `WavFileSource` reaproveita todo o pipeline para
  transcrever gravações.
* **Outro extrator de voz:** implemente `SpeakerEmbedder.embed` e registre em
  `diarize/__init__.py`. O agrupamento é agnóstico à dimensão do vetor.
* **Outro formato de transcript oficial:** acrescente um leitor em
  `attribution.py` que devolva `list[Cue]`; o alinhamento e a renomeação não
  mudam.
