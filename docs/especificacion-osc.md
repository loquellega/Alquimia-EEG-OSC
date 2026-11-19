# Especificación OSC · Alquimia EEG OSC v0.8

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

## Interpretación (por casco)

| Dirección | Argumentos | Notas |
|-----------|------------|-------|
| `/eeg/{casco}/indice/{calma\|imaginacion\|atencion\|activacion\|valencia\|movimiento}` | `f 0–1` | 0.5 = línea base de la persona |
| `/eeg/{casco}/estado` | `i id, f intensidad` | 0 neutral, 1 calma, 2 imaginación, 3 atención, 4 activación, 5 movimiento |
| `/eeg/{casco}/estado/cambio` | `i id` | evento al cambiar |
| `/eeg/{casco}/estado/tiempo` | `f` | segundos en el estado actual |
| `/eeg/{casco}/frase` | `s texto` | evento; **única dirección con texto** |
| `/eeg/{casco}/sesion/tiempo` | `f` | segundos de sesión |
| `/eeg/{casco}/sesion/calibracion` | `f 0–1` | progreso de la línea base (1 = lista) |
| `/eeg/{casco}/sesion/calibrando` | `i 0/1` | evento al empezar y terminar |
| `/eeg/marca` | `s texto` | se reenvía cada marca de escena recibida (para los visores) |

## Entrada (puerto 9210)

| Dirección | Argumentos | Qué hace |
|-----------|------------|----------|
| `/eeg/marca` | texto opcional | marca de escena en la sesión |
| `/eeg/sesion/terminar` | — | guarda la sesión y empieza otra |
| `/eeg/sesion/reiniciar` | — | empieza otra sin guardar |
| `/eeg/sesion/recalibrar` | — | repite la línea base |
| `/eeg/frases/recargar` | — | vuelve a leer `frases.json` |
| `/endeles/…` | lo que sea | no lo usa el puente: lo reenvía tal cual al relevo WebSocket (cues de Endeles Escena: `/endeles/go`, `/endeles/cue i`, `/endeles/cam i`…) |

## Del sistema

| Dirección | Argumentos | Notas |
|-----------|------------|-------|
| `/eeg/frame` | `i seq, f t, i n` | inicio de cada cuadro: secuencia, segundos, cascos activos |
| `/eeg/system/status` | `i` | 0 detenido, 1 conectando, 2 en vivo (1 vez por segundo) |

## Reglas

- Tasa fija (30 Hz por defecto). Los datos lentos repiten su último valor. `/raw` sale a la tasa del casco.
- Solo enteros y decimales en los argumentos, para Isadora (OSC Listener) y TouchDesigner (OSC In CHOP).
- Las grabaciones (`--grabar`) son JSON por líneas (ver abajo). `--reproducir` las vuelve a enviar con el mismo ritmo, por OSC y por el relevo.

## Relevo WebSocket (puerto 9203)

El navegador no puede recibir UDP, así que el puente repite cada cuadro por WebSocket para artefactos web (Endeles Escena, aurigaQ…). Escucha en toda la red local (`--ws-local` para solo esta computadora; `--ws 0` lo apaga). Es de solo salida.

```json
{"seq": 1842, "t": 61.40,
 "d":  {"/eeg/2/band/alpha/norm": 0.41, "/eeg/2/quality": 0.88, "/eeg/2/valid": 1},
 "ev": [["/eeg/2/blink", [1]], ["/endeles/go", []]]}
```

- `d`: valores continuos del cuadro, con la dirección OSC como llave. Un argumento → número; varios → lista. `/raw` no se repite aquí.
- `ev`: eventos en orden: parpadeo, guiño, `/eeg/marca` y todo lo que llegó por `/endeles/…` al 9210.
- Al conectarse, la página recibe `{"cabecera": …}` (si se está grabando o reproduciendo) y un mensaje con `"completo": true` con el último valor de todo.

## Grabación de sesión (`.jsonl`, formato `alquimia-eeg-sesion` v1)

Una línea por objeto JSON:

1. **Cabecera** (primera línea):
   ```json
   {"tipo":"cabecera","formato":"alquimia-eeg-sesion","version":1,"id":"eeg_20261119_201405",
    "persona":"P-7F3A","filtro_casco":"","inicio":"2026-11-19T20:14:05-06:00","tasa":30,
    "via":"cortex","spec_osc":"0.2","programa":"Alquimia EEG OSC 0.8",
    "consentimiento":{"version":"c1","usos":["en_vivo","grabacion","reuso_artefactos"],"caduca":"2027-11-19","anonimo":true},
    "obra":{"titulo":"","escena":"","funcion":""}}
   ```
   Se llena con `--persona`, `--usos`, `--caduca`, `--consentimiento` y `--obra`. `persona` es un código anónimo: el nombre y el consentimiento firmado se guardan **fuera** del archivo.
2. **Cuadros:** `{"t": segundos, "m": [[dirección, [args]], …]}`, igual que antes.
3. **Cierre** (última línea, al salir con Ctrl+C o al terminar): `{"tipo":"cierre","duracion":…,"fin":…,"marcas":[{"t":…,"txt":…}]}`. Si el programa se cerró de golpe, falta; los lectores deben tolerarlo.

Las grabaciones viejas (sin cabecera) se siguen reproduciendo igual. Un artefacto que lea sesiones debe revisar `consentimiento.usos` antes de usar el archivo.
