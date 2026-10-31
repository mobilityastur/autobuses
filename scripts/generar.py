#!/usr/bin/env python3
"""Autobuses Asturias · genera datos/horarios.json a partir de los GTFS del NAP.

Fuentes (Punto de Acceso Nacional, Ministerio de Transportes):
  - ALSA (fichero 1133): se quedan las líneas dentro de Asturias y Oviedo–Madrid.
  - Consorcio de Transportes de Asturias (fichero 1178): toda la red.

Uso en GitHub Actions:   NAP_API_KEY=... python3 scripts/generar.py _site/datos
Uso local con ficheros:  python3 scripts/generar.py salida --zip alsa=alsa.zip --zip cta=cta.zip
"""
import argparse, csv, io, json, math, os, re, sys, unicodedata, urllib.request, zipfile
import datetime as dt
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
DIAS = 52  # días consultables desde ayer (máximo 52: cada día es un bit de un entero de JavaScript)

FUENTES = {
    "cta":  {"nap": 1178, "nombre": "Consorcio de Transportes de Asturias"},
    "alsa": {"nap": 1133, "nombre": "ALSA"},
}

ASTURIAS = (42.85, 43.70, -7.20, -4.50)   # lat min, lat max, lon min, lon max
OVIEDO   = (43.33, 43.40, -5.90, -5.80)
MADRID   = (40.30, 40.56, -3.85, -3.50)   # incluye Barajas

def dentro(caja, lat, lon):
    return caja[0] <= lat <= caja[1] and caja[2] <= lon <= caja[3]

# ---------- descarga ----------

def descargar_nap(fid, clave):
    url = f"https://nap.transportes.gob.es/api/Fichero/download/{fid}"
    req = urllib.request.Request(url, headers={
        "ApiKey": clave, "User-Agent": "autobuses-asturias (mobilityastur)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        datos = r.read()
    if not datos.startswith(b"PK"):
        raise RuntimeError(f"El NAP no devolvió un ZIP para {fid}: {datos[:200]!r}")
    return datos

def filas(z, nombre):
    if nombre not in z.namelist():
        return
    with z.open(nombre) as f:
        texto = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace", newline="")
        for fila in csv.DictReader(texto):
            yield {(k or "").strip(): (v or "").strip() for k, v in fila.items()}

# ---------- nombres ----------

MENORES = {"de", "del", "la", "las", "los", "el", "y", "a", "en", "con", "por", "al"}
SIGLAS = {"ii", "iii", "iv", "huca", "fp", "ies", "cp", "pk", "n-634", "n-630", "alsa", "t4", "t1", "t2"}

def bonito(n):
    n = re.sub(r"\s+", " ", n).strip(" -")
    if not n or (not n.isupper() and any(c.islower() for c in n)):
        return n
    partes = []
    for i, p in enumerate(n.lower().split(" ")):
        if p in SIGLAS:
            partes.append(p.upper())
        elif i and p in MENORES:
            partes.append(p)
        else:
            partes.append("-".join(s[:1].upper() + s[1:] for s in p.split("-")))
    return " ".join(partes).replace("( ", "(").replace(" )", ")")

def clave_texto(n):
    n = unicodedata.normalize("NFD", n.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()

# ---------- calendario ----------

def mascaras(z, desde):
    """service_id -> entero con un bit por día (bit 0 = desde)."""
    claves = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    dias = [desde + dt.timedelta(days=i) for i in range(DIAS)]
    m = defaultdict(int)
    for r in filas(z, "calendar.txt"):
        try:
            ini = dt.datetime.strptime(r["start_date"], "%Y%m%d").date()
            fin = dt.datetime.strptime(r["end_date"], "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        for i, d in enumerate(dias):
            if ini <= d <= fin and r.get(claves[d.weekday()]) == "1":
                m[r["service_id"]] |= 1 << i
    pos = {d.strftime("%Y%m%d"): i for i, d in enumerate(dias)}
    for r in filas(z, "calendar_dates.txt"):
        i = pos.get(r.get("date"))
        if i is None:
            continue
        if r.get("exception_type") == "1":
            m[r["service_id"]] |= 1 << i
        else:
            m[r["service_id"]] &= ~(1 << i)
    return m

def minutos(t):
    try:
        h, mm = t.split(":")[:2]
        return int(h) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None

# ---------- lectura de un feed ----------

def leer_feed(clave, z, desde):
    paradas = {}
    for r in filas(z, "stops.txt"):
        if r.get("location_type") not in ("", "0", None):
            continue
        try:
            paradas[r["stop_id"]] = (r.get("stop_name") or r["stop_id"], float(r["stop_lat"]), float(r["stop_lon"]))
        except (KeyError, ValueError):
            pass

    agencias = {r.get("agency_id", ""): r.get("agency_name", "") for r in filas(z, "agency.txt")}
    agencia_unica = next(iter(agencias.values()), FUENTES[clave]["nombre"]) if len(agencias) == 1 else None
    rutas = {}
    for r in filas(z, "routes.txt"):
        rutas[r["route_id"]] = {
            "corto": r.get("route_short_name", ""),
            "largo": r.get("route_long_name", ""),
            "color": r.get("route_color", "").lstrip("#")[:6],
            "texto": r.get("route_text_color", "").lstrip("#")[:6],
            "operador": agencias.get(r.get("agency_id", ""), agencia_unica or FUENTES[clave]["nombre"]),
        }

    serv = mascaras(z, desde)
    viajes = {}
    for r in filas(z, "trips.txt"):
        m = serv.get(r.get("service_id"), 0)
        if m and r.get("route_id") in rutas:
            viajes[r["trip_id"]] = {"ruta": r["route_id"], "mascara": m,
                                    "letrero": r.get("trip_headsign", ""), "paradas": []}

    for r in filas(z, "stop_times.txt"):
        v = viajes.get(r.get("trip_id"))
        if v is None or r.get("stop_id") not in paradas:
            continue
        t = minutos(r.get("departure_time")) if r.get("departure_time") else minutos(r.get("arrival_time"))
        try:
            seq = int(r.get("stop_sequence", "0"))
        except ValueError:
            seq = 0
        # 1 = se puede subir, 2 = se puede bajar (pickup_type / drop_off_type = 1 lo prohíbe)
        perm = (0 if r.get("pickup_type") == "1" else 1) | (0 if r.get("drop_off_type") == "1" else 2)
        v["paradas"].append((seq, r["stop_id"], t, perm))

    salida = []
    for tid, v in viajes.items():
        ps = sorted(v["paradas"])
        # rellenar horas vacías (paradas intermedias sin hora) por interpolación simple
        conocidos = [i for i, p in enumerate(ps) if p[2] is not None]
        if len(ps) < 2 or len(conocidos) < 2:
            continue
        horas = [p[2] for p in ps]
        for a, b in zip(conocidos, conocidos[1:]):
            for k in range(a + 1, b):
                horas[k] = round(horas[a] + (horas[b] - horas[a]) * (k - a) / (b - a))
        ps = [(sid, h, perm) for (_, sid, _, perm), h in zip(ps, horas) if h is not None]
        salida.append({"fuente": clave, "ruta": v["ruta"], "mascara": v["mascara"],
                       "letrero": v["letrero"], "paradas": ps})
    return paradas, rutas, salida

def filtrar_alsa(paradas, viajes, cuenta):
    quedan = []
    for v in viajes:
        pos = [paradas[s][1:] for s, *_ in v["paradas"]]
        if all(dentro(ASTURIAS, *p) for p in pos):
            quedan.append(v); cuenta["alsa_asturias"] += 1
        elif any(dentro(OVIEDO, *p) for p in pos) and any(dentro(MADRID, *p) for p in pos):
            quedan.append(v); cuenta["alsa_oviedo_madrid"] += 1
        elif any(dentro(ASTURIAS, *p) for p in pos):
            cuenta["alsa_descartados_que_pasan_por_asturias"] += 1
    return quedan

# ---------- unificar paradas de las dos fuentes ----------

def distancia(a, b):
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)

def unificar(paradas_usadas):
    """paradas_usadas: {(fuente, stop_id): (nombre, lat, lon)} -> {(fuente, stop_id): grupo}"""
    claves = list(paradas_usadas)
    padre = list(range(len(claves)))
    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i
    celdas = defaultdict(list)
    for i, k in enumerate(claves):
        _, lat, lon = paradas_usadas[k]
        celdas[(int(lat * 250), int(lon * 180))].append(i)   # celdas de ~450 m
    for (cy, cx), lista in celdas.items():
        vecinos = [j for dy in (-1, 0, 1) for dx in (-1, 0, 1) for j in celdas.get((cy + dy, cx + dx), [])]
        for i in lista:
            ni, lai, loi = paradas_usadas[claves[i]]
            for j in vecinos:
                if j <= i:
                    continue
                nj, laj, loj = paradas_usadas[claves[j]]
                d = distancia((lai, loi), (laj, loj))
                if d < 40 or (d < 250 and clave_texto(ni) == clave_texto(nj)):
                    padre[raiz(i)] = raiz(j)
    return {k: raiz(i) for i, k in enumerate(claves)}

# ---------- principal ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("salida")
    ap.add_argument("--zip", action="append", default=[], help="fuente=ruta.zip (pruebas)")
    ap.add_argument("--desde", help="AAAAMMDD (pruebas)")
    args = ap.parse_args()

    hoy = dt.datetime.now(TZ).date()
    desde = dt.datetime.strptime(args.desde, "%Y%m%d").date() if args.desde else hoy - dt.timedelta(days=1)
    locales = dict(x.split("=", 1) for x in args.zip)
    clave_api = os.environ.get("NAP_API_KEY", "").strip()

    todas_paradas, todas_rutas, todos_viajes, avisos, resumen = {}, {}, [], [], {}
    cuenta = Counter()
    for clave, info in FUENTES.items():
        try:
            if clave in locales:
                datos = open(locales[clave], "rb").read()
            elif clave_api:
                print(f"Descargando {info['nombre']} (NAP {info['nap']})…", flush=True)
                datos = descargar_nap(info["nap"], clave_api)
            else:
                raise RuntimeError("falta NAP_API_KEY")
            z = zipfile.ZipFile(io.BytesIO(datos))
            paradas, rutas, viajes = leer_feed(clave, z, desde)
            total = len(viajes)
            if clave == "alsa":
                cuenta["alsa_total"] = total
                viajes = filtrar_alsa(paradas, viajes, cuenta)
            print(f"  {info['nombre']}: {len(viajes)} de {total} viajes con servicio", flush=True)
            resumen[clave] = len(viajes)
            for k, v in paradas.items():
                todas_paradas[(clave, k)] = v
            for k, v in rutas.items():
                todas_rutas[(clave, k)] = v
            todos_viajes += viajes
        except Exception as e:  # una fuente caída no impide publicar la otra
            print(f"  ¡Error con {info['nombre']}!: {e}", file=sys.stderr, flush=True)
            avisos.append(f"No se pudieron actualizar los horarios de {info['nombre']}.")

    if not todos_viajes:
        sys.exit("No hay ningún horario: no se publica nada nuevo.")

    usadas = {(v["fuente"], s): todas_paradas[(v["fuente"], s)] for v in todos_viajes for s, *_ in v["paradas"]}
    grupo = unificar(usadas)

    # nombre y posición de cada parada unificada: el nombre más repetido, la media de posiciones
    por_grupo = defaultdict(list)
    for k, g in grupo.items():
        por_grupo[g].append(usadas[k])
    orden = sorted(por_grupo, key=lambda g: clave_texto(Counter(n for n, _, _ in por_grupo[g]).most_common(1)[0][0]))
    idx_grupo = {g: i for i, g in enumerate(orden)}
    paradas_out = []
    for g in orden:
        miembros = por_grupo[g]
        nombre = bonito(Counter(n for n, _, _ in miembros).most_common(1)[0][0])
        lat = sum(m[1] for m in miembros) / len(miembros)
        lon = sum(m[2] for m in miembros) / len(miembros)
        paradas_out.append([nombre, round(lat, 5), round(lon, 5)])

    lineas_out, idx_linea = [], {}
    patrones_out, idx_patron = [], {}
    viajes_out, vistos = [], {}
    prioridad = {"cta": 0, "alsa": 1}  # ante un duplicado, gana el del Consorcio
    for v in sorted(todos_viajes, key=lambda v: prioridad[v["fuente"]]):
        r = todas_rutas[(v["fuente"], v["ruta"])]
        # quitar paradas repetidas seguidas (dos andenes de la misma estación)
        secuencia = []
        for s, t, perm in v["paradas"]:
            g = idx_grupo[grupo[(v["fuente"], s)]]
            if secuencia and secuencia[-1][0] == g:
                secuencia[-1][2] |= perm
                continue
            secuencia.append([g, t, perm])
        if len(secuencia) < 2:
            continue
        inicio = secuencia[0][1]
        stops = tuple(g for g, _, _ in secuencia)
        desfases = tuple(t - inicio for _, t, _ in secuencia)
        # permisos como texto ("3" = subir y bajar); vacío si todo está permitido
        permisos = "".join(str(pm) for _, _, pm in secuencia)
        if set(permisos) == {"3"}:
            permisos = ""
        clave_dup = (stops[0], stops[-1], inicio, inicio + desfases[-1])
        # solo se descartan duplicados entre fuentes: dentro de una misma fuente, cada viaje cuenta
        previo = vistos.get((clave_dup, "otra"))
        if v["fuente"] != "cta" and previo is not None and (v["mascara"] & ~previo) == 0:
            cuenta["duplicados_quitados"] += 1
            continue  # ya está el mismo viaje en el Consorcio
        if v["fuente"] == "cta":
            vistos[(clave_dup, "otra")] = (previo or 0) | v["mascara"]
        kl = (r["corto"], r["largo"], r["operador"])
        if kl not in idx_linea:
            idx_linea[kl] = len(lineas_out)
            lineas_out.append([bonito(r["corto"]) if len(r["corto"]) > 4 else r["corto"],
                               bonito(r["largo"]), r["color"], r["texto"], bonito(r["operador"])])
        kp = (idx_linea[kl], stops, desfases, bonito(v["letrero"]), permisos)
        if kp not in idx_patron:
            idx_patron[kp] = len(patrones_out)
            patrones_out.append([kp[0], list(stops), list(desfases), kp[3]] + ([permisos] if permisos else []))
        viajes_out.append([idx_patron[kp], inicio, v["mascara"]])

    viajes_out.sort(key=lambda x: (x[0], x[1]))
    ahora = dt.datetime.now(TZ)
    datos = {
        "generado": ahora.isoformat(timespec="minutes"),
        "desde": desde.strftime("%Y%m%d"),
        "dias": DIAS,
        "avisos": avisos,
        "paradas": paradas_out,
        "lineas": lineas_out,
        "patrones": patrones_out,
        "viajes": viajes_out,
    }
    os.makedirs(args.salida, exist_ok=True)
    with open(os.path.join(args.salida, "horarios.json"), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.salida, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump({"generado": datos["generado"], "viajes_por_fuente": resumen, "detalle": dict(cuenta), "avisos": avisos,
                   "paradas": len(paradas_out), "lineas": len(lineas_out),
                   "patrones": len(patrones_out), "viajes": len(viajes_out)}, f, ensure_ascii=False, indent=1)
    print(f"Listo: {len(paradas_out)} paradas, {len(lineas_out)} líneas, {len(viajes_out)} viajes.")

if __name__ == "__main__":
    main()
