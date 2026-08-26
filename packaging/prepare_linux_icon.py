import sys
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_icon import find_app_icon


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: prepare_linux_icon.py <output.png>")

    source_icon = find_app_icon(PROJECT_ROOT / "ico")
    if source_icon is None:
        raise SystemExit(
            "No supported icon found. Add ico/logo.ico or ico/app.ico."
        )

    output_path = Path(sys.argv[1]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source_icon) as opened_image:
        if hasattr(opened_image, "ico"):
            sizes = opened_image.ico.sizes()
            image = opened_image.ico.getimage(
                max(sizes, key=lambda size: size[0] * size[1])
            )
        else:
            image = opened_image.copy()
        image = image.convert("RGBA")
        if image.size != (256, 256):
            image = image.resize((256, 256), Image.Resampling.LANCZOS)
        image.save(output_path, format="PNG")

    print("Linux icon created from: {0}".format(source_icon))


if __name__ == "__main__":
    main()
