# =====================================================================================
#  Alquimia EEG OSC · visor para TouchDesigner (componente eeg_visor)
#  Recibe por OSC todo lo que manda el puente (puerto 9200), lo convierte en canales CHOP
#  y dibuja cinco franjas que avanzan, una por banda, con rótulos.
#  Diseño, investigación y desarrollo: Héctor Cruz Juárez — https://hectorcruz.com.mx
#  Colección Alchemy of Meaning. · SPDX-License-Identifier: GPL-3.0-or-later
#
#  USO
#   1. En la red donde quieras el componente, crea un Text DAT y pega este archivo.
#   2. Clic derecho sobre el DAT → Run Script (o Ctrl+R con el DAT seleccionado).
#   3. Aparece el COMP «eeg_visor»:
#        out1 (TOP)  → la imagen del visor
#        out2 (CHOP) → todos los datos como canales (eeg_2_band_alpha_norm, eeg_2_blink…)
#   4. Abre el puente (AlquimiaEEGOSC o cortex_osc.py) y listo.
#   5. Si algo no se pudo ajustar, el DAT «eeg_informe» dentro del COMP lo lista:
#      copia su contenido y pégalo en la conversación.
#  Escrito para TouchDesigner 2025. Volver a correrlo reemplaza el componente.
# =====================================================================================

VERSION = '0.1'
NAME = 'eeg_visor'
FAILS = []

# ---------- ayudantes tolerantes a diferencias de versión ----------
def _par(o, name):
	try:
		return getattr(o.par, name)
	except Exception:
		return None

def setv(o, cands, val=None, expr=None, opt=False):
	if isinstance(cands, str):
		cands = [cands]
	for nm in cands:
		p = _par(o, nm)
		if p is None:
			continue
		try:
			if expr is not None:
				p.expr = str(expr)
			else:
				p.val = val
			return True
		except Exception as e:
			if not opt:
				FAILS.append('%s.%s = %r → %s' % (o.name, nm, expr if expr is not None else val, e))
			return False
	if not opt:
		FAILS.append('%s: no existe ninguno de %s' % (o.name, cands))
	return False

def wire(a, b, i=0):
	try:
		a.outputConnectors[0].connect(b.inputConnectors[i])
	except Exception as e:
		FAILS.append('conectar %s → %s[%d]: %s' % (a.name, b.name, i, e))

def place(o, x, y):
	o.nodeX = x * 180
	o.nodeY = -y * 140

def quitar_callbacks_auto(c, nombre):
	o = c.op(nombre + '_callbacks')
	if o is not None:
		o.destroy()

E = 'parent.eeg.par.'

# =====================================================================================
#  CÓDIGO QUE VIVE DENTRO DEL COMPONENTE
# =====================================================================================

LIB = r'''# eeg_lib · recepción OSC y dibujo del visor (Alquimia EEG OSC · GPL-3.0-or-later)
# Vive dentro del componente eeg_visor de TouchDesigner. No depende de td: se puede probar fuera.
import time
import unicodedata
import numpy as np

BANDAS = ['theta', 'alpha', 'betaL', 'betaH', 'gamma']
# colores por franja (RGB 0–255), cercanos a la referencia: verde-amarillo, cian, amarillo, blanco-azul, oliva
COLOR = {
	'theta': (196, 222, 70),
	'alpha': (70, 196, 226),
	'betaL': (232, 220, 80),
	'betaH': (190, 232, 255),
	'gamma': (204, 214, 150),
}
CIAN = np.array((24, 168, 236), np.float32)
BLANCO = np.array((255, 255, 255), np.float32)

V = {}          # dirección (sin «/» inicial) → lista de valores
T = {}          # dirección → hora de llegada
EV = {'blink': 0.0, 'wink': 0.0}
N = {'mensajes': 0}


def recibir(address, args):
	a = address.lstrip('/')
	V[a] = list(args)
	T[a] = time.time()
	N['mensajes'] += 1
	if a.endswith('/blink'):
		EV['blink'] = time.time()
	elif a.endswith('/wink'):
		EV['wink'] = time.time()


def val(a, i=0, d=0.0):
	v = V.get(a)
	try:
		return float(v[i])
	except (TypeError, IndexError, ValueError):
		return d


def edad():
	"""Segundos desde el último mensaje (None si nunca llegó nada)."""
	return (time.time() - max(T.values())) if T else None


def canales():
	"""Todos los datos como pares (nombre de canal, valor), para el Script CHOP."""
	out = []
	for a in sorted(V):
		base = a.replace('/', '_')
		vals = V[a]
		if a.endswith('/blink') or a.endswith('/wink'):
			continue
		if len(vals) == 1:
			out.append((base, float(vals[0])))
		else:
			for i, x in enumerate(vals):
				try:
					out.append(('%s%d' % (base, i + 1), float(x)))
				except (TypeError, ValueError):
					pass
	ahora = time.time()
	out.append(('blink', 1.0 if ahora - EV['blink'] < 0.15 else 0.0))
	out.append(('wink', 1.0 if ahora - EV['wink'] < 0.15 else 0.0))
	e = edad()
	out.append(('recibiendo', 1.0 if (e is not None and e < 1.0) else 0.0))
	return out


class Visor:
	"""Cinco franjas que avanzan de derecha a izquierda; cada columna es un instante."""

	TOP = 12   # alto de la línea de estado

	def __init__(self, w=360, h=540, semilla=7):
		self.w, self.h = int(w), int(h)
		self.img = np.zeros((self.h, self.w, 3), np.float32)
		self.rng = np.random.default_rng(semilla)
		self.suave = {b: 0.0 for b in BANDAS}
		self.prev = {b: None for b in BANDAS}
		self.env = {b: 0.5 for b in BANDAS}
		self.ult_parpadeo = 0.0
		self.sost = {b: (0.0, 0) for b in BANDAS}   # amplitud sostenida y columnas restantes

	def paso(self, casco=2, velocidad=2, ganancia=1.0):
		n = max(1, int(velocidad))
		self.img = np.roll(self.img, -n, axis=1)
		self.img[:, -n:] = 0
		pre = 'eeg/%d' % int(casco)
		valido = val(pre + '/valid', d=1.0) > 0.5
		e = edad()
		vivo = e is not None and e < 1.0
		# cada parpadeo se dibuja una sola vez, como un destello de una columna
		parpadeo = EV['blink'] > self.ult_parpadeo and time.time() - EV['blink'] < 0.5
		if parpadeo:
			self.ult_parpadeo = EV['blink']
		for k in range(n):
			x = self.w - n + k
			for r, b in enumerate(BANDAS):
				self._columna(x, r, b, pre, ganancia, valido, vivo, parpadeo and k == 0)

	def _columna(self, x, r, b, pre, gan, valido, vivo, parpadeo):
		rng = self.rng
		rh = (self.h - self.TOP) // len(BANDAS)
		half = int(rh * 0.40)
		y0 = self.TOP + r * rh + 8
		norm = val('%s/band/%s/norm' % (pre, b), d=0.0) if vivo else 0.0
		self.suave[b] += (norm - self.suave[b]) * 0.35
		nv = self.suave[b]
		# cambio de la potencia promedio entre mensajes: produce las ráfagas
		media = val('%s/band/%s/avg' % (pre, b), d=0.0) if vivo else 0.0
		cambio = 0.0
		if self.prev[b]:
			cambio = abs(media - self.prev[b]) / (abs(self.prev[b]) + 1e-6)
		self.prev[b] = media
		self.env[b] += (min(cambio * 6.0, 1.0) - self.env[b]) * 0.08
		# amplitud de la columna: nivel de la banda (envolvente) × grano por columna, con huecos
		env = 0.22 + 0.78 * nv
		amp = gan * env * (0.30 + 0.95 * rng.random() ** 1.3) * (0.75 + 0.6 * self.env[b])
		if rng.random() < 0.04 + 0.22 * (1.0 - nv):
			amp *= 0.08 + 0.2 * rng.random()
		# bloques que se sostienen varias columnas (el «pixelado» de la referencia)
		a_s, resto = self.sost[b]
		if resto > 0:
			amp = a_s * (0.85 + 0.3 * rng.random())
			self.sost[b] = (a_s, resto - 1)
		elif rng.random() < 0.18:
			self.sost[b] = (amp, int(2 + 6 * rng.random()))
		if not vivo:
			amp = 0.02 + 0.02 * rng.random()
		amp = min(max(amp, 0.0), 1.0)
		h = amp * half

		d = np.arange(-half, half + 1, dtype=np.float32)
		ad = np.abs(d)
		bloque = (ad // 3).astype(np.int32)
		ruido = rng.random(bloque.max() + 1).astype(np.float32)[bloque]
		perfil = np.clip(1.0 - ad / (h + 1e-6), 0.0, 1.0) ** 0.55
		dentro = (ad <= h).astype(np.float32)
		brillo = dentro * perfil * (0.40 + 0.60 * ruido) * (0.65 + 0.35 * rng.random())

		base = np.array(COLOR[b], np.float32)
		w = (perfil ** 3)[:, None] * 0.55
		col = base[None, :] * (1.0 - w) + BLANCO[None, :] * w
		# línea central: cian o blanca, como en la referencia
		if h > 1.5:
			nucleo = ad <= 1
			if rng.random() < 0.8:
				col[nucleo] = CIAN if rng.random() < 0.6 else BLANCO
				brillo[nucleo] = np.maximum(brillo[nucleo], 0.55 + 0.3 * rng.random())
		# halo: bloques tenues fuera de la franja
		if rng.random() < 0.15 + 0.35 * nv:
			lejos = (ad > h) & (ad <= h + 6 + 18 * rng.random())
			brillo = np.where(lejos, (0.06 + 0.12 * ruido) * (ruido > 0.35), brillo)
		if parpadeo:
			brillo = np.maximum(brillo, (ad <= half * 0.75) * 0.7)
			col[:] = BLANCO
		pix = col * brillo[:, None]
		if not valido:
			gris = pix.mean(axis=1, keepdims=True)
			pix = gris.repeat(3, axis=1) * 0.55
		y1 = y0 + rh // 2 - half
		self.img[y1:y1 + len(d), x, :] = pix

	def rgba(self, casco=2, con_rotulos=True):
		"""Imagen RGBA uint8 con el origen abajo (como la espera TouchDesigner)."""
		img = self.img.copy()
		if con_rotulos:
			rotulos(img, casco, self.TOP)
		out = np.empty((self.h, self.w, 4), np.uint8)
		out[..., :3] = np.clip(img, 0, 255).astype(np.uint8)
		out[..., 3] = 255
		return out[::-1].copy()


VISOR = {}


def visor(clave, w, h):
	v = VISOR.get(clave)
	if v is None or v.w != int(w) or v.h != int(h):
		v = VISOR[clave] = Visor(w, h)
	return v


# ---------------------------------------------------------------------------
# Rótulos: tipografía de píxeles 5×7 (solo mayúsculas), dibujada encima del visor
# ---------------------------------------------------------------------------
_F = {
'A':['01110','10001','10001','11111','10001','10001','10001'],'B':['11110','10001','10001','11110','10001','10001','11110'],
'C':['01110','10001','10000','10000','10000','10001','01110'],'D':['11110','10001','10001','10001','10001','10001','11110'],
'E':['11111','10000','10000','11110','10000','10000','11111'],'F':['11111','10000','10000','11110','10000','10000','10000'],
'G':['01110','10001','10000','10111','10001','10001','01111'],'H':['10001','10001','10001','11111','10001','10001','10001'],
'I':['01110','00100','00100','00100','00100','00100','01110'],'J':['00111','00010','00010','00010','00010','10010','01100'],
'K':['10001','10010','10100','11000','10100','10010','10001'],'L':['10000','10000','10000','10000','10000','10000','11111'],
'M':['10001','11011','10101','10101','10001','10001','10001'],'N':['10001','10001','11001','10101','10011','10001','10001'],
'O':['01110','10001','10001','10001','10001','10001','01110'],'P':['11110','10001','10001','11110','10000','10000','10000'],
'Q':['01110','10001','10001','10001','10101','10010','01101'],'R':['11110','10001','10001','11110','10100','10010','10001'],
'S':['01111','10000','10000','01110','00001','00001','11110'],'T':['11111','00100','00100','00100','00100','00100','00100'],
'U':['10001','10001','10001','10001','10001','10001','01110'],'V':['10001','10001','10001','10001','10001','01010','00100'],
'W':['10001','10001','10001','10101','10101','10101','01010'],'X':['10001','10001','01010','00100','01010','10001','10001'],
'Y':['10001','10001','01010','00100','00100','00100','00100'],'Z':['11111','00001','00010','00100','01000','10000','11111'],
'Á':['00010','00100','01110','10001','11111','10001','10001'],'É':['00010','00100','11111','10000','11110','10000','11111'],
'Í':['00010','00100','01110','00100','00100','00100','01110'],'Ó':['00010','00100','01110','10001','10001','10001','01110'],
'Ú':['00010','00100','10001','10001','10001','10001','01110'],'Ñ':['01101','10010','10001','11001','10101','10011','10001'],
'0':['01110','10001','10011','10101','11001','10001','01110'],'1':['00100','01100','00100','00100','00100','00100','01110'],
'2':['01110','10001','00001','00010','00100','01000','11111'],'3':['11110','00001','00001','01110','00001','00001','11110'],
'4':['00010','00110','01010','10010','11111','00010','00010'],'5':['11111','10000','11110','00001','00001','10001','01110'],
'6':['00110','01000','10000','11110','10001','10001','01110'],'7':['11111','00001','00010','00100','01000','01000','01000'],
'8':['01110','10001','10001','01110','10001','10001','01110'],'9':['01110','10001','10001','01111','00001','00010','01100'],
' ':['00000']*7,'.':['00000','00000','00000','00000','00000','01100','01100'],
',':['00000','00000','00000','00000','01100','00100','01000'],':':['00000','01100','01100','00000','01100','01100','00000'],
'-':['00000','00000','00000','11111','00000','00000','00000'],'–':['00000','00000','00000','11111','00000','00000','00000'],
'%':['11000','11001','00010','00100','01000','10011','00011'],'/':['00001','00001','00010','00100','01000','10000','10000'],
'·':['00000','00000','00000','01100','01100','00000','00000'],'?':['01110','10001','00001','00010','00100','00000','00100'],
}
_G = {k: np.array([[c == '1' for c in fila] for fila in v], bool) for k, v in _F.items()}

DESCRIPCION = {
	'theta': ('THETA', '4–8 HZ', 'DIVAGAR, IMAGINAR'),
	'alpha': ('ALFA', '8–12 HZ', 'CALMA, OJOS CERRADOS'),
	'betaL': ('BETA BAJA', '12–16 HZ', 'ATENCIÓN TRANQUILA'),
	'betaH': ('BETA ALTA', '16–25 HZ', 'ALERTA, ESFUERZO'),
	'gamma': ('GAMMA', '25–45 HZ', 'ACTIVIDAD RÁPIDA, MÚSCULO'),
}


def ancho_texto(t):
	return len(t) * 6 - 1


def texto(img, t, x, y, color=(255, 255, 255), fondo=0.55):
	"""Escribe t en img (alto × ancho × 3, origen arriba) con un fondo oscuro para que se lea."""
	# sin acentos (salvo la Ñ): en 5×7 se leen mejor
	t = t.upper()
	t = ''.join(ch if ch == 'Ñ' else unicodedata.normalize('NFD', ch)[0] for ch in t)
	h, w = img.shape[:2]
	x0, y0 = int(x), int(y)
	x1, y1 = min(w, x0 + ancho_texto(t) + 2), min(h, y0 + 9)
	if fondo and x0 - 1 < w and y0 - 1 < h:
		img[max(0, y0 - 1):y1, max(0, x0 - 1):x1] *= (1.0 - fondo)
	c = np.asarray(color, np.float32)
	for i, ch in enumerate(t):
		g = _G.get(ch, _G['?'])
		gx = x0 + i * 6
		if gx + 5 > w or y0 + 7 > h or gx < 0 or y0 < 0:
			continue
		zona = img[y0:y0 + 7, gx:gx + 5]
		zona[g] = c


def rotulos(img, casco, top):
	"""Nombre, rango y valor de cada franja, y una línea de estado arriba."""
	pre = 'eeg/%d' % int(casco)
	rh = (img.shape[0] - top) // len(BANDAS)
	e = edad()
	vivo = e is not None and e < 1.0
	for r, b in enumerate(BANDAS):
		nombre, hz, que = DESCRIPCION[b]
		y = top + r * rh + 3
		n = val('%s/band/%s/norm' % (pre, b), d=float('nan')) if vivo else float('nan')
		v = '—' if n != n else '%.2f' % n
		texto(img, '%s  %s' % (nombre, hz), 3, y, COLOR[b])
		texto(img, que, 3, y + 10, (150, 150, 150))
		texto(img, v.replace('—', '-'), img.shape[1] - ancho_texto(v) - 3, y, (255, 255, 255))
	# estado
	img[:top] = 0
	if not T:
		est = 'ESPERANDO DATOS · PUERTO OSC'
		color = (200, 140, 60)
	elif not vivo:
		est = 'SIN DATOS HACE %d S' % int(e)
		color = (200, 140, 60)
	else:
		modelo = {1: 'EPOC+', 2: 'INSIGHT'}.get(int(casco), 'CASCO %d' % int(casco))
		ok = val(pre + '/valid', d=0) > 0.5
		q = int(round(val(pre + '/quality', d=0) * 100))
		bat = int(round(val(pre + '/battery', d=0) * 100))
		est = '%s · %s · CALIDAD %d%% · BATERÍA %d%%' % (modelo, 'VÁLIDO' if ok else 'REVISA CONTACTO', q, bat)
		color = (118, 196, 17) if ok else (217, 130, 43)
		if time.time() - EV['blink'] < 0.3:
			est += ' · PARPADEO'
	texto(img, est, 3, 2, color, fondo=0)
'''

CB_OSC = r'''# cada mensaje OSC que llega va a eeg_lib
def onReceiveOSC(dat, rowIndex, message, byteData, timeStamp, address, args, peer):
	op('eeg_lib').module.recibir(address, args)
	return
'''

CB_VISOR = r'''# dibuja el visor en cada cuadro
def onSetupParameters(scriptOp):
	return
def onPulse(par):
	return
def onCook(scriptOp):
	absTime.frame
	P = parent.eeg.par
	if not P.Activo.eval():
		return
	lib = op('eeg_lib').module
	v = lib.visor(scriptOp.path, P.Ancho.eval(), P.Alto.eval())
	v.paso(P.Casco.eval(), P.Velocidad.eval(), P.Ganancia.eval())
	scriptOp.copyNumpyArray(v.rgba(P.Casco.eval(), bool(P.Rotulos.eval())))
'''

CB_DATOS = r'''# todos los datos recibidos como canales (una muestra)
def onSetupParameters(scriptOp):
	return
def onPulse(par):
	return
def onCook(scriptOp):
	absTime.frame
	scriptOp.clear()
	scriptOp.numSamples = 1
	for nombre, valor in op('eeg_lib').module.canales():
		try:
			ch = scriptOp.appendChan(nombre)
			ch[0] = valor
		except Exception:
			pass
'''

# =====================================================================================
#  CONSTRUCCIÓN
# =====================================================================================

host = me.parent()
old = host.op(NAME)
if old is not None:
	old.destroy()

c = host.create(baseCOMP, NAME)
c.nodeX = me.nodeX + 220
c.nodeY = me.nodeY
setv(c, 'parentshortcut', 'eeg')
c.color = (0.15, 0.15, 0.15)

# ---------- parámetros ----------
def ipar(pg, name, label, default, lo, hi):
	p = pg.appendInt(name, label=label)[0]
	p.normMin, p.normMax = lo, hi
	p.default = default
	p.val = default
	return p

def fpar(pg, name, label, default, lo, hi):
	p = pg.appendFloat(name, label=label)[0]
	p.normMin, p.normMax = lo, hi
	p.default = default
	p.val = default
	return p

def tpar(pg, name, label, default):
	p = pg.appendToggle(name, label=label)[0]
	p.default = default
	p.val = default
	return p

pg = c.appendCustomPage('EEG')
ipar(pg, 'Puerto', 'Puerto OSC', 9200, 1024, 65535)
ipar(pg, 'Casco', 'Casco (1 = EPOC+, 2 = Insight)', 2, 1, 3)
tpar(pg, 'Activo', 'Activo', True)
pg = c.appendCustomPage('Visor')
ipar(pg, 'Velocidad', 'Velocidad (columnas por cuadro)', 2, 1, 8)
fpar(pg, 'Ganancia', 'Ganancia (alto de las franjas)', 1.0, 0.2, 2.5)
tpar(pg, 'Rotulos', 'Rótulos', True)
ipar(pg, 'Ancho', 'Ancho interno (px)', 360, 120, 1920)
ipar(pg, 'Alto', 'Alto interno (px)', 540, 160, 2160)
ipar(pg, 'Escala', 'Escala de salida (pixelado)', 2, 1, 6)
p = pg.appendStr('Version', label='Versión')[0]; p.val = VERSION; p.readOnly = True

# ---------- red ----------
lib = c.create(textDAT, 'eeg_lib'); lib.text = LIB; place(lib, 0, 0)

cbo = c.create(textDAT, 'cb_osc'); cbo.text = CB_OSC; place(cbo, 0, 1)
osc = c.create(oscinDAT, 'osc'); place(osc, 1, 1)
quitar_callbacks_auto(c, 'osc')
setv(osc, 'port', expr=E + 'Puerto')
setv(osc, 'active', expr=E + 'Activo', opt=True)
setv(osc, 'callbacks', expr="op('cb_osc')")
setv(osc, 'clamp', True, opt=True)
setv(osc, ['maxlines', 'maxrows'], 20, opt=True)

cbv = c.create(textDAT, 'cb_visor'); cbv.text = CB_VISOR; place(cbv, 0, 2)
vis = c.create(scriptTOP, 'visor'); place(vis, 1, 2)
quitar_callbacks_auto(c, 'visor')
setv(vis, 'callbacks', expr="op('cb_visor')")
setv(vis, ['format', 'pixelformat'], 'rgba8fixed', opt=True)

sal = c.create(nullTOP, 'salida'); place(sal, 2, 2)
wire(vis, sal)
setv(sal, 'outputresolution', 'custom')
setv(sal, 'resolutionw', expr=E + 'Ancho*' + E + 'Escala')
setv(sal, 'resolutionh', expr=E + 'Alto*' + E + 'Escala')
setv(sal, 'inputfiltertype', 'nearest', opt=True)
o1 = c.create(outTOP, 'out1'); place(o1, 3, 2)
wire(sal, o1)

cbd = c.create(textDAT, 'cb_datos'); cbd.text = CB_DATOS; place(cbd, 0, 3)
dat = c.create(scriptCHOP, 'datos'); place(dat, 1, 3)
quitar_callbacks_auto(c, 'datos')
setv(dat, 'callbacks', expr="op('cb_datos')")
o2 = c.create(outCHOP, 'out2'); place(o2, 3, 3)
wire(dat, o2)

# ---------- informe ----------
inf = c.create(textDAT, 'eeg_informe'); place(inf, 0, 4)
if FAILS:
	inf.text = 'eeg_visor %s · ajustes que no se pudieron aplicar (%d):\n' % (VERSION, len(FAILS)) + '\n'.join(FAILS)
else:
	inf.text = 'eeg_visor %s · construido sin avisos. Escuchando OSC en el puerto %d.' % (VERSION, c.par.Puerto.eval())
print(inf.text)

# visor flotante para la prueba
try:
	c.viewer = True
	o1.viewer = True
except Exception:
	pass
