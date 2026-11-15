"""The core's themes: Violet (the default) and Blue."""

from p42cloud.ui.themes.models import Theme

VIOLET = Theme(
    "violet",
    "Violet",
    description="Violet accents, a dark violet navigator and raised white cards.",
    radius=4,
    cards="raised",
    font="roboto",
    colors={
        "primary": ("#7d00fa", "#d5baff"),
        "on-primary": ("#ffffff", "#42008a"),
        "primary-container": ("#ecdcff", "#5f00c0"),
        "secondary": ("#645b70", "#cec2db"),
        "secondary-container": ("#eadef7", "#4b4357"),
        "tertiary": ("#ba005c", "#ffb1c5"),
        "tertiary-container": ("#ffd9e1", "#8f0045"),
        "error": ("#ba1a1a", "#ffb4ab"),
        "surface": ("#fef8fc", "#151316"),
        "on-surface": ("#1d1b1e", "#e6e1e6"),
        "on-surface-variant": ("#49454e", "#e8e0eb"),
        "surface-container-low": ("#f8f2f6", "#1d1b1e"),
        "surface-container": ("#f2ecf1", "#211f22"),
        "surface-container-high": ("#ede6eb", "#2b292d"),
        "surface-container-highest": ("#e6e1e6", "#363437"),
        "outline": ("#7b757f", "#958e99"),
        "outline-variant": ("#cbc4cf", "#49454e"),
        "inverse-surface": ("#323033", "#e6e1e6"),
        "page-background": ("#eceaf1", "#121014"),
        "card": ("#ffffff", "#211f22"),
        "toolbar": ("#eceaf1", "#121014"),
        "nav-background": ("#2a1758", "#1c1236"),
        "nav-background-end": ("#5b3f8c", "#3a2a5c"),
        "on-nav": ("#ffffff", "#ece6f5"),
        "on-nav-variant": ("#cfc4e6", "#b8adcc"),
        "nav-accent": ("#ffffff", "#d5baff"),
        "nav-active": ("#ffffff", "#ece6f5"),
        "on-nav-active": ("#1d1b1e", "#1d1b1e"),
        "message-information": ("#dbe8ff", "#13305a"),
        "on-message-information": ("#0b2a52", "#dbe8ff"),
        "message-warning": ("#ffefc2", "#4a3800"),
        "on-message-warning": ("#3d2e00", "#ffe08a"),
        "message-critical": ("#ffdad6", "#690005"),
        "on-message-critical": ("#410002", "#ffdad6"),
        # The first chart colour follows the theme's violet.
        "chart-1": ("#6250d6", "#9085e9"),
        "chart-7": ("#2a78d6", "#3987e5"),
    },
)

BLUE = Theme(
    "blue",
    "Blue",
    description="The app's built-in blue, with a light navigator and outlined cards.",
    colors={
        "page-background": ("#faf9fd", "#121316"),
        "card": ("#faf9fd", "#121316"),
        "toolbar": ("#efedf0", "#1f2022"),
        "nav-background": ("#f4f3f6", "#1a1b1f"),
        "nav-accent": ("#005cbb", "#abc7ff"),
    },
)

THEMES = (VIOLET, BLUE)
