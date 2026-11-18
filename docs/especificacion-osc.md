# Especificación OSC · Alquimia EEG OSC v0.3

Salida común para todas las vías de lectura (Cortex hoy, lector propio después). La pieza escénica no tiene que saber qué vía está activa.

## Capas

| # | Capa | Vía A (Cortex) | Vía B (propia, en desarrollo) |
|---|------|----------------|-------------------------------|
| 1 | Conexión | WebSocket `wss://localhost:6868` | BLE y descifrado propio |
| 2 | Señal | la resuelve Emotiv | filtros propios (1–45 Hz, notch de 60 Hz) |
| 3 | Rasgos | flujos `pow`, `met`, `fac`, `com`, `mot`, `dev` (`eeg` en cascos de consumo) | FFT: bandas, parpadeo, giro, contacto |
| 4 | Escena | suavizado y calibración móvil → valores 0–1 | (común) |
| 5 | Salida | OSC/UDP, puerto 9200 | (común) |

## Cascos y canales

| `{casco}` | Modelo | Canales (en este orden) |
|-----------|--------|-------------------------|
| 1 | EPOC / EPOC+ / EPOC X | AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4 |
| 2 | Insight | AF3 T7 Pz T8 AF4 |
| 3 | otro | según lo que reporte Cortex |

## Por casco

| Dirección | Argumentos | Notas |
|-----------|------------|-------|
| `/eeg/{casco}/band/{theta\|alpha\|betaL\|betaH\|gamma}` | `f` × canales | potencia por canal |
| `/eeg/{casco}/band/{banda}/avg` | `f` | promedio de los canales |
| `/eeg/{casco}/band/{banda}/norm` | `f 0–1` | calibración móvil, escala logarítmica |
| `/eeg/{casco}/blink` | `i 1` | evento |
| `/eeg/{casco}/wink` | `i -1/1` | -1 izquierdo, 1 derecho |
| `/eeg/{casco}/face/upper` | `i acción, f fuerza` | 0 neutral, 1 ceja arriba, 2 ceño |
| `/eeg/{casco}/face/lower` | `i acción, f fuerza` | 0 neutral, 1 sonrisa, 2 aprieto, 3 risa, 4 mueca izq., 5 mueca der. |
| `/eeg/{casco}/mental` | `i acción, f fuerza` | 0 neutral, 1 push, 2 pull, 3 lift, 4 drop, 5 left, 6 right, 7 rotateLeft, 8 rotateRight, 9 rotateClockwise, 10 rotateCounterClockwise, 11 rotateForwards, 12 rotateReverse, 13 disappear |
| `/eeg/{casco}/metric/{attention\|relaxation\|stress\|engagement\|excitement\|interest\|focus}` | `f 0–1` | en baja resolución se actualizan cada ~10 s |
| `/eeg/{casco}/gyro` | `f x, f y` | giro de cabeza respecto al inicio, -1 a 1 |
| `/eeg/{casco}/contact` | `f` × canales | 0–1 por electrodo |
| `/eeg/{casco}/quality` | `f 0–1` | calidad global |
| `/eeg/{casco}/battery` | `f 0–1` | |
| `/eeg/{casco}/raw` | `f` × canales | EEG crudo en µV, por muestra; solo con `--crudo` |
| `/eeg/{casco}/source` | `i` | 0 simulación, 1 Cortex, 2 lector propio |
| `/eeg/{casco}/valid` | `i 0/1` | 0 si la calidad < 0.4 o si pasan más de 500 ms sin datos |

## Del sistema

| Dirección | Argumentos | Notas |
|-----------|------------|-------|
| `/eeg/frame` | `i seq, f t, i n` | inicio de cada cuadro: secuencia, segundos, cascos activos |
| `/eeg/system/status` | `i` | 0 detenido, 1 conectando, 2 en vivo (1 vez por segundo) |

## Reglas

- Tasa fija (30 Hz por defecto). Los datos lentos repiten su último valor. `/raw` sale a la tasa del casco.
- Solo enteros y decimales en los argumentos, para Isadora (OSC Listener) y TouchDesigner (OSC In CHOP).
- Las grabaciones (`--grabar`) son JSON por líneas: `{"t": segundos, "m": [[dirección, [args]], …]}`. `--reproducir` las vuelve a enviar con el mismo ritmo.
