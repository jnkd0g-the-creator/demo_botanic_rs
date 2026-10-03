"""Детерминированное демонстрационное облако Pisum sativum, координаты в мм."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ORGAN_NAMES = ("Стебель", "Листочки", "Прилистники", "Усики", "Бобы", "Цветки")
ORGAN_COLORS = np.array(((109, 167, 64), (72, 149, 81), (91, 165, 100),
                         (161, 192, 100), (128, 181, 65), (225, 204, 236)), dtype=np.uint8)


@dataclass(frozen=True)
class PeaCloud:
    points: np.ndarray
    colors: np.ndarray
    organs: np.ndarray
    metrics: dict


def _unit(vector):
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


class _Plant:
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.points, self.colors, self.organs = [], [], []
        self.counts = dict(nodes=12, leaflets=0, stipules=0, tendrils=0, pods=0, flowers=0)
        self.leaf_area = 0.0

    def add(self, points, organ, brightness=None):
        points = np.asarray(points).reshape(-1, 3)
        colors = np.tile(ORGAN_COLORS[organ].astype(float), (len(points), 1))
        colors += self.rng.normal(0, 7, (len(points), 1))
        if brightness is not None:
            colors *= np.asarray(brightness).reshape(-1, 1)
        self.points.append(points + self.rng.normal(0, 0.18, points.shape))
        self.colors.append(np.clip(colors, 0, 255).astype(np.uint8))
        self.organs.append(np.full(len(points), organ, dtype=np.uint8))

    def tube(self, path, radius, organ=0, sides=10, flatten=1.0):
        path = np.asarray(path)
        tangent = np.gradient(path, axis=0)
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-8)
        reference = np.tile([0., 0., 1.], (len(path), 1))
        reference[np.abs(tangent[:, 2]) > 0.9] = [1., 0., 0.]
        u = np.cross(tangent, reference)
        u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-8)
        v = np.cross(tangent, u)
        angle = np.arange(sides) * 2 * np.pi / sides
        radius = np.broadcast_to(np.asarray(radius), (len(path),))
        points = (path[:, None, :] + radius[:, None, None]
                  * (u[:, None, :] * np.cos(angle)[None, :, None]
                     + v[:, None, :] * np.sin(angle)[None, :, None] * flatten))
        brightness = np.broadcast_to(0.85 + 0.2 * np.cos(angle), (len(path), sides))
        self.add(points, organ, brightness.ravel())

    @staticmethod
    def stem(t):
        t = np.asarray(t)
        return np.stack((14 * np.sin(t * 7) + 20 * t,
                         12 * np.sin(t * 9) - 8 * t, 1240 * t), axis=-1)

    def leaf(self, base, direction, length, width, organ=1):
        u = _unit(np.asarray(direction))
        v = _unit(np.cross(u, [0, 0, 1]))
        t = self.rng.uniform(0, 1, 480 if organ == 1 else 360)
        side = self.rng.uniform(-1, 1, len(t))
        contour = np.sin(np.pi * t) ** 0.8
        points = base + t[:, None] * length * u + (side * contour * width)[:, None] * v
        points[:, 2] += 9 * np.sin(np.pi * t) * (1 - side * side) - 4 * side * side
        self.add(points, organ, 0.93 + 0.20 * (1 - np.abs(side)))
        vein_t = np.linspace(0, 1, 55)
        vein = base + vein_t[:, None] * length * u
        vein[:, 2] += 9 * np.sin(np.pi * vein_t) + 0.4
        self.tube(vein, np.linspace(0.65, 0.12, len(vein)), organ, sides=4)
        self.counts["leaflets" if organ == 1 else "stipules"] += 1
        self.leaf_area += np.pi * length * width / 2

    def tendril(self, base, direction, length):
        u = _unit(direction)
        v = _unit(np.cross(u, [0, 0, 1]))
        w = np.cross(u, v)
        t = np.linspace(0, 1, 110)
        curl = np.clip((t - 0.5) * 2, 0, 1)
        phase = curl * np.pi * 4.5
        radius = 10 * np.sin(curl * np.pi / 2)
        path = (base + (t * length)[:, None] * u
                + (radius * np.sin(phase))[:, None] * v
                + (radius * (1 - np.cos(phase)))[:, None] * w)
        self.tube(path, np.linspace(0.9, 0.3, len(t)), 3, sides=6)
        self.counts["tendrils"] += 1

    def pod(self, base, radial, length):
        t = np.linspace(0, 1, 110)
        path = base + (15 * t + 10 * np.sin(np.pi * t))[:, None] * radial
        path[:, 2] -= length * t
        radii = (6.5 + 0.85 * np.sin(t * np.pi * 12)) * np.sin(np.pi * t) ** 0.55 + 0.25
        self.tube(path, radii, 4, sides=16, flatten=0.65)
        self.counts["pods"] += 1

    def petal(self, center, radial, radii, count=370):
        normal = self.rng.normal(size=(count, 3))
        normal /= np.linalg.norm(normal, axis=1, keepdims=True)
        side = _unit(np.cross(radial, [0, 0, 1]))
        basis = np.array([side, radial, [0, 0, 1]])
        self.add(center + (normal * radii) @ basis, 5, 0.95 + 0.12 * normal[:, 2])

    def flower(self, base, radial):
        self.petal(base + [0, 0, 9], radial, [13, 3, 16])
        side = _unit(np.cross(radial, [0, 0, 1]))
        for sign in (-1, 1):
            self.petal(base + sign * 8 * side + 3 * radial, radial, [7, 5, 9])
        self.petal(base + 7 * radial - [0, 0, 5], radial, [5, 6, 7])
        self.counts["flowers"] += 1

    def build(self):
        t = np.linspace(0, 1, 480)
        stem = self.stem(t)
        self.tube(stem, 3.0 - 1.8 * t, sides=14)
        stem_length = float(np.linalg.norm(np.diff(stem, axis=0), axis=1).sum())
        for i, level in enumerate(np.linspace(0.105, 0.91, self.counts["nodes"])):
            base = self.stem(level)
            angle = i * 2.39996 + 0.25 * np.sin(i)
            radial = np.array([np.cos(angle), np.sin(angle), 0.])
            side = np.array([-np.sin(angle), np.cos(angle), 0.])
            size = 0.68 + 0.32 * np.sin(np.pi * (i + 1) / 13)
            length = 128 * size
            p = np.linspace(0, 1, 75)
            branch = base + (p * length)[:, None] * radial
            branch[:, 2] += p * (25 + i * 2) + 12 * np.sin(p * np.pi)
            self.tube(branch, np.linspace(1.8, 0.75, len(p)), sides=8)
            for sign in (-1, 1):
                self.leaf(base + sign * 2 * side, radial * -0.18 + side * sign + [0, 0, -0.28],
                          62 * size, 29 * size, organ=2)
            for fraction in (0.40, 0.78):
                start = branch[round(fraction * (len(branch) - 1))]
                for sign in (-1, 1):
                    direction = radial * 0.45 + sign * side * 0.9 + [0, 0, 0.10 + 0.05 * i]
                    self.leaf(start, direction, (78 - 10 * fraction) * size, 24 * size)
            for sign in (-1, 0, 1):
                direction = radial * 0.75 + side * sign * 0.5 + [0, 0, 0.65]
                self.tendril(branch[-1], direction, (88 if sign else 115) * size)
            if 3 <= i <= 7:
                for j in range(1 if i == 7 else 2):
                    anchor = branch[48 + j * 16]
                    stalk_end = anchor + side * (14 if j else -14) + [0, 0, -20]
                    self.tube(np.linspace(anchor, stalk_end, 22), 0.85, sides=7)
                    self.pod(stalk_end, radial, 80 + 8 * j)
            if i >= 8:
                anchor = branch[58]
                tip = anchor + 22 * side + [0, 0, 24]
                self.tube(np.linspace(anchor, tip, 22), 0.8, sides=7)
                self.flower(tip, radial)
        points = np.concatenate(self.points).astype(np.float32)
        points[:, 2] = np.maximum(points[:, 2], 0)
        organs = np.concatenate(self.organs)
        metrics = {**self.counts, "height_cm": round(float(points[:, 2].max()) / 10, 1),
                   "stem_length_cm": round(stem_length / 10, 1),
                   "leaf_area_cm2": round(float(self.leaf_area) / 100, 1),
                   "lower_pod_height_cm": round(float(points[organs == 4, 2].min()) / 10, 1),
                   "productive_nodes": 5,
                   "seeds": 54, "grain_mass_g": 12.6}
        return PeaCloud(points, np.concatenate(self.colors), organs, metrics)


def make_pea_cloud(seed: int = 42) -> PeaCloud:
    return _Plant(seed).build()
