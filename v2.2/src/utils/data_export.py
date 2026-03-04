"""
Data export utilities for VF Curve Manager.

Handles:
- Excel export with openpyxl
- PNG plot generation with matplotlib
- Data formatting for tables
"""

import os
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for thread safety
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
from scipy.interpolate import interp1d
import logging

log = logging.getLogger(__name__)

# Absolute path to the project-root Logs/ folder, anchored to this file
# src/utils/data_export.py -> src/utils/ -> src/ -> project root -> Logs/
_LOGS_ROOT = Path(__file__).parent.parent.parent / 'Logs'


def export_dataframe_to_excel(df, filepath, sheet_name='VF Curve'):
    """
    Export pandas DataFrame to Excel file.

    Args:
        df: pandas DataFrame
        filepath: Full path to Excel file
        sheet_name: Sheet name (default 'VF Curve')

    Returns:
        bool: True on success, False on error
    """
    try:
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
        return True
    except Exception as ex:
        log.error("Failed to export Excel: %s", ex)
        return False


def export_multiple_sheets(dataframes_dict, filepath):
    """
    Export multiple DataFrames to different sheets in one Excel file.

    Args:
        dataframes_dict: Dict of {sheet_name: dataframe}
        filepath: Full path to Excel file

    Returns:
        bool: True on success, False on error
    """
    try:
        with pd.ExcelWriter(filepath) as writer:
            for sheet_name, df in dataframes_dict.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        return True
    except Exception as ex:
        log.error("Failed to export multiple sheets: %s", ex)
        return False


def plot_vf_curve(df, label, filepath, interp_enabled=True):
    """
    Generate VF curve plot and save as PNG.

    Args:
        df: DataFrame with 'WP', voltage column, frequency column
        label: Domain label for plot title
        filepath: Full path to PNG file
        interp_enabled: Enable interpolation for smooth curves

    Returns:
        bool: True on success, False on error
    """
    try:
        plt.figure(figsize=(8, 5))

        # Extract frequency (column 2) and voltage (column 1)
        x = df.iloc[:, 2].values  # Frequency
        y = df.iloc[:, 1].values  # Voltage

        # Filter out None/NaN values
        mask = (~pd.isnull(x)) & (~pd.isnull(y))
        x_valid = x[mask]
        y_valid = y[mask]

        if len(x_valid) == 0:
            log.warning("No valid data points for %s", label)
            return False

        # Plot with or without interpolation
        if interp_enabled and len(x_valid) > 2:
            # Handle duplicate x values by averaging y values
            unique_x, indices = np.unique(x_valid, return_inverse=True)
            avg_y = np.zeros_like(unique_x, dtype=float)
            for i, ux in enumerate(unique_x):
                avg_y[i] = np.mean(y_valid[indices == i])

            # Interpolation
            interp_kind = 'cubic' if len(unique_x) > 3 else 'linear'
            if len(unique_x) > 1:
                xnew = np.linspace(np.min(unique_x), np.max(unique_x), 200)
                f_interp = interp1d(unique_x, avg_y, kind=interp_kind, fill_value='extrapolate')
                ynew = f_interp(xnew)
                plt.plot(xnew, ynew, label=label + f' (interpolated: {interp_kind})', color='tab:blue')
                plt.scatter(unique_x, avg_y, color='tab:orange', label=label + ' (original)', s=50)
            else:
                plt.plot(unique_x, avg_y, marker='o', label=label, markersize=8)
        else:
            plt.plot(x_valid, y_valid, marker='o', label=label, markersize=8, linewidth=2)

        plt.xlabel("Frequency (MHz)", fontsize=12)
        plt.ylabel("Voltage (V)", fontsize=12)
        plt.title(f"VF Curve: {label}", fontsize=14, fontweight='bold')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        plt.savefig(filepath, dpi=100)
        plt.close()
        return True

    except Exception as ex:
        log.error("Failed to plot VF curve: %s", ex)
        plt.close()
        return False


def plot_cumulative_curves(dataframes, labels, filepath, interp_enabled=True):
    """
    Generate cumulative VF curves plot for multiple domains.

    Args:
        dataframes: List of DataFrames
        labels: List of domain labels
        filepath: Full path to PNG file
        interp_enabled: Enable interpolation

    Returns:
        bool: True on success, False on error
    """
    try:
        plt.figure(figsize=(10, 7))

        colors = plt.cm.tab10(np.linspace(0, 1, len(dataframes)))

        for idx, (df, label) in enumerate(zip(dataframes, labels)):
            # Extract frequency and voltage
            x = df.iloc[:, 2].values
            y = df.iloc[:, 1].values

            mask = (~pd.isnull(x)) & (~pd.isnull(y))
            x_valid = x[mask]
            y_valid = y[mask]

            if len(x_valid) == 0:
                continue

            color = colors[idx]

            # Plot with or without interpolation
            if interp_enabled and len(x_valid) > 2:
                unique_x, indices = np.unique(x_valid, return_inverse=True)
                avg_y = np.array([np.mean(y_valid[indices == i]) for i in range(len(unique_x))])

                interp_kind = 'cubic' if len(unique_x) > 3 else 'linear'
                if len(unique_x) > 1:
                    xnew = np.linspace(np.min(unique_x), np.max(unique_x), 200)
                    ynew = interp1d(unique_x, avg_y, kind=interp_kind, fill_value='extrapolate')(xnew)
                    plt.plot(xnew, ynew, label=label + f' (interpolated)', color=color)
                    plt.scatter(unique_x, avg_y, label=label + ' (original)', alpha=0.6, color=color, s=40)
                else:
                    plt.plot(unique_x, avg_y, marker='o', label=label, color=color, markersize=8)
            else:
                plt.plot(x_valid, y_valid, marker='o', label=label, color=color, markersize=8, linewidth=2)

        plt.xlabel('Frequency (MHz)', fontsize=12)
        plt.ylabel('Voltage (V)', fontsize=12)
        plt.title('VF Curve: All Selected Domains', fontsize=14, fontweight='bold')
        plt.legend(fontsize=10)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        plt.savefig(filepath, dpi=100)
        plt.close()
        return True

    except Exception as ex:
        log.error("Failed to plot cumulative curves: %s", ex)
        plt.close()
        return False


def plot_before_after(df_before, df_after, label, filepath):
    """
    Generate before/after comparison plot.

    Args:
        df_before: Before DataFrame
        df_after: After DataFrame
        label: Domain label
        filepath: Full path to PNG file

    Returns:
        bool: True on success, False on error
    """
    try:
        plt.figure(figsize=(8, 5))

        # Before data
        x_b = df_before.iloc[:, 2].values
        y_b = df_before.iloc[:, 1].values
        mask_b = (~pd.isnull(x_b)) & (~pd.isnull(y_b))
        x_b_valid = x_b[mask_b]
        y_b_valid = y_b[mask_b]

        # After data
        x_a = df_after.iloc[:, 2].values
        y_a = df_after.iloc[:, 1].values
        mask_a = (~pd.isnull(x_a)) & (~pd.isnull(y_a))
        x_a_valid = x_a[mask_a]
        y_a_valid = y_a[mask_a]

        # Plot
        if len(x_b_valid) > 0:
            plt.plot(x_b_valid, y_b_valid, marker='o', label='Before', color='tab:blue', linewidth=2, markersize=8)
        if len(x_a_valid) > 0:
            plt.plot(x_a_valid, y_a_valid, marker='s', label='After', color='tab:orange', linewidth=2, markersize=8)

        plt.xlabel('Frequency (MHz)', fontsize=12)
        plt.ylabel('Voltage (V)', fontsize=12)
        plt.title(f'VF Curve Comparison - {label}', fontsize=14, fontweight='bold')
        plt.legend(fontsize=11)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        plt.savefig(filepath, dpi=100)
        plt.close()
        return True

    except Exception as ex:
        log.error("Failed to plot before/after: %s", ex)
        plt.close()
        return False


def create_timestamped_filename(prefix, extension, directory='Logs'):
    """
    Create timestamped filename in Logs directory.

    Args:
        prefix: Filename prefix
        extension: File extension (e.g., 'xlsx', 'png')
        directory: Directory name (default 'Logs')

    Returns:
        str: Full path to timestamped file
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{prefix}_{timestamp}.{extension}"

    # Always write to the project-root Logs/ folder regardless of cwd
    logs_dir = _LOGS_ROOT
    logs_dir.mkdir(parents=True, exist_ok=True)

    return str(logs_dir / filename)


def ensure_logs_directory(directory='Logs'):
    """
    Ensure Logs directory exists.

    Args:
        directory: Directory name (default 'Logs')

    Returns:
        str: Full path to logs directory
    """
    # Always write to the project-root Logs/ folder regardless of cwd
    logs_dir = _LOGS_ROOT
    logs_dir.mkdir(parents=True, exist_ok=True)
    return str(logs_dir)
