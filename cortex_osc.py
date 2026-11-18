#!/usr/bin/env python3
"""
Alquimia EEG OSC v0.7 — vía A (Cortex)
Puente entre los cascos Emotiv (Insight, EPOC+) y OSC para escena.

Lee los flujos de Cortex (bandas, expresiones, métricas, movimiento, contacto),
los suaviza y normaliza, y los envía por OSC según la especificación de
docs/especificacion-osc.md (puerto 9200, solo enteros y decimales).

Modos:
  python cortex_osc.py                 en vivo con Cortex
  python cortex_osc.py --simular       datos sintéticos, sin casco (para probar TD/Isadora)
  python cortex_osc.py --reproducir X  reproduce una grabación .jsonl
  python cortex_osc.py --monitor       además abre el monitor de contacto (asistente y avanzado)

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

import interpretacion as interp

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


CONGELADO = getattr(sys, "frozen", False)  # True dentro del .exe (PyInstaller)


def carpeta_programa() -> Path:
    """Carpeta del .exe o del script (para .env y grabaciones)."""
    return Path(sys.executable).parent if CONGELADO else Path(__file__).parent


def carpeta_recursos() -> Path:
    """Carpeta con monitor.html (dentro del .exe, PyInstaller la extrae a _MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def archivo_frases() -> Path:
    propio = carpeta_programa() / "frases.json"
    return propio if propio.exists() else carpeta_recursos() / "frases.json"


def carpeta_sesiones() -> Path:
    return Path.cwd() / "sesiones"


AJUSTES_UI = {   # lo que el dashboard guarda y lee
    "colores": {"calma": "#3d8bff", "imaginacion": "#d846ef", "atencion": "#f5b301",
                "activacion": "#ff4d2e", "valencia": "#9aa0a6", "movimiento": "#2fd6b5"},
    "visibles": ["calma", "imaginacion", "atencion", "activacion"],
    "paneles": {"izq": 300, "der": 320, "abajo": 230},
    "ventana_vivo": 0, "rotulos_marcas": True, "electrodos": "compacto",
    "etiquetas": ["risa", "silencio", "música", "cambio de luz", "entra personaje", "sale personaje",
                  "texto clave", "tensión", "sorpresa", "aplauso", "oscuro", "movimiento del público"],
}


def lista_sesiones():
    """Sesiones guardadas (los .json de la carpeta), de la más nueva a la más vieja."""
    out = []
    carpeta = carpeta_sesiones()
    if not carpeta.exists():
        return out
    for f in sorted(carpeta.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            r = d.get("resumen", {})
            out.append({"id": f.stem, "nombre": r.get("nombre") or f.stem, "inicio": r.get("inicio", ""),
                        "duracion": r.get("total_s") or r.get("duracion_s", 0), "analizado": r.get("duracion_s", 0),
                        "marcas": len(r.get("marcas", [])), "porcentaje": r.get("porcentaje", {}),
                        "tamano": f.stat().st_size})
        except (OSError, ValueError):
            continue
    return out


def archivo_sesion(ident, ext):
    """Ruta segura de un archivo de sesión (solo nombres simples dentro de la carpeta)."""
    if not ident or not all(c.isalnum() or c in "-_." for c in ident) or ".." in ident:
        return None
    f = carpeta_sesiones() / f"{ident}.{ext}"
    return f if f.exists() else None


def ajustes_ui():
    d = json.loads(json.dumps(AJUSTES_UI))
    guardado = leer_config().get("ui") or {}
    for k, v in guardado.items():
        if k in d and isinstance(v, type(d[k])):
            if isinstance(v, dict):
                d[k].update({kk: vv for kk, vv in v.items()})
            else:
                d[k] = v
    return d


def archivo_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "AlquimiaEEGOSC" / "config.json"


def leer_config():
    try:
        d = json.loads(archivo_config().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def guardar_config(**cambios):
    d = leer_config()
    d.update(cambios)
    f = archivo_config()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def leer_credenciales():
    """Primero el .env o las variables de entorno; si no, la configuración guardada desde el monitor."""
    cid = os.environ.get("EMOTIV_CLIENT_ID")
    sec = os.environ.get("EMOTIV_CLIENT_SECRET")
    if cid and sec:
        return cid.strip(), sec.strip()
    d = leer_config()
    if d.get("client_id") and d.get("client_secret"):
        return d["client_id"].strip(), d["client_secret"].strip()
    return None


def destino_valido(texto):
    """'host:puerto' → (host, puerto) o None."""
    host, _, puerto = str(texto).strip().rpartition(":")
    host = host or "127.0.0.1"
    if not puerto.isdigit() or not (1 <= int(puerto) <= 65535):
        return None
    if len(host) > 253 or not all(c.isalnum() or c in ".-" for c in host):
        return None
    return host, int(puerto)


def guardar_credenciales(cid, sec):
    guardar_config(client_id=cid.strip(), client_secret=sec.strip())
    # lo nuevo manda sobre un .env viejo durante esta sesión
    os.environ["EMOTIV_CLIENT_ID"] = cid.strip()
    os.environ["EMOTIV_CLIENT_SECRET"] = sec.strip()


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
        self.cq = None            # contacto por canal, niveles 0–4 de Cortex
        self.senal = None         # intensidad de la conexión inalámbrica 0–1
        self.paquetes = 0
        self.t_parpadeo = 0.0

    # --- Capa 3: rasgos desde Cortex ---
    def recibir(self, flujo, datos, ahora, out=None, crudo=False):
        self.ultimo = ahora
        self.paquetes += 1
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
            self.t_parpadeo = time.monotonic()
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
                if not self.canales:
                    self.canales = list(d)
                niveles = [d[ch] for ch in self.canales if isinstance(d.get(ch), (int, float))]
                self.cq = [int(round(x)) for x in niveles]
                cq = [float(x) / 4.0 for x in niveles]
                if cq:
                    self.contacto = [clip(x, 0.0, 1.0) for x in cq]
                if isinstance(total, (int, float)):
                    self.calidad = clip(float(total) / 100.0, 0.0, 1.0)
                elif cq:
                    self.calidad = sum(self.contacto) / len(self.contacto)
            elif c == "Signal" and isinstance(v, (int, float)):
                self.senal = clip(float(v), 0.0, 1.0)
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
        out.send(f"{p}/valid", [1 if self.valido(ahora) else 0])

    def valido(self, ahora):
        return (ahora - self.ultimo) < 0.5 and self.calidad >= 0.4

    def resumen(self, ahora):
        """Estado para el monitor de contacto."""
        bandas = {}
        for b in BANDS:
            vals = self.band.get(b)
            if vals:
                bandas[b] = round(self.band_rango[b].norm(math.log10(sum(vals) / len(vals) + 1e-6)), 3)
        return {
            "n": self.n, "id": self.id, "fuente": self.fuente,
            "canales": self.canales, "cq": self.cq or [],
            "calidad": round(self.calidad, 3), "bateria": self.bateria, "senal": self.senal,
            "edad": round(ahora - self.ultimo, 2) if self.ultimo else None,
            "valido": self.valido(ahora), "paquetes": self.paquetes, "bandas": bandas,
            "parpadeo": round(ahora - self.t_parpadeo, 2) if self.t_parpadeo else None,
            "gyro": self.gyro,
        }


# ---------------------------------------------------------------------------
# Salida OSC y grabación
# ---------------------------------------------------------------------------
class Salida:
    def __init__(self, destinos, grabar=None):
        self.destinos = []
        self.clientes = []
        self.configurar(destinos)
        self.enviados = 0
        self.cuadro = []
        self.archivo = None
        if grabar:
            Path(grabar).parent.mkdir(parents=True, exist_ok=True)
            self.archivo = open(grabar, "a", encoding="utf-8")
            log("Grabando en", grabar)

    def configurar(self, destinos):
        nuevos, clientes = [], []
        for d in destinos:
            hp = destino_valido(d)
            if hp and f"{hp[0]}:{hp[1]}" not in nuevos:
                nuevos.append(f"{hp[0]}:{hp[1]}")
                clientes.append(SimpleUDPClient(*hp))
        if not nuevos:
            nuevos, clientes = ["127.0.0.1:9200"], [SimpleUDPClient("127.0.0.1", 9200)]
        self.destinos, self.clientes = nuevos, clientes   # se reemplazan de una vez

    def send(self, addr, args):
        for c in self.clientes:
            try:
                c.send_message(addr, args)
            except OSError:
                pass   # un destino caído no detiene a los demás
        self.enviados += 1
        self.cuadro.append([addr, args])

    def cerrar_cuadro(self, t):
        if self.archivo and self.cuadro:
            self.archivo.write(json.dumps({"t": round(t, 4), "m": self.cuadro}) + "\n")
        self.cuadro = []

    def cerrar(self):
        if self.archivo:
            self.archivo.close()


def interprete_de(estado, n):
    """Un intérprete (índices, estado, frases, sesión) por casco, creado al primer dato."""
    ints = estado.setdefault("interpretes", {})
    if n not in ints:
        a = estado.get("args")
        narr = estado.get("narrador") or interp.Narrador(archivo_frases())
        estado["narrador"] = narr
        it = interp.Interprete(narr, calibracion=getattr(a, "calibracion", 60), contraste=getattr(a, "contraste", 1.0),
                               permanencia=getattr(a, "permanencia", 8))
        guardado = leer_config().get("interpretacion") or {}
        it.ajustar(**{k: v for k, v in guardado.items() if k in it.cfg and not getattr(a, k + "_cli", False)})
        it.sesion.carpeta = carpeta_sesiones()
        if estado.get("sesion_nombre"):
            it.sesion.nombre = estado["sesion_nombre"]
        ints[n] = it
    return ints[n]


async def bucle_salida(cascos, out, hz, estado):
    estado.setdefault("hz", hz)
    t0 = time.monotonic()
    seq = 0
    t_interp = 0.0
    while True:
        hz = max(1, min(120, int(estado["hz"])))
        periodo = 1.0 / hz
        ahora = time.monotonic()
        out.send("/eeg/frame", [seq, float(ahora - t0), len(cascos)])
        if seq % hz == 0:
            out.send("/eeg/system/status", [estado["status"]])
        con_interp = not getattr(estado.get("args"), "sin_interpretacion", False)
        actualizar = ahora - t_interp >= 0.25
        if actualizar:
            t_interp = ahora
        for c in list(cascos.values()):
            c.emitir(out, ahora)
            if con_interp:
                it = interprete_de(estado, c.n)
                if actualizar:
                    it.actualizar(c, ahora)
                it.emitir(out, c.n, ahora)
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


async def esperar_credenciales(args, estado, rechazadas=None):
    pasos = estado["pasos"]
    avisado = False
    while True:
        c = leer_credenciales()
        if c and c != rechazadas:
            pasos["credenciales"] = True
            return c
        pasos["credenciales"] = False
        if not args.monitor:
            sys.exit("Faltan los datos de tu app de Cortex. Ponlos en el archivo .env "
                     "o abre el puente con --monitor para escribirlos ahí.")
        if not avisado:
            if not rechazadas:
                estado["mensaje"] = "Escribe en el monitor el Client ID y el Client Secret de tu app de Cortex."
            log(estado["mensaje"])
            avisado = True
        await asyncio.sleep(1)


async def autorizar(cx, client_id, secret, debito):
    """authorize con debit: suma sesiones a la cuota local de esta computadora (error -32019 si se agota)."""
    try:
        return (await cx.llamar("authorize", clientId=client_id, clientSecret=secret, debit=debito))["cortexToken"]
    except CortexError as e:
        log("authorize con debit no funcionó, pruebo sin él:", e)
        return (await cx.llamar("authorize", clientId=client_id, clientSecret=secret))["cortexToken"]


async def abrir_sesion(cx, token, hid, client_id, secret, debito):
    """createSession; si la cuota local se agotó (-32019), vuelve a autorizar con debit y reintenta."""
    try:
        return await cx.llamar("createSession", cortexToken=token, headset=hid, status="active"), token
    except CortexError as e:
        if "-32019" not in str(e):
            raise
        log("Cuota de sesiones agotada; pidiendo más (debit)…")
        token = (await cx.llamar("authorize", clientId=client_id, clientSecret=secret,
                                 debit=max(debito, 10)))["cortexToken"]
        return await cx.llamar("createSession", cortexToken=token, headset=hid, status="active"), token


async def modo_cortex(args, out, cascos, estado):
    pasos = estado["pasos"]
    client_id, secret = await esperar_credenciales(args, estado)

    por_sesion = {}  # sid -> Casco

    def al_recibir(d):
        casco = por_sesion.get(d.get("sid"))
        if not casco:
            return
        ahora = time.monotonic()
        for flujo in ("pow", "met", "fac", "com", "dev", "mot", "eeg"):
            if flujo in d:
                casco.recibir(flujo, d[flujo], ahora, out, args.crudo)

    # Paso 1: Launcher (Cortex). Espera sin cerrarse.
    cx = Cortex(args.url, al_recibir)
    estado["status"] = 1
    avisado = False
    while True:
        try:
            await cx.conectar()
            break
        except OSError:
            if not avisado:
                estado["mensaje"] = "Esperando el EMOTIV Launcher… ábrelo e inicia sesión."
                log(estado["mensaje"])
                avisado = True
            await asyncio.sleep(3)
    pasos["cortex"] = True
    log("Conectado con Cortex.")

    # Paso 2: aprobación de la app (si Cortex rechaza los datos, se piden de nuevo)
    while True:
        try:
            r = await cx.llamar("requestAccess", clientId=client_id, clientSecret=secret)
            avisado = False
            while not r.get("accessGranted"):
                pasos["esperando_aprobacion"] = True
                if not avisado:
                    estado["mensaje"] = "Aprueba la app «Alquimia EEG OSC» en el EMOTIV Launcher."
                    log(estado["mensaje"])
                    avisado = True
                await asyncio.sleep(3)
                r = await cx.llamar("requestAccess", clientId=client_id, clientSecret=secret)
            pasos["esperando_aprobacion"] = False
            token = await autorizar(cx, client_id, secret, args.debito)
            break
        except CortexError as e:
            pasos["esperando_aprobacion"] = False
            estado["mensaje"] = (f"Cortex no aceptó los datos de la app ({e}). Revisa el Client ID y el Secret, "
                                 f"y que el Launcher tenga tu sesión iniciada.")
            client_id, secret = await esperar_credenciales(args, estado, rechazadas=(client_id, secret))
    estado["mensaje"] = ""
    pasos["autorizado"] = True
    log("Autorizado.")

    # Paso 3: buscar y conectar el casco. Espera sin cerrarse.
    avisado = False
    while True:
        await cx.llamar("controlDevice", command="refresh")
        await asyncio.sleep(3)
        lista = await cx.llamar("queryHeadsets") or []
        if args.casco:
            lista = [h for h in lista if args.casco.upper() in h["id"].upper()]
        if lista:
            break
        if not avisado:
            estado["mensaje"] = "Buscando el casco… enciéndelo (luz blanca) y revisa el Bluetooth de la computadora."
            log(estado["mensaje"])
            avisado = True

    pasos["casco"] = ", ".join(h["id"] for h in lista)
    for h in lista:
        if h.get("status") != "connected":
            pasos["casco_estado"] = "conectando"
            estado["mensaje"] = f"Conectando {h['id']}…"
            log(estado["mensaje"])
            await cx.llamar("controlDevice", command="connect", headset=h["id"])
    while True:
        lista2 = {h["id"]: h for h in (await cx.llamar("queryHeadsets") or [])}
        if all(lista2.get(h["id"], {}).get("status") == "connected" for h in lista):
            break
        await asyncio.sleep(2)
    pasos["casco_estado"] = "conectado"

    streams = STREAMS_BASE + (["eeg"] if args.crudo else [])
    for h in lista:
        hid = h["id"]
        try:
            ses, token = await abrir_sesion(cx, token, hid, client_id, secret, args.debito)
        except CortexError as e:
            estado["mensaje"] = f"No pude abrir sesión con {hid}: {e}"
            log(estado["mensaje"])
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
        pasos["flujos"] = list(casco.cols)
        log(f"Casco {n} ({hid}), flujos: {', '.join(casco.cols)}")
        for fail in res.get("failure", []):
            log(f"  sin flujo {fail.get('streamName')}: {fail.get('message')}")

    if not cascos:
        estado["mensaje"] = "No quedó ningún casco suscrito."
        log(estado["mensaje"])
        await asyncio.sleep(2)
        sys.exit(1)
    estado["status"] = 2
    estado["mensaje"] = ""
    log(f"En vivo. Enviando OSC a {', '.join(args.destino)} a {args.hz} Hz. Ctrl+C para salir.")
    await asyncio.Event().wait()


# ---------------------------------------------------------------------------
# Monitor de contacto (página local con asistente y modo avanzado)
# ---------------------------------------------------------------------------
class Monitor:
    def __init__(self, cascos, estado, puerto, modo, out=None):
        self.out = out
        self._env = (0, time.monotonic(), 0.0)
        self.cascos = cascos
        self.estado = estado
        self.puerto = puerto
        self.modo = modo
        self.clientes = set()

    async def iniciar(self, abrir=True):
        import http.server
        import threading
        import webbrowser
        import websockets
        html = carpeta_recursos() / "monitor.html"
        monitor = self
        puerto_ws = self.puerto + 1

        class Pagina(http.server.BaseHTTPRequestHandler):
            def _json(self_, d, codigo=200):
                cuerpo = json.dumps(d, ensure_ascii=False).encode("utf-8")
                self_.send_response(codigo)
                self_.send_header("Content-Type", "application/json; charset=utf-8")
                self_.send_header("Cache-Control", "no-store")
                self_.send_header("Content-Length", str(len(cuerpo)))
                self_.end_headers()
                self_.wfile.write(cuerpo)

            def do_GET(self_):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self_.path)
                q = parse_qs(u.query)
                if u.path == "/historia":
                    ints = monitor.estado.get("interpretes") or {}
                    if not ints:
                        return self_._json({"filas": [], "total": 0, "marcas": [], "frases": [], "vacia": True})
                    n = int((q.get("casco") or [sorted(ints)[0]])[0])
                    it = ints.get(n) or ints[sorted(ints)[0]]
                    try:
                        desde = int((q.get("desde") or ["0"])[0])
                    except ValueError:
                        desde = 0
                    return self_._json(it.historia_desde(desde))
                if u.path == "/sesiones":
                    return self_._json({"carpeta": str(carpeta_sesiones()), "sesiones": lista_sesiones()})
                if u.path in ("/sesiones/archivo", "/sesiones/csv", "/sesiones/txt"):
                    ext = {"/sesiones/archivo": "json", "/sesiones/csv": "csv", "/sesiones/txt": "txt"}[u.path]
                    f = archivo_sesion((q.get("id") or [""])[0], ext)
                    if f is None:
                        self_.send_error(404)
                        return
                    cuerpo = f.read_bytes()
                    tipo = {"json": "application/json", "csv": "text/csv", "txt": "text/plain"}[ext]
                    self_.send_response(200)
                    self_.send_header("Content-Type", f"{tipo}; charset=utf-8")
                    self_.send_header("Cache-Control", "no-store")
                    if ext != "json":
                        self_.send_header("Content-Disposition", f'attachment; filename="{f.name}"')
                    self_.send_header("Content-Length", str(len(cuerpo)))
                    self_.end_headers()
                    self_.wfile.write(cuerpo)
                    return
                if u.path == "/ajustes":
                    ints = monitor.estado.get("interpretes") or {}
                    cfg = next(iter(ints.values())).cfg if ints else (leer_config().get("interpretacion") or {})
                    return self_._json({"ui": ajustes_ui(), "interpretacion": cfg,
                                        "osc": {"destinos": monitor.out.destinos if monitor.out else [],
                                                "hz": monitor.estado.get("hz", 30)},
                                        "carpeta_sesiones": str(carpeta_sesiones()),
                                        "config": str(archivo_config()), "frases": str(archivo_frases())})
                if u.path not in ("/", "/index.html"):
                    self_.send_error(404)
                    return
                cuerpo = html.read_bytes().replace(b"__WS_PORT__", str(puerto_ws).encode())
                self_.send_response(200)
                self_.send_header("Content-Type", "text/html; charset=utf-8")
                self_.send_header("Content-Length", str(len(cuerpo)))
                self_.end_headers()
                self_.wfile.write(cuerpo)

            def do_POST(self_):
                # El encabezado propio obliga al navegador a pedir permiso (CORS) a otras páginas,
                # así que solo el monitor puede escribir aquí.
                if self_.path not in ("/credenciales", "/osc", "/sesion", "/ajustes") or self_.headers.get("X-Alquimia") != "1":
                    self_.send_error(403)
                    return
                try:
                    n = int(self_.headers.get("Content-Length", "0"))
                    d = json.loads(self_.rfile.read(min(n, 4096)) or b"{}")
                    if self_.path == "/ajustes":
                        ui = d.get("ui")
                        if isinstance(ui, dict):
                            actual = leer_config().get("ui") or {}
                            for k in AJUSTES_UI:
                                if k in ui:
                                    actual[k] = ui[k]
                            guardar_config(ui=actual)
                        it_cfg = d.get("interpretacion")
                        if isinstance(it_cfg, dict):
                            limpio = {}
                            for k, (lo, hi) in {"calibracion": (5, 600), "contraste": (0.1, 5), "suavizado": (0.2, 60),
                                                "permanencia": (1, 120), "margen": (0.01, 0.4), "cada": (5, 600)}.items():
                                if k in it_cfg:
                                    v = float(it_cfg[k])
                                    if not (lo <= v <= hi):
                                        raise ValueError
                                    limpio[k] = v
                            guardar_config(interpretacion={**(leer_config().get("interpretacion") or {}), **limpio})
                            for it in (monitor.estado.get("interpretes") or {}).values():
                                it.ajustar(**limpio)
                    elif self_.path == "/sesion":
                        ok_ = sesion_accion(monitor.estado, str(d.get("accion", "")), d)
                        if not ok_:
                            raise ValueError
                    elif self_.path == "/credenciales":
                        cid, sec = str(d.get("client_id", "")).strip(), str(d.get("client_secret", "")).strip()
                        if len(cid) < 8 or len(sec) < 8:
                            raise ValueError
                        guardar_credenciales(cid, sec)
                    else:
                        lista = [x for x in d.get("destinos", []) if destino_valido(x)][:8]
                        hz = int(d.get("hz", monitor.estado.get("hz", 30)))
                        if not lista or not (1 <= hz <= 120):
                            raise ValueError
                        monitor.out.configurar(lista)
                        monitor.estado["hz"] = hz
                        guardar_config(osc={"destinos": monitor.out.destinos, "hz": hz})
                        log("Salida OSC:", ", ".join(monitor.out.destinos), f"a {hz} Hz")
                    ok, cuerpo = 200, b'{"ok":true}'
                except (ValueError, TypeError, OSError):
                    ok, cuerpo = 400, b'{"ok":false}'
                self_.send_response(ok)
                self_.send_header("Content-Type", "application/json")
                self_.send_header("Content-Length", str(len(cuerpo)))
                self_.end_headers()
                self_.wfile.write(cuerpo)

            def log_message(self_, *a):
                pass

        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.puerto), Pagina)
            await websockets.serve(self._cliente, "127.0.0.1", puerto_ws)
        except OSError:
            sys.exit(f"Los puertos {self.puerto} y {puerto_ws} están ocupados. ¿Hay otro puente abierto? "
                     f"Ciérralo o usa --monitor-puerto con otro número.")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        asyncio.create_task(self._emitir())
        url = f"http://localhost:{self.puerto}"
        log("Monitor de contacto en", url)
        if abrir:
            webbrowser.open(url)

    async def _cliente(self, ws):
        self.clientes.add(ws)
        try:
            await ws.wait_closed()
        finally:
            self.clientes.discard(ws)

    def instantanea(self):
        ahora = time.monotonic()
        osc = None
        if self.out is not None:
            n0, t0, r = self._env
            if ahora - t0 >= 1.0:
                r = (self.out.enviados - n0) / (ahora - t0)
                self._env = (self.out.enviados, ahora, r)
            osc = {"destinos": self.out.destinos, "hz": self.estado.get("hz", 30), "por_segundo": round(r)}
        historia = None
        ints = self.estado.get("interpretes") or {}
        if ints:
            n = sorted(ints)[0]
            historia = ints[n].resumen_vivo(ahora)
            historia["n"] = n
            historia["ultima_exportacion"] = self.estado.get("ultima_exportacion", "")
        return {
            "historia": historia,
            "osc": osc,
            "modo": self.modo, "status": self.estado["status"],
            "mensaje": self.estado.get("mensaje", ""), "pasos": self.estado["pasos"],
            "cascos": [c.resumen(ahora) for c in sorted(self.cascos.values(), key=lambda c: c.n)],
        }

    async def _emitir(self):
        while True:
            if self.clientes:
                msg = json.dumps(self.instantanea())
                for ws in list(self.clientes):
                    try:
                        await ws.send(msg)
                    except Exception:
                        self.clientes.discard(ws)
            await asyncio.sleep(0.1)


def sesion_accion(estado, accion, d=None):
    """Acciones de sesión desde el monitor o por OSC."""
    d = d or {}
    ints = estado.get("interpretes") or {}
    if accion == "marcar":
        texto = str(d.get("texto") or "").strip()[:120]
        for it in ints.values():
            it.sesion.marcar(texto or f"marca {len(it.sesion.marcas) + 1}")
        out = estado.get("out")
        if out is not None:   # se reenvía para que los visores la dibujen
            out.send("/eeg/marca", [texto or "marca"])
        log("Marca:", texto or "(sin texto)")
    elif accion == "terminar":
        exportar_sesiones(estado)
        for it in ints.values():
            it.sesion.detener()
        log("Sesión terminada. Pulsa «iniciar» para empezar otra.")
    elif accion in ("iniciar", "reiniciar"):
        nombre = str(d.get("nombre") or "").strip()[:80] or f"sesión {datetime.now():%H:%M}"
        estado["sesion_nombre"] = nombre
        for it in ints.values():
            if accion == "iniciar" and it.sesion.grabando and it.sesion.filas:
                base = it.sesion.exportar(it.narrador, etiqueta=f"casco{it_n(estado, it)}")
                if base:
                    log("Sesión anterior guardada:", base.with_suffix(".txt"))
            it.sesion.reiniciar(nombre)
        log(f"Sesión iniciada: {nombre}")
    elif accion == "nombre":
        nombre = str(d.get("nombre") or "").strip()[:80]
        if not nombre:
            return False
        estado["sesion_nombre"] = nombre
        for it in ints.values():
            it.sesion.nombre = nombre
    elif accion == "recalibrar":
        for it in ints.values():
            it.recalibrar()
        log("Recalibrando línea base.")
    elif accion == "ajustar":
        for it in ints.values():
            it.ajustar(contraste=d.get("contraste"), suavizado=d.get("suavizado"),
                       permanencia=d.get("permanencia"), margen=d.get("margen"), cada=d.get("cada"))
    elif accion == "recargar_frases":
        narr = estado.get("narrador")
        if narr:
            narr.cargar()
            log("Frases recargadas de", narr.archivo)
    else:
        return False
    return True


def it_n(estado, it):
    for n, x in (estado.get("interpretes") or {}).items():
        if x is it:
            return n
    return 0


def exportar_sesiones(estado, reiniciar=False):
    hechos = []
    for n, it in (estado.get("interpretes") or {}).items():
        if not it.sesion.grabando:
            continue   # ya se guardó al terminarla
        base = it.sesion.exportar(it.narrador, etiqueta=f"casco{n}")
        if base:
            hechos.append(str(base))
            log("Sesión guardada:", base.with_suffix(".txt"), "(.json, .csv)")
        if reiniciar:
            it.sesion.reiniciar()
    if hechos:
        estado["ultima_exportacion"] = hechos[-1]
    return hechos


async def entrada_osc(estado, puerto):
    """Escucha marcas y órdenes de sesión (Isadora, QLab, TouchDesigner…)."""
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import AsyncIOOSCUDPServer
    disp = Dispatcher()

    def orden(accion, con_texto=False):
        def h(_addr, *x):
            # sin valor de retorno: python-osc lo mandaría de vuelta como respuesta
            texto = " ".join(str(v) for v in x)
            sesion_accion(estado, accion, {"texto": texto, "nombre": texto} if con_texto else {})
        return h

    disp.map("/eeg/marca", orden("marcar", True))
    for accion in ("terminar", "reiniciar", "recalibrar"):
        disp.map(f"/eeg/sesion/{accion}", orden(accion))
    disp.map("/eeg/sesion/iniciar", orden("iniciar", True))
    disp.map("/eeg/frases/recargar", orden("recargar_frases"))
    try:
        srv = AsyncIOOSCUDPServer(("0.0.0.0", puerto), disp, asyncio.get_running_loop())
        await srv.create_serve_endpoint()
        log(f"Escuchando marcas OSC en el puerto {puerto} (/eeg/marca, /eeg/sesion/terminar…)")
    except OSError as e:
        log(f"No pude abrir el puerto de entrada OSC {puerto}: {e}")


# ---------------------------------------------------------------------------
# Modo simulación (sin casco) y reproducción
# ---------------------------------------------------------------------------
async def modo_simular(args, out, cascos, estado):
    import random
    n = 2
    c = Casco(n, "INSIGHT-SIMULADO", fuente=0, suavizado=args.suavizado)
    canales = ["AF3", "T7", "Pz", "T8", "AF4"]
    c.cols = {
        "pow": [f"{ch}/{b}" for ch in canales for b in BANDS],
        "met": ["attention", "eng", "exc", "str", "rel", "int", "foc"],
        "fac": ["eyeAct", "uAct", "uPow", "lAct", "lPow"],
        "dev": ["Battery", "Signal", canales + ["OVERALL"], "BatteryPercent"],
        "mot": ["Q0", "Q1", "Q2", "Q3"],
    }
    pasos = estado["pasos"]
    pasos.update(credenciales=True, cortex=True, autorizado=True, casco=c.id, casco_estado="conectado",
                 flujos=list(c.cols))
    cascos[n] = c
    estado["status"] = 2
    log("Simulando el casco 2 (Insight). Enviando OSC a", ", ".join(args.destino))
    # El contacto mejora poco a poco, electrodo por electrodo, para probar el asistente
    llegada = {ch: 4 + 5 * i + random.uniform(0, 2) for i, ch in enumerate(canales)}
    t0 = time.monotonic()
    k = 0
    while True:
        t = time.monotonic() - t0
        ahora = time.monotonic()
        lento = 0.5 + 0.5 * math.sin(t / 8.0)
        # cada banda pesa distinto con el tiempo: así cambian las proporciones y aparecen «estados»
        peso = [1 + 0.8 * math.sin(t / 9.0 + fase) for fase in (0.0, 1.7, 3.1, 4.4, 5.6)]
        c.recibir("pow", [(2 + 6 * lento) * peso[i % 5] * (1 + 0.15 * math.sin(t * 3 + i)) + random.random() * 0.3
                          for i in range(25)], ahora)
        if k % 4 == 0:
            niveles = [int(clip((t - llegada[ch]) * 0.6 + 1, 0, 4)) if t > llegada[ch] - 3 else 0
                       for ch in canales]
            total = int(100 * sum(niveles) / (4 * len(niveles)))
            c.recibir("dev", [4, 0.9, niveles + [total], 80], ahora)
        if k % 8 == 0:
            c.recibir("met", [lento, 0.6, 0.4, 1 - lento, lento, 0.5, 0.7], ahora)
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
    cfg_osc = leer_config().get("osc") or {}
    if not args.destino_cli and cfg_osc.get("destinos"):
        args.destino = cfg_osc["destinos"]
    if not args.hz_cli and cfg_osc.get("hz"):
        args.hz = int(cfg_osc["hz"])
    out = Salida(args.destino, grabar)
    args.destino = out.destinos
    try:
        if args.reproducir:
            await modo_reproducir(args, out)
            return
        cascos = {}
        estado = {"status": 0, "mensaje": "", "args": args, "out": out,
                  "pasos": {"credenciales": False, "cortex": False, "esperando_aprobacion": False, "autorizado": False,
                            "casco": "", "casco_estado": "", "flujos": []}}
        if args.monitor:
            estado["hz"] = args.hz
            mon = Monitor(cascos, estado, args.monitor_puerto, "simulacion" if args.simular else "cortex", out)
            await mon.iniciar(abrir=not args.no_abrir)
        tarea = asyncio.create_task(bucle_salida(cascos, out, args.hz, estado))
        if args.osc_entrada:
            await entrada_osc(estado, args.osc_entrada)
        try:
            if args.simular:
                await modo_simular(args, out, cascos, estado)
            else:
                await modo_cortex(args, out, cascos, estado)
        finally:
            tarea.cancel()
            exportar_sesiones(estado)
    finally:
        out.cerrar()


def main():
    if load_dotenv:
        load_dotenv(carpeta_programa() / ".env")
    ap = argparse.ArgumentParser(description="Puente EEG (Emotiv Cortex) → OSC")
    ap.add_argument("--destino", action="append",
                    help="host:puerto de destino OSC (se puede repetir). Por defecto 127.0.0.1:9200")
    ap.add_argument("--hz", type=int, default=None, help="tasa de salida OSC (30 por defecto)")
    ap.add_argument("--suavizado", type=float, default=0.3, help="0–1; más bajo = más suave")
    ap.add_argument("--casco", help="usar solo el casco cuyo id contenga este texto (p. ej. INSIGHT)")
    ap.add_argument("--crudo", action="store_true", help="enviar también el EEG crudo en /eeg/{casco}/raw")
    ap.add_argument("--grabar", action="store_true", help="grabar la sesión en grabaciones/*.jsonl")
    ap.add_argument("--monitor", action="store_true",
                    help="abrir el monitor de contacto (asistente y modo avanzado) en el navegador")
    ap.add_argument("--monitor-puerto", type=int, default=9201, help="puerto del monitor (9201; usa también el siguiente)")
    ap.add_argument("--no-abrir", action="store_true", help="con --monitor: no abrir el navegador solo")
    ap.add_argument("--simular", action="store_true", help="datos sintéticos, sin casco")
    ap.add_argument("--reproducir", metavar="ARCHIVO", help="reproducir una grabación .jsonl")
    ap.add_argument("--bucle", action="store_true", help="con --reproducir: repetir sin fin")
    ap.add_argument("--calibracion", type=float, default=60, help="segundos de línea base en reposo (60)")
    ap.add_argument("--contraste", type=float, default=1.0, help="exagera (>1) o suaviza (<1) los índices")
    ap.add_argument("--permanencia", type=float, default=8, help="segundos que un estado debe sostenerse para cambiar (8)")
    ap.add_argument("--osc-entrada", type=int, default=9210, help="puerto para recibir marcas de escena (9210; 0 = no)")
    ap.add_argument("--sin-interpretacion", action="store_true", help="no calcular índices, estados ni frases")
    ap.add_argument("--debito", type=int, default=10,
                    help="sesiones que se piden a Cortex al autorizar (debit). 10 por defecto")
    ap.add_argument("--url", default=CORTEX_URL, help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.destino_cli = bool(args.destino)
    args.destino = args.destino or ["127.0.0.1:9200"]
    args.hz_cli = args.hz is not None
    for k in ("calibracion", "contraste", "permanencia"):
        setattr(args, k + "_cli", any(a.startswith("--" + k) for a in sys.argv))
    args.hz = args.hz or 30
    if CONGELADO and len(sys.argv) == 1:
        args.monitor = True  # doble clic en el .exe: abre el monitor
    if CONGELADO:
        os.chdir(carpeta_programa())  # las grabaciones quedan junto al .exe
    if not args.reproducir:
        log("Alquimia EEG OSC v0.7 · Ctrl+C o cerrar esta ventana para salir.")
    try:
        asyncio.run(principal(args))
    except KeyboardInterrupt:
        log("Detenido.")
    except SystemExit as e:
        if e.code not in (None, 0):
            log(e.code if isinstance(e.code, str) else "Error.")
            if CONGELADO:
                input("Pulsa Enter para cerrar…")
        raise SystemExit(1 if e.code else 0)


if __name__ == "__main__":
    main()
