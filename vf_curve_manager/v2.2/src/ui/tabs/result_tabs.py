"""Result tab widget builders for VF Curve Manager UI.

Each function creates a standalone QWidget tab showing operation results.
Extracted from CurveManagerUI so the main class stays focused on
orchestration logic.
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QFrame, QSplitter
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
import tabulate

def _create_result_tab(df, excel_path, png_path):
    """Create result tab with table overlaid on top-left of graph."""
    from PyQt5.QtWidgets import QFrame
    
    tab = QWidget()
    tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    
    # Use absolute positioning for overlay
    if png_path:
        # Main container with graph as background
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Graph container (fills entire space)
        graph_container = QWidget()
        graph_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        graph_layout = QVBoxLayout(graph_container)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        
        # Export info at top (absolute positioning)
        info_label = QLabel(f"<b>Plot:</b> {png_path}")
        info_label.setStyleSheet("""
            background-color: rgba(255, 255, 255, 230);
            color: #333;
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 10px;
        """)
        info_label.setParent(graph_container)
        info_label.move(10, 10)
        info_label.adjustSize()
        
        # Graph
        img_label = QLabel()
        img_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        pixmap = QPixmap()
        pixmap.load(png_path)
        img_label.setPixmap(pixmap)
        img_label.setScaledContents(True)
        
        graph_scroll = QScrollArea()
        graph_scroll.setWidget(img_label)
        graph_scroll.setWidgetResizable(True)
        graph_layout.addWidget(graph_scroll)
        
        main_layout.addWidget(graph_container)
        
        # Table overlay (top-left corner) - Semi-transparent with frame
        table_overlay = QFrame(graph_container)
        table_overlay.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 240);
                border: 2px solid #0071c5;
                border-radius: 8px;
            }
        """)
        
        table_layout = QVBoxLayout(table_overlay)
        table_layout.setContentsMargins(10, 10, 10, 10)
        
        # Table title
        table_title = QLabel("<b>📊 VF Curve Data</b>")
        table_title.setStyleSheet("color: #0071c5; font-size: 12px; background: transparent;")
        table_layout.addWidget(table_title)
        
        # Table content
        table_str = tabulate.tabulate(df.values.tolist(), df.columns.tolist(),
                                     tablefmt='github', floatfmt='.4f')
        table_label = QLabel(f"<pre style='margin:0; font-size: 10px;'>{table_str}</pre>")
        table_label.setStyleSheet("""
            background: transparent;
            color: #212529;
            font-family: 'Courier New';
        """)
        table_label.setWordWrap(False)
        
        table_scroll = QScrollArea()
        table_scroll.setWidget(table_label)
        table_scroll.setWidgetResizable(True)
        table_scroll.setMaximumHeight(300)
        table_scroll.setMaximumWidth(500)
        table_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)
        table_layout.addWidget(table_scroll)
        
        # Excel export link
        excel_label = QLabel(f"<a href='file:///{excel_path}' style='font-size: 10px;'>📁 Open Excel</a>")
        excel_label.setOpenExternalLinks(True)
        excel_label.setStyleSheet("background: transparent; padding: 5px;")
        table_layout.addWidget(excel_label)
        
        # Position overlay at top-left
        table_overlay.setParent(graph_container)
        table_overlay.setGeometry(20, 50, 500, 320)
        table_overlay.show()
        
        # Update position on resize (keep at top-left)
        def update_overlay_position():
            table_overlay.move(20, 50)
        
        graph_container.resizeEvent = lambda event: update_overlay_position()
        
    else:
        # No graph, just show table
        main_layout = QVBoxLayout(tab)
        info_label = QLabel(f"<b>Exported to:</b> {excel_path}")
        info_label.setStyleSheet("color: #666; padding: 5px;")
        main_layout.addWidget(info_label)
        
        table_str = tabulate.tabulate(df.values.tolist(), df.columns.tolist(),
                                     tablefmt='github', floatfmt='.4f')
        table_label = QLabel(f"<pre>{table_str}</pre>")
        table_label.setStyleSheet("background-color: #f9f9f9; padding: 10px; font-family: 'Courier New';")
        
        table_scroll = QScrollArea()
        table_scroll.setWidget(table_label)
        table_scroll.setWidgetResizable(True)
        main_layout.addWidget(table_scroll)
    
    return tab


def _create_cumulative_tab(excel_path, png_path):
    """Create cumulative tab with image."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    
    info_label = QLabel(f'Cumulative Excel: {excel_path}\nCumulative PNG: {png_path}')
    layout.addWidget(info_label)
    
    img_label = QLabel()
    pixmap = QPixmap()
    pixmap.load(png_path)
    img_label.setPixmap(pixmap)
    img_label.setScaledContents(True)
    
    scroll = QScrollArea()
    scroll.setWidget(img_label)
    scroll.setWidgetResizable(True)
    layout.addWidget(scroll)
    
    return tab


def _create_bump_result_tab(df_before, df_after, excel_path, png_path, verification):
    """Create bump result tab with before/after comparison side-by-side."""
    from PyQt5.QtWidgets import QSplitter
    
    tab = QWidget()
    main_layout = QVBoxLayout(tab)
    
    # Verification message at top
    if verification['success']:
        ver_msg = "✓ Bump verified successfully - all voltages within tolerance"
        ver_color = "green"
    else:
        ver_msg = "⚠ Bump completed but some voltages outside tolerance"
        ver_color = "orange"
    
    ver_label = QLabel(f'<span style="color:{ver_color}; font-weight:bold; font-size: 14px;">{ver_msg}</span>')
    ver_label.setStyleSheet("padding: 8px; background-color: #f0f0f0; border-radius: 4px;")
    main_layout.addWidget(ver_label)
    
    # File paths
    info_label = QLabel(f"<b>Exported to:</b> {excel_path} | <b>Plot:</b> {png_path}")
    info_label.setStyleSheet("color: #666; padding: 5px;")
    main_layout.addWidget(info_label)
    
    # Splitter for side-by-side layout
    splitter = QSplitter(Qt.Horizontal)
    
    # Left side: Before/After tables
    tables_widget = QWidget()
    tables_layout = QVBoxLayout(tables_widget)
    tables_layout.setContentsMargins(5, 5, 5, 5)
    
    # Before table
    tables_layout.addWidget(QLabel("<b>Before:</b>"))
    before_str = tabulate.tabulate(df_before.values.tolist(), df_before.columns.tolist(),
                                  tablefmt='github', floatfmt='.4f')
    before_label = QLabel(f"<pre>{before_str}</pre>")
    before_label.setStyleSheet("background-color: #fff9e6; padding: 10px; font-family: 'Courier New';")
    before_label.setWordWrap(False)
    tables_layout.addWidget(before_label)
    
    # After table
    tables_layout.addWidget(QLabel("<b>After:</b>"))
    after_str = tabulate.tabulate(df_after.values.tolist(), df_after.columns.tolist(),
                                 tablefmt='github', floatfmt='.4f')
    after_label = QLabel(f"<pre>{after_str}</pre>")
    after_label.setStyleSheet("background-color: #e6f9e6; padding: 10px; font-family: 'Courier New';")
    after_label.setWordWrap(False)
    tables_layout.addWidget(after_label)
    
    tables_scroll = QScrollArea()
    tables_scroll.setWidget(tables_widget)
    tables_scroll.setWidgetResizable(True)
    
    splitter.addWidget(tables_scroll)
    
    # Right side: Graph
    graph_widget = QWidget()
    graph_layout = QVBoxLayout(graph_widget)
    graph_layout.setContentsMargins(5, 5, 5, 5)
    
    img_label = QLabel()
    pixmap = QPixmap()
    pixmap.load(png_path)
    img_label.setPixmap(pixmap)
    img_label.setScaledContents(True)
    
    graph_scroll = QScrollArea()
    graph_scroll.setWidget(img_label)
    graph_scroll.setWidgetResizable(True)
    graph_layout.addWidget(graph_scroll)
    
    splitter.addWidget(graph_widget)
    
    # Set initial sizes (40-60 split: tables smaller, graph larger)
    splitter.setSizes([400, 600])
    
    main_layout.addWidget(splitter)
    
    return tab


# ── Shared progress/timeout engine ───────────────────────────────────────
