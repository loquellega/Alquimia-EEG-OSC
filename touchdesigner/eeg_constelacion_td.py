# =====================================================================================
#  Alquimia EEG OSC · constelación para TouchDesigner (componente eeg_constelacion)
#  La mente como constelación: cinco nubes de líneas (calma, imaginación, atención,
#  activación, neutral) que respiran con su índice; hilos que se acumulan con cada
#  transición; nodos con el minuto y la frase; marcas de escena; la cabeza mueve la cámara.
#  Diseño, investigación y desarrollo: Héctor Cruz Juárez — https://hectorcruz.com.mx
#  Colección Alchemy of Meaning. · SPDX-License-Identifier: GPL-3.0-or-later
#
#  USO
#   1. Crea un Text DAT, pega este archivo y haz clic derecho → Run Script.
#   2. Aparece el COMP «eeg_constelacion»; out1 es la imagen.
#   3. Escucha OSC en el puerto 9205 (para no chocar con eeg_visor en el 9200):
#      en el monitor del puente, modo avanzado → salida OSC → + destino 127.0.0.1:9205 → aplicar.
#      Si no usas eeg_visor, puedes poner Puerto = 9200 y listo.
#   4. Si algo no se pudo ajustar, el DAT «eeg_informe» dentro del COMP lo lista.
#  Escrito para TouchDesigner 2025. Volver a correrlo reemplaza el componente.
# =====================================================================================

VERSION = '0.1'
NAME = 'eeg_constelacion'
FAILS = []

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

E = 'parent.const.par.'

LIB = r'''# constelacion_lib · la mente como constelación (Alquimia EEG OSC · GPL-3.0-or-later)
# Vive dentro del componente eeg_constelacion de TouchDesigner. No depende de td: se puede probar fuera.
#
#  · cinco nubes de líneas, una por estado; cada una respira con su índice
#  · hilos blancos que se acumulan con cada transición: el tejido es el recorrido de la sesión
#  · nodos con su minuto y su frase; las marcas de escena en otro color
#  · la cabeza (giroscopio) mueve la cámara; abajo, la gráfica comprimida de la sesión
import math
import time
import unicodedata
import numpy as np

ESTADOS = ['neutral', 'calma', 'imaginacion', 'atencion', 'activacion', 'movimiento']
NUBES = ['calma', 'imaginacion', 'atencion', 'activacion', 'neutral']
NOM = {'neutral': 'NEUTRAL', 'calma': 'CALMA', 'imaginacion': 'IMAGINACION', 'atencion': 'ATENCION',
       'activacion': 'ACTIVACION', 'movimiento': 'MOVIMIENTO'}
CENTRO = {
	'calma': (0.05, 1.30, 0.10),
	'imaginacion': (-1.40, 0.40, 0.45),
	'atencion': (1.35, 0.45, -0.35),
	'activacion': (0.80, -1.20, 0.35),
	'neutral': (-0.75, -1.10, -0.45),
}
# tres tonos por nube (RGB 0–1): mezcla de azules y rojos como en la referencia, con un dominante por estado
PALETA = {
	'calma': [(0.25, 0.62, 1.00), (0.40, 0.85, 1.00), (0.95, 0.45, 0.55)],
	'imaginacion': [(0.85, 0.45, 1.00), (0.35, 0.55, 1.00), (1.00, 0.45, 0.60)],
	'atencion': [(1.00, 0.80, 0.30), (1.00, 0.45, 0.35), (0.35, 0.60, 1.00)],
	'activacion': [(1.00, 0.30, 0.30), (1.00, 0.60, 0.25), (0.40, 0.55, 1.00)],
	'neutral': [(0.55, 0.62, 0.80), (0.70, 0.70, 0.75), (0.95, 0.50, 0.55)],
}
COLOR_NODO = {e: np.array(PALETA[e][0], np.float32) for e in NUBES}
COLOR_NODO['movimiento'] = np.array((0.85, 0.51, 0.17), np.float32)

# ---------------------------------------------------------------------------
# Recepción
# ---------------------------------------------------------------------------
V = {}
T = {}
S = {'estado': 'neutral', 'cambios': [], 'frases': [], 'marcas': [], 'blink': 0.0, 'historial': [], 'ult_seg': -1,
     'sesion': 0.0}


def reiniciar():
	S.update(cambios=[], frases=[], marcas=[], historial=[], ult_seg=-1)
	for c in list(CONST.values()):
		c.reiniciar()


def recibir(address, args):
	a = address.lstrip('/')
	args = list(args)
	V[a] = args
	T[a] = time.time()
	p = a.split('/')
	if len(p) >= 3 and p[0] == 'eeg' and p[1].isdigit():
		resto = '/'.join(p[2:])
		if resto == 'estado/cambio' and args:
			nuevo = ESTADOS[int(args[0])] if 0 <= int(args[0]) < len(ESTADOS) else 'neutral'
			S['cambios'].append((time.time(), S['estado'], nuevo))
			S['estado'] = nuevo
		elif resto == 'estado' and args:
			i = int(args[0])
			if 0 <= i < len(ESTADOS) and not S['cambios']:
				S['estado'] = ESTADOS[i]
		elif resto == 'frase' and args:
			S['frases'].append((time.time(), S['sesion'], S['estado'], str(args[0])))
		elif resto == 'blink':
			S['blink'] = time.time()
		elif resto == 'sesion/tiempo' and args:
			S['sesion'] = float(args[0])
			seg = int(S['sesion'])
			if seg != S['ult_seg']:
				S['ult_seg'] = seg
				fila = [S['sesion'], ESTADOS.index(S['estado'])] + [val('%s/indice/%s' % ('/'.join(p[:2]), k), d=0.5)
				                                                   for k in ('calma', 'imaginacion', 'atencion', 'activacion')]
				S['historial'].append(fila)
				del S['historial'][:-3600]
	elif a == 'eeg/marca' and args:   # por si TouchDesigner también reenvía marcas
		S['marcas'].append((time.time(), S['sesion'], ' '.join(str(x) for x in args)))


def marcar(texto):
	S['marcas'].append((time.time(), S['sesion'], str(texto)))


def val(a, i=0, d=0.0):
	v = V.get(a)
	try:
		return float(v[i])
	except (TypeError, IndexError, ValueError):
		return d


def edad():
	return (time.time() - max(T.values())) if T else None


# ---------------------------------------------------------------------------
# Tipografía de píxeles 5×7 (misma que el visor)
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
'Ñ':['01101','10010','10001','11001','10101','10011','10001'],
'0':['01110','10001','10011','10101','11001','10001','01110'],'1':['00100','01100','00100','00100','00100','00100','01110'],
'2':['01110','10001','00001','00010','00100','01000','11111'],'3':['11110','00001','00001','01110','00001','00001','11110'],
'4':['00010','00110','01010','10010','11111','00010','00010'],'5':['11111','10000','11110','00001','00001','10001','01110'],
'6':['00110','01000','10000','11110','10001','10001','01110'],'7':['11111','00001','00010','00100','01000','01000','01000'],
'8':['01110','10001','10001','01110','10001','10001','01110'],'9':['01110','10001','10001','01111','00001','00010','01100'],
' ':['00000']*7,'.':['00000','00000','00000','00000','00000','01100','01100'],
',':['00000','00000','00000','00000','01100','00100','01000'],':':['00000','01100','01100','00000','01100','01100','00000'],
'-':['00000','00000','00000','11111','00000','00000','00000'],'%':['11000','11001','00010','00100','01000','10011','00011'],
'/':['00001','00001','00010','00100','01000','10000','10000'],'·':['00000','00000','00000','01100','01100','00000','00000'],
'?':['01110','10001','00001','00010','00100','00000','00100'],'!':['00100','00100','00100','00100','00100','00000','00100'],
'¿':['00100','00000','00100','01000','10000','10001','01110'],'¡':['00100','00000','00100','00100','00100','00100','00100'],
'(':['00010','00100','01000','01000','01000','00100','00010'],')':['01000','00100','00010','00010','00010','00100','01000'],
'>':['10000','01000','00100','00010','00100','01000','10000'],
}
_G = {k: np.array([[c == '1' for c in fila] for fila in v], bool) for k, v in _F.items()}


def _limpia(t):
	return ''.join(ch if ch == 'Ñ' else unicodedata.normalize('NFD', ch)[0] for ch in str(t).upper())


def ancho_texto(t, e=1):
	return (len(_limpia(t)) * 6 - 1) * e


def texto(img, t, x, y, color=(1, 1, 1), e=1, fondo=0.0):
	t = _limpia(t)
	h, w = img.shape[:2]
	x0, y0 = int(x), int(y)
	if fondo:
		x1, y1 = min(w, x0 + ancho_texto(t, e) + 2 * e), min(h, y0 + 9 * e)
		if x1 > 0 and y1 > 0:
			img[max(0, y0 - 2 * e):y1, max(0, x0 - 2 * e):x1] *= (1.0 - fondo)
	c = np.asarray(color, np.float32)
	for i, ch in enumerate(t):
		g = _G.get(ch, _G['?'])
		if e > 1:
			g = g.repeat(e, 0).repeat(e, 1)
		gx = x0 + i * 6 * e
		gh, gw = g.shape
		if gx < 0 or y0 < 0 or gx + gw > w or y0 + gh > h:
			continue
		img[y0:y0 + gh, gx:gx + gw][g] = c


def mmss(s):
	s = max(0, int(s))
	return '%d:%02d' % (s // 60, s % 60)


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------
class Nube:
	def __init__(self, nombre, rng, n=210, k=4):
		self.nombre = nombre
		self.c = np.array(CENTRO[nombre], np.float32)
		d = rng.normal(size=(n, 3)).astype(np.float32)
		d /= np.linalg.norm(d, axis=1, keepdims=True)
		# blob irregular (como la referencia): radio con lóbulos
		lob = 1 + 0.28 * np.sin(3 * d[:, 0] + rng.random() * 6) * np.cos(2 * d[:, 1] + rng.random() * 6) \
			+ 0.18 * np.sin(5 * d[:, 2] + rng.random() * 6)
		r = 0.48 * lob * (0.35 + 0.65 * rng.random(n) ** 0.35)
		self.p0 = d * r[:, None]
		self.fase = rng.random(n).astype(np.float32) * 6.283
		dist = np.linalg.norm(self.p0[:, None, :] - self.p0[None, :, :], axis=2)
		lineas = set()
		for i in range(n):
			cerca = np.argsort(dist[i])[1:10]
			for j in rng.choice(cerca, size=k, replace=False):
				lineas.add((min(i, int(j)), max(i, int(j))))
			if rng.random() < 0.5:   # cuerdas largas que enredan la nube
				j = int(rng.integers(n))
				if j != i:
					lineas.add((min(i, j), max(i, j)))
		self.lineas = np.array(sorted(lineas), np.int32)
		pal = np.array(PALETA[nombre], np.float32)
		elec = rng.choice(len(pal), size=len(self.lineas), p=[0.55, 0.3, 0.15])
		self.col = pal[elec] * (0.75 + 0.5 * rng.random((len(self.lineas), 1))).astype(np.float32)
		self.rango = rng.permutation(len(self.lineas)).astype(np.float32) / len(self.lineas)
		self.escala = 0.7
		self.brillo = 0.5

	def puntos(self, t, mov=0.0, rng=None):
		w = 1 + 0.05 * np.sin(t * 0.8 + self.fase)[:, None]
		p = self.c + self.p0 * self.escala * w
		if mov > 0.02 and rng is not None:
			p = p + rng.normal(scale=0.05 * mov, size=p.shape).astype(np.float32)
		return p


class Constelacion:
	def __init__(self, semilla=11):
		self.rng = np.random.default_rng(semilla)
		self.nubes = {e: Nube(e, self.rng) for e in NUBES}
		self.yaw = 0.0
		self.gyro = np.zeros(2, np.float32)
		self.t0 = time.time()
		self.grano = None
		self.reiniciar()

	def reiniciar(self):
		self.hilos = np.zeros((0, 5), np.float32)   # nubeA, iA, nubeB, iB, nacimiento
		self.cambios_vistos = 0
		self.nodos_pos = {}

	# --- hilos de las transiciones ---
	def _tejer(self, ahora, max_hilos):
		cam = S['cambios']
		while self.cambios_vistos < len(cam):
			t, a, b = cam[self.cambios_vistos]
			self.cambios_vistos += 1
			if a == 'movimiento' or b == 'movimiento' or a == b:
				continue
			ia, ib = NUBES.index(a), NUBES.index(b)
			n = 60
			na, nb = len(self.nubes[a].p0), len(self.nubes[b].p0)
			nuevos = np.stack([np.full(n, ia), self.rng.integers(na, size=n), np.full(n, ib),
			                   self.rng.integers(nb, size=n), t + self.rng.random(n) * 0.8], axis=1).astype(np.float32)
			self.hilos = np.concatenate([self.hilos, nuevos])[-int(max_hilos):]

	def _nodo(self, k, estado):
		"""Posición fija de un nodo: en una capa alrededor de la nube de su estado."""
		clave = (k, estado)
		if clave not in self.nodos_pos:
			r = np.random.default_rng(1000 + k)
			d = r.normal(size=3)
			d /= np.linalg.norm(d)
			base = np.array(CENTRO.get(estado, (0, 0, 0)), np.float32)
			if estado == 'movimiento':
				base = np.zeros(3, np.float32)
			self.nodos_pos[clave] = (base + d * (0.62 + 0.25 * r.random())).astype(np.float32)
		return self.nodos_pos[clave]

	# --- cámara ---
	def _proyectar(self, P, W, H, dist=6.2):
		cy, sy = math.cos(self.yaw_total), math.sin(self.yaw_total)
		cp, sp = math.cos(self.pitch), math.sin(self.pitch)
		x = P[..., 0] * cy + P[..., 2] * sy
		z = -P[..., 0] * sy + P[..., 2] * cy
		y = P[..., 1] * cp - z * sp
		z = P[..., 1] * sp + z * cp
		z = z + dist
		f = min(W, H) * 1.55
		X = W * 0.5 + x * f / z
		Y = H * 0.46 - y * f / z
		return X, Y, z

	def render(self, W, H, casco=2, orbita=6.0, exposicion=1.0, max_hilos=1200, rotulos=True, ahora=None):
		ahora = time.time() if ahora is None else ahora
		t = ahora - self.t0
		pre = 'eeg/%d' % casco
		e = edad()
		vivo = e is not None and e < 2.0
		self._tejer(ahora, max_hilos)

		# cámara: órbita lenta + giro de la cabeza
		g = np.array([val(pre + '/gyro', 0), val(pre + '/gyro', 1)], np.float32) if vivo else np.zeros(2, np.float32)
		self.gyro += (g - self.gyro) * 0.15
		self.yaw += math.radians(orbita) / 30.0
		self.yaw_total = self.yaw + self.gyro[0] * math.radians(70)
		self.pitch = math.radians(12) + self.gyro[1] * math.radians(35)

		idx = {k: val('%s/indice/%s' % (pre, k), d=0.5) for k in ('calma', 'imaginacion', 'atencion', 'activacion', 'movimiento')}
		lead = max(idx[k] - 0.5 for k in ('calma', 'imaginacion', 'atencion', 'activacion'))
		idx['neutral'] = float(np.clip(1.0 - lead * 3.0, 0, 1)) * 0.8
		mov = idx['movimiento'] if vivo else 0.0
		cal = val(pre + '/sesion/calibracion', d=1.0)
		actual = S['estado']

		acc = np.zeros((H * W, 3), np.float32)
		xs_all, ys_all, cs_all = [], [], []

		def segmentos(A, B, col, peso, hasta=None):
			XA, YA, ZA = self._proyectar(A, W, H)
			XB, YB, ZB = self._proyectar(B, W, H)
			if hasta is not None:     # hilos que crecen de A hacia B
				XB = XA + (XB - XA) * hasta
				YB = YA + (YB - YA) * hasta
			largo = np.hypot(XB - XA, YB - YA)
			n = np.clip(np.ceil(largo / 1.6), 2, 500).astype(np.int64)
			ids = np.repeat(np.arange(len(n)), n)
			ini = np.cumsum(n) - n
			u = ((np.arange(n.sum()) - ini[ids]) / np.maximum(n[ids] - 1, 1)).astype(np.float32)
			X = XA[ids] + (XB[ids] - XA[ids]) * u
			Y = YA[ids] + (YB[ids] - YA[ids]) * u
			prof = np.clip(1.6 - ((ZA[ids] + ZB[ids]) * 0.5 - 6.2) * 0.35, 0.35, 1.4)
			xs_all.append(X)
			ys_all.append(Y)
			cs_all.append(col[ids] * (peso[ids] * prof)[:, None] if np.ndim(peso) else col[ids] * (peso * prof)[:, None])

		# nubes
		for nom, nube in self.nubes.items():
			v = idx.get(nom, 0.5) if vivo else 0.3
			obj_esc = 0.55 + 0.85 * v
			obj_br = (0.22 + 1.25 * v ** 1.6) * (1.45 if nom == actual else 1.0)
			if cal < 1.0:
				obj_br *= 0.35 + 0.65 * cal
			nube.escala += (obj_esc - nube.escala) * 0.06
			nube.brillo += (obj_br - nube.brillo) * 0.06
			P = nube.puntos(t, mov, self.rng)
			visibles = nube.rango < (0.30 + 0.70 * v)
			L = nube.lineas[visibles]
			if not len(L):
				continue
			peso = np.full(len(L), 0.16 * nube.brillo, np.float32)
			segmentos(P[L[:, 0]], P[L[:, 1]], nube.col[visibles], peso)

		# hilos de las transiciones
		if len(self.hilos):
			hl = self.hilos
			puntos = {i: self.nubes[n].puntos(t) for i, n in enumerate(NUBES)}
			A = np.stack([puntos[int(a)][int(i)] for a, i in hl[:, :2]])
			B = np.stack([puntos[int(b)][int(i)] for b, i in hl[:, 2:4]])
			edad_h = ahora - hl[:, 4]
			crece = np.clip(edad_h / 1.4, 0, 1)
			flash = 1.0 + (1.5 if ahora - S['blink'] < 0.25 else 0.0)
			nuevo = np.clip(1.0 - edad_h / 6.0, 0, 1)
			peso = (0.075 + 0.16 * nuevo) * flash
			col = np.tile(np.array([[0.95, 0.95, 0.97]], np.float32), (len(hl), 1))
			segmentos(A, B, col, peso.astype(np.float32), hasta=crece)

		# nodos: cada frase es un nodo; las consecutivas se unen con una línea roja
		frases = S['frases']
		pos_n = [self._nodo(k, est) for k, (_, _, est, _) in enumerate(frases)]
		if len(pos_n) > 1:
			A = np.stack(pos_n[:-1])
			B = np.stack(pos_n[1:])
			col = np.tile(np.array([[1.0, 0.32, 0.36]], np.float32), (len(A), 1))
			segmentos(A, B, col, np.full(len(A), 0.30, np.float32))
		marcas = S['marcas']
		pos_m = []
		for k, (_, ts, _) in enumerate(marcas):
			r = np.random.default_rng(5000 + k)
			d = r.normal(size=3)
			d /= np.linalg.norm(d)
			pos_m.append((d * 1.9).astype(np.float32))

		# acumular líneas
		if xs_all:
			X = np.concatenate(xs_all)
			Y = np.concatenate(ys_all)
			C = np.concatenate(cs_all)
			ok = (X >= 0) & (X < W - 1) & (Y >= 0) & (Y < H - 1)
			ii = (Y[ok].astype(np.int64) * W + X[ok].astype(np.int64))
			for ch in range(3):
				acc[:, ch] += np.bincount(ii, weights=C[ok, ch], minlength=H * W)
		img = acc.reshape(H, W, 3)

		# resplandor barato: submuestreo a 1/4, difuminado y sumado
		h4, w4 = H // 4, W // 4
		if h4 > 2 and w4 > 2:
			b = img[:h4 * 4, :w4 * 4]
			peq = (b[0::4, 0::4] + b[1::4, 2::4] + b[2::4, 1::4] + b[3::4, 3::4]) * 0.25
			peq = (peq * 2 + np.roll(peq, 1, 0) + np.roll(peq, -1, 0) + np.roll(peq, 1, 1) + np.roll(peq, -1, 1)) / 6
			b += peq.repeat(4, 0).repeat(4, 1) * 1.1

		img *= 1.7 * exposicion
		img /= (1.0 + img)                      # mapeo de tonos (Reinhard): satura suave, como luz

		# fondo gris con grano, como la referencia (cuatro granos precalculados que se alternan)
		if self.grano is None or self.grano.shape[1:] != (H, W):
			self.grano = (0.085 + 0.02 * self.rng.random((4, H, W))).astype(np.float32)
		fondo = self.grano[int(t * 12) % 4]
		img += fondo[..., None] * (1.0 - img)

		# esferas de los nodos y marcas
		def esfera(p, radio, color, brillo=1.0):
			X, Y, Z = self._proyectar(p[None, :], W, H)
			x, y = float(X[0]), float(Y[0])
			r = max(2.0, radio * 6.2 / float(Z[0]))
			x0, x1 = int(max(0, x - r - 1)), int(min(W, x + r + 2))
			y0, y1 = int(max(0, y - r - 1)), int(min(H, y + r + 2))
			if x0 >= x1 or y0 >= y1:
				return x, y, r
			yy, xx = np.mgrid[y0:y1, x0:x1]
			dd = np.hypot(xx - x + r * 0.3, yy - y + r * 0.3) / r      # luz arriba a la izquierda
			m = np.hypot(xx - x, yy - y) <= r
			sh = np.clip(1.15 - dd * 0.75, 0.25, 1.15)[..., None]
			zona = img[y0:y1, x0:x1]
			zona[m] = (np.asarray(color, np.float32) * sh * brillo)[m]
			return x, y, r

		etiquetas = []
		for k, (p, (_, ts, est, fr)) in enumerate(zip(pos_n, frases)):
			ult = k == len(frases) - 1
			pul = 1.0 + (0.25 * math.sin(t * 4) if ult else 0.0)
			x, y, r = esfera(p, 7.0 * pul if ult else 5.0, COLOR_NODO.get(est, COLOR_NODO['neutral']))
			etiquetas.append((x, y, r, '%s %s' % (mmss(ts), fr), ult, COLOR_NODO.get(est, COLOR_NODO['neutral'])))
		for p, (_, ts, tx) in zip(pos_m, marcas):
			x, y, r = esfera(p, 5.5, (1.0, 0.82, 0.25))
			etiquetas.append((x, y, r, '> %s %s' % (mmss(ts), tx), False, np.array((1.0, 0.82, 0.25), np.float32)))

		# rótulos de los nodos (como la segunda referencia)
		if rotulos:
			for x, y, r, tx, ult, col in etiquetas[-14:]:
				tx = tx if len(tx) <= 34 else tx[:33] + '.'
				tx_x = int(x + r + 4)
				if tx_x + ancho_texto(tx) > W - 4:
					tx_x = int(x - r - 4 - ancho_texto(tx))
				texto(img, tx, tx_x, int(y - 4), (1, 1, 1) if ult else (0.78, 0.78, 0.8), 1, fondo=0.75)
			for nom, nube in self.nubes.items():   # nombre de cada nube
				X, Y, Z = self._proyectar(nube.c[None, :] + np.array([[0, 0.62 * nube.escala + 0.1, 0]], np.float32), W, H)
				col = np.array(PALETA[nom][0]) * (1.0 if nom == actual else 0.6)
				texto(img, NOM[nom], int(X[0] - ancho_texto(NOM[nom]) / 2), int(Y[0]), col, 1, fondo=0.5)
			self._hud(img, W, H, pre, vivo, cal, idx, actual)
		out = np.empty((H, W, 4), np.uint8)
		out[..., :3] = np.clip(img * 255, 0, 255).astype(np.uint8)
		out[..., 3] = 255
		return out[::-1].copy()

	def _hud(self, img, W, H, pre, vivo, cal, idx, actual):
		# arriba: estado, tiempo
		if not T:
			texto(img, 'ESPERANDO DATOS · PUERTO OSC', 8, 8, (0.85, 0.55, 0.25), 1)
		elif not vivo:
			texto(img, 'SIN DATOS HACE %d S' % int(edad()), 8, 8, (0.85, 0.55, 0.25), 1)
		else:
			inten = val(pre + '/estado', 1, d=0.5)
			texto(img, '%s %d%%' % (NOM.get(actual, actual), int(inten * 100)), 8, 8,
			      np.array(PALETA.get(actual, PALETA['neutral'])[0]), 2)
			texto(img, 'SESION %s · %d TRANSICIONES · %d FRASES' % (mmss(S['sesion']), len(S['cambios']), len(S['frases'])),
			      8, 30, (0.6, 0.6, 0.62), 1)
			if cal < 1.0:
				texto(img, 'CALIBRANDO LINEA BASE %d%% · QUIETO, OJOS ABIERTOS' % int(cal * 100), 8, 42, (0.85, 0.55, 0.25), 1)
		# abajo: frase actual y gráfica comprimida
		gh = 54
		gy = H - gh - 8
		if S['frases']:
			fr = S['frases'][-1][3]
			e = 2 if ancho_texto(fr, 2) <= W - 16 else 1
			texto(img, fr, 8, gy - 12 - 9 * e, (1, 1, 1), e, fondo=0.6)
		Hh = S['historial']
		if len(Hh) > 1:
			img[gy - 2:gy + gh + 2, 6:W - 6] *= 0.35
			n = len(Hh)
			xs = (6 + np.arange(n) * (W - 13) / (n - 1)).astype(int)
			arr = np.array(Hh, np.float32)
			for j, nom in enumerate(('calma', 'imaginacion', 'atencion', 'activacion')):
				ys = (gy + (1 - arr[:, 2 + j]) * (gh - 10)).astype(int)
				col = np.array(PALETA[nom][0], np.float32)
				for a in range(n - 1):
					y0, y1 = sorted((ys[a], ys[a + 1]))
					img[y0:y1 + 1, xs[a]:xs[a + 1] + 1] = col
			for a in range(n):
				est = ESTADOS[int(arr[a, 1])]
				img[gy + gh - 6:gy + gh, xs[a]:xs[min(a + 1, n - 1)] + 1] = COLOR_NODO.get(est, COLOR_NODO['neutral']) * 0.9
			for _, ts, _ in S['marcas']:
				if Hh[0][0] <= ts <= Hh[-1][0]:
					x = int(6 + (ts - Hh[0][0]) / max(1e-6, Hh[-1][0] - Hh[0][0]) * (W - 13))
					img[gy - 4:gy + gh, x] = (1.0, 0.82, 0.25)
		texto(img, 'ALCHEMY OF MEANING', W - ancho_texto('ALCHEMY OF MEANING') - 8, 8, (0.35, 0.35, 0.37), 1)


CONST = {}


def constelacion(clave):
	c = CONST.get(clave)
	if c is None:
		c = CONST[clave] = Constelacion()
	return c
'''

CB_OSC = r'''# cada mensaje OSC que llega va a const_lib
def onReceiveOSC(dat, rowIndex, message, byteData, timeStamp, address, args, peer):
	op('const_lib').module.recibir(address, args)
	return
'''

CB_IMAGEN = r'''# dibuja la constelación (lo dispara eeg_cuadro)
def onSetupParameters(scriptOp):
	return
def onPulse(par):
	return
def onCook(scriptOp):
	P = parent.const.par
	if not P.Activo.eval():
		return
	c = op('const_lib').module.constelacion(scriptOp.path)
	img = c.render(int(P.Ancho.eval()), int(P.Alto.eval()), int(P.Casco.eval()), P.Orbita.eval(),
	               P.Exposicion.eval(), int(P.Hilos.eval()), bool(P.Rotulos.eval()))
	scriptOp.copyNumpyArray(img)
'''

CB_CUADRO = r'''# recalcula la imagen cada N cuadros (los Script OP solos no lo hacen)
def onFrameStart(frame):
	P = parent.const.par
	if P.Activo.eval() and frame % max(1, int(P.Cadacuadros.eval())) == 0:
		op('constelacion').cook(force=True)
	return
'''

PAREXEC = r'''# botones del componente
def onPulse(par):
	lib = op('const_lib').module
	if par.name == 'Reiniciar':
		lib.reiniciar()
	elif par.name == 'Marcar':
		lib.marcar(par.owner.par.Textomarca.eval() or 'marca')
	return
def onValueChange(par, prev):
	return
'''

host = me.parent()
old = host.op(NAME)
if old is not None:
	old.destroy()

c = host.create(baseCOMP, NAME)
c.nodeX = me.nodeX + 220
c.nodeY = me.nodeY - 200
setv(c, 'parentshortcut', 'const')
c.color = (0.15, 0.15, 0.15)

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
ipar(pg, 'Puerto', 'Puerto OSC', 9205, 1024, 65535)
ipar(pg, 'Casco', 'Casco (1 = EPOC+, 2 = Insight)', 2, 1, 3)
tpar(pg, 'Activo', 'Activo', True)
pg.appendPulse('Reiniciar', label='Reiniciar tejido')
p = pg.appendStr('Textomarca', label='Texto de la marca')[0]; p.val = 'marca'
pg.appendPulse('Marcar', label='Marcar (solo aquí)')
pg = c.appendCustomPage('Imagen')
fpar(pg, 'Orbita', 'Órbita automática (°/s)', 6, 0, 30)
fpar(pg, 'Exposicion', 'Exposición', 1.0, 0.2, 3)
ipar(pg, 'Hilos', 'Hilos máximos', 1200, 100, 4000)
tpar(pg, 'Rotulos', 'Rótulos, frase y gráfica', True)
ipar(pg, 'Ancho', 'Ancho interno (px)', 600, 200, 1600)
ipar(pg, 'Alto', 'Alto interno (px)', 750, 200, 1600)
ipar(pg, 'Escala', 'Escala de salida', 2, 1, 4)
ipar(pg, 'Cadacuadros', 'Dibujar cada N cuadros', 2, 1, 6)
p = pg.appendStr('Version', label='Versión')[0]; p.val = VERSION; p.readOnly = True

lib = c.create(textDAT, 'const_lib'); lib.text = LIB; place(lib, 0, 0)

cbo = c.create(textDAT, 'cb_osc'); cbo.text = CB_OSC; place(cbo, 0, 1)
osc = c.create(oscinDAT, 'osc'); place(osc, 1, 1)
quitar_callbacks_auto(c, 'osc')
setv(osc, 'port', expr=E + 'Puerto')
setv(osc, 'active', expr=E + 'Activo', opt=True)
setv(osc, 'callbacks', expr="op('cb_osc')")
setv(osc, 'clamp', True, opt=True)
setv(osc, ['maxlines', 'maxrows'], 20, opt=True)

cbi = c.create(textDAT, 'cb_imagen'); cbi.text = CB_IMAGEN; place(cbi, 0, 2)
img = c.create(scriptTOP, 'constelacion'); place(img, 1, 2)
quitar_callbacks_auto(c, 'constelacion')
setv(img, 'callbacks', expr="op('cb_imagen')")
setv(img, ['format', 'pixelformat'], 'rgba8fixed', opt=True)

sal = c.create(nullTOP, 'salida'); place(sal, 2, 2)
wire(img, sal)
setv(sal, 'outputresolution', 'custom')
setv(sal, 'resolutionw', expr=E + 'Ancho*' + E + 'Escala')
setv(sal, 'resolutionh', expr=E + 'Alto*' + E + 'Escala')
setv(sal, 'inputfiltertype', 'linear', opt=True)
o1 = c.create(outTOP, 'out1'); place(o1, 3, 2)
wire(sal, o1)

ex = c.create(executeDAT, 'eeg_cuadro'); ex.text = CB_CUADRO; place(ex, 0, 3)
setv(ex, 'framestart', True)
setv(ex, 'active', True, opt=True)

px = c.create(parameterexecuteDAT, 'botones'); px.text = PAREXEC; place(px, 0, 4)
setv(px, 'op', expr='parent.const')
setv(px, 'pars', 'Reiniciar Marcar')
setv(px, 'custom', True, opt=True); setv(px, 'builtin', False, opt=True)
setv(px, 'onpulse', True, opt=True)

inf = c.create(textDAT, 'eeg_informe'); place(inf, 0, 5)
if FAILS:
	inf.text = 'eeg_constelacion %s · ajustes que no se pudieron aplicar (%d):\n' % (VERSION, len(FAILS)) + '\n'.join(FAILS)
else:
	inf.text = ('eeg_constelacion %s · construido sin avisos. Escuchando OSC en el puerto %d: agrega 127.0.0.1:%d '
	            'como destino en el monitor del puente (modo avanzado → salida OSC).' % (VERSION, c.par.Puerto.eval(), c.par.Puerto.eval()))
print(inf.text)
try:
	c.viewer = True
except Exception:
	pass
