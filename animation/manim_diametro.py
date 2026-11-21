from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ============================================================================
#  PARTE 1 · DATOS Y LÓGICA PURA (sin Manim)
# ============================================================================

SCHEMA = "1.0"


def _search_dirs():
    here = Path(__file__).resolve().parent
    candidates = [Path.cwd(), here, here / "outputs", Path.cwd() / "outputs",
                  here.parent / "outputs"]
    seen, result = set(), []
    for d in candidates:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def resolve_trace_path(env_var, names):
    """Ruta del JSON: variable de entorno > búsqueda por nombre habitual."""
    explicit = os.environ.get(env_var)
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"{env_var}={explicit} no existe.")
        return p
    for d in _search_dirs():
        for name in names:
            p = d / name
            if p.is_file():
                return p
    return None


def load_trace(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema_version") != SCHEMA:
        raise ValueError(f"schema_version {data.get('schema_version')!r} no soportada "
                         f"(se espera {SCHEMA!r}).")
    for key in ("graph", "diameter", "events"):
        if key not in data:
            raise ValueError(f"Falta la clave '{key}' en el JSON.")
    return data


def single_node_trace():
    """Traza idéntica a la que emite el C++ para un árbol de un solo nodo."""
    ev = []

    def phase(ph, k):
        base = len(ev)
        ev.extend([
            {"step": base, "phase": ph, "type": "phase_start", "start": 1},
            {"step": base + 1, "phase": ph, "type": "node_discovered", "node": 1,
             "distance": 0, "parent": None},
            {"step": base + 2, "phase": ph, "type": "node_enqueued", "node": 1},
            {"step": base + 3, "phase": ph, "type": "node_dequeued", "node": 1},
            {"step": base + 4, "phase": ph, "type": "node_finished", "node": 1},
            {"step": base + 5, "phase": ph, "type": "phase_end", "start": 1,
             "farthest_node": 1, "max_distance": 0},
        ])

    phase("bfs_1", 1)
    phase("bfs_2", 2)
    ev.append({"step": 12, "phase": "result", "type": "diameter_path",
               "nodes": [1], "edges": []})
    ev.append({"step": 13, "phase": "result", "type": "diameter_result",
               "endpoints": [1, 1], "length": 0})
    return {
        "schema_version": SCHEMA,
        "algorithm": "tree_diameter_two_bfs",
        "graph": {"type": "undirected_unweighted_tree", "node_count": 1,
                  "nodes": [1], "edges": []},
        "diameter": {"length": 0, "endpoints": [1, 1], "path_nodes": [1],
                     "path_edges": []},
        "events": ev,
        "stats": {"bfs_runs": 2, "events_count": 14},
    }


def tree_layout(trace):
    """
    Posiciones abstractas (x = profundidad, y = ranura vertical).

    El árbol se dibuja con raíz en el extremo `a` del diámetro. Así las columnas
    coinciden con las distancias del BFS 2 y el camino del diámetro queda como una
    línea recta horizontal (cada nodo hereda la altura de su hijo más profundo).
    Devuelve (pos, ancho, alto) con pos[n] = (x, y) y y centrado en 0.
    """
    graph = trace["graph"]
    nodes = list(graph["nodes"])
    adj = {n: [] for n in nodes}
    for e in graph["edges"]:
        adj[e["source"]].append(e["target"])
        adj[e["target"]].append(e["source"])

    root = trace["diameter"]["endpoints"][0]
    parent = {root: None}
    depth = {root: 0}
    children = {n: [] for n in nodes}
    order = [root]
    qi = 0
    while qi < len(order):
        u = order[qi]
        qi += 1
        for v in adj[u]:
            if v not in parent:
                parent[v] = u
                depth[v] = depth[u] + 1
                children[u].append(v)
                order.append(v)
    if len(order) != len(nodes):
        raise ValueError("El grafo del JSON no es conexo.")

    # altura de cada subárbol (iterativo, de las hojas hacia la raíz)
    height = {n: 0 for n in nodes}
    heavy = {n: None for n in nodes}
    for u in reversed(order):
        best = None
        for c in children[u]:
            if best is None or height[c] > height[best]:
                best = c
        if best is not None:
            height[u] = height[best] + 1
            heavy[u] = best

    # el hijo pesado va al centro de la lista de hijos
    for u in nodes:
        ch = children[u]
        if len(ch) > 2 and heavy[u] is not None:
            others = [c for c in ch if c != heavy[u]]
            k = len(others) // 2
            children[u] = others[:k] + [heavy[u]] + others[k:]

    # ranuras: hojas consecutivas; cada nodo toma la ranura de su hijo pesado
    slot = {}
    next_slot = 0
    stack = [(root, 0)]
    while stack:
        u, i = stack.pop()
        if i < len(children[u]):
            stack.append((u, i + 1))
            stack.append((children[u][i], 0))
        elif not children[u]:
            slot[u] = next_slot
            next_slot += 1
        else:
            slot[u] = slot[heavy[u]]

    mid = (next_slot - 1) / 2.0
    pos = {n: (float(depth[n]), -(slot[n] - mid)) for n in nodes}
    return pos, float(max(depth.values())), float(next_slot - 1)


@dataclass
class Step:
    kind: str
    phase: str
    data: dict
    last_step: int


def group_events(events):
    """
    Agrupa los eventos del JSON en pasos de animación:
        edge_examined(new) + node_discovered + node_enqueued  -> examine_new
        node_discovered(origen) + node_enqueued               -> discover_start
    El resto de eventos se reproduce uno a uno.
    """
    evs = sorted(events, key=lambda e: e["step"])
    n = len(evs)
    steps, i = [], 0
    simple = {"phase_start": "phase_start", "node_dequeued": "dequeue",
              "node_finished": "finish", "phase_end": "phase_end",
              "diameter_path": "path", "diameter_result": "result"}
    while i < n:
        e = evs[i]
        t = e["type"]
        ph = e.get("phase", "")
        if t in simple:
            steps.append(Step(simple[t], ph, e, e["step"]))
            i += 1
        elif t == "node_discovered":
            if e.get("parent") is not None:
                raise ValueError(f"node_discovered sin edge_examined previo (step {e['step']}).")
            if i + 1 >= n or evs[i + 1]["type"] != "node_enqueued" or evs[i + 1]["node"] != e["node"]:
                raise ValueError(f"node_discovered sin node_enqueued (step {e['step']}).")
            steps.append(Step("discover_start", ph, e, evs[i + 1]["step"]))
            i += 2
        elif t == "edge_examined":
            outcome = e.get("outcome")
            if outcome == "new":
                if i + 2 >= n:
                    raise ValueError("Traza truncada tras edge_examined(new).")
                d, q = evs[i + 1], evs[i + 2]
                if (d["type"] != "node_discovered" or q["type"] != "node_enqueued"
                        or d["node"] != e["to"] or q["node"] != e["to"]
                        or d["parent"] != e["from"]):
                    raise ValueError(f"Eventos inconsistentes tras edge_examined (step {e['step']}).")
                data = dict(e)
                data["distance"] = d["distance"]
                steps.append(Step("examine_new", ph, data, q["step"]))
                i += 3
            elif outcome == "already_seen":
                steps.append(Step("examine_seen", ph, e, e["step"]))
                i += 1
            else:
                raise ValueError(f"outcome desconocido: {outcome!r}")
        else:
            raise ValueError(f"Evento no soportado: {t!r} (step {e['step']}).")
    return steps


# ============================================================================
#  PARTE 2 · ANIMACIÓN (Manim)
# ============================================================================
# --- fin de la parte pura ---
from manim import (  # noqa: E402
    Scene, config, VGroup, Text, Circle, Line, RoundedRectangle,
    FadeIn, FadeOut, Create, Write, Indicate, LaggedStart, AnimationGroup,
    UP, DOWN, RIGHT,
)

BG = "#0f1117"
config.background_color = BG

C_TXT = "#e5e7eb"
C_DIM = "#9aa3b2"
C_BFS1 = "#4aa3ff"
C_BFS2 = "#3ddc97"
C_PATH = "#ff4d6d"
C_GOLD = "#ffd54a"
C_QUEUED = "#f4c430"
C_EDGE = "#4b5260"
C_EDGE_DIM = "#262b36"
C_EDGE_SEEN = "#ff6b6b"
PHASE_COLOR = {1: C_BFS1, 2: C_BFS2}

# estado -> (relleno, borde, texto)
NODE_STYLES = {
    "unvisited": ("#2a2f3a", "#7a8290", "#e5e7eb"),
    "queued":    ("#f4c430", "#fff1b8", "#10131a"),
    "current":   ("#ff8a2a", "#ffffff", "#10131a"),
    "done1":     ("#4aa3ff", "#cfe6ff", "#10131a"),
    "done2":     ("#3ddc97", "#d0fbe9", "#10131a"),
    "path":      ("#ff4d6d", "#ffffff", "#ffffff"),
    "dim":       ("#1b1f27", "#3b4150", "#5b6270"),
}

# geometría (el marco de Manim mide 14.22 x 8)
POS_TITLE = np.array([0.0, 3.60, 0.0])
POS_SUB = np.array([0.0, 3.05, 0.0])
POS_CNT = np.array([6.25, 3.62, 0.0])
TREE_W, TREE_H, TREE_CX, TREE_CY = 11.6, 3.1, 0.0, 0.40
QX0, QDX, QCAP, QY = -4.0, 0.74, 11, -2.45
POS_CAP = np.array([0.0, -3.35, 0.0])
POS_LEG = np.array([0.0, -3.80, 0.0])
EDGE_W, TREE_EDGE_W, PATH_EDGE_W, EXAM_EDGE_W = 3.0, 6.0, 9.0, 8.0


def _fit(mob, max_width):
    if mob.width > max_width:
        mob.scale_to_fit_width(max_width)
    return mob


def _vec(x, y):
    return np.array([x, y, 0.0])


class _TraceMixin:
    TRACE_ENV = "TRACE_JSON"
    TRACE_NAMES = ("trace.json", "animacion_diametro.json")
    FALLBACK_SINGLE = False

    # ---------------------------------------------------------------- carga
    def _load(self, env, names, fallback_single=False):
        path = resolve_trace_path(env, names)
        if path is None:
            if fallback_single:
                print("[manim_diametro] JSON del caso borde no encontrado; "
                      "se usa la traza interna de 1 nodo.")
                return single_node_trace()
            raise FileNotFoundError(
                f"No encontré el JSON ({', '.join(names)}). Define {env} o ejecuta "
                "desde la carpeta del proyecto.")
        print(f"[manim_diametro] leyendo {path}")
        return load_trace(path)

    def construct(self):
        self.play_trace(self._load(self.TRACE_ENV, self.TRACE_NAMES, self.FALLBACK_SINGLE))

    # ---------------------------------------------------------------- utilidades
    def _t(self, seconds):
        return max(0.05, seconds / self.speed)

    def _play(self, anims, seconds):
        anims = [a for a in anims if a is not None]
        if anims:
            self.play(*anims, run_time=self._t(seconds))
        else:
            self.wait(self._t(seconds))

    def _slot_text(self, name, text, size, color, pos, max_w=13.2):
        old = getattr(self, name, None)
        if old is not None:
            self.remove(old)
        new = Text(text, font_size=size, color=color)
        _fit(new, max_w)
        new.move_to(pos)
        new.set_z_index(10)
        self.add(new)
        setattr(self, name, new)
        return new

    def _drop_slot(self, name):
        old = getattr(self, name, None)
        if old is not None:
            self.remove(old)
        setattr(self, name, None)

    def _paint(self, n, state):
        fill, stroke, txt = NODE_STYLES[state]
        self.nstate[n] = state
        return [
            self.circ[n].animate.set_fill(fill, opacity=1).set_stroke(stroke, width=3.5),
            self.nlab[n].animate.set_color(txt),
        ]

    def _edge_anim(self, eid, color, width, remember=True):
        width = width * self.kw
        if remember:
            self.estyle[eid] = (color, width)
        return self.edge[eid].animate.set_stroke(color, width=width)

    def _edge_restore(self, eid):
        color, width = self.estyle[eid]
        return self.edge[eid].animate.set_stroke(color, width=width)

    # ---------------------------------------------------------------- estado
    def _init_state(self, trace):
        self.trace = trace
        self.nodes = list(trace["graph"]["nodes"])
        self.n = len(self.nodes)
        self.adj = {m: [] for m in self.nodes}
        for e in trace["graph"]["edges"]:
            self.adj[e["source"]].append(e["target"])
            self.adj[e["target"]].append(e["source"])
        self.events = sorted(trace["events"], key=lambda e: e["step"])
        self.total = len(self.events)
        self.steps = group_events(trace["events"])

        env_speed = os.environ.get("TRACE_SPEED")
        if env_speed:
            self.speed = max(0.1, float(env_speed))
        else:
            self.speed = max(1.0, min(10.0, len(self.steps) / 90.0))

        for name in ("caption", "subtitle", "counter", "more_txt", "title", "note"):
            setattr(self, name, None)
        self.nstate = {m: "unvisited" for m in self.nodes}
        self.estyle = {}
        self.dist = {}
        self.marks = []          # anillos y etiquetas de fase (se limpian en cada BFS)
        self.start_tag = None
        self.queue = []          # [(nodo, caja)]
        self.parent = {}
        self.cur_phase = 1

        pos, wx, wy = tree_layout(trace)
        sx = TREE_W / wx if wx > 0 else float("inf")
        sy = TREE_H / wy if wy > 0 else float("inf")
        s = min(sx, sy, 1.9)
        if s == float("inf"):
            s = 1.0
        self.r = 0.55 if self.n == 1 else min(0.42, 0.34 * s)
        self.kw = max(0.35, min(1.0, self.r / 0.3))
        self.fs_id = max(8, int(self.r * 80))
        self.fs_dist = max(8, int(self.r * 52))
        self.pt = {m: _vec(TREE_CX + (pos[m][0] - wx / 2.0) * s, TREE_CY + pos[m][1] * s)
                   for m in self.nodes}
        if self.r < 0.12:
            print("[manim_diametro] Aviso: árbol muy grande; las etiquetas serán diminutas. "
                  "Considera TRACE_SPEED=8 y render en 4K.")

        self.edge, self.circ, self.nlab = {}, {}, {}
        for e in trace["graph"]["edges"]:
            line = Line(self.pt[e["source"]], self.pt[e["target"]],
                        stroke_width=EDGE_W * self.kw, color=C_EDGE)
            line.set_z_index(0)
            self.edge[e["id"]] = line
            self.estyle[e["id"]] = (C_EDGE, EDGE_W * self.kw)
        fill, stroke, txt = NODE_STYLES["unvisited"]
        for m in self.nodes:
            c = Circle(radius=self.r, stroke_width=3.5, fill_opacity=1)
            c.set_fill(fill, opacity=1).set_stroke(stroke, width=3.5)
            c.move_to(self.pt[m])
            c.set_z_index(2)
            lab = Text(str(m), font_size=self.fs_id, color=txt)
            _fit(lab, self.r * 1.55)
            lab.move_to(self.pt[m])
            lab.set_z_index(3)
            self.circ[m], self.nlab[m] = c, lab

    # ---------------------------------------------------------------- cola visual
    def _queue_slot(self, i):
        return _vec(QX0 + min(i, QCAP) * QDX, QY)

    def _new_box(self, n):
        rect = RoundedRectangle(corner_radius=0.1, width=0.62, height=0.62,
                                stroke_width=3, stroke_color=C_QUEUED)
        rect.set_fill("#1d2230", opacity=1)
        num = Text(str(n), font_size=24, color=C_TXT)
        _fit(num, 0.5)
        box = VGroup(rect, num)
        box.set_z_index(6)
        return box

    def _enqueue(self, n):
        idx = len(self.queue)
        box = self._new_box(n)
        box.move_to(self._queue_slot(idx))
        self.queue.append((n, box))
        if idx >= QCAP:
            box.set_opacity(0)
            self.add(box)
            return None
        return FadeIn(box, shift=UP * 0.35)

    def _dequeue(self, n):
        if not self.queue or self.queue[0][0] != n:
            raise ValueError(f"La cola visual no coincide con node_dequeued({n}).")
        _, front = self.queue.pop(0)
        anims = [FadeOut(front, shift=UP * 0.5)]
        for i, (_, box) in enumerate(self.queue):
            anims.append(box.animate.move_to(self._queue_slot(i))
                         .set_opacity(1 if i < QCAP else 0))
        return anims

    def _update_more(self):
        extra = len(self.queue) - QCAP
        if extra > 0:
            self._slot_text("more_txt", f"+{extra}", 22, C_DIM, self._queue_slot(QCAP) + _vec(0.15, 0))
        else:
            self._drop_slot("more_txt")

    # ---------------------------------------------------------------- etiquetas
    def _dist_label(self, n, d):
        t = Text(f"d={d}", font_size=self.fs_dist, color=C_TXT)
        t.set_stroke(BG, width=4, background=True)
        t.move_to(self.pt[n] + _vec(0, -(self.r * 1.3 + 0.07 + t.height / 2)))
        t.set_z_index(6)
        self.dist[n] = t
        return t

    def _tag(self, n, text, color):
        t = Text(text, font_size=max(12, int(self.r * 46)), color=color)
        t.set_stroke(BG, width=4, background=True)
        t.move_to(self.pt[n] + _vec(0, self.r * 1.3 + 0.06 + t.height / 2))
        t.set_z_index(8)
        return t

    def _ring(self, n, color, width=5):
        ring = Circle(radius=self.r * 1.3, stroke_color=color, stroke_width=width, fill_opacity=0)
        ring.move_to(self.pt[n])
        ring.set_z_index(7)
        return ring

    # ---------------------------------------------------------------- escena
    def play_trace(self, trace):
        self._init_state(trace)
        self._intro()
        for st in self.steps:
            self._step(st)
        self.wait(self._t(3.0))

    def _intro(self):
        n = self.n
        title = ("Caso borde · árbol de un solo nodo (n = 1)" if n == 1
                 else "Diámetro de un árbol con dos BFS")
        self.title = Text(title, font_size=32, weight="BOLD", color="#ffffff")
        _fit(self.title, 10.4)
        self.title.move_to(POS_TITLE)

        # leyenda
        items = []
        for label, state in (("Sin visitar", "unvisited"), ("En cola", "queued"),
                             ("Procesando", "current"), ("Terminado BFS 1", "done1"),
                             ("Terminado BFS 2", "done2"), ("Camino del diámetro", "path")):
            dot = Circle(radius=0.09, stroke_width=1.5, fill_opacity=1)
            dot.set_fill(NODE_STYLES[state][0], opacity=1).set_stroke(NODE_STYLES[state][1], width=1.5)
            txt = Text(label, font_size=16, color=C_DIM)
            items.append(VGroup(dot, txt).arrange(RIGHT, buff=0.1))
        legend = VGroup(*items).arrange(RIGHT, buff=0.45)
        _fit(legend, 13.2)
        legend.move_to(POS_LEG)

        # cola
        qlabel = Text("Cola (FIFO)", font_size=22, color=C_QUEUED)
        qlabel.move_to(_vec(-5.7, QY))
        qfront = Text("frente", font_size=15, color=C_DIM)
        qfront.move_to(self._queue_slot(0) + _vec(0, -0.5))
        self.queue_static = VGroup(qlabel, qfront)

        self.play(Write(self.title), run_time=self._t(1.2))

        edge_list = list(self.edge.values())
        lag = min(0.3, 3.0 / max(1, len(self.nodes)))
        node_anims = [AnimationGroup(FadeIn(self.circ[m]), FadeIn(self.nlab[m]))
                      for m in self.nodes]
        if edge_list:
            self.play(LaggedStart(*[Create(l) for l in edge_list], lag_ratio=lag),
                      run_time=self._t(1.2))
        self.play(LaggedStart(*node_anims, lag_ratio=lag), run_time=self._t(1.2))
        self.play(FadeIn(legend), FadeIn(self.queue_static), run_time=self._t(0.6))

        if n == 1:
            self._slot_text("note",
                            "Sin aristas: ambos BFS empiezan y terminan en el mismo nodo",
                            22, C_GOLD, _vec(0, -1.25))
        self._slot_text("caption",
                        f"Árbol no dirigido y no ponderado · n = {n} nodos, {n - 1} aristas · "
                        "distancia = número de aristas",
                        22, C_DIM, POS_CAP)
        self.wait(self._t(1.2))

    # ---------------------------------------------------------------- pasos
    def _step(self, st):
        self._slot_text("counter", f"evento {st.last_step}/{self.total - 1}", 15, C_DIM, POS_CNT)
        handler = getattr(self, "_on_" + st.kind)
        handler(st)

    def _cap(self, text, color=C_TXT):
        self._slot_text("caption", text, 24, color, POS_CAP)

    def _on_phase_start(self, st):
        k = 1 if st.phase == "bfs_1" else 2
        s = st.data["start"]
        self.cur_phase = k
        self.parent = {}
        sub = (f"BFS 1 · origen s = {s}" if k == 1
               else f"BFS 2 · origen a = {s} (el más lejano de BFS 1)")
        self._slot_text("subtitle", sub, 26, PHASE_COLOR[k], POS_SUB)
        self._cap(f"Comienza BFS {k} en el nodo {s}: se parte de distancia 0")

        anims = []
        for m in self.nodes:
            if self.nstate[m] != "unvisited":
                anims += self._paint(m, "unvisited")
        for eid in self.edge:
            if self.estyle[eid] != (C_EDGE, EDGE_W * self.kw):
                anims.append(self._edge_anim(eid, C_EDGE, EDGE_W))
        for mob in list(self.dist.values()) + self.marks:
            anims.append(FadeOut(mob))
        for _, box in self.queue:
            anims.append(FadeOut(box))
        if self.start_tag is not None:
            anims.append(FadeOut(self.start_tag))
        self.dist, self.marks, self.queue, self.start_tag = {}, [], [], None
        self._drop_slot("more_txt")
        self._play(anims, 0.7)

    def _on_discover_start(self, st):
        s = st.data["node"]
        self.parent[s] = None
        self._cap(f"Se descubre {s} con d = 0 y se encola")
        tag_text = "inicio" if self.cur_phase == 1 else "inicio (a)"
        self.start_tag = self._tag(s, tag_text, "#ffffff")
        anims = self._paint(s, "queued")
        anims += [FadeIn(self._dist_label(s, 0)), FadeIn(self.start_tag), self._enqueue(s)]
        self._update_more()
        self._play(anims, 0.6)

    def _on_dequeue(self, st):
        u = st.data["node"]
        has_nb = len(self.adj[u]) > 0
        self._cap(f"Sacar {u} de la cola" +
                  (": examinar sus vecinos" if has_nb else ": no tiene vecinos"))
        anims = self._paint(u, "current") + self._dequeue(u)
        self._update_more()
        self._play(anims, 0.5)

    def _on_examine_new(self, st):
        d = st.data
        u, v, eid, dist = d["from"], d["to"], d["edge_id"], d["distance"]
        self.parent[v] = u
        self._cap(f"Arista {u}–{v} nueva: d({v}) = d({u}) + 1 = {dist}, "
                  f"padre({v}) = {u}; se encola {v}")
        self._play([self._edge_anim(eid, C_GOLD, EXAM_EDGE_W, remember=False)], 0.25)
        anims = [self._edge_anim(eid, PHASE_COLOR[self.cur_phase], TREE_EDGE_W)]
        anims += self._paint(v, "queued")
        anims += [FadeIn(self._dist_label(v, dist)), self._enqueue(v)]
        self._update_more()
        self._play(anims, 0.55)

    def _on_examine_seen(self, st):
        d = st.data
        u, v, eid = d["from"], d["to"], d["edge_id"]
        why = f"es el padre de {u}" if self.parent.get(u) == v else "ya visitado"
        self._cap(f"Arista {u}–{v}: {v} ya fue visto ({why}); se ignora")
        self._play([self._edge_anim(eid, C_EDGE_SEEN, EXAM_EDGE_W - 2, remember=False)], 0.25)
        self._play([self._edge_restore(eid)], 0.2)

    def _on_finish(self, st):
        u = st.data["node"]
        self._cap(f"{u} terminado")
        self._play(self._paint(u, "done1" if self.cur_phase == 1 else "done2"), 0.3)

    def _on_phase_end(self, st):
        d = st.data
        k = self.cur_phase
        s, f, dist = d["start"], d["farthest_node"], d["max_distance"]
        if k == 1:
            self._cap(f"Fin de BFS 1: el más lejano de {s} es {f} (d = {dist}) → extremo a = {f}",
                      C_GOLD)
        else:
            self._cap(f"Fin de BFS 2: el más lejano de a = {s} es b = {f}; "
                      f"d(a, b) = {dist} aristas = diámetro", C_GOLD)
        ring = self._ring(f, C_GOLD)
        far_tag = "a" if k == 1 else "b"
        anims = [Create(ring), Indicate(self.circ[f], scale_factor=1.25, color="#ffffff")]
        if f == s and self.start_tag is not None:
            self.remove(self.start_tag)
            start_txt = "inicio" if k == 1 else "inicio (a)"
            tag = self._tag(f, f"{start_txt} = {far_tag}", C_GOLD)
            self.start_tag = None
        else:
            tag = self._tag(f, far_tag, C_GOLD)
        anims.append(FadeIn(tag))
        self.marks += [ring, tag]
        self._play(anims, 0.9)
        self.wait(self._t(1.1))

    def _on_path(self, st):
        d = st.data
        nodes, edges = d["nodes"], d["edges"]
        on_path = set(nodes)
        a, b = nodes[0], nodes[-1]
        self._slot_text("subtitle", "Resultado · camino del diámetro", 26, C_PATH, POS_SUB)
        self._cap(f"Reconstrucción: desde b = {b} se siguen los padres de BFS 2 hasta a = {a}",
                  C_TXT)

        # atenuar lo que no pertenece al camino
        path_edges = set(edges)
        anims = []
        for m in self.nodes:
            if m not in on_path:
                anims += self._paint(m, "dim")
        for eid in self.edge:
            if eid not in path_edges:
                anims.append(self._edge_anim(eid, C_EDGE_DIM, EDGE_W))
        for m, lab in list(self.dist.items()):
            if m not in on_path:
                anims.append(FadeOut(lab))
                del self.dist[m]
        self._play(anims, 0.8)

        # recorrer el camino en el orden recibido (a → b)
        rt = max(0.08, min(0.45, 6.0 / max(1, len(nodes))))
        for i, m in enumerate(nodes):
            anims = self._paint(m, "path")
            if i > 0:
                anims.append(self._edge_anim(edges[i - 1], C_PATH, PATH_EDGE_W))
            self._play(anims, rt)
        if len(nodes) <= 40:
            self.play(LaggedStart(*[Indicate(self.circ[m], scale_factor=1.2, color="#ffffff")
                                    for m in nodes], lag_ratio=0.15),
                      run_time=self._t(1.0))

    def _on_result(self, st):
        d = st.data
        length, (ea, eb) = d["length"], d["endpoints"]
        nodes = self.trace["diameter"]["path_nodes"]
        names = [str(m) for m in nodes]
        if len(names) > 12:
            names = names[:5] + ["…"] + names[-5:]
        path_txt = " → ".join(names) if len(nodes) > 1 else f"[{nodes[0]}] (un solo nodo)"
        unit = "arista" if length == 1 else "aristas"
        big = Text(f"Diámetro = {length} {unit}", font_size=38, weight="BOLD", color=C_PATH)
        small = Text(f"Extremos: {ea} y {eb}   ·   camino: {path_txt}", font_size=24, color=C_TXT)
        panel = VGroup(big, small).arrange(DOWN, buff=0.18)
        _fit(panel, 13.0)
        panel.move_to(_vec(0, QY + 0.15))
        panel.set_z_index(10)
        if self.n == 1:
            self._cap("Caso borde: con n = 1 no hay aristas, ambos BFS visitan solo el nodo 1 "
                      "→ diámetro 0", C_GOLD)
        else:
            self._cap(f"Diámetro = longitud del camino a → b = d(a, b) en BFS 2", C_GOLD)
        self._drop_slot("more_txt")
        self._play([FadeOut(self.queue_static), FadeIn(panel, shift=UP * 0.3)], 1.0)
        self.wait(self._t(2.0))


# ============================================================================
#  ESCENAS
# ============================================================================

class DiametroArbol(_TraceMixin, Scene):
    """Árbol principal: BFS 1, BFS 2 y camino del diámetro resaltado."""
    TRACE_ENV = "TRACE_JSON"
    TRACE_NAMES = ("trace.json", "animacion_diametro.json")
    FALLBACK_SINGLE = False


class CasoBorde(_TraceMixin, Scene):
    """Caso borde: árbol con un solo nodo (diámetro 0)."""
    TRACE_ENV = "TRACE_EDGE_JSON"
    TRACE_NAMES = ("trace_un_nodo.json", "animacion_un_nodo.json")
    FALLBACK_SINGLE = True


class VideoCompleto(_TraceMixin, Scene):
    """Árbol principal seguido del caso borde, en un solo video."""

    def construct(self):
        main = self._load("TRACE_JSON", ("trace.json", "animacion_diametro.json"))
        edge = self._load("TRACE_EDGE_JSON", ("trace_un_nodo.json", "animacion_un_nodo.json"),
                          fallback_single=True)
        self.play_trace(main)
        self.play(FadeOut(*self.mobjects), run_time=0.8)
        self.wait(0.3)
        self.play_trace(edge)


# ============================================================================
#  AYUDANTE DE LÍNEA DE COMANDOS
# ============================================================================

def _cli():
    ap = argparse.ArgumentParser(description="Renderiza la animación del diámetro con Manim.")
    ap.add_argument("--scene", default="DiametroArbol",
                    choices=["DiametroArbol", "CasoBorde", "VideoCompleto"])
    ap.add_argument("--quality", default="h", choices=["l", "m", "h", "p", "k"],
                    help="l=480p15, m=720p30, h=1080p60, p=1440p60, k=2160p60 (4K)")
    ap.add_argument("--json", help="JSON del árbol principal")
    ap.add_argument("--edge-json", help="JSON del caso borde")
    ap.add_argument("--speed", type=float, help="multiplicador de velocidad")
    ap.add_argument("--preview", action="store_true", help="abrir el video al terminar")
    args = ap.parse_args()

    env = os.environ.copy()
    if args.json:
        key = "TRACE_EDGE_JSON" if args.scene == "CasoBorde" else "TRACE_JSON"
        env[key] = str(Path(args.json).resolve())
    if args.edge_json:
        env["TRACE_EDGE_JSON"] = str(Path(args.edge_json).resolve())
    if args.speed:
        env["TRACE_SPEED"] = str(args.speed)

    cmd = [sys.executable, "-m", "manim", "render", f"-q{args.quality}", "--format=mp4"]
    if args.preview:
        cmd.append("-p")
    cmd += [str(Path(__file__).resolve()), args.scene]
    print("$", " ".join(cmd))
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    _cli()
