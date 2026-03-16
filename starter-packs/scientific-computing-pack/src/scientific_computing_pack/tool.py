"""Scientific computing tool using numpy and scipy."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats as scipy_stats
from scipy.fft import fft, fftfreq
from scipy.linalg import solve


def run(operation: str, data: list | dict, **kwargs: Any) -> dict:
    """Perform scientific computing operations.

    Args:
        operation: One of "stats", "linreg", "fft", "solve".
        data: Input data. Format depends on operation:
            - "stats": a list of numbers, or {"values": [...]}.
            - "linreg": {"x": [...], "y": [...]}.
            - "fft": a list of signal values, or {"signal": [...], "sample_rate": float}.
            - "solve": {"A": [[...], ...], "b": [...]}.
        **kwargs: Additional parameters per operation.

    Returns:
        A dict with the computation results.
    """
    if operation == "stats":
        values = _extract_values(data)
        if isinstance(values, dict):
            return values  # error dict
        arr = np.array(values, dtype=float)
        result: dict[str, Any] = {
            "operation": "stats",
            "count": len(arr),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "std": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
            "variance": float(np.var(arr, ddof=1)) if len(arr) > 1 else 0.0,
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "sum": float(np.sum(arr)),
            "range": float(np.ptp(arr)),
        }
        # Percentiles
        for p in (25, 50, 75):
            result[f"p{p}"] = float(np.percentile(arr, p))

        # Skewness and kurtosis (require >= 3 values)
        if len(arr) >= 3:
            result["skewness"] = float(scipy_stats.skew(arr))
            result["kurtosis"] = float(scipy_stats.kurtosis(arr))

        return result

    elif operation == "linreg":
        if not isinstance(data, dict) or "x" not in data or "y" not in data:
            return {"error": "linreg requires data as {'x': [...], 'y': [...]}."}

        x = np.array(data["x"], dtype=float)
        y = np.array(data["y"], dtype=float)

        if len(x) != len(y):
            return {"error": f"x and y must have the same length (got {len(x)} and {len(y)})."}
        if len(x) < 2:
            return {"error": "Need at least 2 data points for linear regression."}

        slope, intercept, r_value, p_value, std_err = scipy_stats.linregress(x, y)

        y_pred = slope * x + intercept
        residuals = y - y_pred

        return {
            "operation": "linreg",
            "slope": float(slope),
            "intercept": float(intercept),
            "r_squared": float(r_value ** 2),
            "r_value": float(r_value),
            "p_value": float(p_value),
            "std_err": float(std_err),
            "equation": f"y = {slope:.6g}x + {intercept:.6g}",
            "residuals": residuals.tolist(),
            "n_points": len(x),
        }

    elif operation == "fft":
        if isinstance(data, dict):
            signal = data.get("signal", data.get("values", []))
            sample_rate = data.get("sample_rate", 1.0)
        elif isinstance(data, list):
            signal = data
            sample_rate = kwargs.get("sample_rate", 1.0)
        else:
            return {"error": "fft requires a list of signal values or {'signal': [...], 'sample_rate': float}."}

        arr = np.array(signal, dtype=float)
        n = len(arr)
        if n == 0:
            return {"error": "Signal data is empty."}

        # Compute FFT
        yf = fft(arr)
        xf = fftfreq(n, d=1.0 / float(sample_rate))

        # Only take positive frequencies
        positive_mask = xf >= 0
        freqs = xf[positive_mask].tolist()
        magnitudes = np.abs(yf[positive_mask]).tolist()
        phases = np.angle(yf[positive_mask]).tolist()

        # Find dominant frequencies (top peaks)
        mag_arr = np.abs(yf[positive_mask])
        if len(mag_arr) > 1:
            # Skip DC component (index 0), find peaks
            sorted_indices = np.argsort(mag_arr[1:])[::-1] + 1
            top_n = min(5, len(sorted_indices))
            dominant = [
                {"frequency": freqs[i], "magnitude": magnitudes[i]}
                for i in sorted_indices[:top_n]
            ]
        else:
            dominant = []

        return {
            "operation": "fft",
            "n_samples": n,
            "sample_rate": float(sample_rate),
            "frequencies": freqs,
            "magnitudes": magnitudes,
            "phases": phases,
            "dominant_frequencies": dominant,
        }

    elif operation == "solve":
        if not isinstance(data, dict) or "A" not in data or "b" not in data:
            return {"error": "solve requires data as {'A': [[...], ...], 'b': [...]}."}

        try:
            A = np.array(data["A"], dtype=float)
            b = np.array(data["b"], dtype=float)
        except (ValueError, TypeError) as exc:
            return {"error": f"Invalid matrix data: {exc}"}

        if A.ndim != 2:
            return {"error": f"A must be a 2D matrix, got {A.ndim}D."}
        if A.shape[0] != A.shape[1]:
            return {"error": f"A must be square, got shape {A.shape}."}
        if A.shape[0] != len(b):
            return {"error": f"Dimension mismatch: A is {A.shape}, b has {len(b)} elements."}

        try:
            x = solve(A, b)
        except np.linalg.LinAlgError as exc:
            return {"error": f"Cannot solve system: {exc}"}

        # Verify solution
        residual = np.linalg.norm(A @ x - b)

        return {
            "operation": "solve",
            "solution": x.tolist(),
            "residual_norm": float(residual),
            "system_size": A.shape[0],
            "determinant": float(np.linalg.det(A)),
        }

    else:
        return {
            "error": f"Unknown operation '{operation}'. Use stats, linreg, fft, or solve.",
        }


def _extract_values(data: list | dict) -> list | dict:
    """Extract a list of numeric values from various input formats."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "values" in data:
            return data["values"]
        if "data" in data:
            return data["data"]
        return {"error": "Dict data must have a 'values' or 'data' key for stats operation."}
    return {"error": f"Expected list or dict, got {type(data).__name__}."}
