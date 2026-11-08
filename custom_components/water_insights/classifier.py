"""Small numpy-only classifiers with JSON-serializable state."""
from __future__ import annotations

from typing import Any

import numpy as np


class _Scaler:
    def __init__(self, mean: np.ndarray | None = None, std: np.ndarray | None = None):
        self.mean = mean
        self.std = std

    def fit(self, X: np.ndarray) -> _Scaler:
        mean = np.nanmean(np.where(np.isnan(X).all(axis=0), 0.0, X), axis=0) if X.size else None
        mean = np.nan_to_num(np.nanmean(X, axis=0)) if X.size else mean
        Xf = np.where(np.isnan(X), mean, X)
        std = Xf.std(axis=0)
        std[std < 1e-9] = 1.0
        self.mean, self.std = mean, std
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        Xf = np.where(np.isnan(X), self.mean, X)
        return (Xf - self.mean) / self.std

    def to_dict(self) -> dict[str, Any]:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _Scaler:
        return cls(np.array(d["mean"], dtype=float), np.array(d["std"], dtype=float))


class Model:
    """Base class."""

    type = "base"

    def __init__(self) -> None:
        self.classes: list[str] = []
        self.scaler = _Scaler()

    def fit(self, X: np.ndarray, y: list[str]) -> Model:  # pragma: no cover
        raise NotImplementedError

    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class SoftmaxModel(Model):
    """L2-regularized multinomial logistic regression (class-balanced)."""

    type = "softmax"

    def __init__(self, l2: float = 1e-3, iters: int = 800, lr: float = 0.5) -> None:
        super().__init__()
        self.l2, self.iters, self.lr = l2, iters, lr
        self.W: np.ndarray | None = None

    @staticmethod
    def _expand(Z: np.ndarray) -> np.ndarray:
        # quadratic terms give the linear model some non-linearity
        return np.hstack([Z, Z**2 / 4.0, np.ones((Z.shape[0], 1))])

    def fit(self, X: np.ndarray, y: list[str]) -> SoftmaxModel:
        self.classes = sorted(set(y))
        idx = {c: i for i, c in enumerate(self.classes)}
        Z = self._expand(self.scaler.fit(X).transform(X))
        n, k = Z.shape[0], len(self.classes)
        Y = np.zeros((n, k))
        Y[np.arange(n), [idx[c] for c in y]] = 1.0
        w = (n / (k * Y.sum(axis=0)))[None, :] * Y  # balanced sample weights
        sw = w.sum(axis=1, keepdims=True)
        W = np.zeros((Z.shape[1], k))
        for _ in range(self.iters):
            P = self._softmax(Z @ W)
            grad = Z.T @ ((P - Y) * sw) / n + self.l2 * W
            W -= self.lr * grad
        self.W = W
        return self

    @staticmethod
    def _softmax(S: np.ndarray) -> np.ndarray:
        S = S - S.max(axis=1, keepdims=True)
        E = np.exp(S)
        return E / E.sum(axis=1, keepdims=True)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._softmax(self._expand(self.scaler.transform(X)) @ self.W)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "classes": self.classes,
            "scaler": self.scaler.to_dict(),
            "W": self.W.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SoftmaxModel:
        m = cls()
        m.classes = list(d["classes"])
        m.scaler = _Scaler.from_dict(d["scaler"])
        m.W = np.array(d["W"], dtype=float)
        return m


class KnnModel(Model):
    """Distance-weighted k-nearest neighbours on standardized features."""

    type = "knn"

    def __init__(self, k: int = 7) -> None:
        super().__init__()
        self.k = k
        self.X: np.ndarray | None = None
        self.y: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: list[str]) -> KnnModel:
        self.classes = sorted(set(y))
        idx = {c: i for i, c in enumerate(self.classes)}
        self.X = self.scaler.fit(X).transform(X)
        self.y = np.array([idx[c] for c in y], dtype=int)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Z = self.scaler.transform(X)
        k = min(self.k, len(self.y))
        out = np.zeros((Z.shape[0], len(self.classes)))
        for start in range(0, Z.shape[0], 256):
            chunk = Z[start : start + 256]
            d = np.sqrt(((chunk[:, None, :] - self.X[None, :, :]) ** 2).sum(axis=2))
            nn = np.argpartition(d, k - 1, axis=1)[:, :k]
            for r in range(chunk.shape[0]):
                for j in nn[r]:
                    out[start + r, self.y[j]] += 1.0 / (d[r, j] + 0.1)
        out += 0.01  # smoothing: never fully certain
        return out / out.sum(axis=1, keepdims=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "k": self.k,
            "classes": self.classes,
            "scaler": self.scaler.to_dict(),
            "X": np.round(self.X, 5).tolist(),
            "y": self.y.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnnModel:
        m = cls(int(d["k"]))
        m.classes = list(d["classes"])
        m.scaler = _Scaler.from_dict(d["scaler"])
        m.X = np.array(d["X"], dtype=float)
        m.y = np.array(d["y"], dtype=int)
        return m


class NoveltyGuard:
    """Flags inputs unlike anything seen for the predicted class.

    Per class: diagonal Gaussian in standardized space; the threshold is a high
    quantile of the training distances. Unfamiliar inputs get confidence 0, so
    they stay open instead of being classified confidently wrong.
    """

    def __init__(self) -> None:
        self.stats: dict[str, dict[str, Any]] = {}

    def fit(self, Z: np.ndarray, y: list[str], quantile: float = 0.99) -> NoveltyGuard:
        ya = np.array(y)
        for c in sorted(set(y)):
            Zc = Z[ya == c]
            mu = Zc.mean(axis=0)
            sd = np.maximum(Zc.std(axis=0), 0.25)
            d = np.sqrt((((Zc - mu) / sd) ** 2).mean(axis=1))
            thr = float(np.quantile(d, quantile)) * 1.25 if len(d) > 1 else 3.0
            self.stats[c] = {"mu": mu.tolist(), "sd": sd.tolist(), "thr": thr}
        return self

    def familiar(self, Z: np.ndarray, classes: list[str], idx: np.ndarray) -> np.ndarray:
        out = np.ones(Z.shape[0], dtype=bool)
        for r, i in enumerate(idx):
            s = self.stats.get(classes[i])
            if s is None:
                continue
            d = np.sqrt((((Z[r] - np.array(s["mu"])) / np.array(s["sd"])) ** 2).mean())
            out[r] = d <= s["thr"]
        return out

    def to_dict(self) -> dict[str, Any]:
        return self.stats

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NoveltyGuard:
        g = cls()
        g.stats = d
        return g


def guarded_confidence(model: Model, guard: NoveltyGuard, X: np.ndarray):
    """Return (probabilities, predicted index, confidence with novelty applied)."""
    P = model.predict_proba(X)
    idx = P.argmax(axis=1)
    conf = P.max(axis=1)
    ok = guard.familiar(model.scaler.transform(X), model.classes, idx)
    return P, idx, np.where(ok, conf, 0.0)


CANDIDATES: dict[str, type[Model]] = {"softmax": SoftmaxModel, "knn": KnnModel}


def model_from_dict(d: dict[str, Any]) -> Model:
    """Rebuild a model from its dict."""
    return CANDIDATES[d["type"]].from_dict(d)
