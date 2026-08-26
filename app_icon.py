from pathlib import Path


PREFERRED_ICON_NAMES = (
    "logo.ico",
    "app.ico",
)


def find_app_icon(icon_directory):
    """Return the preferred ICO file from the application's icon directory."""
    icon_directory = Path(icon_directory)
    if not icon_directory.is_dir():
        return None

    icons_by_name = {
        path.name.lower(): path
        for path in icon_directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".ico"
    }
    for preferred_name in PREFERRED_ICON_NAMES:
        selected = icons_by_name.get(preferred_name)
        if selected is not None:
            return selected

    return None
