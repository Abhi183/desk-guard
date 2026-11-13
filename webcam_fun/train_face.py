"""Teach the app more views of your face from photos you already have.

  ./run train                 uses the selfies in snaps/ and the photos in caught/
  ./run train ~/Pictures/me   any folders or image files

Only faces that already look like you are added (same rule the app uses live), so
other people in those photos are skipped. Your face must have been learned first.
"""

import argparse
import sys
from pathlib import Path

import cv2

from webcam_fun.recognition import FaceId
from webcam_fun.vision import ROOT, FaceDetector

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
PASSES = 3  # a sample added late can make an earlier photo qualify


def image_files(sources: list[Path]) -> list[Path]:
    files = []
    for source in sources:
        if source.is_dir():
            files.extend(sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES))
        elif source.suffix.lower() in IMAGE_SUFFIXES and source.is_file():
            files.append(source)
        else:
            print(f"skipped {source}: not a folder or a jpg/png file")
    return files


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", type=Path,
                   default=[ROOT / "snaps", ROOT / "caught"])
    args = p.parse_args()
    face_id = FaceId(lambda _error: None)
    if face_id.learning:
        sys.exit("Your face has not been learned yet. Start Desk Guard and look at the "
                 "camera for a few seconds first.")
    detector = FaceDetector()
    embeddings = []
    for path in image_files(args.sources):
        image = cv2.imread(str(path))
        if image is None:
            print(f"skipped {path.name}: could not read it")
            continue
        try:
            embeddings.extend(face_id.embed(image, face) for face in detector.detect_faces(image))
        except cv2.error as e:
            print(f"skipped {path.name}: {e}")
    before = face_id.samples
    for _ in range(PASSES):
        added = sum(face_id.train(embedding) for embedding in embeddings)
        if not added:
            break
    print(f"Looked at {len(embeddings)} faces. Your profile went from {before} to "
          f"{face_id.samples} samples. Restart Desk Guard to use it.")


if __name__ == "__main__":
    main()
