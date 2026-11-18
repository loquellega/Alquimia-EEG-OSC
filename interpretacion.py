"""
Alquimia EEG OSC · capa de interpretación, narración y sesión.

De las bandas a unos pocos índices con sentido escénico, calibrados contra la línea base
de cada persona; de los índices a un estado que solo cambia si se sostiene; del estado a
frases de una librería editable; y de todo eso a una sesión con marcas, fragmentos y relato.

Son interpretaciones, no lectura de la mente: relaciones conocidas entre bandas comparadas
con la misma persona en reposo. Úsalas como material escénico.

Diseño, investigación y desarrollo: Héctor Cruz Juárez — https://hectorcruz.com.mx
Colección Alchemy of Meaning. · GPL-3.0-or-later
"""
import csv
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path

INDICES = ["calma", "imaginacion", "atencion", "activacion", "valencia", "movimiento"]
ESTADOS = ["neutral", "calma", "imaginacion", "atencion", "activacion", "movimiento"]
ID = {e: i for i, e in enumerate(ESTADOS)}
NOMBRE = {"neutral": "neutral", "calma": "calma", "imaginacion": "imaginación",
          "atencion": "atención", "activacion": "activación", "movimiento": "movimiento"}


def _clip(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


def _sig(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def mmss(seg):
    seg = max(0, int(round(seg)))
    return f"{seg // 60}:{seg % 60:02d}"


# ---------------------------------------------------------------------------
# Línea base
# ---------------------------------------------------------------------------
class Base:
    """Media y desviación de un rasgo: primero la calibración; luego se adapta muy despacio."""

    def __init__(self, tau=300.0):
        self.muestras = []
        self.m = None
        self.v = None
        self.tau = tau

    def agregar(self, x):
        self.muestras.append(x)

    def cerrar(self):
        if len(self.muestras) >= 5:
            n = len(self.muestras)
            m = sum(self.muestras) / n
            v = sum((x - m) ** 2 for x in self.muestras) / max(1, n - 1)
            self.m, self.v = m, max(v, 1e-6)
        self.muestras = []

    def z(self, x, dt):
        if self.m is None:      # sin calibración: arranca con el primer valor
            self.m, self.v = x, 0.05
        z = (x - self.m) / math.sqrt(self.v)
        a = 1.0 - math.exp(-dt / self.tau)   # deriva lenta: sigue al cuerpo a lo largo de la función
        self.m += a * (x - self.m)
        self.v += a * ((x - self.m) ** 2 - self.v)
        self.v = max(self.v, 1e-6)
        return z


# ---------------------------------------------------------------------------
# Frases
# ---------------------------------------------------------------------------
class Narrador:
    """Elige frases de frases.json sin repetirlas hasta agotar cada grupo."""

    def __init__(self, archivo, semilla=None):
        self.archivo = Path(archivo)
        self.rng = random.Random(semilla)
        self.bolsas = {}
        self.datos = {}
        self.cargar()

    def cargar(self):
        try:
            self.datos = json.loads(self.archivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.datos = {}
        self.bolsas = {}

    def _de(self, clave, lista):
        lista = [f for f in (lista or []) if isinstance(f, str) and f.strip()]
        if not lista:
            return None
        bolsa = self.bolsas.get(clave)
        if not bolsa:
            bolsa = lista[:]
            self.rng.shuffle(bolsa)
            self.bolsas[clave] = bolsa
        return bolsa.pop()

    def frase(self, estado, intensidad, anterior=None, tipo="cambio"):
        nivel = "intenso" if intensidad >= 0.78 else "medio" if intensidad >= 0.62 else "suave"
        est = self.datos.get("estados", {}).get(estado, {})
        tr = self.datos.get("transiciones", {})
        if tipo == "cambio" and anterior:
            for k in (f"{anterior}>{estado}", f"*>{estado}", f"{anterior}>*"):
                f = self._de("t:" + k, tr.get(k))
                if f and self.rng.random() < 0.6:
                    return f
        if tipo == "sigue":
            f = self._de("s:" + estado, self.datos.get("sigue", {}).get(estado))
            if f:
                return f
        return self._de(f"e:{estado}:{nivel}", est.get(nivel)) or self._de(f"e:{estado}", sum(
            (est.get(n, []) for n in ("suave", "medio", "intenso")), []))

    def nombre(self, estado):
        return self.datos.get("estados", {}).get(estado, {}).get("nombre", NOMBRE.get(estado, estado))


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------
class Sesion:
    def __init__(self, carpeta="sesiones"):
        self.carpeta = Path(carpeta)
        self.reiniciar()

    def reiniciar(self):
        self.inicio_reloj = datetime.now()
        self.t0 = time.monotonic()
        self.filas = []          # una por segundo
        self.marcas = []         # (t, texto)
        self.frases = []         # (t, estado, frase)
        self.parpadeos = 0
        self.ultima = -1.0

    def t(self):
        return time.monotonic() - self.t0

    def marcar(self, texto):
        self.marcas.append((round(self.t(), 2), str(texto)))

    def registrar(self, idx, estado, intensidad, calibrando, valido):
        t = self.t()
        if t - self.ultima < 1.0:
            return
        self.ultima = t
        fila = {"t": round(t, 1), "estado": estado, "intensidad": round(intensidad, 3),
                "calibrando": int(calibrando), "valido": int(valido)}
        fila.update({k: round(idx.get(k, 0.5), 3) for k in INDICES})
        self.filas.append(fila)

    def fragmentos(self, minimo=6.0):
        """Tramos continuos del mismo estado (sin calibración), de al menos `minimo` segundos."""
        out, cur = [], None
        for f in self.filas:
            if f["calibrando"]:
                continue
            if cur and f["estado"] == cur["estado"]:
                cur["fin"] = f["t"]
                cur["suma"] += f["intensidad"]
                cur["n"] += 1
                cur["pico"] = max(cur["pico"], f["intensidad"])
            else:
                if cur:
                    out.append(cur)
                cur = {"estado": f["estado"], "inicio": f["t"], "fin": f["t"], "suma": f["intensidad"],
                       "n": 1, "pico": f["intensidad"]}
        if cur:
            out.append(cur)
        res = []
        for c in out:
            if c["fin"] - c["inicio"] + 1 >= minimo:
                frs = [fr for (t, e, fr) in self.frases if e == c["estado"] and c["inicio"] - 3 <= t <= c["fin"] + 1]
                res.append({"estado": c["estado"], "inicio": c["inicio"], "fin": c["fin"] + 1,
                            "intensidad_media": round(c["suma"] / c["n"], 3), "pico": round(c["pico"], 3),
                            "frase": frs[0] if frs else ""})
        return res

    def resumen(self):
        utiles = [f for f in self.filas if not f["calibrando"]]
        dur = utiles[-1]["t"] - utiles[0]["t"] + 1 if utiles else 0
        por = {}
        for f in utiles:
            por[f["estado"]] = por.get(f["estado"], 0) + 1
        prom = {k: round(sum(f[k] for f in utiles) / len(utiles), 3) for k in INDICES} if utiles else {}
        picos = {}
        for k in ("calma", "imaginacion", "atencion", "activacion"):
            if utiles:
                m = max(utiles, key=lambda f: f[k])
                picos[k] = {"t": m["t"], "valor": m[k]}
        return {
            "inicio": self.inicio_reloj.isoformat(timespec="seconds"),
            "duracion_s": round(dur, 1),
            "porcentaje": {e: round(100 * n / len(utiles), 1) for e, n in sorted(por.items(), key=lambda x: -x[1])} if utiles else {},
            "promedios": prom,
            "picos": picos,
            "parpadeos": self.parpadeos,
            "marcas": [{"t": t, "texto": x} for t, x in self.marcas],
            "fragmentos": self.fragmentos(),
        }

    def relato(self, narrador=None):
        r = self.resumen()
        nombre = (lambda e: narrador.nombre(e)) if narrador else (lambda e: NOMBRE.get(e, e))
        lineas = [f"Sesión del {self.inicio_reloj:%d/%m/%Y %H:%M} · {mmss(r['duracion_s'])} analizados "
                  f"(de {mmss(self.filas[-1]['t'] if self.filas else 0)} en total)", ""]
        marcas = list(r["marcas"])
        for fr in r["fragmentos"]:
            while marcas and marcas[0]["t"] <= fr["inicio"]:
                m = marcas.pop(0)
                lineas.append(f"  ▸ {mmss(m['t'])}  marca: {m['texto']}")
            linea = f"{mmss(fr['inicio'])}–{mmss(fr['fin'])}  {nombre(fr['estado'])} ({int(fr['intensidad_media'] * 100)}%)"
            if fr["frase"]:
                linea += f"  · {fr['frase']}"
            lineas.append(linea)
        for m in marcas:
            lineas.append(f"  ▸ {mmss(m['t'])}  marca: {m['texto']}")
        if r["porcentaje"]:
            lineas += ["", "Tiempo en cada estado:"]
            lineas += [f"  {nombre(e)}: {p}%" for e, p in r["porcentaje"].items()]
        return "\n".join(lineas)

    def exportar(self, narrador=None, etiqueta="sesion"):
        if not self.filas:
            return None
        self.carpeta.mkdir(parents=True, exist_ok=True)
        base = self.carpeta / f"{etiqueta}_{self.inicio_reloj:%Y%m%d_%H%M%S}"
        datos = {"resumen": self.resumen(), "frases": [{"t": t, "estado": e, "frase": f} for t, e, f in self.frases],
                 "linea_de_tiempo": self.filas}
        base.with_suffix(".json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.filas[0].keys()) + ["marca"])
            w.writeheader()
            marcas = dict()
            for t, x in self.marcas:
                marcas.setdefault(int(t), []).append(x)
            for f in self.filas:
                w.writerow({**f, "marca": " | ".join(marcas.get(int(f["t"]), []))})
        base.with_suffix(".txt").write_text(self.relato(narrador), encoding="utf-8")
        return base


# ---------------------------------------------------------------------------
# Intérprete
# ---------------------------------------------------------------------------
class Interprete:
    """Una instancia por casco. actualizar() se llama unas 4 veces por segundo."""

    def __init__(self, narrador, calibracion=60.0, contraste=1.0, suavizado=4.0, permanencia=8.0,
                 margen=0.08, cada=25.0, usar_metricas=True):
        self.narrador = narrador
        self.cfg = dict(calibracion=calibracion, contraste=contraste, suavizado=suavizado,
                        permanencia=permanencia, margen=margen, cada=cada, usar_metricas=usar_metricas)
        self.base = {k: Base() for k in INDICES if k != "movimiento"}
        self.sesion = Sesion()
        self.recalibrar()

    # --- control ---
    def recalibrar(self):
        self.t_cal = None          # empieza a contar cuando hay señal válida
        self.calibrando = True
        self.idx = {k: 0.5 for k in INDICES}
        self.idx["movimiento"] = 0.0
        self.estado = "neutral"
        self.intensidad = 0.5
        self.candidato = None
        self.cand_desde = 0.0
        self.desde = time.monotonic()
        self.t_frase = 0.0
        self.frase = ""
        self.eventos = []
        self.ult = None
        self.gyro_prev = None
        for b in self.base.values():
            b.muestras, b.m, b.v = [], None, None

    def ajustar(self, **kw):
        for k, v in kw.items():
            if k in self.cfg and v is not None:
                self.cfg[k] = type(self.cfg[k])(v)

    # --- rasgos crudos desde el casco ---
    @staticmethod
    def _crudos(casco):
        bandas = casco.band
        if not all(b in bandas and bandas[b] for b in ("theta", "alpha", "betaL", "betaH", "gamma")):
            return None
        n = len(bandas["alpha"])
        buenos = list(range(n))
        if casco.cq and len(casco.cq) == n:   # deja fuera los electrodos con mal contacto
            b = [i for i, q in enumerate(casco.cq) if q >= 3]
            buenos = b or [i for i, q in enumerate(casco.cq) if q >= 2] or buenos
        P = {k: max(1e-6, sum(bandas[k][i] for i in buenos) / len(buenos)) for k in bandas}
        tot = sum(P[k] for k in ("theta", "alpha", "betaL", "betaH", "gamma"))
        crudo = {
            "calma": math.log(P["alpha"] / tot),
            "imaginacion": math.log(P["theta"] / (P["betaL"] + P["betaH"])),
            "atencion": math.log((P["betaL"] + P["betaH"]) / (P["alpha"] + P["theta"])),   # índice de compromiso β/(α+θ)
            "activacion": math.log((P["betaH"] + P["gamma"]) / (P["theta"] + P["alpha"] + P["betaL"])),
        }
        canales = casco.canales or []
        if "AF3" in canales and "AF4" in canales:   # asimetría frontal alfa: ln(der) − ln(izq)
            i3, i4 = canales.index("AF3"), canales.index("AF4")
            if i3 in buenos and i4 in buenos:
                crudo["valencia"] = math.log(max(1e-6, bandas["alpha"][i4])) - math.log(max(1e-6, bandas["alpha"][i3]))
        return crudo

    def actualizar(self, casco, ahora=None):
        ahora = time.monotonic() if ahora is None else ahora
        dt = 0.25 if self.ult is None else _clip(ahora - self.ult, 0.01, 2.0)
        self.ult = ahora
        valido = casco.valido(ahora)
        c = self.cfg

        # movimiento de cabeza (no necesita línea base)
        if casco.gyro is not None:
            g = casco.gyro
            if self.gyro_prev is not None:
                vel = math.hypot(g[0] - self.gyro_prev[0], g[1] - self.gyro_prev[1]) / dt
                obj = _clip(vel / 0.6)
                self.idx["movimiento"] += (obj - self.idx["movimiento"]) * (1 - math.exp(-dt / 0.6))
            self.gyro_prev = g

        crudo = self._crudos(casco) if valido else None
        if crudo is not None:
            if self.t_cal is None:
                self.t_cal = ahora
                self._evento("/sesion/calibrando", [1])
            if self.calibrando:
                if self.idx["movimiento"] < 0.5:   # no calibrar mientras la cabeza se mueve
                    for k, v in crudo.items():
                        self.base[k].agregar(v)
                if ahora - self.t_cal >= c["calibracion"]:
                    for b in self.base.values():
                        b.cerrar()
                    self.calibrando = False
                    self._evento("/sesion/calibrando", [0])
            else:
                a = 1.0 - math.exp(-dt / max(0.1, c["suavizado"]))
                for k, v in crudo.items():
                    obj = _sig(c["contraste"] * self.base[k].z(v, dt))
                    self.idx[k] += (obj - self.idx[k]) * a
                if c["usar_metricas"]:   # mezcla con las métricas de Emotiv cuando existen
                    m = getattr(casco, "metricas", {}) or {}
                    for k, mk in (("calma", "relaxation"), ("atencion", "attention"), ("activacion", "excitement")):
                        if mk in m:
                            self.idx[k] += (0.4 * m[mk] + 0.6 * self.idx[k] - self.idx[k]) * a
                self._decidir(ahora)

        if casco.t_parpadeo and ahora - casco.t_parpadeo < dt:
            self.sesion.parpadeos += 1
        self.sesion.registrar(self.idx, self.estado, self.intensidad, self.calibrando, valido)

    def _decidir(self, ahora):
        c = self.cfg
        if self.idx["movimiento"] > 0.6:
            cand, inten = "movimiento", self.idx["movimiento"]
        else:
            lideres = {k: self.idx[k] - 0.5 for k in ("calma", "imaginacion", "atencion", "activacion")}
            k, ventaja = max(lideres.items(), key=lambda kv: kv[1])
            cand, inten = (k, self.idx[k]) if ventaja >= c["margen"] else ("neutral", 0.5)
        if cand == self.estado:
            self.candidato = None
            self.intensidad += (inten - self.intensidad) * 0.3
            if self.t_frase and ahora - self.t_frase >= c["cada"] and self.estado != "neutral":
                self._decir(self.narrador.frase(self.estado, self.intensidad, tipo="sigue"), ahora)
            return
        if cand != self.candidato:
            self.candidato, self.cand_desde = cand, ahora
            return
        espera = c["permanencia"] * (0.5 if cand == "movimiento" else 1.0)
        if ahora - self.cand_desde >= espera:
            anterior = self.estado
            self.estado, self.intensidad, self.desde = cand, inten, ahora
            self.candidato = None
            self._evento("/estado/cambio", [ID[cand]])
            self._decir(self.narrador.frase(cand, inten, anterior=anterior), ahora)

    def _decir(self, frase, ahora):
        if not frase:
            return
        self.frase, self.t_frase = frase, ahora
        self.sesion.frases.append((round(self.sesion.t(), 1), self.estado, frase))
        self._evento("/frase", [frase])

    def _evento(self, sufijo, args):
        self.eventos.append((sufijo, args))

    # --- salida OSC ---
    def emitir(self, out, n, ahora=None):
        ahora = time.monotonic() if ahora is None else ahora
        p = f"/eeg/{n}"
        for suf, args in self.eventos:
            out.send(p + suf, args)
        self.eventos.clear()
        for k in INDICES:
            out.send(f"{p}/indice/{k}", [float(self.idx[k])])
        out.send(f"{p}/estado", [ID[self.estado], float(self.intensidad)])
        out.send(f"{p}/estado/tiempo", [float(ahora - self.desde)])
        cal = 0.0
        if self.calibrando:
            cal = 0.0 if self.t_cal is None else _clip((ahora - self.t_cal) / max(1.0, self.cfg["calibracion"]))
        out.send(f"{p}/sesion/calibracion", [float(1.0 if not self.calibrando else cal)])
        out.send(f"{p}/sesion/tiempo", [float(self.sesion.t())])

    def resumen_vivo(self, ahora=None):
        ahora = time.monotonic() if ahora is None else ahora
        cal = None
        if self.calibrando:
            cal = 0.0 if self.t_cal is None else _clip((ahora - self.t_cal) / max(1.0, self.cfg["calibracion"]))
        return {"estado": self.estado, "nombre": self.narrador.nombre(self.estado), "id": ID[self.estado],
                "intensidad": round(self.intensidad, 3), "desde": round(ahora - self.desde, 1),
                "frase": self.frase, "calibracion": cal,
                "indices": {k: round(v, 3) for k, v in self.idx.items()},
                "sesion_t": round(self.sesion.t(), 1), "marcas": len(self.sesion.marcas),
                "historial": [[f["t"], ID[f["estado"]]] + [f[k] for k in INDICES] for f in self.sesion.filas[-600:]],
                "cfg": self.cfg}
