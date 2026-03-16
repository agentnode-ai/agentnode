"""Data visualization tool using matplotlib and pandas."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import pandas as pd


def run(
    data: dict,
    chart_type: str = "bar",
    title: str = "",
    output_path: str = "",
) -> dict:
    """Create a chart from data and save it to a file.

    Args:
        data: Data to plot. Accepts these formats:
            - {"x": [...], "y": [...]} for simple plots
            - {"x": [...], "y1": [...], "y2": [...]} for multi-series
            - {"labels": [...], "values": [...]} for pie charts
            - {"values": [...]} for histograms
            - A dict of {column_name: [values...]} for DataFrame-based plotting
        chart_type: One of "bar", "line", "scatter", "pie", "histogram".
        title: Chart title. Auto-generated if empty.
        output_path: File path to save the chart. Uses a temp file if empty.

    Returns:
        A dict with chart_path and chart_type.
    """
    valid_types = ("bar", "line", "scatter", "pie", "histogram")
    if chart_type not in valid_types:
        return {"error": f"Unknown chart_type '{chart_type}'. Use one of {valid_types}."}

    fig, ax = plt.subplots(figsize=(10, 6))

    try:
        if chart_type == "pie":
            labels = data.get("labels", data.get("x", []))
            values = data.get("values", data.get("y", []))
            if not labels or not values:
                return {"error": "Pie chart requires 'labels'/'x' and 'values'/'y' in data."}
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.set_aspect("equal")

        elif chart_type == "histogram":
            values = data.get("values", data.get("y", data.get("x", [])))
            bins = data.get("bins", 10)
            if not values:
                return {"error": "Histogram requires 'values' in data."}
            ax.hist(values, bins=int(bins), edgecolor="black", alpha=0.7)
            ax.set_xlabel("Value")
            ax.set_ylabel("Frequency")

        elif chart_type in ("bar", "line", "scatter"):
            x = data.get("x")
            if x is None:
                # Try DataFrame-style: all keys are columns
                df = pd.DataFrame(data)
                if df.empty:
                    return {"error": "No plottable data found."}
                if chart_type == "bar":
                    df.plot(kind="bar", ax=ax)
                elif chart_type == "line":
                    df.plot(kind="line", ax=ax)
                elif chart_type == "scatter":
                    cols = list(df.columns)
                    if len(cols) >= 2:
                        df.plot.scatter(x=cols[0], y=cols[1], ax=ax)
                    else:
                        df.plot(kind="line", ax=ax)
            else:
                # Find all y-series (keys starting with 'y')
                y_keys = [k for k in data if k.startswith("y")]
                if not y_keys:
                    y_keys = [k for k in data if k != "x"]

                if not y_keys:
                    return {"error": "Data must contain at least one y-series."}

                for y_key in sorted(y_keys):
                    y = data[y_key]
                    label = y_key if len(y_keys) > 1 else None
                    if chart_type == "bar":
                        ax.bar(x, y, label=label, alpha=0.7)
                    elif chart_type == "line":
                        ax.plot(x, y, label=label, marker="o")
                    elif chart_type == "scatter":
                        ax.scatter(x, y, label=label, alpha=0.7)

                if len(y_keys) > 1:
                    ax.legend()

                ax.set_xlabel("x")
                ax.set_ylabel("y")

        # Set title
        if title:
            ax.set_title(title)
        else:
            ax.set_title(f"{chart_type.capitalize()} Chart")

        plt.tight_layout()

        # Determine output path
        if not output_path:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".png", prefix="chart_", delete=False
            )
            output_path = tmp.name
            tmp.close()

        # Ensure parent directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        return {
            "chart_path": str(Path(output_path).resolve()),
            "chart_type": chart_type,
            "title": title or f"{chart_type.capitalize()} Chart",
        }

    except Exception as exc:
        plt.close(fig)
        return {"error": f"Failed to create chart: {exc}"}
