import sys
from pathlib import Path

from PIL import Image
from openpyxl import load_workbook

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.midjourney_runner import MidjourneyRunner


def test_images_are_inserted_into_cells(tmp_path):
    out_dir = tmp_path / "images"
    out_dir.mkdir()
    img_path = out_dir / "1_sample.png"
    Image.new("RGB", (60, 40), "red").save(img_path)

    runner = MidjourneyRunner("Test")
    workbook_path = tmp_path / "images.xlsx"
    runner._create_images_workbook(str(out_dir), str(workbook_path))

    wb = load_workbook(workbook_path)
    ws = wb.active
    assert ws._images, "Workbook should contain an image"
    img = ws._images[0]
    assert img.anchor._from.col == 2
    assert img.anchor._from.row == 1
