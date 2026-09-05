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

## Linha do tempo

Cada trilha tem seu próprio relógio de áudio (blocos consumidos × duração do bloco).
No primeiro bloco de cada trilha, o pipeline guarda a diferença para o instante em que a
gravação começou e soma esse deslocamento em todos os tempos. Sem isso, uma trilha que
abre 200 ms depois da outra ficaria permanentemente adiantada na transcrição.

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
* **Diarização:** o ponto de entrada é o `_transcrever` do pipeline, sobre o áudio da
  fala fechada, antes de `session.add_segment`.
