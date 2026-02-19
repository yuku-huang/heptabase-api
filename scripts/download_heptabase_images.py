import argparse
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests


HEPTABASE_API = "https://api.heptabase.com/v1/whiteboard-sharing/?secret="
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
CONTENT_TYPE_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}


def is_uuid(value):
    return isinstance(value, str) and bool(UUID_PATTERN.match(value))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download images from Heptabase shared whiteboard data."
    )
    parser.add_argument(
        "--whiteboard-id",
        help="Heptabase shared whiteboard secret.",
    )
    parser.add_argument(
        "--input-json",
        default="data.json",
        help="Read local payload JSON instead of fetching from API.",
    )
    parser.add_argument(
        "--from-local-json",
        action="store_true",
        help="Use --input-json as source payload.",
    )
    parser.add_argument(
        "--output-dir",
        default="heptabase-assets",
        help="Directory to store downloaded images.",
    )
    parser.add_argument(
        "--manifest",
        default="heptabase-images-manifest.json",
        help="Path to output manifest JSON.",
    )
    parser.add_argument(
        "--rewrite-output",
        default="",
        help="Optional path to write payload with image src rewritten to local files.",
    )
    parser.add_argument(
        "--local-prefix",
        default="./heptabase-assets",
        help="Prefix used when rewriting image src.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if local file already exists.",
    )
    return parser.parse_args()


def load_payload(args):
    if args.from_local_json:
        with open(args.input_json, "r", encoding="utf-8") as f:
            return json.load(f)

    if not args.whiteboard_id:
        raise ValueError("Missing --whiteboard-id when not using --from-local-json.")

    res = requests.get(HEPTABASE_API + args.whiteboard_id, timeout=args.timeout)
    res.raise_for_status()
    return res.json()


def get_payload_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def get_cards_container(payload_data):
    cards = payload_data.get("cards")
    if isinstance(cards, list):
        return cards

    for value in payload_data.values():
        if not isinstance(value, list) or not value:
            continue
        sample = value[0]
        if isinstance(sample, dict) and "id" in sample and "content" in sample:
            return value
    return []


def get_default_owner_id(payload_data):
    whiteboards = payload_data.get("whiteboards")
    if isinstance(whiteboards, list) and whiteboards:
        first = whiteboards[0]
        if isinstance(first, dict):
            return first.get("createdBy")
    return None


def clean_url(url):
    if not isinstance(url, str):
        return None
    trimmed = url.strip()
    if not trimmed:
        return None
    return trimmed.split("#")[0]


def resolve_image_url(attrs, owner_id):
    src = clean_url(attrs.get("src"))
    if src:
        return src

    file_id = attrs.get("fileId")
    if file_id and owner_id:
        return f"https://media.heptabase.com/v1/images/{owner_id}/{file_id}/image.png"
    return None


def iter_image_nodes(node):
    if isinstance(node, dict):
        if node.get("type") == "image":
            attrs = node.get("attrs")
            if isinstance(attrs, dict):
                yield node, attrs

        content = node.get("content")
        if isinstance(content, list):
            for child in content:
                yield from iter_image_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from iter_image_nodes(child)


def extension_from_url_or_type(url, content_type):
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext and 0 < len(ext) <= 6:
        return ext

    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type in CONTENT_TYPE_TO_EXT:
        return CONTENT_TYPE_TO_EXT[content_type]

    guessed = mimetypes.guess_extension(content_type) if content_type else None
    return guessed or ".bin"


def make_filename(card_id, attrs, url, ext):
    image_id = attrs.get("id") or attrs.get("fileId")
    if not image_id:
        image_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return Path(card_id) / f"{image_id}{ext}"


def download_file(url, output_path, timeout, force=False):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        return False

    response = requests.get(url, stream=True, timeout=timeout)
    response.raise_for_status()

    ext = extension_from_url_or_type(url, response.headers.get("content-type"))
    if output_path.suffix != ext:
        output_path = output_path.with_suffix(ext)

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return str(output_path)


def process_images(payload, output_dir, local_prefix, timeout=25, force=False):
    payload_data = get_payload_data(payload)
    cards = get_cards_container(payload_data)
    default_owner = get_default_owner_id(payload_data)
    output_dir = Path(output_dir)

    manifest = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }

    for card in cards:
        card_id = card.get("id")
        if not is_uuid(card_id):
            continue

        raw_content = card.get("content")
        if not isinstance(raw_content, str):
            continue

        try:
            doc = json.loads(raw_content)
        except json.JSONDecodeError:
            continue

        owner_id = card.get("createdBy") or default_owner
        changed = False

        for _node, attrs in iter_image_nodes(doc):
            image_url = resolve_image_url(attrs, owner_id)
            if not image_url:
                continue

            ext = extension_from_url_or_type(image_url, None)
            relative_file = make_filename(card_id, attrs, image_url, ext)
            absolute_file = output_dir / relative_file

            item = {
                "cardId": card_id,
                "imageId": attrs.get("id"),
                "fileId": attrs.get("fileId"),
                "sourceUrl": image_url,
                "localFile": str(absolute_file).replace("\\", "/"),
                "status": "skipped",
            }

            try:
                saved = download_file(
                    image_url,
                    absolute_file,
                    timeout=timeout,
                    force=force,
                )
                if saved:
                    item["localFile"] = str(saved).replace("\\", "/")
                    item["status"] = "downloaded"
                    manifest["downloaded"] += 1
                else:
                    manifest["skipped"] += 1
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                manifest["failed"] += 1
                manifest["items"].append(item)
                continue

            local_src = f"{local_prefix.rstrip('/')}/{relative_file.as_posix()}"
            attrs["src"] = local_src
            changed = True
            manifest["items"].append(item)

        if changed:
            card["content"] = json.dumps(doc, ensure_ascii=False)

    return payload, manifest


def main():
    args = parse_args()
    payload = load_payload(args)
    rewritten_payload, manifest = process_images(
        payload=payload,
        output_dir=args.output_dir,
        local_prefix=args.local_prefix,
        timeout=args.timeout,
        force=args.force,
    )

    with open(args.manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if args.rewrite_output:
        with open(args.rewrite_output, "w", encoding="utf-8") as f:
            json.dump(rewritten_payload, f, ensure_ascii=False, indent=2)

    print(
        f"downloaded={manifest['downloaded']} skipped={manifest['skipped']} "
        f"failed={manifest['failed']}"
    )


if __name__ == "__main__":
    main()
