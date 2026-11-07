"""Semantic colour tokens shared by the renderers and embedded in exported data.

``CurveSeries.color_hint`` / ``Region.color_hint`` reference these token
names, so a client can resolve them with ``EnvelopeData.palette[theme][token]``.
"""

PALETTE: dict[str, dict[str, str]] = {
    "light": {
        "background": "#FFFFFF",
        "text": "#0F172A",
        "muted": "#64748B",
        "grid": "#94A3B8",
        "slate": "#334155",
        "cobalt": "#1D4ED8",
        "coral": "#E8590C",
        "amber": "#D97706",
        "teal": "#0F766E",
        "violet": "#6D28D9",
        "crimson": "#B91C1C",
        "safe": "#2563EB",
    },
    "dark": {
        "background": "#0B1120",
        "text": "#E2E8F0",
        "muted": "#94A3B8",
        "grid": "#475569",
        "slate": "#CBD5E1",
        "cobalt": "#60A5FA",
        "coral": "#FF8A5B",
        "amber": "#FBBF24",
        "teal": "#2DD4BF",
        "violet": "#A78BFA",
        "crimson": "#F87171",
        "safe": "#3B82F6",
    },
}


def resolve_color(token: str, theme: str = "light") -> str:
    """Token -> hex. Literal ``#rrggbb`` strings pass through."""
    if token.startswith("#"):
        return token
    return PALETTE[theme].get(token, PALETTE[theme]["text"])
