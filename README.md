# Alquimia EEG OSC

**v0.1** · Puente entre cascos EEG Emotiv (Insight, EPOC+) y OSC para teatro, danza y música.

![Mensajes OSC](docs/capturas/02-mensajes-osc.png)

*[English below](#english)*

## Qué es

Lee los datos de un casco Emotiv a través de **Cortex** (el servicio del EMOTIV Launcher): bandas de frecuencia, parpadeos y expresiones faciales, métricas de atención o relajación, giro de cabeza y calidad de contacto. Los suaviza, los normaliza de 0 a 1 y los envía por **OSC** a TouchDesigner, Isadora o cualquier programa que lo reciba.

Está pensado para escena: estados de un intérprete o de alguien del público que mueven un paisaje, una música o un cue, y también para grabar sesiones y analizar una obra escena por escena.

- Salida a tasa fija, solo con números (sin texto), con `valid` y `quality` para saber cuándo confiar en la señal.
- Calibración automática por persona (`/norm`).
- Grabación y reproducción de sesiones, para ensayar sin casco y tener respaldo en función.
- Modo de simulación para armar el patch sin casco.

Especificación completa: [docs/especificacion-osc.md](docs/especificacion-osc.md).

## Instalación

1. Python 3.10 o más reciente.
2. `pip install -r requirements.txt`
3. Copia `.env.example` como `.env` y pega tu **Client ID** y **Client Secret**. Se obtienen registrando una app en emotiv.com → My Account → Cortex Apps. El `.env` no se sube al repo.
4. Instala el **EMOTIV Launcher** y entra con tu cuenta.

## Uso

1. Abre el Launcher, prende el casco y conéctalo (Bluetooth o USB Receiver).
2. `python cortex_osc.py`
3. La primera vez, aprueba la app en el Launcher.

| Opción | Qué hace |
|---|---|
| `--simular` | datos sintéticos sin casco |
| `--grabar` | guarda la sesión en `grabaciones/eeg_FECHA.jsonl` |
| `--reproducir ARCHIVO` | reproduce una grabación (`--bucle` para repetir) |
| `--destino host:puerto` | destino OSC; se puede repetir. Por defecto `127.0.0.1:9200` |
| `--crudo` | EEG crudo en `/eeg/{casco}/raw` (cascos de consumo como el Insight) |
| `--casco INSIGHT` | usa solo el casco cuyo id contenga ese texto |
| `--hz 30` · `--suavizado 0.3` | tasa de salida · suavizado (más bajo = más suave) |

**Recibir:** en TouchDesigner usa un OSC In CHOP en el puerto 9200. En Isadora, pon el puerto OSC en 9200 (Preferencias → Comunicaciones) y usa el actor OSC Listener.

**Cascos:** `/eeg/1/…` = EPOC+, `/eeg/2/…` = Insight.

**Nota:** con Cortex sin licencia de pago, los cascos de consumo (Insight) entregan todos los flujos, incluido el EEG crudo; los profesionales (EPOC) todo excepto el EEG crudo y las métricas de alta resolución. En esta versión el EEG no lee emociones, sino estimaciones de estado; úsalas como textura escénica, no como verdad.

## Capturas / Screenshots

| | |
|---|---|
| ![Conexión](docs/capturas/01-conexion-cortex.png) | ![OSC](docs/capturas/02-mensajes-osc.png) |
| Conexión con Cortex · *Connecting to Cortex* | Mensajes OSC recibidos (modo simulación) · *OSC messages received (simulation mode)* |

---

<a id="english"></a>
## English

**Alquimia EEG OSC** bridges Emotiv EEG headsets (Insight, EPOC+) to OSC for theatre, dance and music. It reads Cortex (EMOTIV Launcher) streams (band power, blinks and facial expressions, performance metrics, head motion and contact quality), smooths and normalises them to 0–1, and sends them over OSC to TouchDesigner, Isadora or any OSC client.

**Install:** Python 3.10+, `pip install -r requirements.txt`, copy `.env.example` to `.env` with your Cortex app Client ID and Secret (emotiv.com → My Account → Cortex Apps), and install the EMOTIV Launcher.

**Run:** connect the headset in the Launcher, then `python cortex_osc.py` and approve the app in the Launcher the first time. Use `--simular` to test without a headset, `--grabar` to record a session and `--reproducir FILE` to play it back. Default destination is `127.0.0.1:9200`.

Full OSC specification (in Spanish): [docs/especificacion-osc.md](docs/especificacion-osc.md). Addresses are self-explanatory (`/eeg/2/band/alpha/norm`, `/eeg/2/blink`, `/eeg/2/valid`…).

## Licencia / License

GPL-3.0-or-later. Ver [LICENSE](LICENSE).

## Créditos / Credits

Diseño, investigación y desarrollo: Héctor Cruz Juárez — https://hectorcruz.com.mx  
Colección Alchemy of Meaning.
