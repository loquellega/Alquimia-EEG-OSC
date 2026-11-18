#!/usr/bin/env python3
"""
Alquimia EEG OSC v0.1 — vía A (Cortex)
Puente entre los cascos Emotiv (Insight, EPOC+) y OSC para escena.

Lee los flujos de Cortex (bandas, expresiones, métricas, movimiento, contacto),
los suaviza y normaliza, y los envía por OSC según la especificación de
docs/especificacion-osc.md (puerto 9200, solo enteros y decimales).

Modos:
  python cortex_osc.py                 en vivo con Cortex
  python cortex_osc.py --simular       datos sintéticos, sin casco (para probar TD/Isadora)
  python cortex_osc.py --reproducir X  reproduce una grabación .jsonl

Diseño, investigación y desarrollo: Héctor Cruz Juárez — https://hectorcruz.com.mx
Colección Alchemy of Meaning. · GPL-3.0-or-later
"""
import argparse
import asyncio
import json
import math
import os
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path

from pythonosc.udp_client import SimpleUDPClient

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv es opcional
    load_dotenv = None

CORTEX_URL = "wss://localhost:6868"
BANDS = ["theta", "alpha", "betaL", "betaH", "gamma"]
STREAMS_BASE = ["pow", "met", "fac", "com", "mot", "dev"]

# Tablas de códigos (solo enteros en OSC)
FACE_UPPER = {"surprise": 1, "frown": 2}
FACE_LOWER = {"smile": 1, "clench": 2, "laugh": 3, "smirkLeft": 4, "smirkRight": 5}
MENTAL = {"neutral": 0, "push": 1, "pull": 2, "lift": 3, "drop": 4, "left": 5,
          "right": 6, "rotateLeft": 7, "rotateRight": 8, "rotateClockwise": 9,
          "rotateCounterClockwise": 10, "rotateForwards": 11, "rotateReverse": 12,
          "disappear": 13}
METRICS = {"eng": "engagement", "exc": "excitement", "str": "stress", "rel": "relaxation",
           "int": "interest", "foc": "focus", "attention": "attention", "att": "attention"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def casco_numero(headset_id: str) -> int:
    s = headset_id.upper()
    if "EPOC" in s:
        return 1
    if "INSIGHT" in s:
        return 2
    return 3


def clip(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Capa 4: escena (suavizado, calibración móvil)
# ---------------------------------------------------------------------------
class Rango:
    """Mínimo y máximo móviles con olvido lento: calibración automática por intérprete."""

    def __init__(self, olvido=0.002):
        self.lo = self.hi = None
        self.olvido = olvido

    def update(self, v):
        if self.lo is None:
            self.lo = self.hi = v
            return
        self.lo = min(self.lo, v)
        self.hi = max(self.hi, v)
        self.lo += (v - self.lo) * self.olvido
        self.hi += (v - self.hi) * self.olvido

    def norm(self, v):
        if self.lo is None or self.hi - self.lo < 1e-9:
            return 0.5
        return clip((v - self.lo) / (self.hi - self.lo), 0.0, 1.0)


class Casco:
    def __init__(self, numero, headset_id="", fuente=1, suavizado=0.3):
        self.n = numero
        self.id = headset_id
        self.fuente = fuente
        self.a = suavizado
        self.canales = []
        self.band = {}            # banda -> [valor por canal]
        self.band_rango = {b: Rango() for b in BANDS}
        self.metricas = {}
        self.face_upper = None    # (acción, fuerza)
        self.face_lower = None
        self.mental = None
        self.gyro = None          # (x, y) en -1..1
        self.gyro_base = None
        self.gyro_rango = [Rango(), Rango()]
        self.contacto = None
        self.calidad = 0.0
        self.bateria = None
        self.ultimo = 0.0
        self.eventos = []         # mensajes sueltos (parpadeo, guiño)
        self.cols = {}            # flujo -> columnas que reporta Cortex

    # --- Capa 3: rasgos desde Cortex ---
    def recibir(self, flujo, datos, ahora, out=None, crudo=False):
        self.ultimo = ahora
        cols = self.cols.get(flujo)
        if cols is None:
            return
        if flujo == "pow":
            self._pow(cols, datos)
        elif flujo == "met":
            self._met(cols, datos)
        elif flujo == "fac":
            self._fac(dict(zip(cols, datos)))
        elif flujo == "com":
            d = dict(zip(cols, datos))
            self.mental = (MENTAL.get(d.get("act"), 0), float(d.get("pow") or 0.0))
        elif flujo == "dev":
            self._dev(cols, datos)
        elif flujo == "mot":
            self._mot(dict(zip(cols, datos)))
        elif flujo == "eeg" and crudo and out is not None:
            d = dict(zip(cols, datos))
            vals = [float(d[c]) for c in self.canales if isinstance(d.get(c), (int, float))]
            if vals:
                out.send(f"/eeg/{self.n}/raw", vals)

    def _pow(self, cols, datos):
        nuevos = {}
        orden = []
        for c, v in zip(cols, datos):
            if "/" not in c or v is None:
                continue
            ch, b = c.split("/", 1)
            if ch not in orden:
                orden.append(ch)
            nuevos.setdefault(b, {})[ch] = float(v)
        if not self.canales:
            self.canales = orden
        for b, por_canal in nuevos.items():
            vals = [por_canal.get(ch, 0.0) for ch in self.canales]
            prev = self.band.get(b)
            if prev and len(prev) == len(vals):
                vals = [p + (v - p) * self.a for p, v in zip(prev, vals)]
            self.band[b] = vals
            if b in self.band_rango:
                self.band_rango[b].update(math.log10(sum(vals) / len(vals) + 1e-6))

    def _met(self, cols, datos):
        for c, v in zip(cols, datos):
            if c.endswith(".isActive") or v is None:
                continue
            nombre = METRICS.get(c)
            if nombre and isinstance(v, (int, float)):
                prev = self.metricas.get(nombre)
                v = float(v)
                self.metricas[nombre] = v if prev is None else prev + (v - prev) * self.a

    def _fac(self, d):
        ojo = d.get("eyeAct")
        if ojo == "blink":
            self.eventos.append((f"/eeg/{self.n}/blink", [1]))
        elif ojo == "winkL":
            self.eventos.append((f"/eeg/{self.n}/wink", [-1]))
        elif ojo == "winkR":
            self.eventos.append((f"/eeg/{self.n}/wink", [1]))
        if "uAct" in d:
            self.face_upper = (FACE_UPPER.get(d.get("uAct"), 0), float(d.get("uPow") or 0.0))
        if "lAct" in d:
            self.face_lower = (FACE_LOWER.get(d.get("lAct"), 0), float(d.get("lPow") or 0.0))

    def _dev(self, cols, datos):
        for c, v in zip(cols, datos):
            if isinstance(c, list) and isinstance(v, list):
                d = dict(zip(c, v))
                total = d.pop("OVERALL", None)
                cq = [float(d[ch]) / 4.0 for ch in (self.canales or list(d)) if ch in d]
                if cq:
                    self.contacto = [clip(x, 0.0, 1.0) for x in cq]
                if isinstance(total, (int, float)):
                    self.calidad = clip(float(total) / 100.0, 0.0, 1.0)
                elif cq:
                    self.calidad = sum(self.contacto) / len(self.contacto)
            elif c == "BatteryPercent" and isinstance(v, (int, float)):
                self.bateria = clip(float(v) / 100.0, 0.0, 1.0)

    def _mot(self, d):
        if all(k in d for k in ("Q0", "Q1", "Q2", "Q3")):
            w, x, y, z = (float(d[k]) for k in ("Q0", "Q1", "Q2", "Q3"))
            yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
            pitch = math.degrees(math.asin(clip(2 * (w * y - z * x), -1.0, 1.0)))
            if self.gyro_base is None:
                self.gyro_base = (yaw, pitch)
            dy = (yaw - self.gyro_base[0] + 180) % 360 - 180
            dp = pitch - self.gyro_base[1]
            nuevo = (clip(dy / 90.0, -1, 1), clip(dp / 90.0, -1, 1))
        elif "GYROX" in d and "GYROY" in d:
            gx, gy = float(d["GYROX"]), float(d["GYROY"])
            self.gyro_rango[0].update(gx)
            self.gyro_rango[1].update(gy)
            nuevo = (self.gyro_rango[0].norm(gx) * 2 - 1, self.gyro_rango[1].norm(gy) * 2 - 1)
        else:
            return
        if self.gyro is None:
            self.gyro = nuevo
        else:
            self.gyro = tuple(p + (v - p) * self.a for p, v in zip(self.gyro, nuevo))

    # --- Capa 5: salida ---
    def emitir(self, out, ahora):
        p = f"/eeg/{self.n}"
        for addr, args in self.eventos:
            out.send(addr, args)
        self.eventos.clear()
        for b in BANDS:
            vals = self.band.get(b)
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            out.send(f"{p}/band/{b}", [float(v) for v in vals])
            out.send(f"{p}/band/{b}/avg", [float(avg)])
            out.send(f"{p}/band/{b}/norm", [float(self.band_rango[b].norm(math.log10(avg + 1e-6)))])
        if self.face_upper:
            out.send(f"{p}/face/upper", [int(self.face_upper[0]), float(self.face_upper[1])])
        if self.face_lower:
            out.send(f"{p}/face/lower", [int(self.face_lower[0]), float(self.face_lower[1])])
        if self.mental:
            out.send(f"{p}/mental", [int(self.mental[0]), float(self.mental[1])])
        for nombre, v in self.metricas.items():
            out.send(f"{p}/metric/{nombre}", [float(clip(v, 0.0, 1.0))])
        if self.gyro:
            out.send(f"{p}/gyro", [float(self.gyro[0]), float(self.gyro[1])])
        if self.contacto:
            out.send(f"{p}/contact", [float(v) for v in self.contacto])
        out.send(f"{p}/quality", [float(self.calidad)])
        if self.bateria is not None:
            out.send(f"{p}/battery", [float(self.bateria)])
        out.send(f"{p}/source", [int(self.fuente)])
        valido = (ahora - self.ultimo) < 0.5 and self.calidad >= 0.4
        out.send(f"{p}/valid", [1 if valido else 0])


# ---------------------------------------------------------------------------
# Salida OSC y grabación
# ---------------------------------------------------------------------------
class Salida:
    def __init__(self, destinos, grabar=None):
        self.clientes = []
        for d in destinos:
            host, _, puerto = d.rpartition(":")
            self.clientes.append(SimpleUDPClient(host or "127.0.0.1", int(puerto)))
        self.cuadro = []
        self.archivo = None
        if grabar:
            Path(grabar).parent.mkdir(parents=True, exist_ok=True)
            self.archivo = open(grabar, "a", encoding="utf-8")
            log("Grabando en", grabar)

    def send(self, addr, args):
        for c in self.clientes:
            c.send_message(addr, args)
        self.cuadro.append([addr, args])

    def cerrar_cuadro(self, t):
        if self.archivo and self.cuadro:
            self.archivo.write(json.dumps({"t": round(t, 4), "m": self.cuadro}) + "\n")
        self.cuadro = []

    def cerrar(self):
        if self.archivo:
            self.archivo.close()


async def bucle_salida(cascos, out, hz, estado):
    periodo = 1.0 / hz
    t0 = time.monotonic()
    seq = 0
    while True:
        ahora = time.monotonic()
        out.send("/eeg/frame", [seq, float(ahora - t0), len(cascos)])
        if seq % hz == 0:
            out.send("/eeg/system/status", [estado["status"]])
        for c in list(cascos.values()):
            c.emitir(out, ahora)
        out.cerrar_cuadro(ahora - t0)
        seq += 1
        await asyncio.sleep(max(0.0, periodo - (time.monotonic() - ahora)))


# ---------------------------------------------------------------------------
# Capa 1: conexión con Cortex
# ---------------------------------------------------------------------------
class CortexError(Exception):
    pass


class Cortex:
    def __init__(self, url, al_recibir):
        self.url = url
        self.al_recibir = al_recibir
        self.pendientes = {}
        self.sig_id = 0
        self.ws = None

    async def conectar(self):
        import websockets
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # Cortex usa un certificado propio en localhost
        self.ws = await websockets.connect(self.url, ssl=ctx, max_size=None)
        asyncio.create_task(self._leer())

    async def llamar(self, metodo, **params):
        self.sig_id += 1
        rid = self.sig_id
        fut = asyncio.get_running_loop().create_future()
        self.pendientes[rid] = fut
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": metodo, "params": params}))
        resp = await asyncio.wait_for(fut, 30)
        if "error" in resp:
            raise CortexError(f"{metodo}: {resp['error'].get('message')} (código {resp['error'].get('code')})")
        return resp.get("result")

    async def _leer(self):
        async for msg in self.ws:
            d = json.loads(msg)
            rid = d.get("id")
            if rid in self.pendientes:
                self.pendientes.pop(rid).set_result(d)
            elif "warning" in d:
                w = d["warning"]
                log("Aviso de Cortex:", w.get("code"), w.get("message"))
            else:
                self.al_recibir(d)
        log("Cortex cerró la conexión.")


async def modo_cortex(args, out, cascos, estado):
    client_id = os.environ.get("EMOTIV_CLIENT_ID")
    secret = os.environ.get("EMOTIV_CLIENT_SECRET")
    if not client_id or not secret:
        sys.exit("Faltan EMOTIV_CLIENT_ID y EMOTIV_CLIENT_SECRET en el archivo .env")

    por_sesion = {}  # sid -> Casco

    def al_recibir(d):
        casco = por_sesion.get(d.get("sid"))
        if not casco:
            return
        ahora = time.monotonic()
        for flujo in ("pow", "met", "fac", "com", "dev", "mot", "eeg"):
            if flujo in d:
                casco.recibir(flujo, d[flujo], ahora, out, args.crudo)

    cx = Cortex(args.url, al_recibir)
    log("Conectando con Cortex en", args.url, "…")
    try:
        await cx.conectar()
    except OSError as e:
        sys.exit(f"No pude conectar con Cortex ({e}). ¿Está abierto el EMOTIV Launcher?")

    estado["status"] = 1
    r = await cx.llamar("requestAccess", clientId=client_id, clientSecret=secret)
    espera = 0
    while not r.get("accessGranted"):
        if espera == 0:
            log("Aprueba el acceso de la app en el EMOTIV Launcher…")
        await asyncio.sleep(3)
        espera += 3
        if espera > 120:
            sys.exit("No se aprobó el acceso en el Launcher.")
        r = await cx.llamar("requestAccess", clientId=client_id, clientSecret=secret)
    token = (await cx.llamar("authorize", clientId=client_id, clientSecret=secret))["cortexToken"]
    log("Autorizado.")

    # Buscar y conectar cascos
    await cx.llamar("controlDevice", command="refresh")
    await asyncio.sleep(3)
    for intento in range(20):
        lista = await cx.llamar("queryHeadsets") or []
        if args.casco:
            lista = [h for h in lista if args.casco.upper() in h["id"].upper()]
        if lista:
            break
        if intento == 0:
            log("Buscando cascos… (enciéndelo y revisa el Bluetooth)")
        await asyncio.sleep(3)
    else:
        sys.exit("No encontré ningún casco.")

    for h in lista:
        if h.get("status") != "connected":
            log("Conectando", h["id"], "…")
            await cx.llamar("controlDevice", command="connect", headset=h["id"])
    for _ in range(20):
        lista2 = {h["id"]: h for h in (await cx.llamar("queryHeadsets") or [])}
        if all(lista2.get(h["id"], {}).get("status") == "connected" for h in lista):
            break
        await asyncio.sleep(2)

    streams = STREAMS_BASE + (["eeg"] if args.crudo else [])
    for h in lista:
        hid = h["id"]
        try:
            ses = await cx.llamar("createSession", cortexToken=token, headset=hid, status="active")
        except CortexError as e:
            log("No pude abrir sesión con", hid, "→", e)
            continue
        n = casco_numero(hid)
        casco = Casco(n, hid, fuente=1, suavizado=args.suavizado)
        cascos[n] = casco
        por_sesion[ses["id"]] = casco
        res = await cx.llamar("subscribe", cortexToken=token, session=ses["id"], streams=streams)
        for ok in res.get("success", []):
            casco.cols[ok["streamName"]] = ok["cols"]
        if "eeg" in casco.cols:
            casco.canales = [c for c in casco.cols["eeg"]
                             if c not in ("COUNTER", "INTERPOLATED", "RAW_CQ", "MARKER_HARDWARE", "MARKERS")]
        nombres = ", ".join(casco.cols)
        log(f"Casco {n} ({hid}) → flujos: {nombres}")
        for fail in res.get("failure", []):
            log(f"  sin flujo {fail.get('streamName')}: {fail.get('message')}")

    if not cascos:
        sys.exit("No quedó ningún casco suscrito.")
    estado["status"] = 2
    log(f"En vivo. Enviando OSC a {', '.join(args.destino)} a {args.hz} Hz. Ctrl+C para salir.")
    await asyncio.Event().wait()


# ---------------------------------------------------------------------------
# Modo simulación (sin casco) y reproducción
# ---------------------------------------------------------------------------
async def modo_simular(args, out, cascos, estado):
    n = 2
    c = Casco(n, "SIMULADO", fuente=0, suavizado=args.suavizado)
    canales = ["AF3", "T7", "Pz", "T8", "AF4"]
    c.cols = {
        "pow": [f"{ch}/{b}" for ch in canales for b in BANDS],
        "met": ["attention", "eng", "exc", "str", "rel", "int", "foc"],
        "fac": ["eyeAct", "uAct", "uPow", "lAct", "lPow"],
        "dev": ["Battery", "Signal", canales + ["OVERALL"], "BatteryPercent"],
        "mot": ["Q0", "Q1", "Q2", "Q3"],
    }
    cascos[n] = c
    estado["status"] = 2
    log("Simulando el casco 2 (Insight). Enviando OSC a", ", ".join(args.destino))
    t0 = time.monotonic()
    k = 0
    while True:
        t = time.monotonic() - t0
        ahora = time.monotonic()
        lento = 0.5 + 0.5 * math.sin(t / 8.0)
        c.recibir("pow", [(2 + 6 * lento) * (1 + 0.2 * math.sin(t * 3 + i)) for i in range(25)], ahora)
        if k % 8 == 0:
            c.recibir("met", [lento, 0.6, 0.4, 1 - lento, lento, 0.5, 0.7], ahora)
            c.recibir("dev", [4, 2, [4, 3, 4, 4, 3, 90], 80], ahora)
        ojo = "blink" if k % 40 == 0 else "neutral"
        c.recibir("fac", [ojo, "neutral", 0.0, "smile" if lento > 0.8 else "neutral", lento], ahora)
        yaw = math.radians(20 * math.sin(t / 3))
        c.recibir("mot", [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)], ahora)
        k += 1
        await asyncio.sleep(0.125)  # 8 Hz, como el flujo pow de Cortex


async def modo_reproducir(args, out):
    lineas = Path(args.reproducir).read_text(encoding="utf-8").splitlines()
    cuadros = [json.loads(l) for l in lineas if l.strip()]
    if not cuadros:
        sys.exit("La grabación está vacía.")
    log(f"Reproduciendo {len(cuadros)} cuadros de {args.reproducir}")
    while True:
        t_ini = time.monotonic()
        t_base = cuadros[0]["t"]
        for c in cuadros:
            espera = (c["t"] - t_base) - (time.monotonic() - t_ini)
            if espera > 0:
                await asyncio.sleep(espera)
            for addr, a in c["m"]:
                out.send(addr, a)
            out.cuadro = []
        if not args.bucle:
            break
        log("Repitiendo…")


# ---------------------------------------------------------------------------
async def principal(args):
    grabar = None
    if args.grabar and not args.reproducir:
        grabar = str(Path("grabaciones") / f"eeg_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    out = Salida(args.destino, grabar)
    try:
        if args.reproducir:
            await modo_reproducir(args, out)
            return
        cascos, estado = {}, {"status": 0}
        tarea = asyncio.create_task(bucle_salida(cascos, out, args.hz, estado))
        try:
            if args.simular:
                await modo_simular(args, out, cascos, estado)
            else:
                await modo_cortex(args, out, cascos, estado)
        finally:
            tarea.cancel()
    finally:
        out.cerrar()


def main():
    if load_dotenv:
        load_dotenv(Path(__file__).with_name(".env"))
    ap = argparse.ArgumentParser(description="Puente EEG (Emotiv Cortex) → OSC")
    ap.add_argument("--destino", action="append",
                    help="host:puerto de destino OSC (se puede repetir). Por defecto 127.0.0.1:9200")
    ap.add_argument("--hz", type=int, default=30, help="tasa de salida OSC (30 por defecto)")
    ap.add_argument("--suavizado", type=float, default=0.3, help="0–1; más bajo = más suave")
    ap.add_argument("--casco", help="usar solo el casco cuyo id contenga este texto (p. ej. INSIGHT)")
    ap.add_argument("--crudo", action="store_true", help="enviar también el EEG crudo en /eeg/{casco}/raw")
    ap.add_argument("--grabar", action="store_true", help="grabar la sesión en grabaciones/*.jsonl")
    ap.add_argument("--simular", action="store_true", help="datos sintéticos, sin casco")
    ap.add_argument("--reproducir", metavar="ARCHIVO", help="reproducir una grabación .jsonl")
    ap.add_argument("--bucle", action="store_true", help="con --reproducir: repetir sin fin")
    ap.add_argument("--url", default=CORTEX_URL, help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.destino = args.destino or ["127.0.0.1:9200"]
    try:
        asyncio.run(principal(args))
    except KeyboardInterrupt:
        log("Detenido.")


if __name__ == "__main__":
    main()
