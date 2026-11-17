"""Phase 3 - scalar Hopkins partially-coherent imaging (TCC -> SOCS), FFT based.

Conventions (all lengths nm, spatial frequencies 1/nm, arrays indexed [iy, ix]):
  * Pixel (ix, iy) of a Grid has its centre at (x0 + (ix+0.5)*pixel, y0 + (iy+0.5)*pixel).
  * Source coordinates are in sigma units: spatial frequency of a source point = sigma * NA / wavelength.
  * Pupil H(f) = circ(|f| <= NA/wavelength) * exp(i*2*pi*W(f)); W in waves (defocus + named Zernike terms).
    Defocus z > 0 means the image plane is displaced by +z along the propagation axis (sign convention TODO-VERIFY vs a
    commercial tool); exact scalar path: W = n*z*(cos(theta) - 1) / wavelength, sin(theta) = wavelength*|f|/n.
  * Equations: Hopkins/TCC [Feng24 Eq.1-2], SOCS I = sum_i lambda_i |Phi_i (x) O|^2 [Feng24 Eq.3] (notes/formulas.md).
  * TCC is normalised so that TCC(0,0) = 1  =>  a clear-field mask (t = 1 everywhere) images to I = 1 exactly.
  * The TCC is built on the frequency lattice of a *kernel domain* (side kernel_nm, Nk = kernel_nm/pixel pixels) and the
    source is sampled on that same lattice (optionally finer by `source_oversample`). For a mask domain equal to the
    kernel domain the result is the exact periodic Hopkins image; for larger domains the kernels are applied as real-space
    convolution kernels truncated to the kernel domain (optical interaction range).
  * Scalar thin-mask only. Hooks for later phases: `Imager.mask_hook` (complex transmission -> complex transmission, e.g.
    3-D mask / vector corrections) and the complex `fg`/`bg` transmissions in `transmission_from_polygons` (att-PSM).
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.geometry import Polygon as _SPoly

from .geometry import Polygon, bbox_of, union


# ======================================================================================
# Grid and rasterisation
# ======================================================================================
@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    nx: int
    ny: int
    pixel: float

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.ny, self.nx)

    @property
    def xs(self) -> np.ndarray:
        return self.x0 + (np.arange(self.nx) + 0.5) * self.pixel

    @property
    def ys(self) -> np.ndarray:
        return self.y0 + (np.arange(self.ny) + 0.5) * self.pixel

    @property
    def extent(self) -> Tuple[float, float, float, float]:
        """(x0, x1, y0, y1) in nm, for matplotlib imshow(origin='lower')."""
        return (self.x0, self.x0 + self.nx * self.pixel, self.y0, self.y0 + self.ny * self.pixel)

    @staticmethod
    def from_bbox(bbox, pixel: float, margin: float = 0.0) -> "Grid":
        x0, y0, x1, y1 = bbox
        x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
        return Grid(x0, y0, int(math.ceil((x1 - x0) / pixel)), int(math.ceil((y1 - y0) / pixel)), float(pixel))


def _shapely(p: Polygon):
    return _SPoly(p.outer, [h for h in p.holes])


def rasterize_coverage(polys: Sequence[Polygon], grid: Grid, supersample: int = 4,
                       layer: Optional[Tuple[int, int]] = None) -> np.ndarray:
    """Area-weighted coverage in [0, 1] per pixel (polygons are unioned first, so overlaps do not double count).
    Each pixel is sampled on a supersample x supersample sub-grid (point-in-polygon, holes respected); axis-aligned
    edges on sub-pixel boundaries are therefore exact. Cost ~ O(covered pixels * supersample^2)."""
    if layer is not None:
        polys = [p for p in polys if p.key == tuple(layer)]
    cov = np.zeros(grid.shape)
    if not polys:
        return cov
    s = int(supersample)
    sub = grid.pixel / s
    for p in union(list(polys)):
        bx0, by0, bx1, by1 = p.bbox
        i0 = max(0, int(math.floor((bx0 - grid.x0) / grid.pixel)))
        i1 = min(grid.nx, int(math.ceil((bx1 - grid.x0) / grid.pixel)))
        j0 = max(0, int(math.floor((by0 - grid.y0) / grid.pixel)))
        j1 = min(grid.ny, int(math.ceil((by1 - grid.y0) / grid.pixel)))
        if i1 <= i0 or j1 <= j0:
            continue
        sx = grid.x0 + (np.arange(i0 * s, i1 * s) + 0.5) * sub
        sy = grid.y0 + (np.arange(j0 * s, j1 * s) + 0.5) * sub
        X, Y = np.meshgrid(sx, sy)
        inside = shapely.contains_xy(_shapely(p), X, Y).astype(float)
        blk = inside.reshape(j1 - j0, s, i1 - i0, s).mean(axis=(1, 3))
        cov[j0:j1, i0:i1] += blk
    return np.clip(cov, 0.0, 1.0)


def transmission_from_polygons(polys: Sequence[Polygon], grid: Grid, tone: str = "clear_field",
                               fg: Optional[complex] = None, bg: Optional[complex] = None,
                               supersample: int = 4, layer: Optional[Tuple[int, int]] = None) -> np.ndarray:
    """Complex amplitude transmission map t(x, y) = bg*(1-cov) + fg*cov.
    tone='clear_field' : polygons are absorber (default fg=0, bg=1)   - chrome on glass, drawn shapes block light.
    tone='dark_field'  : polygons are openings (default fg=1, bg=0)   - contact holes etc.
    Override fg/bg for attenuated PSM, e.g. tone='clear_field', fg=-sqrt(0.06) (6 % att-PSM, 180 deg phase)."""
    if tone not in ("clear_field", "dark_field"):
        raise ValueError("tone must be 'clear_field' or 'dark_field'")
    dfl_fg, dfl_bg = (0.0, 1.0) if tone == "clear_field" else (1.0, 0.0)
    fg = dfl_fg if fg is None else fg
    bg = dfl_bg if bg is None else bg
    cov = rasterize_coverage(polys, grid, supersample, layer)
    return bg * (1.0 - cov) + fg * cov


# ======================================================================================
# Source and pupil
# ======================================================================================
@dataclass
class Source:
    """Illumination intensity J(sx, sy) in sigma units. kind: conventional | annular | dipole | quasar.
    conventional: sigma. annular: sigma_in, sigma_out. dipole/quasar: annular sectors sigma_in..sigma_out with full
    opening angle `opening_deg`, poles centred on +-x (dipole, orientation 'x') / +-y ('y') or on the diagonals (quasar,
    rotated by `rotate_deg`, default 45 deg)."""
    kind: str = "conventional"
    sigma: float = 0.5
    sigma_in: float = 0.5
    sigma_out: float = 0.8
    opening_deg: float = 30.0
    orientation: str = "x"
    rotate_deg: float = 45.0

    @staticmethod
    def conventional(sigma: float) -> "Source":
        return Source("conventional", sigma=sigma)

    @staticmethod
    def annular(sigma_in: float, sigma_out: float) -> "Source":
        return Source("annular", sigma_in=sigma_in, sigma_out=sigma_out)

    @staticmethod
    def dipole(sigma_in: float, sigma_out: float, opening_deg: float = 30.0, orientation: str = "x") -> "Source":
        return Source("dipole", sigma_in=sigma_in, sigma_out=sigma_out, opening_deg=opening_deg, orientation=orientation)

    @staticmethod
    def quasar(sigma_in: float, sigma_out: float, opening_deg: float = 30.0, rotate_deg: float = 45.0) -> "Source":
        return Source("quasar", sigma_in=sigma_in, sigma_out=sigma_out, opening_deg=opening_deg, rotate_deg=rotate_deg)

    @property
    def sigma_max(self) -> float:
        return self.sigma if self.kind == "conventional" else self.sigma_out

    def __call__(self, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
        r = np.hypot(sx, sy)
        if self.kind == "conventional":
            return (r <= self.sigma + 1e-12).astype(float)
        ring = (r >= self.sigma_in - 1e-12) & (r <= self.sigma_out + 1e-12)
        if self.kind == "annular":
            return ring.astype(float)
        if self.kind in ("dipole", "quasar"):
            ang = np.degrees(np.arctan2(sy, sx))
            if self.kind == "dipole":
                centres = [0.0, 180.0] if self.orientation == "x" else [90.0, -90.0]
            else:
                centres = [self.rotate_deg + 90.0 * k for k in range(4)]
            ok = np.zeros_like(r, dtype=bool)
            for c in centres:
                d = (ang - c + 180.0) % 360.0 - 180.0
                ok |= np.abs(d) <= self.opening_deg / 2 + 1e-9
            return (ring & ok).astype(float)
        raise ValueError(f"unknown source kind {self.kind!r}")


def _zernike_waves(rho: np.ndarray, phi: np.ndarray, ab: Dict[str, float]) -> np.ndarray:
    """Named aberrations, coefficients in waves, normalised pupil radius rho in [0, 1]."""
    terms = {
        "astig0": lambda: rho ** 2 * np.cos(2 * phi),
        "astig45": lambda: rho ** 2 * np.sin(2 * phi),
        "coma_x": lambda: (3 * rho ** 3 - 2 * rho) * np.cos(phi),
        "coma_y": lambda: (3 * rho ** 3 - 2 * rho) * np.sin(phi),
        "spherical": lambda: 6 * rho ** 4 - 6 * rho ** 2 + 1,
        "trefoil_x": lambda: rho ** 3 * np.cos(3 * phi),
        "trefoil_y": lambda: rho ** 3 * np.sin(3 * phi),
    }
    w = np.zeros_like(rho)
    for k, c in ab.items():
        if k not in terms:
            raise KeyError(f"unknown aberration {k!r}; available: {sorted(terms)}")
        w = w + c * terms[k]()
    return w


def pupil(fx: np.ndarray, fy: np.ndarray, wavelength: float, na: float, defocus_nm: float = 0.0,
          aberrations: Optional[Dict[str, float]] = None, n_image: float = 1.0) -> np.ndarray:
    """Complex pupil H(fx, fy): unit-amplitude disk of radius NA/wavelength times exp(i 2 pi W)."""
    f = np.hypot(fx, fy)
    inside = f <= na / wavelength * (1 + 1e-12)
    H = inside.astype(complex)
    if defocus_nm or aberrations:
        W = np.zeros_like(f)
        if defocus_nm:
            s2 = np.clip((wavelength * f / n_image) ** 2, 0.0, 0.999999)
            W = W + n_image * defocus_nm * (np.sqrt(1.0 - s2) - 1.0) / wavelength
        if aberrations:
            W = W + _zernike_waves(np.clip(f * wavelength / na, 0, 1), np.arctan2(fy, fx), aberrations)
        H = H * np.exp(2j * np.pi * W)
    return H


# ======================================================================================
# SOCS kernels
# ======================================================================================
@dataclass
class Kernels:
    """SOCS decomposition. `lams` descending, normalised so sum(lams) = TCC trace / TCC(0,0)."""
    lams: np.ndarray                 # (N,)
    phi_real: np.ndarray             # (N, Nk, Nk) complex, real-space kernels, origin at index Nk//2 (fftshifted)
    pixel: float
    nk: int
    energy_fraction: float           # kept / total eigenvalue sum
    n_total: int                     # number of non-negligible eigenvalues before truncation
    _cache: Dict[Tuple[int, int], np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def n(self) -> int:
        return len(self.lams)

    def freq_kernels(self, shape: Tuple[int, int]) -> np.ndarray:
        """Phi_i(f) on the FFT frequency lattice of an array of `shape` (ny, nx): kernels centred, zero padded / cropped."""
        if shape in self._cache:
            return self._cache[shape]
        ny, nx = shape
        out = np.zeros((self.n, ny, nx), dtype=complex)
        sy, ty = _overlap(self.nk, ny)
        sx, tx = _overlap(self.nk, nx)
        out[:, ty, tx] = self.phi_real[:, sy, sx]
        out = np.fft.fft2(np.fft.ifftshift(out, axes=(1, 2)), axes=(1, 2))
        if out.nbytes < 512e6:
            self._cache[shape] = out
        return out


def _overlap(nk: int, n: int):
    """Slices (source, target) copying a length-nk array whose origin is nk//2 into a length-n array with origin n//2."""
    so, to = nk // 2, n // 2
    a = max(0, so - to)
    b = min(nk, so - to + n)
    return slice(a, b), slice(a - so + to, b - so + to)


# ======================================================================================
# Imager
# ======================================================================================
@dataclass
class Imager:
    """Partially coherent scalar imaging system (wavelength, NA, source, defocus, aberrations)."""
    wavelength: float = 193.0
    na: float = 0.75
    source: Source = field(default_factory=lambda: Source.conventional(0.5))
    defocus_nm: float = 0.0
    aberrations: Dict[str, float] = field(default_factory=dict)
    n_image: float = 1.0
    kernel_nm: Optional[float] = None       # kernel domain side; default 8 * wavelength / NA
    energy: float = 0.99                    # SOCS truncation: kept eigenvalue fraction
    max_kernels: int = 256
    source_oversample: int = 2              # source lattice spacing = 1 / (kernel_nm * source_oversample)
    mask_hook: Optional[Callable[[np.ndarray], np.ndarray]] = None   # later: 3-D mask / vector correction
    _kcache: Dict[tuple, Kernels] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ helpers
    @property
    def fcut(self) -> float:
        """Coherent cutoff frequency NA/lambda (1/nm)."""
        return self.na / self.wavelength

    @property
    def cutoff_pitch_dense(self) -> float:
        """Smallest resolvable dense line/space pitch lambda / (NA (1 + sigma_max))."""
        return self.wavelength / (self.na * (1 + self.source.sigma_max))

    def recommended_pixel(self) -> float:
        """Largest pixel that samples the intensity without aliasing: lambda / (4 NA)."""
        return self.wavelength / (4 * self.na)

    def _key(self, pixel: float, kernel_nm: float) -> tuple:
        return (pixel, kernel_nm, self.wavelength, self.na, repr(self.source), self.defocus_nm,
                tuple(sorted(self.aberrations.items())), self.n_image, self.energy, self.max_kernels,
                self.source_oversample)

    def _pupil(self, fx, fy):
        return pupil(fx, fy, self.wavelength, self.na, self.defocus_nm, self.aberrations, self.n_image)

    def _source_points(self, nk: int, pixel: float):
        """Source samples (frequencies, weights) on a lattice of spacing 1/(Lk*oversample); centre point always kept."""
        df = 1.0 / (nk * pixel * self.source_oversample)
        rmax = self.source.sigma_max * self.fcut
        m = int(math.floor(rmax / df + 1e-9))
        k = np.arange(-m, m + 1)
        KX, KY = np.meshgrid(k, k)
        sx, sy = KX * df, KY * df
        J = self.source(sx / self.fcut, sy / self.fcut)
        sel = J > 0
        if not sel.any():
            sx, sy, J, sel = np.array([[0.0]]), np.array([[0.0]]), np.array([[1.0]]), np.array([[True]])
        return sx[sel], sy[sel], J[sel]

    # ------------------------------------------------------------------ TCC -> SOCS
    def kernels(self, pixel: float, kernel_nm: Optional[float] = None) -> Kernels:
        kernel_nm = kernel_nm or self.kernel_nm or 8.0 * self.wavelength / self.na
        key = self._key(pixel, kernel_nm)
        if key in self._kcache:
            return self._kcache[key]
        nk = max(8, int(round(kernel_nm / pixel)))
        fmax = self.fcut * (1 + self.source.sigma_max)
        if fmax > 0.5 / pixel:
            warnings.warn(f"pixel {pixel} nm undersamples the mask spectrum (need <= {0.5 / fmax:.1f} nm)")
        elif 2 * self.fcut > 0.5 / pixel:
            warnings.warn(f"pixel {pixel} nm may alias the intensity (recommended <= {self.recommended_pixel():.1f} nm)")
        fx1 = np.fft.fftfreq(nk, d=pixel)
        FX, FY = np.meshgrid(fx1, fx1)
        sel = np.flatnonzero((FX ** 2 + FY ** 2 <= fmax ** 2 * (1 + 1e-9)).ravel())
        fxs, fys = FX.ravel()[sel], FY.ravel()[sel]
        sx, sy, J = self._source_points(nk, pixel)
        if len(sel) * len(J) > 8e7:
            raise MemoryError("TCC too large: increase pixel, reduce kernel_nm or source_oversample")
        # TCC(f', f'') = sum_s J_s H(s+f') conj(H(s+f''))  = C C^H with C[i, s] = sqrt(J_s) H(s + f_i)
        C = self._pupil(fxs[:, None] + sx[None, :], fys[:, None] + sy[None, :]) * np.sqrt(J)[None, :]
        i0 = int(np.flatnonzero((fxs == 0) & (fys == 0))[0])
        t00 = float(np.sum(np.abs(C[i0]) ** 2))
        if t00 <= 0:
            raise ValueError("TCC(0,0) = 0: source lies outside the pupil (sigma too large for a clear field).")
        U, S, _ = np.linalg.svd(C / math.sqrt(t00), full_matrices=False)
        lam = S ** 2
        tot = lam.sum()
        keep = int(np.searchsorted(np.cumsum(lam) / tot, self.energy - 1e-12) + 1)
        keep = max(1, min(keep, self.max_kernels, len(lam)))
        phi = np.zeros((keep, nk * nk), dtype=complex)
        phi[:, sel] = U[:, :keep].T
        phi = phi.reshape(keep, nk, nk)
        phi_real = np.fft.fftshift(np.fft.ifft2(phi, axes=(1, 2)), axes=(1, 2))
        K = Kernels(lam[:keep], phi_real, pixel, nk, float(lam[:keep].sum() / tot), int(np.sum(lam > 1e-12 * lam[0])))
        self._kcache[key] = K
        return K

    # ------------------------------------------------------------------ imaging
    def image_from_transmission(self, t: np.ndarray, pixel: float, boundary: str = "periodic",
                                pad_value: Optional[complex] = None, kernels: Optional[Kernels] = None) -> np.ndarray:
        """Aerial image I(x, y) of a complex transmission map (clear field -> 1).
        boundary 'periodic': mask repeats (FFT wrap). 'padded': mask is embedded in `pad_value` (default: its mean edge
        value) with a margin of half the kernel domain, so features near the edge see an unwrapped surround."""
        t = np.asarray(t, dtype=complex)
        if self.mask_hook is not None:
            t = self.mask_hook(t)
        K = kernels or self.kernels(pixel)
        ny, nx = t.shape
        pad = 0
        if boundary == "padded":
            pad = K.nk // 2
            if pad_value is None:
                pad_value = np.mean(np.concatenate([t[0], t[-1], t[:, 0], t[:, -1]]))
            t = np.pad(t, pad, mode="constant", constant_values=pad_value)
        elif boundary != "periodic":
            raise ValueError("boundary must be 'periodic' or 'padded'")
        F = np.fft.fft2(t)
        PH = K.freq_kernels(t.shape)
        I = np.zeros(t.shape)
        for lam, ph in zip(K.lams, PH):
            I += lam * np.abs(np.fft.ifft2(F * ph)) ** 2
        return I[pad:pad + ny, pad:pad + nx] if pad else I

    def image(self, polys: Sequence[Polygon], grid: Grid, tone: str = "clear_field", fg=None, bg=None,
              boundary: str = "padded", supersample: int = 4, layer=None) -> np.ndarray:
        """Rasterise polygons and image them. Returns I on `grid` (shape (ny, nx))."""
        t = transmission_from_polygons(polys, grid, tone, fg, bg, supersample, layer)
        pv = (bg if bg is not None else (1.0 if tone == "clear_field" else 0.0)) if boundary == "padded" else None
        return self.image_from_transmission(t, grid.pixel, boundary, pv)

    # ------------------------------------------------------------------ independent reference (Abbe)
    def abbe_image(self, t: np.ndarray, pixel: float, kernel_nm: Optional[float] = None) -> np.ndarray:
        """Direct source-point (Abbe) summation on the same source lattice; periodic boundary; no SVD, no truncation.
        Mathematically equal to the full-rank SOCS image when t.shape == kernel domain. Used as a test oracle."""
        t = np.asarray(t, dtype=complex)
        ny, nx = t.shape
        sx, sy, J = self._source_points(max(ny, nx), pixel)
        fx = np.fft.fftfreq(nx, d=pixel)[None, :]
        fy = np.fft.fftfreq(ny, d=pixel)[:, None]
        F = np.fft.fft2(t)
        I = np.zeros(t.shape)
        norm = float(np.sum(J * np.abs(self._pupil(sx, sy)) ** 2))
        for a, b, w in zip(sx, sy, J):
            I += w * np.abs(np.fft.ifft2(F * self._pupil(fx + a, fy + b))) ** 2
        return I / norm
