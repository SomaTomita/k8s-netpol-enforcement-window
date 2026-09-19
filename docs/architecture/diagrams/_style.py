"""Shared style definitions. Each diagram imports this to keep a consistent look."""

GRAPH_ATTR = {
    "fontsize": "16",
    "fontname": "Helvetica",
    "pad": "0.4",
    "splines": "ortho",
    "nodesep": "0.5",
    "ranksep": "0.9",
    "bgcolor": "white",
}

NODE_ATTR = {
    "fontsize": "11",
    "fontname": "Helvetica",
}

EDGE_ATTR = {
    "fontsize": "10",
    "fontname": "Helvetica",
}

CLUSTER_MEASURE = {"bgcolor": "#eef6ff", "style": "rounded", "fontsize": "13"}
CLUSTER_TARGET = {"bgcolor": "#fff4e6", "style": "rounded", "fontsize": "13"}
CLUSTER_ANALYSIS = {"bgcolor": "#eefaf0", "style": "rounded", "fontsize": "13"}
CLUSTER_EXTERNAL = {"bgcolor": "#f4f4f4", "style": "rounded", "fontsize": "13"}
CLUSTER_WARN = {"bgcolor": "#ffecec", "style": "rounded", "fontsize": "13"}

# Edges that highlight the primary measured quantities.
E_MEASURE = {"color": "#c0392b", "penwidth": "2.2", "fontcolor": "#c0392b"}
E_DATA = {"color": "#2c3e50"}
E_CONTROL = {"color": "#7f8c8d", "style": "dashed"}
