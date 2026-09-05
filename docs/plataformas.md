# O que cada plataforma oferece (e por que a captura local ganha)

Levantamento das rotas oficiais de transcrição das três plataformas pedidas, com a
pergunta que interessa aqui: **dá para ter transcrição ao vivo sem colocar um bot na
sala?** Consultado em setembro de 2026; APIs mudam, confira os links antes de decidir.

## Resumo

| Plataforma | Transcrição ao vivo por API | Transcrição depois da reunião | O que a rota ao vivo exige |
|---|---|---|---|
| Microsoft Teams | não, sem bot | sim (`callTranscript` no Graph) | Graph Communications **Bot** Media SDK — o bot entra na chamada |
| Cisco Webex | sim, com ressalvas | sim (Meeting Transcripts API) | o SDK JavaScript **entra na reunião** como cliente, e o Webex Assistant precisa estar ligado |
| Google Meet | não | sim (artefatos da Meet REST API) | não existe fluxo oficial ao vivo; o que circula é raspagem das legendas no DOM por extensão |

Nenhuma das três entrega o que o projeto pede — texto ao vivo, em pt-BR, sem
participante extra e sem depender da plataforma. Daí a captura local.

## Microsoft Teams

* O `callTranscript` do Microsoft Graph recupera a transcrição **depois** que a reunião
  termina, e só se a transcrição tiver sido ligada durante a chamada.
* As legendas ao vivo do cliente Teams existem, mas não são salvas nem expostas por API.
* Para áudio ao vivo, a rota oficial é o Graph Communications Bot Media SDK: um bot que
  entra na chamada, recebe as trilhas e as encaminha para o serviço de transcrição que
  você escolher. É exatamente o que este projeto evita.

## Cisco Webex

* A **Meeting Transcripts API** lista e baixa transcrições prontas (`vttDownloadLink`,
  `txtDownloadLink`) depois da reunião. Desde fevereiro de 2024, a transcrição é gerada
  automaticamente quando o Cisco AI Assistant está ligado, mesmo sem gravação.
* O **SDK JavaScript** é a exceção do quadro: ele recebe transcrição em tempo real. Só
  que a instância do SDK precisa **entrar na reunião**, e o Webex Assistant tem que
  estar ativo (manualmente ou via `enabledWebexAssistantByDefault` na API de
  preferências). Ou seja: é um cliente a mais na sala, com dependência de licença e de
  configuração do tenant.

## Google Meet

* A Meet REST API trabalha com **artefatos**: gravações, transcrições e entradas de
  transcrição, disponíveis pouco depois do fim da conferência e gravados no Drive do
  organizador. As entradas são apagadas 30 dias após a conferência.
* Legendas ao vivo existem no cliente, mas não são salvas nem publicadas por API.
* As soluções "ao vivo" que se vê por aí são bots que entram na chamada ou extensões do
  Chrome que raspam as legendas do DOM da página — frágil e sujeito a quebrar a cada
  mudança de interface.

## Por que o loopback local resolve

O ponto de captura do Escriba fica **abaixo** de todas essas diferenças: o áudio que sai
pelas caixas de som já é a mistura final da reunião, seja ela do Teams, do Webex, do
Meet, de uma ligação telefônica no viva-voz ou de um vídeo gravado. Uma implementação
serve para todas, não depende de licença, de permissão de administrador nem de API
estável — e o áudio não sai da máquina.

O preço, já dito no README: sem os metadados da plataforma, não dá para separar fala
sobreposta, nem gravar reunião da qual você não participa.

Quanto a saber quem é quem, o transcript pós-reunião destas mesmas APIs vira uma
peça útil: ele traz os nomes e os tempos, o que permite nomear retroativamente as
vozes que o Escriba agrupou e, a partir dos centroides, cadastrar essas vozes para
que apareçam com nome já ao vivo na reunião seguinte. É o comando `escriba nomear`.
Hoje ele lê o arquivo que você baixa da plataforma; buscar pelo Graph, pela Webex
Meeting Transcripts API ou pela Meet REST API é a extensão natural, e a única parte
que exige credenciais e consentimento de administrador.

## Fontes

- [Get callTranscript — Microsoft Graph v1.0](https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0)
- [Use live captions in Microsoft Teams meetings](https://support.microsoft.com/en-us/teams/meetings/use-live-captions-in-microsoft-teams-meetings)
- [Any available API for live transcript of a meeting — Microsoft Community Hub](https://techcommunity.microsoft.com/discussions/teamsdeveloper/any-available-api-for-live-transcript-of-a-meeting/3924884)
- [Meeting Transcripts — Webex for Developers](https://developer.webex.com/meeting/docs/api/v1/meeting-transcripts)
- [How to Receive Real-Time Meeting Transcription with the Webex JavaScript SDK](https://developer.webex.com/blog/how-to-receive-real-time-meeting-transcription-with-the-webex-javascript-sdk)
- [Google Meet REST API overview](https://developers.google.com/workspace/meet/api/guides/overview)
- [Work with artifacts — Google Meet](https://developers.google.com/workspace/meet/api/guides/artifacts)
- [conferenceRecords.transcripts.entries.get — Google Meet](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/get)
- [Loopback Recording — Microsoft Learn (WASAPI)](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
- [PyAudioWPatch — PortAudio com loopback WASAPI](https://github.com/s0d3s/PyAudioWPatch)
- [AudioCap — captura de áudio do sistema no macOS 14.4+](https://github.com/insidegui/AudioCap)
