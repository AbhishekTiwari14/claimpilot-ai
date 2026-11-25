"""Generated synthetic pixels and matching offline fixture observations."""

from functools import cache
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageDraw


@cache
def sample_image(*, irrelevant: bool = False) -> bytes:
    image = Image.new("RGB", (480, 320), "#d7cec1")
    draw = ImageDraw.Draw(image)
    if irrelevant:
        draw.ellipse((100, 50, 380, 270), fill="#0068a4")
    else:
        draw.rectangle((0, 255, 480, 320), fill="#846e56")
        draw.rectangle((350, 95, 479, 254), fill="#725742")
        draw.polygon([(55, 70), (125, 74), (182, 126), (135, 188), (79, 175)], fill="#9a8055")
        draw.line([(150, 124), (172, 153), (165, 176), (184, 204)], fill="#544c43", width=3)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def offline_observation(photo_id: str, original_sha256: str) -> dict:
    is_damage = original_sha256 == sha256(sample_image()).hexdigest()
    is_irrelevant = original_sha256 == sha256(sample_image(irrelevant=True)).hexdigest()
    quality = "usable" if is_damage else "irrelevant" if is_irrelevant else "insufficient"
    detail = (
        "This synthetic fixture depicts a stained wall. Its origin and extent are unverified."
        if is_damage else "This synthetic image has no property scene."
        if is_irrelevant else "Offline mode has no observation fixture for these pixels."
    )
    return {
        "image_id": photo_id,
        "image_quality": quality,
        "visible": {
            "observed_damage_types": ["surface_discoloration", "cracking"] if is_damage else [],
            "affected_areas": ["interior wall"] if is_damage else [],
            "materials": ["painted wall surface"] if is_damage else [],
            "visible_indicators": ["brown staining", "thin visible line"] if is_damage else [],
        },
        "interpretation": {
            "apparent_severity": "minor" if is_damage else "unknown",
            "potential_safety_concerns": [],
            "suggested_specializations": ["general_restoration"] if is_damage else [],
        },
        "limitations": [detail],
        "follow_up_questions": ["Is the stain still changing?"] if is_damage else [],
    }
