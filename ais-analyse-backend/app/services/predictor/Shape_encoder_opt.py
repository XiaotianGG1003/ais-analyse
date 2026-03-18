import math
import numpy as np
import pygeohash as pgh
from shapely.geometry import LineString

########################################################
# ShapeEncoder
########################################################
GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
GEOHASH_MAP = {c: i for i, c in enumerate(GEOHASH_BASE32)}

class ShapeEncoder:
    """
    Encode a trajectory (obs) into ST-Shape binary code
    """



    def __init__(
        self,
        geohash_precision=8,
        bits_per_shape=8,
    ):
        self.geohash_precision = geohash_precision
        self.bits_per_shape = bits_per_shape

        self.shape_ranges = {
            "convexity": (0.10, 1.00),
            "fractality": (1.30, 3.00),
            "nperimeter": (0.08, 0.25),
            "nproximity": (0.17, 0.32)
        }


    ####################################################
    # Public API
    ####################################################

    def encode(self, obs, timestamps):
        """
        Parameters
        ----------
        obs : np.ndarray, shape (T,2)
            latitude, longitude
        timestamps : np.ndarray, shape (T,)
            unix timestamp (seconds)

        Returns
        -------
        code : List[int]
            ST-Shape binary code
        """

        points = self._build_geohash_time_points(obs, timestamps)
        polygon = self._trajectory_to_polygon(points)

        if (not polygon.is_valid) or polygon.is_empty or len(points) < 3:
            return self._degenerate_shape_code()

        features = self._compute_shape_features(points, polygon)

        codes = []
        for name in ["convexity", "fractality", "nperimeter", "nproximity"]:
            vmin, vmax = self.shape_ranges[name]
            codes.append(
                self._binary_quantize(
                    features[name], vmin, vmax, self.bits_per_shape
                )
            )

        return self._interleave_codes(codes)

    ####################################################
    # Step 1: (lat,lon,time) -> (geohash_int, time)
    ####################################################
    def _degenerate_shape_code(self):
        """
        Return a valid but non-informative ST-Shape code.
        """
        total_bits = self.bits_per_shape * 4  # 4 shape features
        return [0] * total_bits

    def _build_geohash_time_points(self, obs, timestamps):
        xs = []
        ys = []

        for (lat, lon), t in zip(obs, timestamps):
            gh = pgh.encode(lat, lon, precision=self.geohash_precision)
            gh_int = 0
            for c in gh:
                gh_int = gh_int * 32 + GEOHASH_MAP[c]

            xs.append(float(gh_int))
            ys.append(float(t))

        xs = np.asarray(xs)
        ys = np.asarray(ys)

        # -------- 关键：归一化到 [0,1] --------
        if len(xs) > 1:
            xs = (xs - xs.min()) / (xs.max() - xs.min() + 1e-9)
            ys = (ys - ys.min()) / (ys.max() - ys.min() + 1e-9)
        else:
            xs[:] = 0.5
            ys[:] = 0.5

        return list(zip(xs, ys))

    ####################################################
    # Step 2: trajectory -> polygon
    ####################################################

    def _trajectory_to_polygon(self, points, buffer_radius=0.01):
        """
        Convert trajectory into polygon by buffering the LineString
        """
        line = LineString(points)
        poly = line.buffer(buffer_radius)
        return poly

    ####################################################
    # Step 3: shape features
    ####################################################

    def _compute_shape_features(self, points, poly, eps=1e-6):
        return {
            "convexity": self._convexity(poly),
            "fractality": self._fractality(poly, eps),
            "nperimeter": self._nperimeter(poly, eps),
            "nproximity": self._n_v_proximity(points, poly, eps)
        }

    def _convexity(self, poly):
        hull = poly.convex_hull
        if hull.area == 0:
            return 0.0
        return poly.area / hull.area

    def _fractality(self, poly, eps):
        A = max(poly.area, eps)
        P = max(poly.length, eps)
        return 1-math.log(A) / (2*math.log(P))

    def _nperimeter(self, poly, eps):
        A = max(poly.area, eps)
        P = poly.length
        return (2.0 * math.sqrt(math.pi * A))/P

    def _n_v_proximity(self, points, poly, eps):
        cx, cy = poly.centroid.coords[0]
        A = max(poly.area, eps)
        r = (2 / 3) * math.sqrt(A / math.pi)
        dists = [
            math.hypot(x - cx, y - cy)
            for x, y in points
        ]
        mean_dist = np.mean(dists)
        return r / (mean_dist + eps)

    ####################################################
    # Step 4: binary quantization
    ####################################################

    def _binary_quantize(self, value, vmin, vmax, bits):
        value = max(vmin, min(vmax, value))
        code = []
        low, high = vmin, vmax

        for _ in range(bits):
            mid = (low + high) / 2.0
            if value <= mid:
                code.append(0)
                high = mid
            else:
                code.append(1)
                low = mid

        return code

    ####################################################
    # Step 5: interleave shape codes
    ####################################################

    def _interleave_codes(self, codes):
        """
        Interleave multiple bit sequences:
        [a1 a2 a3], [b1 b2 b3] → [a1 b1 a2 b2 a3 b3]
        """
        return [bit for bits in zip(*codes) for bit in bits]
